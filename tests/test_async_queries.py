"""Cross-platform native pipes, query scheduling, and child lifetime checks."""
import asyncio
import contextlib
import io
import json
import os
from pathlib import Path
import runpy
import subprocess
import signal
import sys
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / 'oai-usage'
M = runpy.run_path(str(SCRIPT), run_name='async_query_tests')
G = M['main'].__globals__
FIXTURE = Path(__file__).parent / 'fixtures/prices.json'

# Run with the current interpreter on every OS; no executable bits or shebang.
APP_SERVER = r'''
import json, sys, time
mode = sys.argv[1]
initialized = False
if mode == 'resist':
    import signal
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
if mode == 'stall':
    time.sleep(30)
for line in sys.stdin:
    request = json.loads(line)
    method = request['method']
    if method == 'initialize':
        assert request['id'] == 0
        assert request['params']['clientInfo']['name'] == 'oai-usage'
        if mode == 'init_error':
            print(json.dumps({'id': 0, 'error': {}}), flush=True)
        else:
            print(json.dumps({'id': 0, 'result': {}}), flush=True)
    elif method == 'initialized':
        initialized = True
    elif method == 'account/rateLimits/read':
        assert initialized and request['id'] == 1
        if mode == 'error':
            print(json.dumps({'id': 1, 'error': {}}), flush=True)
        elif mode == 'eof':
            sys.stdout.write('{"id":1')
            sys.stdout.flush()
            sys.exit(7)
        elif mode == 'oversize':
            sys.stdout.write('x' * (8 * 1024 * 1024 + 1))
            sys.stdout.flush()
            time.sleep(30)
        elif mode == 'no_quota':
            print(json.dumps({'id': 1, 'result': {}}), flush=True)
        elif mode == 'wrong_id':
            for value in ('1', True, 21):
                print(json.dumps({'id': value, 'result': {}}), flush=True)
            time.sleep(30)
        else:
            print('invalid JSON', flush=True)
            print(json.dumps({'method': 'notification'}), flush=True)
            print(json.dumps({'id': 42, 'result': {}}), flush=True)
            reply = json.dumps({'id': 1, 'result': {'rateLimitsByLimitId': {'codex': {
                'primary': {'usedPercent': 25, 'windowDurationMins': 300,
                            'resetsAt': int(time.time()) + 3600}}},
                'rateLimitResetCredits': {'availableCount': 2}}}) + '\n'
            for piece in (reply[:9], reply[9:30], reply[30:]):
                sys.stdout.write(piece)
                sys.stdout.flush()
                time.sleep(.005)
            if mode == 'resist':
                time.sleep(30)
'''


def catalog():
    return M['parse_price_catalog'](json.loads(FIXTURE.read_bytes()), 'github')


async def catalog_async():
    return catalog()


class AsyncQueries(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.fake = self.root / 'app_server.py'
        self.fake.write_text(APP_SERVER, encoding='utf-8')
        self.children = []
        original = G['start_process']

        async def capture(*args, **kwargs):
            child = await original(*args, **kwargs)
            self.children.append(child)
            return child

        self.patcher = patch.dict(G, {'start_process': capture})
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    async def asyncTearDown(self):
        # Check reaping and both ends of each pipe, not just a returned error.
        for child in self.children:
            self.assertIsNotNone(child.returncode)
            if child.stdout:
                self.assertTrue(child.stdout.at_eof())
            if child.stdin:
                self.assertTrue(child.stdin.is_closing())
        pending = [task for task in asyncio.all_tasks() if task is not asyncio.current_task() and not task.done()]
        self.assertEqual(pending, [])

    async def live(self, mode='success', timeout=2):
        return await M['fetch_live'](None, timeout, _command=[sys.executable, str(self.fake), mode])

    async def wait_children(self, count):
        async with asyncio.timeout(3):
            while len(self.children) < count:
                await asyncio.sleep(.005)

    def write_logs(self, stamp=None, limit=False):
        stamp = stamp or M['datetime'].now(M['UTC']).isoformat()
        usage = {'input_tokens': 100, 'cached_input_tokens': 0, 'output_tokens': 0, 'total_tokens': 100}
        rows = [{'type': 'session_meta', 'timestamp': stamp, 'payload': {'id': 'local', 'timestamp': stamp}},
                {'type': 'turn_context', 'payload': {'model': 'gpt-6-sol'}},
                {'type': 'event_msg', 'timestamp': stamp, 'payload': {'type': 'token_count',
                 'info': {'total_token_usage': usage, 'last_token_usage': usage}}}]
        if limit:
            rows[-1]['payload']['rate_limits'] = {'limit_id': 'codex', 'primary': {'used_percent': 10}}
        path = self.root / 'session.jsonl'
        path.write_text('\n'.join(json.dumps(row) for row in rows) + '\n', encoding='utf-8')
        return path

    async def test_protocol_chunked_notifications_and_initialization(self):
        current, error = await self.live()
        self.assertIsNone(error)
        self.assertEqual(current['source'], 'app_server')
        self.assertEqual(current['limits']['codex']['primary']['used_percent'], 25)
        self.assertEqual(current['rate_limit_reset_credits'], {'available_count': 2})

    async def test_protocol_rejection_eof_invalid_result_and_buffer_limit(self):
        for mode, message in [('init_error', 'rejected'), ('error', 'rejected'), ('eof', 'exited'),
                              ('no_quota', 'recognizable'), ('oversize', 'too large')]:
            with self.subTest(mode=mode):
                current, error = await self.live(mode)
                self.assertIsNone(current)
                self.assertIn(message, error)

    async def test_total_timeout_and_request_id_matching(self):
        for mode in ('stall', 'wrong_id'):
            with self.subTest(mode=mode):
                current, error = await self.live(mode, .3)
                self.assertIsNone(current)
                self.assertIn('exceeded 0.3 seconds', error)

    async def test_cancel_inflight_query_reaps_child(self):
        task = asyncio.create_task(self.live('stall', 10))
        await self.wait_children(1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task

    async def test_cancel_after_cleanup_started_still_finishes_cleanup(self):
        original = G['_stop_process']
        entered, release = asyncio.Event(), asyncio.Event()
        async def delayed(child):
            entered.set()
            await release.wait()
            await original(child)
        with patch.dict(G, {'_stop_process': delayed}):
            task = asyncio.create_task(self.live())
            await entered.wait()
            task.cancel()
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task

    @unittest.skipIf(os.name == 'nt', 'Windows terminate forcibly stops the child')
    async def test_cancel_cleanup_of_child_ignoring_terminate_still_kills(self):
        original = G['_stop_process']
        entered = asyncio.Event()
        async def cleanup(child):
            entered.set()
            await original(child)
        with patch.dict(G, {'_stop_process': cleanup}):
            task = asyncio.create_task(self.live('resist'))
            try:
                await entered.wait()
                await asyncio.sleep(.02)
                task.cancel()
                async with asyncio.timeout(3):
                    with self.assertRaises(asyncio.CancelledError):
                        await task
                self.assertEqual(self.children[-1].returncode, -signal.SIGKILL)
            finally:
                if self.children[-1].returncode is None:
                    self.children[-1].kill()
                    await self.children[-1].wait()

    async def test_cancel_during_spawn_still_reaps_child(self):
        original_spawn = asyncio.create_subprocess_exec
        started = asyncio.Event()

        async def delayed(*args, **kwargs):
            child = await original_spawn(*args, **kwargs)
            self.children.append(child)
            started.set()
            await asyncio.sleep(.05)
            return child

        with patch.object(asyncio, 'create_subprocess_exec', delayed):
            task = asyncio.create_task(self.live('stall', 10))
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

    async def test_cancel_during_failed_spawn_preserves_cancellation(self):
        started = asyncio.Event()
        async def failed(*args, **kwargs):
            started.set()
            await asyncio.sleep(.02)
            raise OSError('spawn failed')
        with patch.object(asyncio, 'create_subprocess_exec', failed):
            task = asyncio.create_task(self.live('stall', 10))
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

    async def test_live_result_drives_report_and_watch_cycle_projection(self):
        self.write_logs((M['datetime'].now(M['UTC']) - M['timedelta'](minutes=2)).isoformat())
        for command in ('report', 'watch'):
            async def real_live(*args):
                return await self.live()
            with patch.dict(G, {'load_price_catalog_async': catalog_async, 'fetch_live': real_live}), \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                code = await M['async_main']([command, '--root', str(self.root), '--quota', 'live',
                                               '--json', '--count', '1', '--timezone', 'UTC'])
            self.assertEqual(code, 0)
            data = json.loads(output.getvalue())
            self.assertEqual(data['summary']['current_rate_limits']['source'], 'app_server')
            estimate = data['summary']['cycle_estimates']['primary']
            self.assertTrue(estimate['projection_available'])
            local = estimate['local']['api_cost_usd']
            self.assertAlmostEqual(estimate['projected_full_cycle_api_cost_usd'], local * 4)
            self.assertAlmostEqual(estimate['projected_remaining_api_cost_usd'], local * 3)

    async def test_report_queries_concurrently_then_scans_logs(self):
        price_started, quota_started, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        current, _ = await self.live()
        async def price():
            price_started.set()
            await release.wait()
            return catalog()
        async def quota(*args):
            quota_started.set()
            await release.wait()
            # Logs created during observation must be included by the later scan.
            self.write_logs(current['fetched_at'])
            return current, None
        with patch.dict(G, {'load_price_catalog_async': price, 'fetch_live': quota}), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            run = asyncio.create_task(M['async_main'](['--root', str(self.root), '--quota', 'live', '--json']))
            async with asyncio.timeout(2):
                await price_started.wait()
                await quota_started.wait()
            self.assertFalse(run.done())
            release.set()
            self.assertEqual(await run, 0)
        data = json.loads(output.getvalue())
        self.assertEqual(data['summary']['cycle_estimates']['primary']['local']['usage']['total_tokens'], 100)

    async def test_quota_failure_does_not_cancel_price_and_modes_keep_semantics(self):
        self.write_logs(limit=True)
        finished = []
        async def price():
            await asyncio.sleep(.02)
            finished.append(True)
            return catalog()
        async def quota(*args):
            return None, 'login unavailable'
        for mode, source in [('auto', 'session_log'), ('live', 'unavailable')]:
            with patch.dict(G, {'load_price_catalog_async': price, 'fetch_live': quota}), \
                    contextlib.redirect_stdout(io.StringIO()) as output:
                code = await M['async_main'](['--root', str(self.root), '--quota', mode, '--json'])
            self.assertEqual(code, 0)
            data = json.loads(output.getvalue())
            self.assertEqual(data['summary']['current_rate_limits']['source'], source)
        self.assertEqual(len(finished), 2)

    async def test_startup_price_failure_cancels_live_query(self):
        async def price():
            await self.wait_children(1)
            raise ValueError('invalid prices')
        async def quota(*args):
            return await self.live('stall', 10)
        with patch.dict(G, {'load_price_catalog_async': price, 'fetch_live': quota}), \
                contextlib.redirect_stderr(io.StringIO()) as error:
            code = await M['async_main'](['--quota', 'live'])
        self.assertEqual(code, 1)
        self.assertIn('invalid prices', error.getvalue())

    async def test_prices_off_and_logs_never_start_account_queries(self):
        self.write_logs()
        def prohibited(*args, **kwargs):
            self.fail('account query started')
        for argv in (['prices', '--json'], ['--root', str(self.root), '--quota', 'off'],
                     ['watch', '--root', str(self.root), '--quota', 'logs', '--count', '1']):
            with patch.dict(G, {'load_price_catalog_async': catalog_async, 'fetch_live': prohibited,
                                'find_codex': prohibited}), contextlib.redirect_stdout(io.StringIO()):
                await M['async_main'](argv)
        self.assertEqual(self.children, [])

    async def test_watch_repeated_queries_and_stop_clean_all_children(self):
        calls = 0
        async def quota(*args):
            nonlocal calls
            calls += 1
            return await self.live()
        args = M['parse_args'](['watch', '--quota', 'live', '--quota-interval', '.01'])
        scheduler = M['QueryScheduler'](args)
        with patch.dict(G, {'load_price_catalog_async': catalog_async, 'fetch_live': quota}):
            try:
                await scheduler.start()
                async with asyncio.timeout(3):
                    while calls < 3:
                        await asyncio.sleep(.01)
                await self.wait_children(3)
            finally:
                await scheduler.stop()
        self.assertEqual(scheduler.tasks, [])
        self.assertGreaterEqual(len(self.children), 3)

    async def test_background_price_refresh_does_not_pause_frames_and_swaps_atomically(self):
        self.write_logs()
        calls, frames = [], []
        started, release = asyncio.Event(), asyncio.Event()
        initial = catalog()
        changed = M['replace'](initial, prices={**initial.prices,
                       'gpt-6-sol': M['replace'](initial.prices['gpt-6-sol'], input=M['Decimal']('9'))})
        async def price():
            calls.append(True)
            if len(calls) == 1:
                return initial
            started.set()
            await release.wait()
            return changed
        real_sleep, real_report = asyncio.sleep, G['report']
        async def sleep(delay):
            await real_sleep(.001 if delay == 3600 else .01)
        def frame(*args):
            data = real_report(*args)
            frames.append(data)
            if len(frames) == 3:
                self.assertTrue(started.is_set())
                release.set()
            return data
        with patch.dict(G, {'load_price_catalog_async': price, 'report': frame}), \
                patch.object(asyncio, 'sleep', sleep), contextlib.redirect_stdout(io.StringIO()):
            code = await M['async_main'](['watch', '--root', str(self.root), '--quota', 'off', '--json', '--count', '5'])
        self.assertEqual(code, 0)
        costs = [frame['summary']['api_cost_usd'] for frame in frames]
        self.assertEqual(costs[:3], [costs[0]] * 3)
        self.assertGreater(costs[3], costs[0])
        self.assertEqual(costs[3:], [costs[3]] * 2)
        self.assertEqual(frames[3]['pricing']['models']['gpt-6-sol']['input'], '9')

    async def test_price_refresh_failure_keeps_catalog_until_recovery(self):
        initial = catalog()
        changed = M['replace'](initial, verified_at=initial.verified_at, fetched_at='recovered')
        failed, recover = asyncio.Event(), asyncio.Event()
        calls = 0
        async def price():
            nonlocal calls
            calls += 1
            if calls == 1:
                return initial
            if calls == 2:
                failed.set()
                raise ValueError('invalid downloaded catalog')
            await recover.wait()
            return changed
        real_sleep = asyncio.sleep
        async def sleep(delay):
            await real_sleep(.005 if delay == 3600 else delay)
        scheduler = M['QueryScheduler'](M['parse_args'](['watch', '--quota', 'off']))
        with patch.dict(G, {'load_price_catalog_async': price}), patch.object(asyncio, 'sleep', sleep):
            try:
                await scheduler.start()
                async with asyncio.timeout(2):
                    await failed.wait()
                    while not scheduler.price_error:
                        await real_sleep(.001)
                old, _ = scheduler.snapshot()
                self.assertIs(old.prices, initial.prices)
                self.assertIn('last valid prices in memory', old.warnings[-1])
                recover.set()
                async with asyncio.timeout(2):
                    while scheduler.price_error:
                        await real_sleep(.001)
                new, _ = scheduler.snapshot()
                self.assertEqual(new.fetched_at, 'recovered')
                self.assertEqual(new.warnings, initial.warnings)
            finally:
                await scheduler.stop()

    async def test_watch_cancel_cleans_background_refresh(self):
        self.write_logs()
        calls = []
        async def price():
            calls.append(True)
            if len(calls) == 1:
                return catalog()
            return await self.live('stall', 10)
        real_sleep = asyncio.sleep
        async def sleep(delay):
            await real_sleep(.005 if delay == 3600 else delay)
        with patch.dict(G, {'load_price_catalog_async': price}), patch.object(asyncio, 'sleep', sleep), \
                contextlib.redirect_stdout(io.StringIO()):
            task = asyncio.create_task(M['async_main'](['watch', '--root', str(self.root), '--quota', 'off', '--json']))
            await self.wait_children(1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task


class AsyncPrices(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.worker = Path(self.temp.name) / 'worker.py'
        self.children = []
        original = G['start_process']
        async def capture(*args, **kwargs):
            child = await original(*args, **kwargs)
            self.children.append(child)
            return child
        self.patcher = patch.dict(G, {'start_process': capture})
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    async def asyncTearDown(self):
        for child in self.children:
            self.assertIsNotNone(child.returncode)
            self.assertTrue(child.stdout.at_eof())

    async def test_async_worker_success_and_strict_catalog_validation(self):
        self.worker.write_text(f'import sys\nsys.stdout.buffer.write({FIXTURE.read_bytes()!r})\n')
        with patch.dict(G, {'DOWNLOAD_WORKER': self.worker.read_text()}):
            current = await M['load_price_catalog_async']()
        self.assertEqual(current.origin, 'github')
        self.assertIsNotNone(current.fetched_at)
        self.worker.write_text("print('invalid catalog')\n")
        with patch.dict(G, {'DOWNLOAD_WORKER': self.worker.read_text()}), self.assertRaises(ValueError):
            await M['load_price_catalog_async']()

    async def test_async_worker_timeout_cancel_and_size_bound(self):
        self.worker.write_text('import time\ntime.sleep(30)\n')
        with patch.dict(G, {'DOWNLOAD_WORKER': self.worker.read_text()}):
            with self.assertRaisesRegex(M['DownloadError'], 'total time limit'):
                await M['fetch_https_async'](M['PRICE_URL'], total_timeout=.2, socket_timeout=.1)
            task = asyncio.create_task(M['fetch_https_async'](M['PRICE_URL']))
            async with asyncio.timeout(3):
                while len(self.children) < 2:
                    await asyncio.sleep(.005)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.worker.write_text("import sys, time\nsys.stdout.buffer.write(b'x'*10001)\nsys.stdout.flush()\ntime.sleep(30)\n")
        with patch.dict(G, {'DOWNLOAD_WORKER': self.worker.read_text()}), self.assertRaises(M['DownloadError']):
            await M['fetch_https_async'](M['PRICE_URL'], limit=10000)

    async def test_async_url_and_budgets_rejected_before_spawn(self):
        for url, kwargs in [('https://evil.example/', {}), (M['PRICE_URL'], {'limit': 1_000_001}),
                            (M['PRICE_URL'], {'total_timeout': 61}),
                            (M['PRICE_URL'], {'socket_timeout': 7})]:
            with self.subTest(url=url, kwargs=kwargs), self.assertRaises(ValueError):
                await M['fetch_https_async'](url, **kwargs)
        self.assertEqual(self.children, [])


class WindowsDiscovery(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        for context in (patch.dict(G, {'WINDOWS': True}), patch.dict(os.environ, {
                'PATH': str(self.root / 'path'), 'APPDATA': str(self.root / 'roaming'),
                'LOCALAPPDATA': str(self.root / 'local'), 'CODEX_HOME': str(self.root / 'home'),
                'CODEX_CLI_PATH': '', 'CODEX_INSTALL_DIR': ''}),
                patch.object(M['platform'], 'machine', return_value='AMD64')):
            context.start()
            self.addCleanup(context.stop)

    def touch(self, relative):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'fixture')
        return path

    def test_path_executable_and_explicit_environment_priority(self):
        native = self.touch('path/codex.exe')
        override = self.touch('custom/codex.exe')
        self.assertEqual(M['find_codex'](), str(native))
        with patch.dict(os.environ, {'CODEX_CLI_PATH': str(override)}):
            self.assertEqual(M['find_codex'](), str(override))
        self.assertEqual(M['find_codex'](str(override)), str(override))
        self.assertIsNone(M['find_codex'](str(self.root / 'missing.exe')))

    def test_npm_native_layouts_and_architectures(self):
        for arch, triple in [('AMD64', 'x86_64-pc-windows-msvc'), ('ARM64', 'aarch64-pc-windows-msvc')]:
            for layout in ('bin', 'codex'):
                for nested in (False, True):
                    with self.subTest(arch=arch, layout=layout, nested=nested):
                        prefix = self.root / f'{arch}-{layout}-{nested}'
                        shim = prefix / 'codex.cmd'
                        shim.parent.mkdir()
                        shim.write_text('npm wrapper')
                        package = prefix / 'node_modules' / '@openai' / 'codex'
                        modules = package / 'node_modules' if nested else prefix / 'node_modules'
                        platform_name = 'codex-win32-arm64' if arch == 'ARM64' else 'codex-win32-x64'
                        native = modules / '@openai' / platform_name / 'vendor' / triple / layout / 'codex.exe'
                        native.parent.mkdir(parents=True)
                        native.write_bytes(b'fixture')
                        with patch.object(M['platform'], 'machine', return_value=arch):
                            self.assertEqual(M['find_codex'](str(shim)), str(native))

    def test_npm_local_bin_and_vendor_fallback_without_shell(self):
        shim = self.touch('project/node_modules/.bin/codex.ps1')
        native = self.touch('project/node_modules/@openai/codex/vendor/x86_64-pc-windows-msvc/codex/codex.exe')
        self.assertEqual(M['find_codex'](str(shim)), str(native))
        self.assertIsNone(M['find_codex'](str(self.touch('broken/codex.cmd'))))

    def test_standalone_and_desktop_relocated_runtimes(self):
        for relative in ('home/packages/standalone/current/bin/codex.exe',
                         'local/Programs/OpenAI/Codex/bin/codex.exe',
                         'local/OpenAI/Codex/bin/codex.exe',
                         'local/OpenAI/Codex/bin/0123456789abcdef/codex.exe'):
            with self.subTest(relative=relative):
                native = self.touch(relative)
                self.assertEqual(M['find_codex'](), str(native))
                native.unlink()
        self.touch('local/OpenAI/Codex/bin/.staging-0123456789abcdef/codex.exe')
        self.assertIsNone(M['find_codex']())


class InterruptCleanup(unittest.TestCase):
    def test_ctrl_c_during_concurrent_startup_reaps_both_workers(self):
        # SIGINT enters asyncio.Runner's real Ctrl+C handler on either OS. The
        # worker fixture is launched with Python, never a platform shell.
        bootstrap = '''import asyncio, json, runpy, signal, sys
namespace = runpy.run_path(sys.argv[1], run_name="interrupt_fixture")
globals_ = namespace["main"].__globals__
children = []
start = globals_["start_process"]
live = namespace["fetch_live"]
async def capture(*args, **kwargs):
    child = await start(*args, **kwargs)
    children.append(child)
    return child
async def interrupt():
    while len(children) < 2:
        await asyncio.sleep(.005)
    signal.raise_signal(signal.SIGINT)
async def price():
    asyncio.create_task(interrupt())
    return await namespace["fetch_https_async"](namespace["PRICE_URL"])
async def quota(*args):
    return await live(None, 10, _command=[sys.executable, sys.argv[2]])
globals_.update(start_process=capture, load_price_catalog_async=price,
                fetch_live=quota, DOWNLOAD_WORKER="import time; time.sleep(30)")
code = namespace["main"](["--quota", "live", "--json"])
assert code == 0, code
assert len(children) == 2
assert all(child.returncode is not None and child.stdout.at_eof() for child in children)
print(json.dumps({"code": code, "reaped": len(children)}))
'''
        with tempfile.TemporaryDirectory() as root:
            worker = Path(root) / 'stall.py'
            worker.write_text('import time\ntime.sleep(30)\n')
            result = subprocess.run([sys.executable, '-c', bootstrap, str(SCRIPT), str(worker)],
                                    capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {'code': 0, 'reaped': 2})


if __name__ == '__main__':
    unittest.main()
