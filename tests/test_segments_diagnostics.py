"""Synthetic existing-log diagnostics; never reads default Codex directories."""
import copy
import json
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'oai-usage'
M = runpy.run_path(str(SCRIPT), run_name='segment_diagnostics_tests')
G = M['diagnose_segments'].__globals__
CATALOG = Path(__file__).parent / 'fixtures/prices.json'
PRICES = M['parse_price_catalog'](json.loads(CATALOG.read_text()), 'fixture').prices


def meta(sid='one', parent=None, created='2026-09-22T00:00:00Z'):
    return {'type': 'session_meta', 'timestamp': created,
            'payload': {'id': sid, 'timestamp': created, 'forked_from_id': parent}}


def quota(minute, percent, n, reset=1790035200, model='gpt-5.4'):
    usage = {'input_tokens': n, 'output_tokens': 0, 'total_tokens': n,
             'cached_input_tokens': 0, 'cache_write_input_tokens': 0, 'reasoning_output_tokens': 0}
    return {'type': 'event_msg', 'timestamp': f'2026-09-22T00:{minute:02d}:00Z',
            'payload': {'type': 'token_count', 'info': {'total_token_usage': usage, 'last_token_usage': usage},
                        'rate_limits': {'limit_id': 'codex', 'plan_type': 'plus',
                                        'secondary': {'used_percent': percent, 'window_minutes': 10080, 'resets_at': reset}}}}


class SegmentDiagnostics(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, filename, records):
        path = self.root / filename
        path.write_text(''.join(json.dumps(r) + '\n' for r in records), encoding='utf-8')
        return path

    def diagnose(self):
        return M['diagnose_segments']([self.root], PRICES)[0]

    def basic(self):
        return [meta(), {'type': 'turn_context', 'payload': {'model': 'gpt-5.4'}},
                quota(1, 10, 1000), quota(2, 10, 2000), quota(3, 20, 3000)]

    def test_historical_pairs_are_never_estimates_and_zero_kept(self):
        self.write('one.jsonl', self.basic())
        data = self.diagnose()
        self.assertEqual(data['counts']['candidate_pairs'], 2)
        self.assertEqual(data['counts']['zero_delta_pairs'], 1)
        self.assertEqual(data['counts']['eligible_pairs'], 0)
        self.assertIsNone(data['full_cycle_api_equivalent_usd'])
        self.assertTrue(all(not c['eligible_for_estimation'] for c in data['candidates']))
        self.assertTrue(all('historical_cached_endpoint' in c['reasons'] for c in data['candidates']))
        self.assertEqual(data['candidates'][0]['before']['source'], 'historical_log')

    def test_log_duplicate_snapshots_dedup_and_original_accounting_used(self):
        records = self.basic()
        self.write('one.jsonl', records)
        self.write('copy.jsonl', records)
        data = self.diagnose()
        self.assertEqual(data['counts']['quota_observations'], 3)
        self.assertEqual(data['counts']['candidate_pairs'], 2)
        self.assertEqual(data['local_usage']['usage']['input_tokens'], 3000)
        self.assertEqual(data['issues']['duplicate_snapshots_removed'], 3)

    def test_matching_copies_after_conflict_do_not_inflate_conflict_count(self):
        records = self.basic()
        conflicting = copy.deepcopy(records)
        conflicting[2]['payload']['rate_limits']['secondary']['used_percent'] = 11
        self.write('a-original.jsonl', records)
        self.write('b-conflict.jsonl', conflicting)
        baseline = self.diagnose()
        self.assertEqual(baseline['issues']['conflicting_quota_observation'], 1)
        self.assertTrue(baseline['candidates'][0]['before']['conflict'])
        self.assertIn('conflicting_quota_observation', baseline['candidates'][0]['reasons'])
        for name in ('c-copy.jsonl', 'd-copy.jsonl'):
            with self.subTest(name=name):
                self.write(name, records)
                data = self.diagnose()
                self.assertEqual(data['issues']['conflicting_quota_observation'], 1)
                self.assertEqual(data['candidates'], baseline['candidates'])
                self.assertEqual(data['local_usage'], baseline['local_usage'])
                self.assertEqual(data['status'], 'cannot_estimate')
                self.assertEqual(data['counts']['eligible_pairs'], 0)
                self.assertIsNone(data['full_cycle_api_equivalent_usd'])
                self.assertTrue(all(not c['eligible_for_estimation'] for c in data['candidates']))
        conflicting[2]['payload']['rate_limits']['secondary']['used_percent'] = 12
        self.write('e-new-conflict.jsonl', conflicting)
        self.assertEqual(self.diagnose()['issues']['conflicting_quota_observation'], 2)

    def test_fork_parent_history_not_candidate(self):
        records = self.basic()
        records[0] = meta('child', 'parent', '2026-09-22T00:02:30Z')
        self.write('child.jsonl', records)
        data = self.diagnose()
        self.assertEqual(data['counts']['quota_observations'], 1)
        self.assertEqual(data['counts']['candidate_pairs'], 0)

    def test_all_selected_sessions_in_possible_cost_and_overlap_not_summed(self):
        self.write('one.jsonl', self.basic())
        records = [meta('two'), {'type': 'turn_context', 'payload': {'model': 'gpt-5.4'}}, quota(2, 10, 1000)]
        self.write('two.jsonl', records)
        data = self.diagnose()
        self.assertEqual(data['candidates'][0]['local_event_count'], 2)
        self.assertIn('Candidates can overlap.', ' '.join(data['limitations']))

    def test_reset_drift_and_decrease_explicit(self):
        records = self.basic()
        records[3]['payload']['rate_limits']['secondary']['resets_at'] += 1
        records[3]['payload']['rate_limits']['secondary']['used_percent'] = 5
        self.write('one.jsonl', records)
        c = self.diagnose()['candidates'][0]
        self.assertIn('window_reset_or_plan_changed', c['reasons'])
        self.assertIn('quota_decreased_or_cached', c['reasons'])

    def test_unknown_price_is_visible(self):
        records = self.basic()
        records[1]['payload']['model'] = 'unknown-new-model'
        self.write('one.jsonl', records)
        data = self.diagnose()
        self.assertTrue(data['local_usage']['estimate_is_partial'])
        self.assertIn('incomplete_local_price', data['candidates'][0]['reasons'])

    def test_malformed_quota_fields_and_lines(self):
        records = self.basic()
        records[2]['payload']['rate_limits']['secondary']['window_minutes'] = 300.9
        records[3]['payload']['rate_limits']['secondary']['used_percent'] = True
        self.write('one.jsonl', records)
        data = self.diagnose()
        self.assertEqual(data['issues']['invalid_quota_window'], 2)
        self.assertEqual(data['counts']['candidate_pairs'], 0)

    def test_task_markers_are_only_diagnostics(self):
        records = self.basic()
        records += [{'type': 'event_msg', 'timestamp': '2026-09-22T00:04:00Z',
                     'payload': {'type': kind}} for kind in ('task_started', 'task_complete', 'turn_aborted')]
        records += [{'type': 'response_item', 'payload': {'text': 'DO NOT RETAIN THIS SECRET CHAT'}}]
        self.write('one.jsonl', records)
        data = self.diagnose()
        self.assertEqual(data['task_boundary_markers']['task_complete'], 1)
        self.assertNotIn('DO NOT RETAIN', json.dumps(data))
        self.assertEqual(data['counts']['eligible_pairs'], 0)

    def test_empty_or_missing_root_is_actionable_noestimate(self):
        data = self.diagnose()
        self.assertEqual(data['counts']['candidate_pairs'], 0)
        data, _ = M['diagnose_segments']([self.root / 'missing'], PRICES)
        self.assertEqual(data['issues']['missing_roots'], 1)
        self.assertEqual(data['status'], 'cannot_estimate')

    def test_cli_requires_explicit_roots_and_never_uses_network(self):
        proc = subprocess.run([sys.executable, str(SCRIPT), 'segments', 'diagnose', '--price-catalog', str(CATALOG)],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2)
        self.write('one.jsonl', self.basic())
        proc = subprocess.run([sys.executable, str(SCRIPT), 'segments', 'diagnose', '--root', str(self.root),
                               '--price-catalog', str(CATALOG), '--json'], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)['counts']['files'], 1)

    def test_offline_dispatch_does_not_call_query_or_default_scanner(self):
        import asyncio
        import contextlib
        import io
        with patch.dict(G, {'fetch_live': unittest.mock.AsyncMock(side_effect=AssertionError('network forbidden')),
                            'load_price_catalog_async': unittest.mock.AsyncMock(side_effect=AssertionError('network forbidden'))}):
            with contextlib.redirect_stdout(io.StringIO()):
                status = asyncio.run(M['async_main'](['segments', 'diagnose', '--root', str(self.root),
                                                     '--price-catalog', str(CATALOG)]))
        self.assertEqual(status, 0)

    def test_nested_plan_never_exported(self):
        records = self.basic()
        for record in records[2:]:
            record['payload']['rate_limits']['plan_type'] = {'unexpected_blob': 'PRIVATE CONTENT'}
        self.write('one.jsonl', records)
        data = self.diagnose()
        self.assertEqual(data['issues']['invalid_quota_record'], 3)
        self.assertNotIn('PRIVATE CONTENT', json.dumps(data))

    def test_record_type_must_be_event_msg(self):
        records = self.basic()
        for record in records[2:]:
            record['type'] = 'response_item'
            record['unrelated'] = 'event_msg'
        self.write('one.jsonl', records)
        self.assertEqual(self.diagnose()['counts']['quota_observations'], 0)

    def test_changed_input_disables_all_candidate_costs(self):
        self.write('one.jsonl', self.basic())
        changed = self.write('two.jsonl', [meta('two')])
        scanner_type = M['Scanner']
        class ChangingScanner(scanner_type):
            def scan(inner, roots):
                result = super().scan(roots)
                with changed.open('a') as handle:
                    handle.write('{}\n')
                return result
        with patch.dict(G, {'Scanner': ChangingScanner}):
            data = self.diagnose()
        self.assertEqual(data['issues']['log_changed_during_diagnostics'], 1)
        self.assertEqual(data['counts']['candidate_pairs'], 2)
        self.assertTrue(all(c['possible_local_known_api_cost_usd'] is None for c in data['candidates']))
        self.assertTrue(all(not c['cost_alignment_available'] for c in data['candidates']))
        self.assertIn('unavailable (input changed or incomplete)', M['render_segment_diagnostics'](data))

    def test_missing_selected_root_disables_candidate_cost_alignment(self):
        self.write('one.jsonl', self.basic())
        data, _ = M['diagnose_segments']([self.root, self.root / 'missing'], PRICES)
        self.assertEqual(data['issues']['missing_roots'], 1)
        self.assertTrue(all(c['possible_local_known_api_cost_usd'] is None for c in data['candidates']))
        self.assertTrue(all('input_changed_unreadable_or_incomplete' in c['reasons'] for c in data['candidates']))

    def test_conflicting_individual_window_aliases_not_combined(self):
        records = self.basic()
        for record in records[2:]:
            quota = record['payload']['rate_limits']
            quota['individualLimit'] = copy.deepcopy(quota['secondary'])
            quota['individual_limit'] = copy.deepcopy(quota['secondary'])
            quota['individual_limit']['resets_at'] += 1
        self.write('one.jsonl', records)
        data = self.diagnose()
        self.assertEqual(data['issues']['conflicting_window_aliases'], 3)
        self.assertTrue(all(c['window_kind'] == 'secondary' for c in data['candidates']))

    def test_partial_line_is_not_read_past_captured_ledger_offset(self):
        path = self.write('one.jsonl', self.basic())
        with path.open('a') as handle:
            handle.write('{"type":"event_msg","payload":{"type":"token_count"')
        data = self.diagnose()
        self.assertEqual(data['issues']['pending_partial_line'], 1)
        self.assertEqual(data['counts']['quota_observations'], 3)
        self.assertTrue(all(not c['cost_alignment_available'] for c in data['candidates']))

    def test_diagnostic_result_is_not_accepted_as_manifest(self):
        with self.assertRaises(ValueError):
            M['analyze_segments'](self.diagnose(), PRICES)


if __name__ == '__main__':
    unittest.main()
