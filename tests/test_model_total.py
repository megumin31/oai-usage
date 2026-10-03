import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_usage import M, G, U, DT, at, meta, context, token, fixture_catalog, fixture_prices


class ModelTotal(unittest.TestCase):
    def data(self, empty=False):
        rows = []
        if not empty:
            # Cached and cache-write tokens are subsets of input; reasoning is a subset of output.
            for model, tokens in (('alpha', U(100, 90, 0, 100, 20, 200)),
                                  ('beta', U(300, 100, 10, 40, 15, 340)),
                                  ('beta', U(600, 200, 40, 60, 15, 660))):
                rows.append((M['Event']('same-session', at(), model, tokens, tokens.input_tokens),
                             M['Charge'](M['Decimal']('1.25'))))
        session = M['Session']('same-session', None, None, [], [event for event, _ in rows])
        summary = M['aggregate'](rows, [session], M['Calendar'].make('UTC'), None,
                                 at('2026-09-23T00:00:00Z'), 'model')
        summary.update(current_rate_limits=None, cycle_estimates={})
        return {'summary': summary, 'period': {'start': None, 'end': '2026-09-23T00:00:00Z'},
                'diagnostics': {'issues': {}, 'files': int(not empty)}}

    def render(self, data, columns=120, *flags):
        return M['render'](data, M['parse_args'](list(flags)), M['Calendar'].make('UTC'), columns)

    def test_wide_each_model_has_total_after_output_without_double_counting(self):
        text = self.render(self.data())
        header = next(line for line in text.splitlines() if line.startswith('  Name'))
        self.assertEqual(header.split(), ['Name', 'Input', 'Cached', 'Output', 'Total',
                                          'Reasoning', 'Cached/In', 'Out/Total', 'API', 'Cost'])
        for model, expected in (
            ('alpha', ['alpha', '100', '90', '100', '200', '20', '90.0%', '50.0%', '$1.2500']),
            ('beta', ['beta', '900', '300', '100', '1,000', '30', '33.3%', '10.0%', '$2.5000']),
        ):
            with self.subTest(model=model):
                self.assertEqual(next(line for line in text.splitlines()
                                      if line.startswith('  ' + model)).split(), expected)
        self.assertFalse(any(line.startswith('  Total') for line in text.splitlines()))
        self.assertNotIn('Total includes all models', text)

    def test_top_keeps_only_selected_model_with_its_own_total(self):
        text = self.render(self.data(), 120, '--top', '1')
        self.assertNotIn('  alpha', text)
        beta = next(line for line in text.splitlines() if line.startswith('  beta'))
        self.assertEqual(beta.split()[4], '1,000')
        self.assertIn('Showing 1/2 entries', text)
        self.assertFalse(any(line.startswith('  Total') for line in text.splitlines()))

    def test_narrow_report_and_watch_show_each_model_total(self):
        for command in ('report', 'watch'):
            for columns in (30, 40, 80, 100):
                with self.subTest(command=command, columns=columns):
                    text = self.render(self.data(), columns, command)
                    self.assertIn('Total 200', text)
                    self.assertIn('Total 1,000', text)
                    self.assertNotIn('Total 1,200', text)
                    self.assertTrue(all(M['width'](line) <= columns for line in text.splitlines()))
                    if columns >= 80:
                        self.assertIn('In 100 · Cached 90 · Out 100 · Total 200', text)
                        self.assertIn('In 900 · Cached 300 · Out 100 · Total 1,000', text)

    def test_large_counters_preserve_total_and_cost_on_80_to_140_columns(self):
        data = self.data()
        row = data['summary']['by_model_detail']['beta']
        row['usage'] = M['asdict'](U(890548905, 864076544, 0, 3227660, 985498, 893776565))
        row['api_cost_usd'] = 1342.3968
        for columns in (80, 120, 140):
            with self.subTest(columns=columns):
                text = self.render(data, columns)
                self.assertIn('893,776,565', text)
                self.assertIn('$1,342.3968', text)
                self.assertTrue(all(M['width'](line) <= columns for line in text.splitlines()))

    def test_empty_period_keeps_no_records_and_no_aggregate_row(self):
        for columns in (80, 120):
            text = self.render(self.data(empty=True), columns)
            self.assertIn('No records', text)
            self.assertIn('No usage in this report period', text)
            self.assertFalse(any(line.startswith('  Total') for line in text.splitlines()))
            self.assertNotIn('Total 0', text)

    def test_other_dimensions_are_unchanged_and_all_only_adds_model_column(self):
        for by in ('day', 'week', 'month', 'session'):
            text = self.render(self.data(), 120, '--by', by)
            header = next((line for line in text.splitlines() if line.startswith('  Name')), '')
            self.assertNotIn('Total', header.split())
        text = self.render(self.data(), 120, '--all')
        headers = [line for line in text.splitlines() if line.startswith('  Name')]
        self.assertEqual(sum('Total' in line.split() for line in headers), 1)
        self.assertFalse(any(line.startswith('  Total') for line in text.splitlines()))

    def test_color_keeps_wide_column_alignment(self):
        text = self.render(self.data())
        colored = '\n'.join(M['style_line'](line) for line in text.splitlines())
        self.assertEqual(M['re'].sub(r'\x1b\[[0-9;]*m', '', colored), text)
        header = next(line for line in text.splitlines() if line.startswith('  Name'))
        right_edge = header.index('Total') + len('Total')
        for model, count in (('alpha', '200'), ('beta', '1,000')):
            line = next(line for line in text.splitlines() if line.startswith('  ' + model))
            self.assertEqual(line[:right_edge].split()[-1], count)
            self.assertEqual(line[right_edge], ' ')

    def test_json_and_saved_output_retain_original_schema_and_model_totals(self):
        data = self.data()
        before = json.dumps(data, sort_keys=True)
        self.render(data)
        with tempfile.TemporaryDirectory() as root:
            output_path = Path(root) / 'report.json'
            for flags in (['--json'], ['--output', '-'], ['watch', '--count', '1', '--json'],
                          ['--output', str(output_path)]):
                with self.subTest(flags=flags), patch.dict(G, {'report': lambda *a, **kw: data}), \
                     contextlib.redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(M['main']([*flags, '--quota', 'off', '--color']), 0)
                result = json.loads(output_path.read_text() if flags[0] == '--output' and flags[1] != '-'
                                    else output.getvalue())
                self.assertEqual(result, data)
                self.assertEqual(result['summary']['by_model_detail']['alpha']['usage']['total_tokens'], 200)
                self.assertEqual(result['summary']['by_model_detail']['beta']['usage']['total_tokens'], 1000)
        self.assertEqual(json.dumps(data, sort_keys=True), before)

    def test_date_filters_and_duplicate_logs_keep_each_model_total(self):
        class Clock(DT):
            @classmethod
            def now(cls, tz=None):
                return at('2026-09-22T02:00:00Z')
        with tempfile.TemporaryDirectory() as root:
            rows = [meta(created='2026-09-20T00:00:00Z'), context('gpt-6-sol'),
                    token(100, last=100, stamp='2026-09-20T01:00:00Z'), context('gpt-6-astra'),
                    token(300, last=200, stamp='2026-09-22T01:00:00Z')]
            for name in ('a.jsonl', 'duplicate.jsonl'):
                (Path(root) / name).write_text(''.join(json.dumps(row) + '\n' for row in rows))
            for flags, expected in (
                (['--days', 'all'], {'gpt-6-sol': 100, 'gpt-6-astra': 200}),
                (['--today'], {'gpt-6-astra': 200}), (['--days', '1'], {'gpt-6-astra': 200}),
                (['--since', '2026-09-20', '--until', '2026-09-20'], {'gpt-6-sol': 100}),
                (['--since', '2026-09-01', '--until', '2026-09-02'], {}),
            ):
                with self.subTest(flags=flags), patch.dict(G, {'datetime': Clock}):
                    args = M['parse_args'](['--root', root, '--quota', 'off', '--timezone', 'UTC', *flags])
                    data = M['report'](args, M['Scanner'](), fixture_prices(), M['Calendar'].make('UTC'),
                                       [Path(root)], price_catalog=fixture_catalog())
                details = data['summary']['by_model_detail']
                self.assertEqual({model: row['usage']['total_tokens'] for model, row in details.items()}, expected)
                for columns in (80, 120):
                    text = M['render'](data, args, M['Calendar'].make('UTC'), columns)
                    for model, total in expected.items():
                        if columns == 120:
                            line = next(line for line in text.splitlines() if line.startswith('  ' + model))
                            self.assertEqual(line.split()[4], str(total))
                        else:
                            self.assertIn('Total ' + str(total), text)
