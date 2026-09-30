import copy
import io
import json
import os
import runpy
import tempfile
import time
import unittest
import urllib.request
from datetime import datetime, timedelta
from http.client import IncompleteRead
from pathlib import Path
from unittest.mock import Mock, patch

import oai_price_catalog as prices

FIXTURE = Path(__file__).parent / 'fixtures/prices.json'


def sample():
    raw = json.loads(FIXTURE.read_text())
    raw['verified_at'] = datetime.now(prices.UTC).date().isoformat()
    return raw


def encoded(raw):
    return json.dumps(raw).encode()


class CatalogSecurity(unittest.TestCase):
    def load(self, raw):
        with patch.object(prices, 'fetch_https', return_value=encoded(raw)):
            return prices.load_price_catalog()

    def test_legacy_session_scope_is_normalized_and_upstream_tier_source_is_valid(self):
        raw = sample()
        self.assertEqual(raw['models']['gpt-5.5']['long_context']['scope'], 'session')
        result = self.load(raw)
        self.assertEqual(result.prices['gpt-5.5'].long_scope, 'request')
        raw['models']['gpt-5.5']['long_context']['source'] = prices.MODELS_DEV_URL
        self.assertEqual(self.load(raw).prices['gpt-5.5'].rule_source, prices.MODELS_DEV_URL)

    def test_optional_cache_read_prices_are_preserved_as_unknown(self):
        raw = sample()
        row = raw['models']['gpt-6-sol']
        row['cached_input'] = None
        row['long_context']['cached_input'] = None
        price = self.load(raw).prices['gpt-6-sol']
        self.assertIsNone(price.cached)
        self.assertIsNone(price.long_cached)

    def test_live_catalog_provenance_and_no_price_writes(self):
        raw = sample()
        with patch.object(prices, 'atomic_write', side_effect=AssertionError('Must not write prices')), \
                tempfile.TemporaryDirectory() as directory, \
                patch.dict(os.environ, {'XDG_CACHE_HOME': directory, 'OAI_USAGE_OFFLINE_PRICES': '1'}):
            result = self.load(raw)
            self.assertEqual(result.origin, 'github')
            self.assertIsNotNone(result.fetched_at)
            self.assertEqual(result.prices['gpt-6-sol'].source, prices.MODELS_DEV_URL)
            self.assertEqual(prices.catalog_info(result)['model_source'], prices.MODEL_LIST_URL)
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_old_schema_and_invalid_provenance_are_rejected(self):
        raw = sample()
        for key, value in (('schema_version', 1), ('provider', 'anthropic'),
                           ('source', 'https://evil.test/api.json'), ('model_source', None)):
            bad = copy.deepcopy(raw)
            bad[key] = value
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'Unable to load prices'):
                self.load(bad)
        row = raw['models']['gpt-6-sol']
        for target, key, value in ((row, 'model_source', 'https://developers.openai.com/api/docs/models/other'),
                                  (row['long_context'], 'source', 'https://evil.test/api.json')):
            before = target[key]
            target[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.load(raw)
            target[key] = before

    def test_rejects_future_dates_duplicate_keys_nesting_and_bad_numbers(self):
        bad = sample()
        bad['verified_at'] = '9999-12-31'
        values = [encoded(bad), b'[' * 1100, b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":1.2}',
                  b'{"a":' + b'9' * 1000 + b'}', b'{"a":"' + b'x' * 5000 + b'"}',
                  b'x' * (prices.MAX_BYTES + 1)]
        for value in values:
            with self.subTest(size=len(value)), patch.object(prices, 'fetch_https', return_value=value), \
                    self.assertRaisesRegex(ValueError, 'Unable to load prices'):
                prices.load_price_catalog()
        for rate in ('NaN', 'Infinity', '-1', '1e-999999', '1' * 100):
            bad = sample()
            bad['models']['gpt-6-sol']['input'] = rate
            with self.subTest(rate=rate), self.assertRaises(ValueError):
                self.load(bad)

    def test_transport_failure_does_not_use_local_prices(self):
        for error in (IncompleteRead(b'partial', 20), prices.DownloadError('timeout'), OSError('offline')):
            with self.subTest(error=type(error).__name__), \
                    patch.object(prices, 'fetch_https', side_effect=error), \
                    patch.object(prices, 'read_price_catalog', side_effect=AssertionError('No disk fallback')), \
                    self.assertRaisesRegex(ValueError, 'Unable to load prices'):
                prices.load_price_catalog()

    def test_staleness_and_each_load_fetches_current_prices(self):
        old = sample()
        old['verified_at'] = (datetime.now(prices.UTC).date() - timedelta(days=46)).isoformat()
        new = sample()
        new['models']['gpt-6-sol']['input'] = '3'
        with patch.object(prices, 'fetch_https', side_effect=[encoded(old), encoded(new)]) as fetch:
            first = prices.load_price_catalog()
            second = prices.load_price_catalog()
            self.assertTrue(prices.catalog_info(first)['stale'])
            self.assertFalse(prices.catalog_info(second)['stale'])
            self.assertEqual(second.prices['gpt-6-sol'].input, 3)
            self.assertEqual(fetch.call_count, 2)


class DownloadSecurity(unittest.TestCase):
    def test_upstream_budget_does_not_expand_client_or_official_limits(self):
        with patch.object(prices.subprocess, 'run', return_value=Mock(returncode=0, stdout=b'{}')) as proc:
            prices.fetch_https(prices.MODELS_DEV_URL, limit=8_000_000)
            self.assertEqual(proc.call_args.args[0][-2], '8000000')
            for url, limit in ((prices.PRICE_URL, 1_000_001),
                               ('https://developers.openai.com/api/docs/models/all.md', 2_000_001),
                               (prices.MODELS_DEV_URL, 8_000_001)):
                with self.subTest(url=url), self.assertRaises(ValueError):
                    prices.fetch_https(url, limit=limit)
        for url in ('https://models.dev.evil.test/api.json', 'https://models.dev/api.json?x=1',
                    'https://models.dev@evil.test/api.json', 'http://models.dev/api.json'):
            with self.subTest(url=url), self.assertRaises(prices.DownloadError):
                prices.validate_download_url(url)

    def test_only_known_https_urls_and_no_redirects(self):
        for url in ('http://raw.githubusercontent.com/megumin31/oai-usage/main/prices.json',
                    'https://evil.example/prices.json', 'https://developers.openai.com.evil.example/api/docs/pricing.md',
                    'https://developers.openai.com/api/docs/pricing.md?redirect=evil'):
            with self.subTest(url=url), self.assertRaises(prices.DownloadError), patch.object(prices.subprocess, 'run') as proc:
                prices.fetch_https(url)
            proc.assert_not_called()
        prices.validate_download_url('https://developers.openai.com/api/docs/models/all.md')
        for target in ('http://127.0.0.1/', 'https://evil.example/', prices.PRICE_URL):
            with self.subTest(target=target), self.assertRaises(prices.DownloadError):
                prices.NoRedirect().redirect_request(urllib.request.Request(prices.PRICE_URL), None, 302, 'Found', {}, target)

    def test_bounded_reads_and_incomplete_content_length(self):
        class Response(io.BytesIO):
            status = 200
            def geturl(self):
                return prices.PRICE_URL
        for body, headers in ((b'x' * 11, {}), (b'abc', {'Content-Length': '5'}),
                              (b'abc', {'Content-Encoding': 'gzip'})):
            response = Response(body)
            response.headers = headers
            with patch.object(urllib.request, 'build_opener', return_value=Mock(open=Mock(return_value=response))), self.assertRaises(prices.DownloadError):
                prices._download(prices.PRICE_URL, 10, 1)
        response = Response(b'abc')
        response.headers = {'Content-Length': '3'}
        with patch.object(urllib.request, 'build_opener', return_value=Mock(open=Mock(return_value=response))):
            self.assertEqual(prices._download(prices.PRICE_URL, 10, 1), b'abc')

    def test_total_timeout_terminates_a_stalled_worker(self):
        with tempfile.TemporaryDirectory() as root:
            worker = Path(root) / 'worker.py'
            pidfile = Path(root) / 'pid'
            worker.write_text('import os, time\nfrom pathlib import Path\nPath(__file__).with_name("pid").write_text(str(os.getpid()))\ntime.sleep(30)\n')
            start = time.monotonic()
            with patch.object(prices, '__file__', str(worker)), self.assertRaises(prices.DownloadError):
                prices.fetch_https(prices.PRICE_URL, total_timeout=1, socket_timeout=.1)
            self.assertLess(time.monotonic() - start, 5)
            self.assertTrue(pidfile.exists())
            if os.name != 'nt':
                with self.assertRaises(ProcessLookupError):
                    os.kill(int(pidfile.read_text()), 0)


if __name__ == '__main__':
    unittest.main()
