import contextlib
import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_usage import M, G, DT, at, meta, context, token, fixture_catalog, fixture_prices
import test_model_total as model_tests
import test_usage as usage_tests


class Clock(DT):
    @classmethod
    def now(cls, tz=None):
        return at('2026-09-22T02:00:00Z')


class Dashboard(unittest.TestCase):
    def setUp(self):
        self.tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(self.tmp)
        self.calendar = M['Calendar'].make('UTC')
        rows = [meta(created='2026-09-20T00:00:00Z'), context('gpt-6-sol'),
                token(100, last=100, stamp='2026-09-20T01:00:00Z'),
                token(300, last=200, stamp='2026-09-21T01:00:00Z'),
                token(600, last=300, stamp='2026-09-22T01:00:00Z'),
                token(1000, last=400, stamp='2026-09-22T01:45:00Z')]
        (self.root / 'a.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in rows))
        self.current = M['normalize_live']({'rateLimitsByLimitId': {'codex': {
            'limitId': 'codex', 'planType': 'plus',
            'primary': {'usedPercent': 50, 'windowDurationMins': 300,
                        'resetsAt': int(at('2026-09-22T05:00:00Z').timestamp())},
            'secondary': {'usedPercent': 25, 'windowDurationMins': 10080,
                          'resetsAt': int(at('2026-09-28T00:00:00Z').timestamp())}}}},
            at('2026-09-22T01:30:00Z'))

    def report(self, flags=(), current=None, cache=None, scanner=None):
        with patch.dict(G, {'datetime': Clock}):
            args = M['parse_args'](['--timezone', 'UTC', '--quota', 'live', *flags])
            data = M['report'](args, scanner or M['Scanner'](), fixture_prices(), self.calendar, [self.root],
                               cache=cache, price_catalog=fixture_catalog(), live=(current or self.current, None))
        return args, data

    def render(self, data, args, columns=120):
        return M['render'](data, args, self.calendar, columns)

    def test_default_uses_longest_valid_cycle_and_report_time_not_snapshot(self):
        args, data = self.report()
        period = data['period']
        self.assertEqual(period['selection'], 'current_cycle')
        self.assertEqual(period['cycle_window'], 'secondary')
        self.assertEqual(period['start'], '2026-09-21T00:00:00+00:00')
        self.assertEqual(period['end'], '2026-09-22T02:00:00+00:00')
        self.assertEqual(period['cycle_resets_at'], '2026-09-28T00:00:00+00:00')
        self.assertTrue(period['end_exclusive'])
        self.assertEqual(data['summary']['usage']['total_tokens'], 900)
        estimate = data['summary']['cycle_estimates']['secondary']
        self.assertEqual(estimate['local']['usage']['total_tokens'], 500)
        self.assertEqual(estimate['period']['as_of'], '2026-09-22T01:30:00+00:00')
        text = self.render(data, args)
        self.assertIn('Cycle window: Secondary', text)
        self.assertIn('Local through 2026-09-22 01:30 UTC', text)

    def test_explicit_dates_override_default_without_changing_cycle_estimates(self):
        for flags, expected in ((['--days', 'all'], 1000), (['--days', '30'], 1000),
                                (['--days', '1'], 700), (['--today'], 700),
                                (['--since', '2026-09-20', '--until', '2026-09-20'], 100),
                                (['--until', '2026-09-20'], 100)):
            with self.subTest(flags=flags):
                args, data = self.report(flags)
                self.assertEqual(data['period']['selection'], 'explicit')
                self.assertNotIn('cycle_window', data['period'])
                self.assertEqual(data['summary']['usage']['total_tokens'], expected)
                self.assertEqual(data['summary']['cycle_estimates']['secondary']['local']['usage']['total_tokens'], 500)

    def test_expired_secondary_uses_valid_primary(self):
        current = copy.deepcopy(self.current)
        current['limits']['codex']['secondary']['resets_at_utc'] = '2026-09-20T00:00:00+00:00'
        args, data = self.report(current=current)
        self.assertEqual(data['period']['cycle_window'], 'primary')
        self.assertEqual(data['period']['start'], '2026-09-22T00:00:00+00:00')
        self.assertEqual(data['summary']['usage']['total_tokens'], 700)
        self.assertIn('Account:', self.render(data, args))

    def test_valid_cycle_does_not_require_a_positive_usage_percentage(self):
        for used in (0, None):
            current = copy.deepcopy(self.current)
            current['limits']['codex']['secondary']['used_percent'] = used
            _, data = self.report(current=current)
            self.assertEqual(data['period']['selection'], 'current_cycle')
            self.assertEqual(data['period']['cycle_window'], 'secondary')
            self.assertFalse(data['summary']['cycle_estimates']['secondary']['projection_available'])

    def test_no_usable_quota_has_explicit_30_day_fallback(self):
        invalid = []
        missing_duration = copy.deepcopy(self.current)
        missing_duration['limits']['codex']['primary']['window_minutes'] = None
        missing_duration['limits']['codex']['secondary']['window_minutes'] = None
        invalid.append(missing_duration)
        expired = copy.deepcopy(self.current)
        for window in ('primary', 'secondary'):
            expired['limits']['codex'][window]['resets_at_utc'] = '2026-09-20T00:00:00+00:00'
        invalid.append(expired)
        stale_observation = copy.deepcopy(self.current)
        stale_observation['fetched_at'] = '2026-09-19T00:00:00+00:00'
        invalid.append(stale_observation)
        future_observation = copy.deepcopy(self.current)
        future_observation['fetched_at'] = '2026-09-22T03:00:00+00:00'
        invalid.append(future_observation)
        foreign = copy.deepcopy(self.current)
        foreign['limits']['other-model-quota'] = foreign['limits'].pop('codex')
        invalid.append(foreign)
        for current in invalid:
            with self.subTest(current=current):
                args, data = self.report(current=current)
                self.assertEqual(data['period']['selection'], 'fallback')
                self.assertEqual(data['period']['start'], '2026-08-23T02:00:00+00:00')
                self.assertNotIn('cycle_window', data['period'])
                self.assertTrue(data['period']['fallback_reason'])
                self.assertIn('Fallback: last 30 days', self.render(data, args))
        for flags in (['--quota', 'off'], ['--quota', 'logs'], ['--limit', 'missing']):
            args, data = self.report(flags)
            self.assertEqual(data['period']['selection'], 'fallback')
            self.assertIn('Fallback: last 30 days', self.render(data, args))
        args, data = self.report(['--quota', 'off', '--days', 'all'])
        self.assertEqual(data['period']['selection'], 'explicit')
        self.assertIsNone(data['period']['start'])

    def test_log_quota_cycle_and_live_to_logs_fallback(self):
        path = self.root / 'a.jsonl'
        window = self.current['limits']['codex']['secondary']
        row = {'type': 'event_msg', 'timestamp': '2026-09-22T01:30:00Z',
               'payload': {'type': 'token_count', 'rate_limits': {'limit_id': 'codex',
                   'secondary': {'used_percent': 25, 'window_minutes': 10080,
                                 'resets_at': window['resets_at']}}}}
        with path.open('a') as file:
            file.write(json.dumps(row) + '\n')
        for mode in ('logs', 'auto'):
            with self.subTest(mode=mode), patch.dict(G, {'datetime': Clock}):
                args = M['parse_args'](['--quota', mode, '--timezone', 'UTC'])
                data = M['report'](args, M['Scanner'](), fixture_prices(), self.calendar, [self.root],
                                   price_catalog=fixture_catalog(), live=(None, 'Live lookup failed'))
            self.assertEqual(data['period']['selection'], 'current_cycle')
            self.assertEqual(data['summary']['current_rate_limits']['source'], 'session_log')
            self.assertEqual(data['summary']['usage']['total_tokens'], 900)
            self.assertIn('LOG SNAPSHOT', self.render(data, args))

    def test_manual_reset_updates_default_report_without_rescanning(self):
        scanner, cache = M['Scanner'](), M['ReportCache']()
        _, before = self.report(cache=cache, scanner=scanner)
        read = scanner.bytes_read
        current = copy.deepcopy(self.current)
        current['limits']['codex']['secondary']['resets_at_utc'] = '2026-09-29T01:00:00+00:00'
        current['limits']['codex']['secondary']['resets_at'] = int(at('2026-09-29T01:00:00Z').timestamp())
        _, after = self.report(cache=cache, scanner=scanner, current=current)
        self.assertEqual(scanner.bytes_read, read)
        self.assertEqual(before['summary']['usage']['total_tokens'], 900)
        self.assertEqual(after['period']['cycle_window'], 'secondary')
        self.assertEqual(after['period']['start'], '2026-09-22T01:00:00+00:00')
        self.assertEqual(after['summary']['usage']['total_tokens'], 700)

    def test_unified_account_panel_preserves_metadata_and_opt_out(self):
        current = copy.deepcopy(self.current)
        current['limits']['codex']['credits'] = {'balance': '12', 'unlimited': False}
        current['rate_limit_reset_credits'] = {'available_count': 2}
        current['fallback_reason'] = 'Live lookup failed'
        current['limits']['other'] = copy.deepcopy(current['limits']['codex'])
        args, data = self.report(current=current)
        text = self.render(data, args)
        self.assertEqual(text.count('╭─ Account quota'), 1)
        self.assertEqual(text.count('LIVE · codex · plus'), 1)
        self.assertEqual(text.count('50% used'), 1)
        self.assertEqual(text.count('25% used'), 1)
        self.assertIn('Credits: 12', text)
        self.assertIn('Reset credits available: 2', text)
        self.assertIn('Other buckets: other', text)
        self.assertIn('Fallback: Live lookup failed', text)
        args = M['parse_args'](['--no-project'])
        text = self.render(data, args)
        self.assertIn('╭─ Account quota', text)
        self.assertNotIn('Current cycle · Primary', text)
        self.assertNotIn('Est. Remaining', text)
        self.assertIn('50% used', text)

    def test_summary_and_model_details_remain_complete_when_narrow_or_watch(self):
        data = model_tests.ModelTotal().data()
        for command in ('report', 'watch'):
            for columns in (20, 40, 80, 100, 120, 140):
                args = M['parse_args']([command])
                with self.subTest(command=command, columns=columns):
                    text = self.render(data, args, columns)
                    self.assertTrue(all(M['width'](line) <= columns for line in text.splitlines()))
                    summary_text = text.split('│ By model', 1)[1]
                    content = ' '.join(line[2:-2].strip() for line in summary_text.splitlines()
                                       if line.startswith('│ '))
                    if columns >= 120:
                        total = next(line[2:-2].split() for line in text.splitlines() if line.startswith('│ Total '))
                        self.assertEqual(total, ['Total', '1,000', '390', '200', '50',
                                                 '1,200', '39.0%', '16.7%', '$3.7500'])
                    else:
                        for field in ('Input 1,000', 'Cached 390', 'Output 200', 'Total 1,200',
                                      'Reasoning 50', 'Cache write 50', 'Cached/In 39.0%',
                                      'Out/Total 16.7%', 'API cost $3.7500'):
                            self.assertIn(field, content)
                    self.assertIn('╭─ Selected', text)
                    self.assertIn('│ By model', text)
                    self.assertEqual(sum(line.startswith('╭') for line in text.splitlines()), 1)

    def test_combined_account_panel_fits_narrow_and_wide_screens(self):
        args, data = self.report()
        for command in ('report', 'watch'):
            args.command = command
            for columns in (20, 40, 80, 100, 120, 140):
                with self.subTest(command=command, columns=columns):
                    text = self.render(data, args, columns)
                    self.assertTrue(all(M['width'](line) <= columns for line in text.splitlines()))
                    # Wrapping can separate the value from 'used' on very narrow screens.
                    content = ' '.join(line[2:-2].strip(' │') for line in text.splitlines()
                                       if line.startswith('│ '))
                    self.assertIn('50% used', content)
                    self.assertIn('25% used', content)

    def test_unknown_cost_is_explicit_in_summary_and_models(self):
        data = usage_tests.Presentation().data(unknown=True)
        for columns in (80, 140):
            for command in ('report', 'watch'):
                text = self.render(data, M['parse_args']([command]), columns)
                models = text.split('│ By model', 1)[1]
                self.assertIn('$80.0000 + unknown', models)
                self.assertIn('unknown', models)
                self.assertIn('100', models)

    def test_large_partial_model_cost_is_not_clipped_in_wide_panel(self):
        data = model_tests.ModelTotal().data()
        row = data['summary']['by_model_detail']['beta']
        row.update(api_cost_usd=None, known_api_cost_usd=1342.3968, estimate_is_partial=True)
        text = self.render(data, M['parse_args']([]), 140)
        self.assertIn('$1,342.3968 + unknown', text.split('│ By model', 1)[1])

    def test_live_failure_and_empty_report_are_readable_and_json_stays_structured(self):
        with patch.dict(G, {'datetime': Clock}):
            args = M['parse_args'](['--quota', 'live', '--timezone', 'UTC'])
            data = M['report'](args, M['Scanner'](), fixture_prices(), self.calendar, [],
                               price_catalog=fixture_catalog(), live=(None, 'No signed-in account'))
        self.assertEqual(data['period']['selection'], 'fallback')
        self.assertEqual(data['summary']['usage']['total_tokens'], 0)
        text = self.render(data, args)
        self.assertIn('No signed-in account', text)
        self.assertIn('No local usage records in this report period', text)
        total = next(line[2:-2].split() for line in text.splitlines() if line.startswith('│ Total '))
        self.assertEqual(total[1:6], ['0', '0', '0', '0', '0'])
        with patch.dict(G, {'report': lambda *a, **kw: data}), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(M['main'](['--quota', 'off', '--json', '--color']), 1)
        parsed = json.loads(output.getvalue())
        self.assertEqual(parsed, data)
        self.assertNotIn('\033', output.getvalue())

    def test_combined_colors_and_no_color_preserve_content(self):
        args, data = self.report()
        plain = self.render(data, args, 100)
        colored = '\n'.join(M['style_line'](line) for line in plain.splitlines())
        self.assertEqual(M['re'].sub(r'\x1b\[[0-9;]*m', '', colored), plain)
        self.assertIn('\033[36m│', colored)
        with patch.dict(G, {'report': lambda *a, **kw: data}), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(M['main'](['--quota', 'off', '--no-color']), 0)
        self.assertNotIn('\033', output.getvalue())
        self.assertIn('│ By model', output.getvalue())

    def test_local_coverage_and_empty_cycle_fit_report_and_watch(self):
        (self.root / 'a.jsonl').unlink()
        args, data = self.report()
        before = copy.deepcopy(data)
        for command in ('report', 'watch'):
            args.command = command
            for columns in (20, 40, 80, 100, 120, 140):
                with self.subTest(command=command, columns=columns):
                    text = self.render(data, args, columns)
                    self.assertTrue(all(M['width'](line) <= columns for line in text.splitlines()))
                    content = ' '.join(line[2:-2].strip(' │') for line in text.splitlines()
                                       if line.startswith('│ '))
                    self.assertIn('Usage source: Local Codex session logs', content)
                    self.assertIn('No local usage records in this cycle', content)
                    self.assertIn('Quota is account-wide; local logs cover only recorded local sessions.', content)
                    self.assertNotIn('No usage in this report period', text)
                    self.assertNotIn('No records', text)
                    self.assertNotIn('dot', text)
                    self.assertNotIn('other devices', text)
        self.assertEqual(data, before)
        with patch.dict(G, {'report': lambda *a, **kw: data}), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(M['main'](['--quota', 'off', '--json', '--no-color']), 1)
        self.assertEqual(json.loads(output.getvalue()), before)

    def test_empty_explicit_period_preserves_nonempty_cycle_and_local_scope(self):
        args, data = self.report(['--since', '2026-09-01', '--until', '2026-09-02'])
        self.assertEqual(data['summary']['token_events'], 0)
        self.assertGreater(data['summary']['cycle_estimates']['primary']['local']['token_events'], 0)
        text = self.render(data, args)
        self.assertIn('No local usage records in this report period', text)
        self.assertNotIn('No local usage records in this cycle', text)
        self.assertIn('Usage source: Local Codex session logs', text)

    def test_empty_local_period_without_quota_has_no_account_coverage_claim(self):
        (self.root / 'a.jsonl').unlink()
        for flags in (['--quota', 'off'], ['--quota', 'off', '--days', 'all']):
            args, data = self.report(flags)
            for columns in (40, 80, 140):
                with self.subTest(flags=flags, columns=columns):
                    text = self.render(data, args, columns)
                    content = ' '.join(line[2:-2].strip() for line in text.splitlines()
                                       if line.startswith('│ '))
                    self.assertIn('Usage source: Local Codex session logs', content)
                    self.assertIn('No local usage records in this report period', content)
                    self.assertNotIn('Quota is account-wide', content)
                    self.assertNotIn('Account quota', text)

    def test_account_coverage_note_requires_reported_usage(self):
        current = copy.deepcopy(self.current)
        for used in (0, None):
            for kind in ('primary', 'secondary'):
                current['limits']['codex'][kind]['used_percent'] = used
            args, data = self.report(current=current)
            self.assertNotIn('Quota is account-wide', self.render(data, args))
        args, data = self.report(['--no-project'])
        self.assertIn('Quota is account-wide; local logs cover only recorded local sessions.', self.render(data, args))

    def test_two_periods_are_independent_and_models_follow_selected(self):
        args, data = self.report()
        selected, reference = data['period_summaries']
        self.assertEqual(selected['name'], 'Selected')
        self.assertEqual(selected['period'], data['period'])
        self.assertEqual(selected['summary']['usage'], data['summary']['usage'])
        self.assertEqual(selected['summary']['usage']['total_tokens'], 900)
        self.assertEqual(reference['name'], 'Last 30 days')
        self.assertEqual(reference['summary']['usage']['total_tokens'], 1000)
        self.assertEqual(reference['summary']['sessions'], 1)
        self.assertEqual(reference['summary']['token_events'], 4)
        self.assertEqual(reference['period']['start'], '2026-08-23T02:00:00+00:00')
        self.assertEqual(reference['period']['end'], data['generated_at'])
        self.assertEqual(reference['period']['timezone'], 'UTC')
        self.assertEqual(reference['source'], 'local_session_logs')
        self.assertEqual(sum(row['usage']['total_tokens'] for row in data['summary']['by_model_detail'].values()), 900)
        for columns in (20, 40, 80, 120, 140):
            text = self.render(data, args, columns)
            self.assertTrue(all(M['width'](line) <= columns for line in text.splitlines()))
            content = ' '.join(line[2:-2].strip() for line in text.splitlines() if line.startswith('│ '))
            self.assertIn('Last 30 days', text)
            self.assertIn('Reference', content)
            self.assertIn('totals are separate', content)
            if columns >= 40:
                self.assertEqual(text.count('│ By model'), 2)

    def test_explicit_selected_keeps_rolling_reference_at_now(self):
        args, data = self.report(['--since', '2026-09-20', '--until', '2026-09-20'])
        selected, reference = data['period_summaries']
        self.assertEqual(selected['summary']['usage']['total_tokens'], 100)
        self.assertEqual(reference['summary']['usage']['total_tokens'], 1000)
        self.assertEqual(selected['period']['end'], '2026-09-21T00:00:00+00:00')
        self.assertEqual(reference['period']['end'], '2026-09-22T02:00:00+00:00')
        self.assertEqual(sum(data['summary']['by_model']['gpt-6-sol'].values()), 200)
        self.assertIn('Explicit date range', self.render(data, args))

    def test_equal_30_day_bounds_are_shown_once_for_explicit_and_fallback(self):
        for flags in (['--days', '30'], ['--quota', 'off']):
            args, data = self.report(flags)
            self.assertEqual(len(data['period_summaries']), 1)
            self.assertTrue(data['period_summaries'][0]['also_last_30_days'])
            text = self.render(data, args)
            self.assertIn('Selected also covers the last 30 days', text)
            self.assertNotIn('Last 30 days: Reference', text)
        # Different bounds must stay separate even when both happen to be empty.
        (self.root / 'a.jsonl').unlink()
        _, data = self.report(['--days', '1'])
        self.assertEqual(len(data['period_summaries']), 2)

    def test_reference_boundaries_are_start_inclusive_and_end_exclusive(self):
        rows = [meta('bounds', created='2026-08-20T00:00:00Z'), context('gpt-6-sol'),
                token(7, last=7, stamp='2026-08-23T01:59:59Z'),
                token(18, last=11, stamp='2026-08-23T02:00:00Z'),
                token(31, last=13, stamp='2026-09-22T02:00:00Z'),
                token(48, last=17, stamp='2026-09-22T02:00:01Z')]
        (self.root / 'bounds.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in rows))
        _, data = self.report()
        selected, reference = data['period_summaries']
        self.assertEqual(selected['summary']['usage']['total_tokens'], 900)
        self.assertEqual(reference['summary']['usage']['total_tokens'], 1011)

    def test_each_period_model_table_preserves_totals_and_ratios(self):
        args, data = self.report()
        text = self.render(data, args, 140)
        headers = [line[2:-2].split() for line in text.splitlines() if line.startswith('│ Name ')]
        self.assertEqual(len(headers), 2)
        self.assertEqual(headers[0], headers[1])
        self.assertEqual(headers[0], ['Name', 'Input', 'Cached', 'Output', 'Reasoning',
                                     'Total', 'Cached/In', 'Out/Total', 'API', 'Cost'])
        numeric = model_tests.ModelTotal().data()
        text = self.render(numeric, M['parse_args']([]), 140)
        total = next(line[2:-2].split() for line in text.splitlines() if line.startswith('│ Total '))
        self.assertEqual(total[6:8], ['39.0%', '16.7%'])  # Recomputed aggregate ratios.
        beta = next(line[2:-2].split() for line in text.splitlines() if line.startswith('│ beta '))
        self.assertEqual(beta[5], '1,000')
        self.assertEqual(beta[6:8], ['33.3%', '10.0%'])

    def test_wide_period_model_table_keeps_large_partial_cost(self):
        args, data = self.report()
        reference = data['period_summaries'][1]['summary']
        reference.update(api_cost_usd=None, known_api_cost_usd=1510.5825, estimate_is_partial=True)
        reference['usage'] = M['asdict'](M['Usage'](1810709816, 1734637440, 0, 7049109, 2397604, 1817758925))
        reference['by_model_detail'] = {'large-model': {key: reference[key] for key in M['Bucket']().export()}}
        reference['by_model'] = {'large-model': reference['usage']}
        text = self.render(data, args, 140)
        period = text.split('╭─ Last 30 days', 1)[1]
        self.assertIn('│ Name ', period)
        self.assertIn('│ large-model ', period)
        self.assertIn('1,817,758,925', period)
        self.assertIn('$1,510.5825 + unknown', period)
        self.assertTrue(all(M['width'](line) <= 140 for line in text.splitlines()))

    def test_empty_current_cycle_keeps_nonempty_reference(self):
        current = copy.deepcopy(self.current)
        current['fetched_at'] = '2026-09-22T01:55:00+00:00'
        current['limits']['codex']['secondary']['resets_at_utc'] = '2026-09-29T01:50:00+00:00'
        args, data = self.report(current=current)
        self.assertEqual(data['period']['selection'], 'current_cycle')
        self.assertEqual(data['summary']['token_events'], 0)
        self.assertEqual(data['period_summaries'][1]['summary']['token_events'], 4)
        text = self.render(data, args, 140)
        self.assertIn('No local usage records in this cycle', text)
        self.assertNotIn('No local usage records', text.split('╭─ Last 30 days', 1)[1])
        self.assertIn('1,000', text.split('╭─ Last 30 days', 1)[1])

    def test_json_reference_is_additive_and_rendering_does_not_mutate_it(self):
        args, data = self.report(['--top', '1'])
        before = copy.deepcopy(data)
        self.render(data, args, 140)
        self.render(data, args, 40)
        self.assertEqual(data, before)
        self.assertEqual(data['summary']['usage']['total_tokens'], 900)
        for flags in (['--json'], ['--output', '-'], ['watch', '--count', '1', '--json']):
            with patch.dict(G, {'report': lambda *a, **kw: data}), contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(M['main']([*flags, '--quota', 'off', '--color']), 0)
            self.assertEqual(json.loads(output.getvalue()), before)
            self.assertNotIn('\033', output.getvalue())

    def test_periods_have_independent_model_sets_and_deduplicated_totals(self):
        rows = [meta('reference', created='2026-09-20T00:00:00Z'), context('reference-only'),
                token(123, last=123, stamp='2026-09-20T02:00:00Z')]
        content = ''.join(json.dumps(row) + '\n' for row in rows)
        (self.root / 'reference.jsonl').write_text(content)
        (self.root / 'reference-copy.jsonl').write_text(content)
        args, data = self.report(['--top', '0'])
        selected, reference = data['period_summaries']
        self.assertEqual(set(selected['summary']['by_model']), {'gpt-6-sol'})
        self.assertEqual(set(reference['summary']['by_model']), {'gpt-6-sol', 'reference-only'})
        self.assertEqual(selected['summary']['by_model']['gpt-6-sol']['total_tokens'], 900)
        self.assertEqual(reference['summary']['by_model']['gpt-6-sol']['total_tokens'], 1000)
        self.assertEqual(reference['summary']['by_model']['reference-only']['total_tokens'], 123)
        self.assertEqual(reference['summary']['usage']['total_tokens'], 1123)
        for item in (selected, reference):
            row = item['summary']
            self.assertEqual(sum(v['total_tokens'] for v in row['by_model'].values()), row['usage']['total_tokens'])
            for tokens in row['by_model'].values():
                self.assertEqual(tokens['total_tokens'], tokens['input_tokens'] + tokens['output_tokens'])
        for columns in (40, 80, 140):
            text = self.render(data, args, columns)
            cycle, recent = text.split('╭─ Last 30 days', 1)
            self.assertNotIn('reference-only', cycle)
            self.assertIn('reference-only', recent)
            self.assertEqual(text.count('│ By model'), 2)
        # --top affects only terminal rows in EACH period; JSON remains complete.
        args.top = 1
        text = self.render(data, args, 140)
        self.assertNotIn('│ reference-only', text)
        self.assertIn('Showing 1/2 models', text)
        self.assertEqual(len(reference['summary']['by_model_detail']), 2)

    def test_each_period_has_its_own_total_footer_without_redundant_overview(self):
        args, data = self.report(['--top', '1'])
        for command in ('report', 'watch'):
            args.command = command
            for columns in (40, 80, 140):
                text = self.render(data, args, columns)
                cycle, reference = text.split('╭─ Last 30 days', 1)
                self.assertNotIn('Period total', text)
                # Metadata only before each period's model table.
                for section in (cycle.split('╭─ This cycle', 1)[1], reference):
                    self.assertNotIn('Input ', section.split('│ By model', 1)[0])
                    self.assertNotIn('API cost ', section.split('│ By model', 1)[0])
                if columns >= 120:
                    cycle_total = next(line[2:-2].split() for line in cycle.splitlines() if line.startswith('│ Total '))
                    reference_total = next(line[2:-2].split() for line in reference.splitlines() if line.startswith('│ Total '))
                    self.assertEqual(cycle_total[5], '900')
                    self.assertEqual(reference_total[5], '1,000')
                else:
                    self.assertIn('Total · API cost', cycle)
                    self.assertIn('Total 900', cycle)
                    self.assertIn('Total · API cost', reference)
                    self.assertIn('Total 1,000', reference)
        self.assertNotIn('Total', data['summary']['by_model'])
        self.assertNotIn('Total', data['period_summaries'][1]['summary']['by_model'])

    def test_partial_total_uses_known_cost_subtotal_and_unique_sessions(self):
        data = model_tests.ModelTotal().data()
        row = data['summary']['by_model_detail']['beta']
        row.update(api_cost_usd=None, estimate_is_partial=True)
        # Unknown pricing contributes tokens, but no fabricated cost.
        data['summary'].update(api_cost_usd=None, estimate_is_partial=True)
        text = self.render(data, M['parse_args'](['--top', '1']), 140)
        total = next(line[2:-2].split() for line in text.splitlines() if line.startswith('│ Total '))
        self.assertEqual(total[1:8], ['1,000', '390', '200', '50', '1,200', '39.0%', '16.7%'])
        self.assertEqual(' '.join(total[8:]), '$3.7500 + unknown')
        text = self.render(data, M['parse_args'](['--top', '1']), 80)
        footer = text.split('│ Total · API cost', 1)[1]
        self.assertIn('Sessions 1 · Events 3', footer)  # Two models share one session.
        self.assertIn('Total covers all models', text)
