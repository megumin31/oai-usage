"""Regression coverage for response receipts, watch overflow, and I/O failures."""
import asyncio
import contextlib
import io
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import time
import unittest
from unittest.mock import AsyncMock, patch

from test_usage import M, G, DT, at, meta, context, token, fixture_catalog, fixture_prices


def usage(n, cached=0, output=0, reasoning=0):
    return dict(input_tokens=n, cached_input_tokens=cached, cache_write_input_tokens=0,
                output_tokens=output, reasoning_output_tokens=reasoning, total_tokens=n + output)


def receipt(response, amount, cumulative, minute=1, owner='own', stamp=None):
    return {'type': 'token_usage_record', 'timestamp': stamp or f'2026-09-22T00:{minute:02d}:00Z',
            'payload': {'thread_id': owner, 'session_id': 'runtime-session',
                        'turn_id': 'turn', 'root_turn_id': 'turn', 'response_id': response,
                        'usage': usage(amount) if isinstance(amount, int) else amount,
                        'thread_token_usage': usage(cumulative) if isinstance(cumulative, int) else cumulative,
                        'turn_token_usage': usage(cumulative) if isinstance(cumulative, int) else cumulative}}


class ResponseLedgers(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def account(self, rows, duplicate=False):
        encoded = ''.join(json.dumps(row) + '\n' for row in rows)
        (self.root / 'a.jsonl').write_text(encoded)
        if duplicate:
            (self.root / 'b.jsonl').write_text(encoded)
        return M['account'](M['Scanner']().scan([self.root]))

    def check(self, rows, total, duplicate=True):
        sessions, issues = self.account([meta(), context(), *rows], duplicate)
        self.assertEqual(sum(e.usage.total_tokens for s in sessions for e in s.events), total)
        return sessions, issues

    def test_native_only_uses_requests_not_initial_or_repeated_cumulative(self):
        self.check([receipt('a', 100, 1000), receipt('b', 100, 1000, 2)], 200)

    def test_no_session_metadata_uses_native_thread_identity(self):
        sessions, _ = self.account([receipt('a', 100, 100)], duplicate=True)
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0].session_id, 'own')
        self.assertEqual(sessions[0].events[0].usage.total_tokens, 100)
        self.assertEqual(sessions[0].events[0].model, 'unknown')

    def test_native_tail_and_legacy_only_prefix_middle_and_tail(self):
        self.check([token(100, 1, last=100), receipt('b', 50, 150, 2), token(150, 2, last=50),
                    token(180, 3, last=30), receipt('c', 70, 250, 4), token(250, 4, last=70),
                    token(270, 5, last=20), receipt('d', 10, 280, 6)], 280)

    def test_delayed_counts_and_equal_timestamp_reverse_order(self):
        for rows in ([receipt('a', 100, 100), receipt('b', 50, 150, 2),
                      token(100, 3, last=100), token(150, 4, last=50)],
                     [token(100, 1, last=100), receipt('a', 100, 100),
                      token(150, 2, last=50), receipt('b', 50, 150, 2)]):
            with self.subTest(rows=rows):
                self.check(rows, 150)

    def test_earlier_equal_legacy_request_is_not_a_new_native_receipt(self):
        self.check([token(100, 1, last=100), receipt('a', 100, 100, 2)], 200)

    def test_upgrade_counter_offset_preserves_legacy_history(self):
        self.check([token(1000, 1, last=1000), receipt('a', 50, 50, 2), token(1050, 2, last=50),
                    receipt('b', 50, 100, 3), token(1100, 3, last=50)], 1100)

    def test_retransmitted_response_id_with_different_timestamp(self):
        self.check([receipt('a', 100, 100), token(100, 1, last=100),
                    receipt('b', 50, 150, 2), token(150, 2, last=50), receipt('a', 100, 100, 3)], 150)

    def test_later_count_already_covers_missing_middle_count(self):
        self.check([receipt('a', 100, 100), receipt('b', 50, 150, 2), token(150, 2, last=50)], 150)

    def test_missing_count_followed_by_legacy_only_request(self):
        sessions, _ = self.check([receipt('a', 100, 100), context('legacy-model'),
                                 token(150, 2, last=50)], 150)
        self.assertEqual([e.model for e in sessions[0].events], ['gpt-6-astra', 'legacy-model'])
        self.assertEqual([e.usage.total_tokens for e in sessions[0].events], [100, 50])

    def test_unproven_overlapping_aggregate_is_not_added_to_native(self):
        _, issues = self.check([receipt('a', 100, 100), token(150, 2)], 100)
        self.assertEqual(issues['unreconciled_usage_intervals'], 1)

    def test_uncertain_aggregate_keeps_distinct_known_legacy_request(self):
        _, issues = self.check([receipt('a', 100, 100), token(160, 2, last=50)], 150)
        self.assertEqual(issues['unreconciled_usage_intervals'], 1)

    def test_quota_repeat_does_not_steal_original_coverage(self):
        self.check([receipt('a', 100, 100), token(100, 1, last=100), token(100, 2, last=100)], 100)

    def test_compaction_checkpoint_is_not_a_new_request(self):
        row = receipt('a', 100, 100)
        self.check([row, token(100, 1, last=100), {'type': 'compacted', 'payload': {
            'latest_token_usage_record': row['payload']}}, token(0, 2, last=0),
            receipt('b', 50, 150, 3), token(50, 3, last=50)], 150)

    def test_compaction_counter_offset_with_no_legacy_delta(self):
        # Context recomputation keeps cumulative but has no valid request usage.
        count = token(100, 2)
        count['payload']['info']['last_token_usage'] = {'total_tokens': 40}
        self.check([receipt('a', 100, 100), token(100, 1, last=100), receipt('b', 50, 150, 2), count,
                    receipt('c', 20, 170, 3), token(120, 3, last=20)], 170)

    def test_upward_counter_domain_change_does_not_invent_residual(self):
        _, issues = self.check([receipt('a', 100, 100), token(100, 1, last=100),
                               receipt('b', 50, 150, 2), token(1150, 2, last=50)], 150)
        self.assertEqual(issues['unreconciled_usage_intervals'], 1)

    def test_all_replayed_timestamps_equal_with_different_counter_domain(self):
        self.check([token(1000, 1), receipt('a', 50, 150), token(1050, 1, last=50),
                    receipt('b', 70, 220), token(1120, 1, last=70)], 1120)

    def test_fork_excludes_parent_receipts_and_native_baseline(self):
        sessions, issues = self.account([meta('child', 'parent'), context(),
            receipt('parent-r', 1000, 1000, owner='parent'),
            receipt('child-r', 50, 1050, 2, owner='child'), token(1050, 2, last=50)], duplicate=True)
        self.assertEqual(sum(e.usage.total_tokens for s in sessions for e in s.events), 50)
        self.assertGreater(issues['inherited_usage_records_excluded'], 0)

    def test_usage_components_model_and_request_time_are_preserved(self):
        a, b = usage(100, 80, 20, 4), usage(30, 10, 5, 2)
        total = {k: a[k] + b[k] for k in a}
        c = token(135, 4)
        c['payload']['info'].update(total_token_usage=total, last_token_usage=b)
        sessions, _ = self.check([receipt('a', a, a), context('other-model'), receipt('b', b, total, 3), c], 155)
        events = sessions[0].events
        self.assertEqual([e.model for e in events], ['gpt-6-astra', 'other-model'])
        self.assertEqual([e.at for e in events], [at('2026-09-22T00:01:00Z'), at('2026-09-22T00:03:00Z')])
        self.assertEqual(sum(e.usage.reasoning_output_tokens for e in events), 6)

    def test_incremental_native_append_invalidates_report_cache(self):
        self.account([meta(), context(), receipt('a', 100, 100)])
        scanner, cache = M['Scanner'](), M['ReportCache']()
        args = M['parse_args'](['--days', 'all', '--quota', 'off'])
        def report():
            return M['report'](args, scanner, fixture_prices(), M['Calendar'].make('UTC'), [self.root], cache=cache, price_catalog=fixture_catalog())
        self.assertEqual(report()['summary']['usage']['total_tokens'], 100)
        with (self.root / 'a.jsonl').open('a') as output:
            output.write(json.dumps(receipt('b', 50, 150, 2)) + '\n')
        self.assertEqual(report()['summary']['usage']['total_tokens'], 150)
        read = scanner.bytes_read
        report()
        self.assertEqual(scanner.bytes_read, read)


class InputAndPeriods(unittest.TestCase):
    def test_complete_invalid_tail_matches_newline_and_is_not_replayed(self):
        depth = 100000
        row = (b'{"type":"event_msg","payload":{"type":"token_count","rate_limits":{"credits":{"balance":' +
               b'[' * depth + b'0' + b']' * depth + b'}}}}')
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'a.jsonl'
            for ending in (b'\n', b''):
                path.write_bytes(row + ending)
                scanner = M['Scanner']()
                logs = scanner.scan([path.parent])
                self.assertEqual(logs[0].issues['malformed_json_lines'], 1)
                self.assertEqual(logs[0].offset, path.stat().st_size)
                with path.open('ab') as stream:
                    stream.write(b'\n' + json.dumps(meta()).encode() + b'\n')
                logs = scanner.scan([path.parent])
                self.assertEqual(logs[0].issues['malformed_json_lines'], 1)
                self.assertTrue(logs[0].metadata_seen)

    def test_today_exact_midnight_empty_and_watch_advances_with_cache(self):
        class Clock(DT):
            stamp = '2026-09-22T03:59:59Z'
            @classmethod
            def now(cls, tz=None):
                return at(cls.stamp)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rows = [meta(), context(), token(100, stamp='2026-09-22T03:30:00Z', last=100)]
            (root / 'a.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in rows))
            args = M['parse_args'](['watch', '--today', '--timezone', 'America/New_York', '--quota', 'off'])
            scanner, cache, calendar = M['Scanner'](), M['ReportCache'](), M['Calendar'].make(args.timezone)
            with patch.dict(G, {'datetime': Clock}):
                for stamp, expected in [('2026-09-22T03:59:59Z', 100), ('2026-09-22T04:00:00Z', 0),
                                        ('2026-09-22T04:00:00.000001Z', 0)]:
                    Clock.stamp = stamp
                    data = M['report'](args, scanner, fixture_prices(), calendar, [root], cache=cache, price_catalog=fixture_catalog())
                    self.assertEqual(data['summary']['usage']['total_tokens'], expected)
                    self.assertEqual(data['period_summaries'][1]['summary']['usage']['total_tokens'], 100)
            self.assertEqual(scanner.bytes_read, (root / 'a.jsonl').stat().st_size)
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            M['parse_args'](['--since', '2026-09-23', '--until', '2026-09-21'])
        with self.assertRaises(ValueError):
            M['bounds'](M['parse_args'](['--since', '2026-09-22']), M['Calendar'].make('UTC'),
                        at('2026-09-22T00:00:00Z'))


class ProcessAndPrices(unittest.IsolatedAsyncioTestCase):
    @unittest.skipUnless(os.name == 'posix', 'Native POSIX process-group lifetime test')
    async def test_inherited_pipe_descendant_and_parent_exit_are_bounded(self):
        for exits in (False, True):
            with self.subTest(parent_exits=exits), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'pid'
                child = 'import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(30)'
                worker = ('import subprocess,sys,time,pathlib\n'
                          f'p=subprocess.Popen([sys.executable,"-c",{child!r}],stdin=subprocess.DEVNULL)\n'
                          f'pathlib.Path({str(path)!r}).write_text(str(p.pid))\n' +
                          ('sys.exit(0)\n' if exits else 'time.sleep(30)\n'))
                started = time.monotonic()
                try:
                    result, error = await M['fetch_live'](None, .15, _command=[sys.executable, '-c', worker])
                    self.assertIsNone(result)
                    self.assertLess(time.monotonic() - started, 1.3)
                    self.assertIn('exceeded', error)
                    pid = int(path.read_text())
                    async with asyncio.timeout(2):
                        while True:
                            try:
                                os.kill(pid, 0)
                            except ProcessLookupError:
                                break
                            await asyncio.sleep(.01)
                finally:
                    if path.exists():
                        try:
                            os.kill(int(path.read_text()), signal.SIGKILL)
                        except ProcessLookupError:
                            pass
        self.assertEqual([task for task in asyncio.all_tasks() if task is not asyncio.current_task() and not task.done()], [])

    async def test_price_format_and_transport_messages_are_distinct(self):
        for value in (b'{}', b'not json', b'\xff'):
            with patch.dict(G, {'fetch_https_async': AsyncMock(return_value=value)}):
                with self.assertRaisesRegex(ValueError, 'invalid price data') as caught:
                    await M['load_price_catalog_async']()
                self.assertNotIn('network', str(caught.exception))
        with patch.dict(G, {'fetch_https_async': AsyncMock(side_effect=M['DownloadError']('timeout'))}):
            with self.assertRaisesRegex(ValueError, 'download failed; check your network'):
                await M['load_price_catalog_async']()

    async def test_refresh_keeps_safe_error_category(self):
        for cause, label in ((M['DownloadError']('socket failure'), 'download failed'),
                             (ValueError('bad catalog'), 'invalid price data')):
            error = ValueError('load failed')
            error.__cause__ = cause
            scheduler = M['QueryScheduler'](M['parse_args'](['watch', '--quota', 'off']))
            scheduler.catalog = fixture_catalog()
            with patch.dict(G, {'load_price_catalog_async': AsyncMock(side_effect=error)}), \
                    patch.object(asyncio, 'sleep', AsyncMock(side_effect=[None, asyncio.CancelledError()])):
                with self.assertRaises(asyncio.CancelledError):
                    await scheduler.poll_prices()
            self.assertIn(label, scheduler.snapshot()[0].warnings[-1])
            self.assertIn('using the last valid prices', scheduler.snapshot()[0].warnings[-1])


class WatchOverflow(unittest.TestCase):
    def test_full_period_panels_survive_overflow_and_resize(self):
        # Use the existing deterministic dashboard fixture, including both periods.
        class Terminal(io.StringIO):
            def isatty(self):
                return True
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'a.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in
                [meta(), context(), token(100, last=100)]))
            args = M['parse_args'](['watch', '--days', 'all', '--quota', 'off'])
            data = M['report'](args, M['Scanner'](), fixture_prices(), M['Calendar'].make('UTC'), [root], price_catalog=fixture_catalog())
            for columns in (120, 140):
                output = Terminal()
                sizes = iter(os.terminal_size((columns, height)) for height in (200, 10, 200))
                def terminal_size(*args):
                    return next(sizes) if args else os.terminal_size((columns, 200))
                with patch.dict(G, {'report': lambda *a, **kw: data, 'terminal_ansi': lambda: (True, None)}), \
                        patch.object(M['shutil'], 'get_terminal_size', side_effect=terminal_size), \
                        contextlib.redirect_stdout(output):
                    result = M['main'](['watch', '--quota', 'off', '--count', '3', '--refresh', '.05', '--no-color', '--timezone', 'UTC'])
                self.assertEqual(result, 0)
                text = output.getvalue()
                self.assertEqual(text.count('\033[?1049h'), 1)
                self.assertEqual(text.count('\033[?1049l'), 1)
                self.assertEqual(text.count('\033[2J'), 1)
                self.assertIn('watch uses scrollback', text)
                body = M['render'](data, args, M['Calendar'].make('UTC'), columns)
                self.assertEqual(text.count(body), 3)
