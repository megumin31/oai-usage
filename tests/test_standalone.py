"""Exercise one installed script with live prices supplied by a transport fixture."""
import asyncio
import contextlib
import io
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

SCRIPT = Path(__file__).resolve().parents[1] / "oai-usage"

FIXTURE = Path(__file__).parent / 'fixtures/prices.json'
BOOTSTRAP = '''import runpy, sys
raw = sys.stdin.buffer.read()
namespace = runpy.run_path(sys.argv[1], run_name="standalone_cli_test")
async def fetch(*args):
    return raw
namespace["main"].__globals__["fetch_https_async"] = fetch
raise SystemExit(namespace["main"](sys.argv[2:]))
'''


class Standalone(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.installed = self.root / 'bin' / 'oai-usage'
        self.installed.parent.mkdir()
        shutil.copy2(SCRIPT, self.installed)
        self.env = {**os.environ, 'PYTHONPATH': '', 'XDG_CACHE_HOME': str(self.root / 'cache'),
                    'CODEX_HOME': str(self.root / 'codex'), 'OAI_USAGE_OFFLINE_PRICES': '1'}

    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(self.installed), *args], cwd=self.root,
                              env=self.env, capture_output=True, text=True, timeout=10)

    def run_mock_cli(self, *args, price_bytes=None):
        raw = FIXTURE.read_bytes() if price_bytes is None else price_bytes
        return subprocess.run([sys.executable, '-c', BOOTSTRAP, str(self.installed), *args],
                              cwd=self.root, env=self.env, input=raw.decode(),
                              capture_output=True, text=True, timeout=10)

    def run_stdin_cli(self, *args, price_bytes=None):
        source = self.installed.read_text(encoding='utf-8')
        if price_bytes is not None:
            namespace = runpy.run_path(str(self.installed), run_name='stdin_transport_fixture')
            # Keep the real subprocess transport; only replace HTTPS in its worker.
            worker = namespace['DOWNLOAD_WORKER'].rsplit('\nif __name__ == "__main__":', 1)[0]
            worker += f'\n_download = lambda *args: {price_bytes!r}\ndownload_main()\n'
            entry = '\nif __name__ == "__main__":\n'
            prefix, suffix = source.rsplit(entry, 1)
            source = prefix + f'\nDOWNLOAD_WORKER = {worker!r}\n' + entry + suffix
        return subprocess.run([sys.executable, '-X', 'utf8', '-', *args], input=source,
                              cwd=self.root, env=self.env, capture_output=True, text=True,
                              encoding='utf-8', timeout=10)

    def test_one_file_version_help_and_live_prices_without_disk_cache(self):
        for args in (('--version',), ('--help',)):
            result = self.run_cli(*args)
            self.assertEqual(result.returncode, 0, result.stderr)
        result = self.run_mock_cli('prices', '--json')
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data['catalog_source'], 'github')
        self.assertIsNotNone(data['fetched_at'])
        self.assertTrue(data['prices'])
        self.assertEqual([p.name for p in self.installed.parent.iterdir()], ['oai-usage'])
        self.assertFalse((self.root / 'cache').exists())
        namespace = runpy.run_path(str(self.installed), run_name='standalone_no_prices')
        self.assertNotIn('BUNDLED_CATALOG', namespace)
        self.assertNotIn('price_cache_path', namespace)

    def test_local_price_files_are_ignored_and_failure_has_no_disk_fallback(self):
        sibling = self.installed.parent / 'prices.json'
        sibling.write_bytes(FIXTURE.read_bytes())
        cache = self.root / 'cache' / 'oai-usage' / 'prices.json'
        cache.parent.mkdir(parents=True)
        cache.write_bytes(FIXTURE.read_bytes())
        result = self.run_mock_cli('prices', '--json', price_bytes=b'bad response')
        self.assertEqual(result.returncode, 1)
        self.assertIn('Unable to load prices from GitHub', result.stderr)
        self.assertEqual(result.stdout, '')
        self.assertEqual(sibling.read_bytes(), FIXTURE.read_bytes())
        self.assertEqual(cache.read_bytes(), FIXTURE.read_bytes())

    def sessions(self):
        sessions = self.root / 'sessions'
        sessions.mkdir()
        rows = [
            {'type': 'session_meta', 'timestamp': '2026-09-22T00:00:00Z',
             'payload': {'id': 'standalone'}},
            {'type': 'turn_context', 'payload': {'model': 'gpt-6-sol'}},
            {'type': 'event_msg', 'timestamp': '2026-09-22T00:01:00Z',
             'payload': {'type': 'token_count', 'info': {
                 'total_token_usage': {'input_tokens': 100, 'output_tokens': 20, 'total_tokens': 120},
                 'last_token_usage': {'input_tokens': 100, 'output_tokens': 20, 'total_tokens': 120}}}},
        ]
        (sessions / 'session.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in rows))
        return sessions

    def test_report_watch_and_output_protection_work_after_copy(self):
        sessions = self.sessions()
        common = ('--root', str(sessions), '--quota', 'off', '--days', 'all', '--json')
        for args in (common, ('watch', '--count', '1', *common)):
            with self.subTest(args=args):
                result = self.run_mock_cli(*args)
                self.assertEqual(result.returncode, 0, result.stderr)
                report = json.loads(result.stdout)
                self.assertEqual(report['summary']['usage']['total_tokens'], 120)
                self.assertIsNotNone(report['summary']['api_cost_usd'])
        before = self.installed.read_bytes()
        rejected = self.run_mock_cli(*common, '--output', str(self.installed))
        self.assertNotEqual(rejected.returncode, 0)
        self.assertEqual(self.installed.read_bytes(), before)
        self.assertFalse((self.root / 'cache').exists())

    def test_watch_refresh_failure_keeps_only_last_valid_prices_in_memory(self):
        sessions = self.sessions()
        namespace = runpy.run_path(str(self.installed), run_name='standalone_watch')
        globals_ = namespace['main'].__globals__
        initial = namespace['parse_price_catalog'](json.loads(FIXTURE.read_bytes()), 'github')
        calls = []
        async def fetch():
            calls.append(True)
            if len(calls) == 1:
                return initial
            raise OSError('offline')
        real_sleep = asyncio.sleep
        async def fast_sleep(delay):
            await real_sleep(.001 if delay == 3600 else .03)
        with patch.dict(globals_, {'load_price_catalog_async': fetch}), \
                patch.object(asyncio, 'sleep', fast_sleep), contextlib.redirect_stdout(io.StringIO()) as output:
            code = namespace['main'](['watch', '--root', str(sessions), '--quota', 'off', '--days', 'all', '--json', '--count', '2'])
        self.assertEqual(code, 0)
        reports = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(len(reports), 2)
        self.assertEqual(reports[0]['summary']['api_cost_usd'], reports[1]['summary']['api_cost_usd'])
        self.assertIn('last valid prices in memory', reports[1]['pricing']['warnings'][0])
        self.assertGreaterEqual(len(calls), 2)
        self.assertFalse((self.root / 'cache').exists())

    def test_same_fixed_worker_runs_from_copied_executable(self):
        namespace = runpy.run_path(str(self.installed), run_name='copied_worker_fixture')
        worker = namespace['DOWNLOAD_WORKER'].rsplit('\nif __name__ == "__main__":', 1)[0]
        worker += f'\n_download = lambda *args: {FIXTURE.read_bytes()!r}\ndownload_main()\n'
        source = self.installed.read_text(encoding='utf-8')
        entry = '\nif __name__ == "__main__":\n'
        prefix, suffix = source.rsplit(entry, 1)
        self.installed.write_text(prefix + f'\nDOWNLOAD_WORKER = {worker!r}\n' + entry + suffix, encoding='utf-8')
        result = self.run_cli('prices', '--json')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)['prices'])
        self.assertEqual([p.name for p in self.installed.parent.iterdir()], ['oai-usage'])

    def test_offline_and_cache_options_are_removed(self):
        for flag in ('--offline-prices', '--bundled-prices', '--reset-price-cache'):
            with self.subTest(flag=flag):
                result = self.run_cli('prices', flag)
                self.assertEqual(result.returncode, 2)
                self.assertIn('unrecognized arguments', result.stderr)

    def test_cli_contains_only_price_consumer_interfaces(self):
        namespace = runpy.run_path(str(self.installed), run_name='consumer_boundary')
        for name in ('read_limited', 'atomic_write', 'fetch_https', 'load_price_catalog', 'download_budget'):
            self.assertNotIn(name, namespace)
        self.assertNotIn('GENERATED PRICE CATALOG', self.installed.read_text(encoding='utf-8'))

    def test_stdin_version_help_and_prices_without_script_or_cache_files(self):
        for args in (('--version',), ('--help',), ('watch', '--help')):
            with self.subTest(args=args):
                result = self.run_stdin_cli(*args)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('oai-usage', result.stdout)
        result = self.run_stdin_cli('prices', '--json', price_bytes=FIXTURE.read_bytes())
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data['catalog_source'], 'github')
        self.assertTrue(data['prices'])
        self.assertEqual([path.name for path in self.root.iterdir()], ['bin'])

    def test_stdin_report_watch_arguments_and_output_protection(self):
        sessions = self.sessions().rename(self.root / '中文 sessions')
        common = ('--root', str(sessions), '--quota', 'off', '--days', 'all', '--json')
        result = self.run_stdin_cli(*common, price_bytes=FIXTURE.read_bytes())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['summary']['usage']['total_tokens'], 120)
        watched = self.run_stdin_cli('watch', '--count', '2', '--refresh', '.05', *common,
                                     price_bytes=FIXTURE.read_bytes())
        self.assertEqual(watched.returncode, 0, watched.stderr)
        frames = [json.loads(line) for line in watched.stdout.splitlines()]
        self.assertEqual(len(frames), 2)
        self.assertTrue(all(frame['summary']['usage']['total_tokens'] == 120 for frame in frames))
        output = self.root / 'report.json'
        saved = self.run_stdin_cli(*common, '--output', str(output), price_bytes=FIXTURE.read_bytes())
        self.assertEqual(saved.returncode, 0, saved.stderr)
        self.assertEqual(json.loads(output.read_text(encoding='utf-8')), json.loads(saved.stdout))
        log = sessions / 'session.jsonl'
        before = log.read_bytes()
        rejected = self.run_stdin_cli(*common, '--output', str(log), price_bytes=FIXTURE.read_bytes())
        self.assertEqual(rejected.returncode, 1)
        self.assertEqual(log.read_bytes(), before)
        self.assertFalse((self.root / 'cache').exists())

    def test_stdin_invalid_prices_exit_without_disk_fallback(self):
        result = self.run_stdin_cli('prices', '--json', price_bytes=b'invalid prices')
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, '')
        self.assertIn('Unable to load prices from GitHub', result.stderr)
        self.assertEqual([path.name for path in self.root.iterdir()], ['bin'])

    def test_embedded_worker_rejects_invalid_urls_and_limits(self):
        namespace = runpy.run_path(str(self.installed), run_name='stdin_worker_security')
        cases = (('https://invalid.example/prices.json', 1000, 1),
                 (namespace['PRICE_URL'], namespace['MAX_BYTES'] + 1, 1),
                 (namespace['MODEL_LIST_URL'] + '.md', 1000, 1),
                 (namespace['MODELS_DEV_URL'], 1000, 1),
                 (namespace['PRICE_URL'], 1000, 4),
                 (namespace['PRICE_URL'], 1000, 0))
        for url, limit, timeout in cases:
            with self.subTest(url=url, limit=limit, timeout=timeout):
                result = subprocess.run([sys.executable, '-c', namespace['DOWNLOAD_WORKER'],
                                         '--download', url, str(limit), str(timeout)],
                                        capture_output=True, text=True, timeout=5)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, '')
                self.assertIn('Price download failed', result.stderr)

    def test_stdin_worker_total_timeout_is_enforced(self):
        namespace = runpy.run_path(str(self.installed), run_name='stdin_worker_timeout')
        globals_ = namespace['fetch_https_async'].__globals__
        start = time.monotonic()
        with patch.dict(globals_, {'DOWNLOAD_WORKER': 'import time; time.sleep(30)'}), \
                self.assertRaisesRegex(namespace['DownloadError'], 'total time limit'):
            asyncio.run(namespace['fetch_https_async'](namespace['PRICE_URL'], total_timeout=.2, socket_timeout=.1))
        self.assertLess(time.monotonic() - start, 3)

    def test_short_long_short_requests_are_priced_independently(self):
        sessions = self.root / 'sessions'
        sessions.mkdir()
        rows = [
            {'type': 'session_meta', 'timestamp': '2026-09-22T00:00:00Z', 'payload': {'id': 'tier-demo'}},
            {'type': 'turn_context', 'payload': {'model': 'gpt-5.5'}},
        ]
        for day, total, last in ((22, 100000, 100000), (23, 400000, 300000), (24, 450000, 50000)):
            rows.append({'type': 'event_msg', 'timestamp': f'2026-09-{day}T01:00:00Z',
                         'payload': {'type': 'token_count', 'info': {
                             'total_token_usage': {'input_tokens': total, 'output_tokens': 0, 'total_tokens': total},
                             'last_token_usage': {'input_tokens': last, 'output_tokens': 0, 'total_tokens': last}}}})
        (sessions / 'session.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in rows))
        result = self.run_mock_cli('--root', str(sessions), '--quota', 'off', '--days', 'all', '--json')
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data['summary']['api_cost_usd'], 3.75)
        self.assertNotIn('long_context_scope', data['pricing']['models']['gpt-5.5'])
        self.assertNotIn('schema_version', data)
        result = self.run_mock_cli('--root', str(sessions), '--quota', 'off', '--since', '2026-09-22',
                                   '--until', '2026-09-22', '--timezone', 'UTC', '--json')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['summary']['api_cost_usd'], .5)

    def test_prices_json_preserves_unknown_cache_rates_as_null(self):
        raw = json.loads(FIXTURE.read_bytes())
        row = raw['models']['gpt-6-sol']
        row['cached_input'] = None
        row['long_context']['cached_input'] = None
        result = self.run_mock_cli('prices', '--json', price_bytes=json.dumps(raw).encode())
        self.assertEqual(result.returncode, 0, result.stderr)
        price = next(row for row in json.loads(result.stdout)['prices'] if row['model'] == 'gpt-6-sol')
        self.assertIsNone(price['cached'])
        self.assertIsNone(price['long_cached_input'])


if __name__ == '__main__':
    unittest.main()
