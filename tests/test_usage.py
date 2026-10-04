import contextlib
import ctypes
import io
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, call, patch

SCRIPT = Path(__file__).resolve().parents[1] / 'oai-usage'
M = runpy.run_path(str(SCRIPT), run_name='usage_tests')
G = M['main'].__globals__
U = M['Usage']
UTC = M['UTC']
DT = M['datetime']


def fixture_prices():
    return fixture_catalog().prices


def fixture_catalog():
    raw = json.loads((Path(__file__).parent / 'fixtures/prices.json').read_text())
    return M['parse_price_catalog'](raw, 'fixture', today=DT.now(UTC).date())


# Keep accounting/presentation tests deterministic; transport is tested separately.
async def fixture_catalog_async():
    return fixture_catalog()


G['load_price_catalog_async'] = fixture_catalog_async


def at(value='2026-09-22T01:00:00Z'):
    return M['timestamp'](value)


def usage(n, out=0, cached=0, write=0):
    return U(n, cached, write, out, 0, n + out)


def meta(sid='own', parent=None, created='2026-09-22T00:00:00Z'):
    return {'type': 'session_meta', 'timestamp': created,
            'payload': {'id': sid, 'session_id': parent or sid, 'timestamp': created,
                        'forked_from_id': parent}}


def context(model='gpt-6-astra'):
    return {'type': 'turn_context', 'payload': {'model': model}}


def token(n, minute=1, last=None, out=0, stamp=None):
    raw = {'total_token_usage': M['asdict'](usage(n, out)), 'model_context_window': 1050000}
    if last is not None:
        raw['last_token_usage'] = M['asdict'](usage(last, out))
    return {'type': 'event_msg', 'timestamp': stamp or f'2026-09-22T00:{minute:02d}:00Z',
            'payload': {'type': 'token_count', 'info': raw}}


class Fixtures(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, rows, newline=True):
        path = self.root / name
        path.write_text('\n'.join(json.dumps(r) for r in rows) + ('\n' if newline else ''))
        return path

    def scan(self):
        return M['account'](M['Scanner']().scan([self.root]))

    def test_identity_and_inherited_metadata(self):
        self.write('child.jsonl', [meta('child', 'parent'), meta('parent'), context(), token(100, last=100)])
        sessions, _ = self.scan()
        self.assertEqual(sessions[0].session_id, 'child')
        self.assertEqual(sessions[0].parent_id, 'parent')

    def test_parent_and_child_remain_independent(self):
        self.write('a.jsonl', [meta('parent'), context(), token(100, last=100)])
        self.write('b.jsonl', [meta('child', 'parent'), context(), token(200, last=200)])
        sessions, _ = self.scan()
        self.assertEqual({s.session_id for s in sessions}, {'parent', 'child'})
        self.assertEqual(sum(e.usage.total_tokens for s in sessions for e in s.events), 300)

    def test_duplicate_rollouts_are_idempotent(self):
        rows = [meta(), context(), token(100, last=100), token(150, 2, last=50)]
        self.write('a.jsonl', rows)
        self.write('b.jsonl', rows)
        sessions, issues = self.scan()
        self.assertEqual(sum(e.usage.total_tokens for e in sessions[0].events), 150)
        self.assertEqual(issues['duplicate_snapshots_removed'], 2)

    def test_partial_overlapping_rollouts(self):
        self.write('a.jsonl', [meta(), context(), token(100, last=100), token(150, 2, last=50)])
        self.write('b.jsonl', [meta(), context(), token(150, 2, last=50), token(180, 3, last=30)])
        sessions, _ = self.scan()
        self.assertEqual(sum(e.usage.total_tokens for e in sessions[0].events), 180)

    def test_separate_segment_larger_than_previous(self):
        self.write('a.jsonl', [meta(), context(), token(100, last=100)])
        self.write('b.jsonl', [meta(created='2026-09-22T00:02:00Z'), context(), token(500, 2, last=500)])
        sessions, _ = self.scan()
        self.assertEqual(sum(e.usage.total_tokens for e in sessions[0].events), 600)

    def test_monotonic_cumulative_is_not_reset_by_request_detail(self):
        self.write('a.jsonl', [meta(), context(), token(100, last=100), token(200, 2, last=200)])
        sessions, issues = self.scan()
        self.assertEqual(sum(e.usage.total_tokens for e in sessions[0].events), 200)
        self.assertFalse(any(e.reset for e in sessions[0].events))
        self.assertEqual(issues['request_detail_exceeds_delta'], 1)
        costs = M['priced_events'](sessions, fixture_prices())
        self.assertFalse(costs[1][1].complete)
        self.assertEqual(costs[1][1].amount, 0)

    def test_duplicate_snapshots_merge_richer_request_metadata(self):
        self.write('a.jsonl', [meta(), context(), token(300000, out=1000)])
        self.write('b.jsonl', [meta(), context(), token(300000, last=300000, out=1000)])
        sessions, _ = self.scan()
        rows = M['priced_events'](sessions, fixture_prices())
        self.assertEqual(rows[0][0].request_input, 300000)
        self.assertEqual(rows[0][1].amount, M['Decimal']('6.075'))
        self.assertTrue(rows[0][1].complete)

    def test_duplicate_metadata_conflicts_remain_uncertain(self):
        for cumulative in (100000, 300000):
            with self.subTest(cumulative=cumulative):
                for name, last in [('a',50000), ('b',300000), ('c',300000)]:
                    self.write(name+'.jsonl', [meta(), context(), token(cumulative, last=last)])
                sessions, issues = self.scan()
                cost = M['priced_events'](sessions, fixture_prices())[0][1]
                self.assertFalse(cost.complete)
                self.assertEqual(cost.amount, 0)
                self.assertGreater(issues['conflicting_request_metadata'], 0)

    def test_zero_counter_reset(self):
        self.write('a.jsonl', [meta(), context(), token(100), token(0, 2), token(150, 3)])
        sessions, _ = self.scan()
        self.assertEqual(sum(e.usage.total_tokens for e in sessions[0].events), 250)
        self.assertEqual(sum(e.reset for e in sessions[0].events), 1)

    def test_decreasing_counter_reset(self):
        self.write('a.jsonl', [meta(), context(), token(100), token(20, 2), token(40, 3)])
        sessions, _ = self.scan()
        self.assertEqual(sum(e.usage.total_tokens for e in sessions[0].events), 140)

    def test_fork_with_inherited_history(self):
        self.write('a.jsonl', [meta('child', 'parent', '2026-09-22T00:02:00Z'), meta('parent'),
            context(), token(100, 1, last=100), token(150, 3, last=50)])
        sessions, issues = self.scan()
        self.assertEqual(sum(e.usage.total_tokens for e in sessions[0].events), 50)
        self.assertEqual(issues['inherited_snapshots_excluded'], 1)

    def test_fork_without_inherited_history(self):
        self.write('a.jsonl', [meta('child', 'parent'), context(), token(1050, 1, last=50)])
        sessions, issues = self.scan()
        self.assertEqual(sessions[0].events[0].usage.total_tokens, 50)
        self.assertEqual(issues['inherited_baseline_tokens_excluded'], 1000)

    def test_fork_without_request_detail_does_not_invent_usage(self):
        self.write('a.jsonl', [meta('child', 'parent'), context(), token(1000), token(1050, 2, last=50)])
        sessions, issues = self.scan()
        self.assertEqual(sessions[0].events[0].usage.total_tokens, 50)
        self.assertEqual(issues['unattributed_fork_baseline_tokens'], 1000)

    def test_incremental_scan_and_partial_write(self):
        path = self.write('a.jsonl', [meta(), context(), token(100, last=100)])
        scanner = M['Scanner']()
        scanner.scan([self.root])
        n = scanner.bytes_read
        scanner.scan([self.root])
        self.assertEqual(scanner.bytes_read, n)
        line = json.dumps(token(150, 2, last=50))
        with path.open('a') as f:
            f.write(line[:20])
        sessions, _ = M['account'](scanner.scan([self.root]))
        self.assertEqual(sum(e.usage.total_tokens for e in sessions[0].events), 100)
        with path.open('a') as f:
            f.write(line[20:] + '\n')
        sessions, _ = M['account'](scanner.scan([self.root]))
        self.assertEqual(sum(e.usage.total_tokens for e in sessions[0].events), 150)
        self.assertLess(scanner.bytes_read - n, 1000)

    def test_truncation_replacement_and_deletion(self):
        path = self.write('a.jsonl', [meta(), context(), token(100), token(150, 2)])
        scanner = M['Scanner']()
        scanner.scan([self.root])
        self.write('a.jsonl', [meta(), context(), token(20)])
        sessions, _ = M['account'](scanner.scan([self.root]))
        self.assertEqual(sessions[0].events[0].usage.total_tokens, 20)
        path.unlink()
        self.assertEqual(scanner.scan([self.root]), [])
        self.assertEqual(scanner.cache, {})

    def test_no_trailing_newline(self):
        self.write('a.jsonl', [meta(), context(), token(100)], newline=False)
        self.assertEqual(self.scan()[0][0].events[0].usage.total_tokens, 100)

    def test_report_cache_reuses_unchanged_accounting(self):
        path = self.write('a.jsonl', [meta(), context(), token(100, last=100)])
        scanner, cache = M['Scanner'](), M['ReportCache']()
        prices = fixture_prices()
        cache.prepare(scanner, scanner.scan([self.root]), prices)
        original_rows = cache.rows
        with path.open('a') as f:
            f.write(json.dumps({'type':'response_item','payload':{'content':'not retained'}}) + '\n')
        cache.prepare(scanner, scanner.scan([self.root]), prices)
        self.assertIs(cache.rows, original_rows)
        self.write('a.jsonl', [meta(), context(), token(200, last=200)])
        cache.prepare(scanner, scanner.scan([self.root]), prices)
        self.assertIsNot(cache.rows, original_rows)
        self.assertEqual(cache.rows[0][0].usage.total_tokens, 200)

    def test_report_cache_reprices_when_catalog_changes(self):
        self.write('a.jsonl', [meta(), context('gpt-6-sol'), token(100000, last=100000)])
        scanner, cache = M['Scanner'](), M['ReportCache']()
        prices = fixture_prices()
        cache.prepare(scanner, scanner.scan([self.root]), prices)
        old_cost = cache.rows[0][1].amount
        changed = dict(prices)
        changed['gpt-6-sol'] = M['replace'](prices['gpt-6-sol'], input=M['Decimal']('3'))
        cache.prepare(scanner, scanner.scan([self.root]), changed)
        self.assertEqual(old_cost, M['Decimal']('.2'))
        self.assertEqual(cache.rows[0][1].amount, M['Decimal']('.3'))

    def test_report_cache_invalidates_at_time_boundary(self):
        self.write('a.jsonl', [meta(), context(), token(100, 1, last=100), token(150, 2, last=50)])
        scanner, cache = M['Scanner'](), M['ReportCache']()
        cache.prepare(scanner, scanner.scan([self.root]), fixture_prices())
        cal = M['Calendar'].make('UTC')
        first = cache.summarize(cal, at('2026-09-22T00:01:00Z'), at(), 'day')
        second = cache.summarize(cal, at('2026-09-22T00:01:01Z'), at(), 'day')
        self.assertEqual(first['usage']['total_tokens'], 150)
        self.assertEqual(second['usage']['total_tokens'], 50)

    def test_limits_without_usage_are_retained(self):
        row = token(0)
        row['payload']['info'] = None
        row['payload']['rate_limits'] = {'limit_id': 'codex', 'primary': {'used_percent': 20}}
        self.write('a.jsonl', [meta(), row])
        rollouts = M['Scanner']().scan([self.root])
        self.assertIsNotNone(M['log_limits'](rollouts))
        self.assertEqual(M['account'](rollouts)[0], [])

    def test_atomic_output_and_source_protection(self):
        out = self.root / 'report.json'
        M['write_json'](out, {'x': 1}, set())
        M['write_json'](out, {'x': 2}, set())
        self.assertEqual(json.loads(out.read_text()), {'x': 2})
        with self.assertRaises(ValueError):
            M['write_json'](out, {'x': float('nan')}, set())
        self.assertEqual(json.loads(out.read_text()), {'x': 2})
        with self.assertRaises(ValueError):
            M['write_json'](out, {}, {out.resolve()})
        self.assertEqual(list(self.root.glob('.oai-usage-*')), [])

    def test_cli_json_matches_saved_file(self):
        self.write('a.jsonl', [meta(), context(), token(100, last=100)])
        dest = self.root / 'out.json'
        bootstrap = '''import runpy, sys
raw = sys.stdin.buffer.read()
namespace = runpy.run_path(sys.argv[1], run_name="cli_fixture")
async def fetch(*args):
    return raw
namespace["main"].__globals__["fetch_https_async"] = fetch
raise SystemExit(namespace["main"](sys.argv[2:]))
'''
        proc = subprocess.run([sys.executable, '-c', bootstrap, str(SCRIPT), '--root', str(self.root), '--days', 'all',
            '--quota', 'off', '--json', '--output', str(dest)], text=True, capture_output=True,
            input=(Path(__file__).parent / 'fixtures/prices.json').read_text())
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout), json.loads(dest.read_text()))

    def test_watch_off_and_logs_never_start_transport(self):
        self.write('a.jsonl', [meta(), context(), token(100)])
        for mode in ('off', 'logs'):
            with patch.dict(G, {'fetch_live': lambda *a, **kw: self.fail('network started'),
                                'find_codex': lambda *a, **kw: self.fail('discovery started')}):
                with contextlib.redirect_stdout(io.StringIO()):
                    result = M['main'](['watch', '--root', str(self.root), '--quota', mode, '--count', '1'])
            self.assertEqual(result, 0)


class Accounting(unittest.TestCase):
    def test_display_uses_the_selected_timezone_offset(self):
        cases = ((8, '中国标准时间', '2026-09-22 09:00 UTC+08:00'),
                 (-6, 'CST', '2026-09-21 19:00 UTC-06:00'),
                 (5.5, 'India Standard Time', '2026-09-22 06:30 UTC+05:30'),
                 (0, 'Coordinated Universal Time', '2026-09-22 01:00 UTC'))
        for hours, name, expected in cases:
            with self.subTest(name=name):
                zone = M['timezone'](M['timedelta'](hours=hours), name)
                calendar = M['Calendar'](zone=zone)
                self.assertEqual(calendar.display(at()), expected)
                self.assertEqual(calendar.display(None), 'unbounded')

    def event(self, model='gpt-6-astra', n=100, out=0, request=None):
        return M['Event']('session', at(), model, usage(n, out), request)

    def test_utc_and_local_midnight(self):
        cal = M['Calendar'].make('Asia/Shanghai')
        stamp = at('2026-09-21T17:00:00Z')
        start = cal.midnight(M['date'](2026, 9, 22))
        self.assertEqual(start, at('2026-09-21T16:00:00Z'))
        self.assertEqual(cal.day(stamp), '2026-09-22')
        self.assertTrue(M['included'](stamp, start, at('2026-09-22T16:00:00Z')))
        self.assertFalse(M['included'](at('2026-09-22T16:00:00Z'), start, at('2026-09-22T16:00:00Z')))

    def test_named_timezone_dst_calendar_day(self):
        cal = M['Calendar'].make('America/New_York')
        first = cal.midnight(M['date'](2026, 3, 8))
        second = cal.midnight(M['date'](2026, 3, 9))
        self.assertEqual((second - first).total_seconds(), 23 * 3600)

    def test_unknown_timestamp_not_in_filtered_period(self):
        self.assertFalse(M['included'](None, None, at()))
        self.assertTrue(M['included'](None, None, None))

    def test_unknown_price_consistency(self):
        e = self.event('internal-unknown', 1000000)
        b = M['Bucket']()
        b.add(e, M['charge'](e, fixture_prices()))
        result = b.export()
        self.assertIsNone(result['api_cost_usd'])
        self.assertEqual(result['known_api_cost_usd'], 0)
        self.assertEqual(result['unpriced_usage']['total_tokens'], 1000000)

    def test_long_context_per_request(self):
        prices = fixture_prices()
        e = self.event(n=300000, out=1000, request=300000)
        self.assertEqual(M['charge'](e, prices).amount, M['Decimal']('6.075'))
        small = self.event(n=150000, out=500, request=150000)
        self.assertEqual(2 * M['charge'](small, prices).amount, M['Decimal']('3.05'))

    def test_new_gpt6_models_include_cache_and_request_long_context(self):
        prices = fixture_prices()
        for model, short_cost, long_cost in (
            ('gpt-6-sol', '.179', '1.215'),
            ('gpt-6-luna', '.00895', '.06075'),
        ):
            with self.subTest(model=model):
                short = M['Event']('s', at(), model, U(100000, 20000, 10000, 1000, 0, 101000), 100000)
                long = self.event(model, n=300000, out=1000, request=300000)
                self.assertEqual(M['charge'](short, prices).amount, M['Decimal'](short_cost))
                self.assertEqual(M['charge'](long, prices).amount, M['Decimal'](long_cost))

    def test_threshold_and_unknown_request(self):
        prices = fixture_prices()
        self.assertEqual(M['charge'](self.event(n=272000, request=272000), prices).amount, M['Decimal']('2.72'))
        unknown = M['charge'](self.event(n=300000), prices)
        self.assertFalse(unknown.complete)
        self.assertEqual(unknown.amount, 0)
        self.assertIn('unknown_request_size', unknown.reasons)
        self.assertTrue(M['charge'](self.event(n=100000), prices).complete)

    def test_short_long_short_requests_are_priced_independently(self):
        events = [
            self.event('gpt-5.5', n=100000, request=100000),
            M['replace'](self.event('gpt-5.5', n=300000, request=300000), at=at('2026-09-23T01:00:00Z')),
            M['replace'](self.event('gpt-5.5', n=50000, request=50000), at=at('2026-09-24T01:00:00Z')),
        ]
        session = M['Session']('session', None, None, [], events)
        rows = M['priced_events']([session], fixture_prices())
        self.assertEqual([cost.amount for _, cost in rows], list(map(M['Decimal'], ['.5', '3', '.25'])))
        selected = sum(cost.amount for event, cost in rows if M['included'](
            event.at, at('2026-09-22T00:00:00Z'), at('2026-09-23T00:00:00Z')))
        self.assertEqual(selected, M['Decimal']('.5'))

    def test_unknown_request_size_does_not_contaminate_other_session_events(self):
        events = [self.event(n=100000, request=100000), self.event(n=300000),
                  self.event(n=50000, request=50000)]
        rows = M['priced_events']([M['Session']('s', None, None, [], events)], fixture_prices())
        self.assertEqual([cost.complete for _, cost in rows], [True, False, True])
        bucket = M['Bucket']()
        for event, cost in rows:
            bucket.add(event, cost)
        result = bucket.export()
        self.assertIsNone(result['api_cost_usd'])
        self.assertEqual(result['known_api_cost_usd'], 1.5)
        self.assertEqual(result['uncertain_pricing_usage']['input_tokens'], 300000)

    def test_conflicting_request_details_prevent_using_a_small_delta_as_a_prompt_bound(self):
        event = M['replace'](self.event(n=50000), request_detail_uncertain=True)
        cost = M['charge'](event, fixture_prices())
        self.assertFalse(cost.complete)
        self.assertEqual(cost.amount, 0)
        self.assertEqual(cost.reasons, ('unknown_request_size',))

    def test_missing_cache_read_price_retains_known_input_output_cost(self):
        price = M['Price'](M['Decimal']('2'), None, M['Decimal']('10'))
        event = M['Event']('s', at(), 'custom', U(100000, 20000, 0, 1000, 0, 101000), 100000)
        cost = M['charge'](event, {'custom': price})
        self.assertFalse(cost.complete)
        self.assertIn('unknown_cache_read_price', cost.reasons)
        self.assertEqual(cost.amount, M['Decimal']('.17'))
        ordinary = self.event('custom', n=100000, out=1000, request=100000)
        self.assertTrue(M['charge'](ordinary, {'custom': price}).complete)

    def test_cache_write_and_reasoning_not_double_counted(self):
        e = M['Event']('s', at(), 'gpt-6-astra', U(100, 50, 20, 10, 5, 110), 100)
        self.assertEqual(M['charge'](e, fixture_prices()).amount, M['Decimal']('.0011'))

    def test_exact_model_override_and_invalid_prices(self):
        prices = M['price_overrides'](['gpt-6-astra=2,.2,10,2.5'], fixture_prices())
        e = self.event('gpt-6-astra', n=1000000, request=100000)
        self.assertEqual(M['charge'](e, prices).amount, M['Decimal']('2'))
        repeated = M['price_overrides'](['gpt-6-sol=0,0,0,0', 'gpt-6-sol=3,.3,15'], fixture_prices())
        self.assertEqual(repeated['gpt-6-sol'].write, M['Decimal']('3.75'))
        self.assertEqual(M['charge'](self.event('gpt-6-sol', n=300000, out=1000, request=300000),
                                      repeated).amount, M['Decimal']('1.8225'))
        for value in ('x=nan,1,2', 'x=-1,0,2', 'x=1,inf,2', '=1,2,3', 'x=1,2', 'x=1e999,1,1'):
            with self.assertRaises(ValueError, msg=value):
                M['price_overrides']([value], fixture_prices())

    def test_model_names_are_not_guessed_from_aliases_or_snapshots(self):
        prices = fixture_prices()
        for model in ('astra', 'gpt-astra', 'gpt6_astra', 'gpt-6-astra-2026-09-04',
                      'gpt-6-astra-preview', 'gpt-6-astra-latest'):
            with self.subTest(model=model):
                charge = M['charge'](self.event(model), prices)
                self.assertFalse(charge.complete)
                self.assertIn('unknown_model', charge.reasons)

    def test_removed_options_are_rejected(self):
        for args in (['--watch'], ['-w'], ['--group-by', 'day'], ['--all-dimensions'],
                     ['--limits-source', 'off'], ['--limits-timeout', '1'], ['--json-out', 'x'],
                     ['--interval', '1'], ['-n', '1'], ['--project'], ['watch', '--ref', '1']):
            with self.subTest(args=args), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                M['parse_args'](args)
            self.assertEqual(error.exception.code, 2)

    def test_usage_validation(self):
        self.assertEqual(U.parse({'input_tokens': 0}), U())
        self.assertIsNone(U.parse({}))
        self.assertIsNone(U.parse({'input_tokens': True}))
        self.assertIsNone(U.parse({'input_tokens': 10**400}))
        self.assertIsNone(U.parse({'input_tokens': 1, 'cached_input_tokens': 2}))

    def test_live_quota_requires_current_multi_bucket_response(self):
        bucket = {'limitId': 'codex', 'primary': {'usedPercent': 20}}
        for value in ({'rateLimits': bucket}, {'rate_limits': bucket},
                      {'rateLimitsByLimitId': {}, 'rateLimits': bucket},
                      {'rate_limits_by_limit_id': {'codex': bucket}}):
            with self.subTest(value=value):
                self.assertIsNone(M['normalize_live'](value, at()))
        current = M['normalize_live']({'rateLimitsByLimitId': {'codex': bucket},
                                       'rateLimitResetCredits': {'availableCount': 2}}, at())
        self.assertEqual(current['limits']['codex']['primary']['used_percent'], 20)
        self.assertEqual(current['rate_limit_reset_credits']['available_count'], 2)

    def test_current_arguments_and_validation(self):
        args = M['parse_args'](['watch', '--quota', 'off', '--refresh', '1', '--output', 'a.json'])
        self.assertEqual((args.command, args.quota, args.refresh, args.output), ('watch', 'off', 1, 'a.json'))
        for values in (['--refresh', 'nan'], ['--since', 'bad'], ['--timezone', 'Not/A/Zone'], ['--since','2026-09-22','--until','2026-09-20']):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                M['parse_args'](values)
            self.assertEqual(error.exception.code, 2)


class Presentation(unittest.TestCase):
    def data(self, used=50, unknown=False):
        now = at('2026-09-22T02:00:00Z')
        current = M['normalize_live']({'rateLimitsByLimitId': {'codex': {'limitId': 'codex', 'primary':
            {'usedPercent': used, 'windowDurationMins': 300, 'resetsAt': int(at('2026-09-22T05:00:00Z').timestamp())},
            'secondary': {'usedPercent': 25, 'windowDurationMins': 10080,
                          'resetsAt': int(at('2026-09-28T00:00:00Z').timestamp())}}}}, at())
        current = M['quota_status']('live', [], (current, None), now, None)
        rows = []
        for stamp, amount in [('2026-09-21T01:00:00Z', 10), ('2026-09-22T00:30:00Z', 20),
                              ('2026-09-22T01:30:00Z', 50)]:
            event = M['Event']('s', at(stamp), 'custom', usage(amount * 100000), 100000)
            rows.append((event, M['Charge'](M['Decimal'](amount))))
        if unknown:
            event = M['Event']('s', at('2026-09-22T00:40:00Z'), 'unknown', usage(100), 100)
            rows.append((event, M['Charge'](complete=False, reasons=('unknown_model',))))
        session = M['Session']('s', None, None, [], [event for event, _ in rows])
        calendar = M['Calendar'].make('UTC')
        summary = M['aggregate'](rows, [session], calendar, None, now, 'model')
        summary['current_rate_limits'] = current
        summary['cycle_estimates'] = M['projections'](rows, current, now)
        return {'summary': summary, 'period': {'start': None, 'end': now.isoformat()},
                'diagnostics': {'issues': {}, 'files': 1}}

    def render(self, data, *args):
        return M['render'](data, M['parse_args'](list(args)), M['Calendar'].make('UTC'), 100)

    def test_current_cycles_are_visible_by_default(self):
        data = self.data()
        text = self.render(data)
        self.assertIn('Current cycle', text)
        self.assertIn('Current cycle · Primary', text)
        self.assertIn('Current cycle · Secondary', text)
        self.assertIn('$20.000000', text)
        self.assertIn('$30.000000', text)
        self.assertIn('~$120.0000', text)
        self.assertIn('Tokens: 2,000,000 total · 2,000,000 in', text)
        self.assertIn('Started  2026-09-21 00:00 UTC', text)
        self.assertIn('Sessions: 1 · Events: 2', text)
        self.assertIn('Reasoning', text)
        self.assertIn('Cached/In', text)
        # The event after the quota observation contributes to the report, not the cycle.
        self.assertEqual(data['summary']['known_api_cost_usd'], 80)
        self.assertEqual(data['summary']['cycle_estimates']['primary']['local']['known_api_cost_usd'], 20)

    def test_zero_account_usage_keeps_local_cost_visible(self):
        text = self.render(self.data(used=0))
        self.assertIn('$20.000000', text)
        self.assertIn('Projection unavailable:', text)
        self.assertNotIn('~$40.0000', text)

    def test_partial_cost_stays_explicit_in_cycles(self):
        data = self.data(unknown=True)
        text = self.render(data)
        self.assertIn('$20.000000 + unknown', text)
        self.assertIn('PARTIAL', text)
        self.assertIsNone(data['summary']['cycle_estimates']['primary']['local']['api_cost_usd'])
        self.assertIsNone(data['summary']['cycle_estimates']['primary']['projected_full_cycle_api_cost_usd'])

    def test_current_cycle_default_and_explicit_opt_out(self):
        data = self.data()
        self.assertIn('Current cycle', self.render(data))
        self.assertNotIn('Current cycle', self.render(data, '--no-project'))
        self.assertIn('Account quota', self.render(data, '--no-project'))
        self.assertEqual(len(data['summary']['cycle_estimates']), 2)

    def test_watch_keeps_both_cycle_amounts_on_100x24_screen(self):
        class Terminal(io.StringIO):
            def isatty(self):
                return True
        output = Terminal()
        with patch.dict(G, {'report': lambda *a, **kw: self.data()}), \
             patch.dict(G, {'terminal_ansi': lambda: (True, None)}), \
             patch.object(M['shutil'], 'get_terminal_size', return_value=os.terminal_size((100, 24))), \
             contextlib.redirect_stdout(output), patch.dict(os.environ):
            os.environ.pop('NO_COLOR', None)
            self.assertEqual(M['main'](['watch', '--quota', 'logs', '--count', '1', '--color']), 0)
        text = output.getvalue()
        self.assertIn('Current cycle · Primary', text)
        self.assertIn('Current cycle · Secondary', text)
        self.assertIn('$20.000000', text)
        self.assertIn('$30.000000', text)
        self.assertIn('~$90.0000', text)
        self.assertIn('Ctrl+C to quit', text)
        self.assertIn('\033[1;36mOAI USAGE', text)
        self.assertIn('watch uses scrollback', text)
        self.assertNotIn('\033[?1049h', text)
        visible = M['re'].sub(r'\x1b\[[?0-9;]*[A-Za-z]', '', text)
        self.assertGreater(len(visible.splitlines()), 24)
        self.assertTrue(all(M['width'](line) <= 100 for line in visible.splitlines()))

    def test_watch_narrow_screen_fits_notice_and_footer(self):
        class Terminal(io.StringIO):
            def isatty(self):
                return True
        for columns in (20, 30, 40):
            output = Terminal()
            with self.subTest(columns=columns), patch.dict(G, {'report': lambda *a, **kw: self.data()}), \
                 patch.dict(G, {'terminal_ansi': lambda: (True, None)}), \
                 patch.object(M['shutil'], 'get_terminal_size', return_value=os.terminal_size((columns, 24))), \
                 contextlib.redirect_stdout(output):
                self.assertEqual(M['main'](['watch', '--quota', 'logs', '--count', '1', '--no-color']), 0)
            visible = M['re'].sub(r'\x1b\[[?0-9;]*[A-Za-z]', '', output.getvalue())
            self.assertTrue(all(M['width'](line) <= columns for line in visible.splitlines()))
            self.assertGreater(len(visible.splitlines()), 24)
            self.assertIn('scrollback', visible)

    def test_help_prices_errors_and_empty_reports_are_english(self):
        with tempfile.TemporaryDirectory() as root:
            for argv in (['--help'], ['report', '--help'], ['watch', '--help'], ['prices'],
                         ['--since', 'bad'], ['--price', 'x=nan,1,2'],
                         ['--root', root, '--quota', 'logs'],
                         ['watch', '--root', root, '--quota', 'off', '--count', '1']):
                with self.subTest(argv=argv), contextlib.redirect_stdout(io.StringIO()) as out, \
                     contextlib.redirect_stderr(io.StringIO()) as err:
                    try:
                        M['main'](argv)
                    except SystemExit:
                        pass
                self.assertTrue(out.getvalue() or err.getvalue())
                self.assertNotRegex(out.getvalue() + err.getvalue(), r'[\u3400-\u9fff]')
        self.assertNotRegex(self.render(self.data()), r'[\u3400-\u9fff]')

    def test_dashboard_uses_dates_not_duration_names(self):
        for args in ([], ['watch'], ['--no-project']):
            text = self.render(self.data(), *args)
            self.assertIn('╭', text)
            self.assertIn('█', text)
            self.assertIn('░', text)
            self.assertNotRegex(text, r'\b(?:7d|5h|weekly|Weekly)\b')
            self.assertTrue(all(M['width'](line) <= 100 for line in text.splitlines()))
        text = self.render(self.data(), 'watch')
        # Both card headers share a line; neither quota window is hidden below the other.
        self.assertTrue(any('Current cycle · Primary' in line and 'Current cycle · Secondary' in line
                            for line in text.splitlines()))
        self.assertIn('09-22 00:00 → 09-22 05:00', text)

    def test_dashboard_colors_are_optional_and_json_has_no_ansi(self):
        for flags, no_color, expected in ((['--color'], False, True), (['--no-color'], False, False),
                                           (['--color'], True, False)):
            with self.subTest(flags=flags, no_color=no_color), patch.dict(G, {'report': lambda *a, **kw: self.data()}), \
                 patch.dict(os.environ), contextlib.redirect_stdout(io.StringIO()) as output:
                if no_color:
                    os.environ['NO_COLOR'] = '1'
                else:
                    os.environ.pop('NO_COLOR', None)
                M['main'](['--quota', 'logs', *flags])
            text = output.getvalue()
            self.assertEqual('\033[' in text, expected)
            self.assertIn('╭', text)
            if expected:
                self.assertIn('\033[1;32m$20.000000', text)
                self.assertIn('\033[36m│', text)
        with patch.dict(G, {'report': lambda *a, **kw: self.data()}), contextlib.redirect_stdout(io.StringIO()) as output:
            M['main'](['--quota', 'logs', '--json', '--color'])
        self.assertNotIn('\033', output.getvalue())
        self.assertEqual(json.loads(output.getvalue())['summary']['cycle_estimates']['primary']['local']['known_api_cost_usd'], 20)

    def test_paired_cycle_bars_use_their_own_usage_color(self):
        data = self.data()
        data['summary']['cycle_estimates']['primary']['account_usage']['used_percent'] = 25
        data['summary']['cycle_estimates']['secondary']['account_usage']['used_percent'] = 95
        line = next(line for line in self.render(data, 'watch').splitlines() if '25% used' in line and '95% used' in line)
        styled = M['style_line'](line)
        self.assertIn('\033[32m█', styled)
        self.assertIn('\033[31m█', styled)

    def test_watch_updates_cycle_after_early_reset_without_log_changes(self):
        class Clock(DT):
            @classmethod
            def now(cls, tz=None):
                return at('2026-09-22T02:00:00Z')
        def current(reset, used):
            return M['normalize_live']({'rateLimitsByLimitId': {'codex': {'limitId': 'codex', 'primary': {
                'usedPercent': used, 'windowDurationMins': 10080, 'resetsAt': int(at(reset).timestamp())}}}}, Clock.now())
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'a.jsonl'
            path.write_text('\n'.join(json.dumps(row) for row in [meta(created='2026-09-21T00:00:00Z'), context(),
                token(100, last=100, stamp='2026-09-21T01:00:00Z'),
                token(300, last=200, stamp='2026-09-22T01:15:00Z')]) + '\n')
            replies = iter([(current('2026-09-25T00:00:00Z', 80), None),
                            (current('2026-09-29T01:00:00Z', 5), None)])
            scanner, cache = M['Scanner'](), M['ReportCache']()
            with patch.dict(G, {'datetime': Clock}):
                args = M['parse_args'](['watch', '--days', 'all', '--quota', 'live'])
                old = M['report'](args, scanner, fixture_prices(), M['Calendar'].make('UTC'), [Path(root)], cache, fixture_catalog(), next(replies))
                bytes_before = scanner.bytes_read
                new = M['report'](args, scanner, fixture_prices(), M['Calendar'].make('UTC'), [Path(root)], cache, fixture_catalog(), next(replies))
            self.assertEqual(scanner.bytes_read, bytes_before)
            before, after = [d['summary']['cycle_estimates']['primary'] for d in (old, new)]
            self.assertEqual(before['local']['usage']['total_tokens'], 300)
            self.assertEqual(after['local']['usage']['total_tokens'], 200)
            self.assertEqual(after['period']['start'], '2026-09-22T01:00:00+00:00')
            self.assertEqual(after['local']['known_api_cost_usd'], .002)
            self.assertEqual(after['projected_full_cycle_api_cost_usd'], .04)
            self.assertEqual(new['summary']['usage']['total_tokens'], 300)

    def test_report_date_filter_does_not_clip_current_cycles(self):
        class Clock(DT):
            @classmethod
            def now(cls, tz=None):
                return at('2026-09-22T02:00:00Z')
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'a.jsonl'
            path.write_text('\n'.join(json.dumps(row) for row in
                [meta(), context(), token(100, last=100)]) + '\n')
            current = self.data()['summary']['current_rate_limits']
            with patch.dict(G, {'datetime': Clock}):
                args = M['parse_args'](['--since', '2026-09-01', '--until', '2026-09-02', '--timezone', 'UTC'])
                data = M['report'](args, M['Scanner'](), fixture_prices(), M['Calendar'].make('UTC'), [Path(root)], price_catalog=fixture_catalog(), live=(current, None))
            self.assertEqual(data['summary']['usage']['total_tokens'], 0)
            self.assertEqual(data['summary']['cycle_estimates']['primary']['local']['usage']['total_tokens'], 100)
            text = M['render'](data, args, M['Calendar'].make('UTC'))
            self.assertIn('$0.001000', text)
            self.assertIn('No local usage records in this report period', text)


class TerminalCompatibility(unittest.TestCase):
    class Terminal(io.StringIO):
        def isatty(self):
            return True

        def fileno(self):
            return 1

    def setUp(self):
        self.output = self.Terminal()
        self.enterContext(contextlib.redirect_stdout(self.output))
        self.enterContext(patch.dict(os.environ))
        os.environ.pop('NO_COLOR', None)

    def windows_console(self, mode=0x0052, readable=True, writable=True):
        console = Mock()

        def get_mode(handle, pointer):
            pointer._obj.value = mode
            return readable

        console.GetConsoleMode.side_effect = get_mode
        console.SetConsoleMode.return_value = writable
        self.enterContext(patch.dict(sys.modules, {'msvcrt': SimpleNamespace(
            get_osfhandle=Mock(return_value=0x123456789))}))
        self.enterContext(patch.object(ctypes, 'WinDLL', return_value=console, create=True))
        self.enterContext(patch.dict(G, {'WINDOWS': True}))
        return console

    def run_dashboard(self, *args):
        data = Presentation().data()
        with patch.dict(G, {'report': lambda *a, **kw: data}), \
             patch.object(M['shutil'], 'get_terminal_size', return_value=os.terminal_size((100, 120))):
            return M['main']([*args, '--quota', 'logs'])

    def test_windows_enables_vt_preserving_and_restoring_mode(self):
        console = self.windows_console()
        ansi, restore = M['terminal_ansi']()
        self.assertTrue(ansi)
        handle = console.GetConsoleMode.call_args.args[0]
        self.assertEqual(handle.value, 0x123456789)
        self.assertEqual(console.GetConsoleMode.argtypes[0], ctypes.wintypes.HANDLE)
        console.SetConsoleMode.assert_called_once_with(handle, 0x0057)
        restore()
        self.assertEqual(console.SetConsoleMode.call_args_list, [call(handle, 0x0057), call(handle, 0x0052)])

    def test_already_enabled_console_needs_no_changes(self):
        console = self.windows_console(mode=0x0057)
        self.assertEqual(M['terminal_ansi'](), (True, None))
        console.SetConsoleMode.assert_not_called()

    def test_windows_watch_restores_screen_and_mode(self):
        console = self.windows_console()
        self.assertEqual(self.run_dashboard('watch', '--count', '1', '--color'), 0)
        text = self.output.getvalue()
        self.assertIn('\033[1;36mOAI USAGE', text)
        self.assertIn('Current cycle · Primary', text)
        self.assertIn('Current cycle · Secondary', text)
        self.assertTrue(text.endswith('\033[?1049l\033[?25h'))
        self.assertEqual([args.args[1] for args in console.SetConsoleMode.call_args_list], [0x0057, 0x0052])

    def test_unsupported_windows_console_prints_plain_watch_frames(self):
        console = self.windows_console(writable=False)
        self.assertEqual(self.run_dashboard('watch', '--count', '1', '--color'), 0)
        self.assertNotIn('\033', self.output.getvalue())
        self.assertIn('Current cycle · Primary', self.output.getvalue())
        console.SetConsoleMode.assert_called_once()

    def test_windows_mode_read_failure_degrades_without_setting_mode(self):
        console = self.windows_console(readable=False)
        self.assertEqual(M['terminal_ansi'](), (False, None))
        console.SetConsoleMode.assert_not_called()

    def test_windows_restores_mode_after_render_error(self):
        console = self.windows_console()
        with patch.dict(G, {'render': Mock(side_effect=ValueError('render failed'))}), \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(self.run_dashboard(), 1)
        self.assertEqual([args.args[1] for args in console.SetConsoleMode.call_args_list], [0x0057, 0x0052])

    def test_json_does_not_touch_windows_console(self):
        console = self.windows_console()
        self.assertEqual(self.run_dashboard('--json', '--color'), 0)
        self.assertNotIn('\033', self.output.getvalue())
        self.assertTrue(json.loads(self.output.getvalue())['summary']['cycle_estimates'])
        console.GetConsoleMode.assert_not_called()
        console.SetConsoleMode.assert_not_called()

    def test_redirected_output_does_not_touch_windows_console(self):
        console = self.windows_console()
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(self.run_dashboard('--color'), 0)
        self.assertIn('\033[1;36mOAI USAGE', output.getvalue())
        console.GetConsoleMode.assert_not_called()
        console.SetConsoleMode.assert_not_called()


class Quotas(unittest.TestCase):
    def current(self, reset='2026-09-22T05:00:00Z', observed='2026-09-22T01:00:00Z', used=50):
        return M['normalize_live']({'rateLimitsByLimitId': {'codex': {'limitId': 'codex', 'primary':
            {'usedPercent': used, 'windowDurationMins': 300, 'resetsAt': int(at(reset).timestamp())}}}}, at(observed))

    def test_expired_window_never_projects(self):
        current = self.current(reset='2026-09-21T05:00:00Z', observed='2026-09-21T01:00:00Z')
        result = M['projections']([], current, at('2026-09-22T01:00:00Z'))
        # Selection is performed separately from protocol normalization.
        current['active_limit_id'] = 'codex'
        result = M['projections']([], current, at())
        self.assertFalse(result['primary']['available'])

    def test_valid_window_projection_and_zero_usage(self):
        current = self.current()
        current['active_limit_id'] = 'codex'
        event = M['Event']('s', at('2026-09-22T00:30:00Z'), 'gpt-6-astra', usage(1000000), 100000)
        rows = [(event, M['charge'](event, fixture_prices()))]
        result = M['projections'](rows, current, at('2026-09-22T02:00:00Z'))['primary']
        self.assertEqual(result['projected_remaining_api_cost_usd'], 10)
        current['limits']['codex']['primary']['used_percent'] = 0
        self.assertFalse(M['projections'](rows, current, at())['primary']['projection_available'])

    def test_cycle_length_is_supplied_by_server_not_assumed_weekly(self):
        current = self.current(reset='2026-09-22T04:00:00Z', observed='2026-09-22T03:00:00Z')
        current['active_limit_id'] = 'codex'
        current['limits']['codex']['primary']['window_minutes'] = 150
        rows = [(M['Event']('s', at(stamp), 'custom', usage(100), 100), M['Charge'](M['Decimal'](cost)))
                for stamp, cost in [('2026-09-22T01:00:00Z', '1'), ('2026-09-22T02:00:00Z', '2')]]
        result = M['projections'](rows, current, at('2026-09-22T03:00:00Z'))['primary']
        self.assertEqual(result['period']['start'], '2026-09-22T01:30:00+00:00')
        self.assertEqual(result['local']['known_api_cost_usd'], 2)
        # A lower percentage with unchanged boundaries does not create a new cycle.
        current['limits']['codex']['primary']['used_percent'] = 10
        updated = M['projections'](rows, current, at('2026-09-22T03:00:00Z'))['primary']
        self.assertEqual(updated['period'], result['period'])
        self.assertEqual(updated['local'], result['local'])

    def test_multiple_buckets_prefer_codex_and_map_authoritative(self):
        current = M['normalize_live']({'rateLimits': {'limitId':'codex','primary':{'usedPercent':99}},
            'rateLimitsByLimitId': {'codex':{'primary':{'usedPercent':10}}, 'extra':{'primary':{'usedPercent':20}}}}, at())
        selected = M['quota_status']('live', [], (current, None), at(), None)
        self.assertEqual(selected['active_limit_id'], 'codex')
        self.assertEqual(selected['limits']['codex']['primary']['used_percent'], 10)
        self.assertEqual(M['normalize_window']({'usedPercent':120})['remaining_percent'], 0)



if __name__ == '__main__':
    unittest.main()
