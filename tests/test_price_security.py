"""Price-table consumer validation and distinct producer download boundaries."""
import asyncio
import copy
import io
import json
import os
import runpy
import tempfile
import time
import unittest
import urllib.request
from datetime import timedelta
from http.client import IncompleteRead
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from scripts import price_support as producer

SCRIPT = Path(__file__).resolve().parents[1] / 'oai-usage'
M = runpy.run_path(str(SCRIPT), run_name='price_security')
G = M['main'].__globals__
WORKER = {'__name__': 'worker_security'}
exec(M['DOWNLOAD_WORKER'], WORKER)
W = SimpleNamespace(**WORKER)
FIXTURE = Path(__file__).parent / 'fixtures/prices.json'


def sample():
    raw = json.loads(FIXTURE.read_text())
    raw['verified_at'] = M['datetime'].now(M['UTC']).date().isoformat()
    return raw


def encoded(raw):
    return json.dumps(raw).encode()


class CatalogSecurity(unittest.IsolatedAsyncioTestCase):
    async def load(self, raw):
        with patch.dict(G, {'fetch_https_async': AsyncMock(return_value=encoded(raw))}):
            return await M['load_price_catalog_async']()

    async def test_only_current_price_structure_is_accepted(self):
        raw = sample()
        price = (await self.load(raw)).prices['gpt-5.5']
        self.assertFalse(hasattr(price, 'long_scope'))
        for change in ('schema_version', 'scope', 'old_source'):
            bad = copy.deepcopy(raw)
            if change == 'schema_version':
                bad['schema_version'] = 2
            elif change == 'scope':
                bad['models']['gpt-5.5']['long_context']['scope'] = 'session'
            else:
                bad['models']['gpt-5.5']['long_context']['source'] = bad['models']['gpt-5.5']['model_source']
            with self.subTest(change=change), self.assertRaises(ValueError):
                await self.load(bad)

    async def test_producer_output_is_consumed_with_identical_rates(self):
        from scripts import update_prices as sync
        fixtures = FIXTURE.parent
        observed = sync.collect((fixtures / 'openai-models-20260926.md').read_text(),
                                (fixtures / 'models-dev-openai-20260926.json').read_bytes())
        raw = sync.updated_catalog(sample(), observed, M['datetime'].now(M['UTC']).date().isoformat())
        consumer = await self.load(raw)
        production = producer.parse_price_catalog(raw, 'candidate')
        self.assertEqual({k: M['asdict'](v) for k, v in consumer.prices.items()},
                         {k: M['asdict'](v) for k, v in production.prices.items()})
        for bad in (sample(), copy.deepcopy(raw)):
            bad['models']['gpt-6-sol']['long_context']['scope'] = 'request'
            with self.assertRaises(ValueError):
                producer.parse_price_catalog(bad, 'candidate')
            with self.assertRaises(ValueError):
                await self.load(bad)

    async def test_optional_cache_read_prices_are_preserved_as_unknown(self):
        raw = sample()
        row = raw['models']['gpt-6-sol']
        row['cached_input'] = row['long_context']['cached_input'] = None
        price = (await self.load(raw)).prices['gpt-6-sol']
        self.assertIsNone(price.cached)
        self.assertIsNone(price.long_cached)

    async def test_live_catalog_provenance_and_no_price_writes(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'XDG_CACHE_HOME': directory}):
            result = await self.load(sample())
            self.assertEqual(result.origin, 'github')
            self.assertIsNotNone(result.fetched_at)
            self.assertEqual(result.prices['gpt-6-sol'].source, M['MODELS_DEV_URL'])
            self.assertEqual(M['catalog_info'](result)['model_source'], M['MODEL_LIST_URL'])
            self.assertEqual(list(Path(directory).iterdir()), [])

    async def test_invalid_provenance_is_rejected(self):
        raw = sample()
        for key, value in (('basis', 'subscription_bill'), ('provider', 'anthropic'),
                           ('source', 'https://evil.test/api.json'), ('model_source', None)):
            bad = copy.deepcopy(raw)
            bad[key] = value
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'Unable to load prices'):
                await self.load(bad)
        row = raw['models']['gpt-6-sol']
        for target, key, value in ((row, 'model_source', 'https://developers.openai.com/api/docs/models/other'),
                                  (row['long_context'], 'source', 'https://evil.test/api.json')):
            before = target[key]
            target[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                await self.load(raw)
            target[key] = before

    async def test_rejects_future_dates_duplicate_keys_nesting_and_bad_numbers(self):
        bad = sample()
        bad['verified_at'] = '9999-12-31'
        values = [encoded(bad), b'[' * 1100, b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":1.2}',
                  b'{"a":' + b'9' * 1000 + b'}', b'{"a":"' + b'x' * 5000 + b'"}',
                  b'x' * (M['MAX_BYTES'] + 1)]
        for value in values:
            with self.subTest(size=len(value)), patch.dict(G, {'fetch_https_async': AsyncMock(return_value=value)}), \
                    self.assertRaisesRegex(ValueError, 'Unable to load prices'):
                await M['load_price_catalog_async']()
        for rate in ('NaN', 'Infinity', '-1', '1e-999999', '1' * 100):
            bad = sample()
            bad['models']['gpt-6-sol']['input'] = rate
            with self.subTest(rate=rate), self.assertRaises(ValueError):
                await self.load(bad)

    async def test_transport_failure_does_not_use_local_prices(self):
        for error in (IncompleteRead(b'partial', 20), M['DownloadError']('timeout'), OSError('offline')):
            with self.subTest(error=type(error).__name__), \
                    patch.dict(G, {'fetch_https_async': AsyncMock(side_effect=error)}), \
                    self.assertRaisesRegex(ValueError, 'Unable to load prices'):
                await M['load_price_catalog_async']()

    async def test_staleness_and_each_load_fetches_current_prices(self):
        old = sample()
        old['verified_at'] = (M['datetime'].now(M['UTC']).date() - timedelta(days=46)).isoformat()
        new = sample()
        new['models']['gpt-6-sol']['input'] = '3'
        fetch = AsyncMock(side_effect=[encoded(old), encoded(new)])
        with patch.dict(G, {'fetch_https_async': fetch}):
            first = await M['load_price_catalog_async']()
            second = await M['load_price_catalog_async']()
        self.assertTrue(M['catalog_info'](first)['stale'])
        self.assertFalse(M['catalog_info'](second)['stale'])
        self.assertEqual(second.prices['gpt-6-sol'].input, 3)
        self.assertEqual(fetch.await_count, 2)

    async def test_consumer_rejects_upstream_urls_and_expanded_budgets_before_spawn(self):
        for url in (M['MODELS_DEV_URL'], M['MODEL_LIST_URL'] + '.md',
                    'https://evil.example/', M['PRICE_URL'] + '?x=1'):
            with self.subTest(url=url), self.assertRaises(M['DownloadError']), \
                    patch.dict(G, {'start_process': AsyncMock()}) as globals_:
                await M['fetch_https_async'](url)
                globals_['start_process'].assert_not_awaited()
        for kwargs in ({'limit': 1_000_001}, {'total_timeout': 7}, {'socket_timeout': 4}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                await M['fetch_https_async'](M['PRICE_URL'], **kwargs)


class DownloadSecurity(unittest.TestCase):
    def test_worker_rejects_upstream_urls_and_redirects(self):
        for url in (M['MODELS_DEV_URL'], M['MODEL_LIST_URL'] + '.md',
                    'https://evil.example/', M['PRICE_URL'] + '?x=1'):
            with self.subTest(url=url), self.assertRaises(W.DownloadError):
                W._download(url, 1000, 1)
        for target in ('http://127.0.0.1/', 'https://evil.example/', W.PRICE_URL):
            with self.subTest(target=target), self.assertRaises(W.DownloadError):
                W.NoRedirect().redirect_request(urllib.request.Request(W.PRICE_URL), None, 302, 'Found', {}, target)

    def test_bounded_reads_and_incomplete_content_length(self):
        class Response(io.BytesIO):
            status = 200
            def geturl(self):
                return W.PRICE_URL
        for body, headers in ((b'x' * 11, {}), (b'abc', {'Content-Length': '5'}),
                              (b'abc', {'Content-Encoding': 'gzip'})):
            response = Response(body)
            response.headers = headers
            with patch.object(urllib.request, 'build_opener', return_value=Mock(open=Mock(return_value=response))), self.assertRaises(W.DownloadError):
                W._download(W.PRICE_URL, 10, 1)
        response = Response(b'abc')
        response.headers = {'Content-Length': '3'}
        with patch.object(urllib.request, 'build_opener', return_value=Mock(open=Mock(return_value=response))):
            self.assertEqual(W._download(W.PRICE_URL, 10, 1), b'abc')

    def test_producer_total_timeout_terminates_a_stalled_worker(self):
        with tempfile.TemporaryDirectory() as root:
            worker = Path(root) / 'worker.py'
            pidfile = Path(root) / 'pid'
            worker.write_text('import os, time\nfrom pathlib import Path\nPath(__file__).with_name("pid").write_text(str(os.getpid()))\ntime.sleep(30)\n')
            start = time.monotonic()
            with patch.object(producer, '__file__', str(worker)), self.assertRaises(producer.DownloadError):
                producer.fetch_https(producer.MODELS_DEV_URL, total_timeout=1, socket_timeout=.1)
            self.assertLess(time.monotonic() - start, 5)
            self.assertTrue(pidfile.exists())
            if os.name != 'nt':
                with self.assertRaises(ProcessLookupError):
                    os.kill(int(pidfile.read_text()), 0)


if __name__ == '__main__':
    unittest.main()
