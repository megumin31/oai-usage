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
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cache = self.root / 'cache/prices.json'
        self.bundle = self.root / 'prices.json'
        self.raw = sample()
        self.bundle.write_bytes(encoded(self.raw))
        for context in (patch.object(prices, 'price_cache_path', return_value=self.cache),
                        patch.object(prices, 'PRICE_FILE', self.bundle),
                        patch.dict(os.environ, {'OAI_USAGE_OFFLINE_PRICES': '0'})):
            context.start()
            self.addCleanup(context.stop)

    def load(self, raw):
        with patch.object(prices, 'fetch_https', return_value=encoded(raw)):
            return prices.load_price_catalog()

    def test_real_bundled_catalog_meets_schema_without_fixed_prices(self):
        actual = Path(prices.__file__).with_name('prices.json')
        catalog = prices.parse_price_catalog(prices.strict_json(prices.read_limited(actual)), 'bundled')
        self.assertTrue(catalog.prices)

    def test_rejects_future_dates_duplicate_keys_nesting_and_bad_numbers(self):
        bad = copy.deepcopy(self.raw)
        bad['verified_at'] = '9999-12-31'
        nested = b'{"unused":' + b'[' * 1100 + b'0' + b']' * 1100 + b'}'
        values = [encoded(bad), nested, b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":1.2}',
                  b'{"a":' + b'9' * 1000 + b'}', b'{"a":"' + b'x' * 5000 + b'"}',
                  b'x' * (prices.MAX_BYTES + 1)]
        for value in values:
            with self.subTest(size=len(value)), patch.object(prices, 'fetch_https', return_value=value):
                result = prices.load_price_catalog()
                self.assertEqual(result.origin, 'bundled')
                self.assertTrue(result.warnings)
                self.assertFalse(self.cache.exists())
        for rate in ('NaN', 'Infinity', '-1', '1e-999999', '1' * 100):
            bad = copy.deepcopy(self.raw)
            bad['models']['gpt-6-sol']['input'] = rate
            with self.subTest(rate=rate), self.assertRaises(ValueError):
                prices.parse_price_catalog(bad, 'candidate')

    def test_valid_remote_replaces_newer_cache_and_recovers_poisoned_cache(self):
        self.load(self.raw)
        correction = copy.deepcopy(self.raw)
        correction['verified_at'] = (datetime.now(prices.UTC).date() - timedelta(days=2)).isoformat()
        correction['models']['gpt-6-sol']['input'] = '3'
        result = self.load(correction)
        self.assertEqual(result.origin, 'github')
        self.assertEqual(result.prices['gpt-6-sol'].input, 3)
        self.assertIsNotNone(result.fetched_at)
        envelope = json.loads(self.cache.read_text())
        self.assertEqual(envelope['catalog'], correction)
        self.assertEqual(envelope['fetched_at'], result.fetched_at)
        poisoned = copy.deepcopy(self.raw)
        poisoned['verified_at'] = '9999-12-31'
        self.cache.write_bytes(encoded(poisoned))
        self.assertEqual(self.load(self.raw).origin, 'github')

    def test_tomorrows_verification_date_preserves_valid_cache(self):
        self.load(self.raw)
        before = self.cache.read_bytes()
        tomorrow = copy.deepcopy(self.raw)
        tomorrow['verified_at'] = (datetime.now(prices.UTC).date() + timedelta(days=1)).isoformat()
        self.assertEqual(self.load(tomorrow).origin, 'cache')
        self.assertEqual(self.cache.read_bytes(), before)

    def test_invalid_response_preserves_cache_and_transport_failure_falls_back(self):
        self.load(self.raw)
        before = self.cache.read_bytes()
        for error in (IncompleteRead(b'partial', 20), prices.DownloadError('timeout'), OSError('offline')):
            with self.subTest(error=type(error).__name__), patch.object(prices, 'fetch_https', side_effect=error):
                self.assertEqual(prices.load_price_catalog().origin, 'cache')
                self.assertEqual(self.cache.read_bytes(), before)
        with patch.object(prices, 'fetch_https', return_value=b'[' * 1100):
            self.assertEqual(prices.load_price_catalog().origin, 'cache')
            self.assertEqual(self.cache.read_bytes(), before)

    def test_previous_cache_offline_bundle_and_reset(self):
        self.load(self.raw)
        newer = copy.deepcopy(self.raw)
        newer['models']['gpt-6-sol']['input'] = '3'
        self.load(newer)
        self.assertTrue(prices.previous_cache_path().exists())
        self.cache.write_bytes(b'bad data')
        with patch.object(prices, 'fetch_https', side_effect=AssertionError('Must stay offline')):
            restored = prices.load_price_catalog(offline=True)
            self.assertEqual(restored.origin, 'previous_cache')
            self.assertEqual(restored.prices['gpt-6-sol'].input, 2)
            self.assertEqual(prices.load_price_catalog(bundled_only=True).origin, 'bundled')
            prices.clear_price_cache()
            self.assertFalse(self.cache.exists())
            self.assertFalse(prices.previous_cache_path().exists())
            self.assertEqual(prices.load_price_catalog(offline=True).origin, 'bundled')
            with patch.dict(os.environ, {'OAI_USAGE_OFFLINE_PRICES': '1'}):
                self.assertEqual(prices.load_price_catalog().origin, 'bundled')

    def test_cache_size_bound_legacy_format_and_readonly_cache(self):
        self.cache.parent.mkdir(parents=True)
        self.cache.write_bytes(encoded(self.raw))
        self.assertIsNone(prices.load_price_catalog(offline=True).fetched_at)
        self.cache.write_bytes(b'x' * (prices.MAX_CACHE_BYTES + 1))
        self.assertEqual(prices.load_price_catalog(offline=True).origin, 'bundled')
        with patch.object(prices, 'cache_price_catalog', side_effect=PermissionError('read only')):
            result = self.load(self.raw)
            self.assertEqual(result.origin, 'github')
            self.assertTrue(result.warnings)

    def test_staleness_and_cli_recovery_metadata(self):
        old = copy.deepcopy(self.raw)
        old['verified_at'] = (datetime.now(prices.UTC).date() - timedelta(days=46)).isoformat()
        self.bundle.write_bytes(encoded(old))
        self.assertTrue(prices.catalog_info(prices.load_price_catalog(bundled_only=True))['stale'])
        self.load(self.raw)
        cli = runpy.run_path(str(Path(prices.__file__).with_name('oai-usage')), run_name='recovery_test')
        from contextlib import redirect_stdout
        output = io.StringIO()
        with redirect_stdout(output), patch.object(prices, 'fetch_https', side_effect=AssertionError('Must stay offline')):
            self.assertEqual(cli['main'](['prices', '--bundled-prices', '--reset-price-cache', '--json']), 0)
        data = json.loads(output.getvalue())
        self.assertEqual(data['catalog_source'], 'bundled')
        self.assertTrue(data['stale'])
        self.assertFalse(self.cache.exists())


class DownloadSecurity(unittest.TestCase):
    def test_only_known_https_urls_and_no_redirects(self):
        for url in ('http://raw.githubusercontent.com/megumin31/oai-usage/main/prices.json',
                    'https://evil.example/prices.json', 'https://developers.openai.com.evil.example/api/docs/pricing.md',
                    'https://developers.openai.com/api/docs/pricing.md?redirect=evil'):
            with self.subTest(url=url), self.assertRaises(prices.DownloadError), patch.object(prices.subprocess, 'run') as proc:
                prices.fetch_https(url)
            proc.assert_not_called()
        prices.validate_download_url('https://developers.openai.com/api/docs/models/gpt-5.4.md')
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
