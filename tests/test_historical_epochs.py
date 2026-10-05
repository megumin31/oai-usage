"""Quota-epoch regressions using invented usage and a tiny metadata-only replay.

The fixture uses wholly invented quota fields and observer labels.
All response costs in this file are invented. No test
reads private logs, credentials, or online prices, or calls a quota endpoint.
"""
from datetime import timedelta
import json
from pathlib import Path

from test_historical_segments import (BASE, RESET, MODEL, OTHER, PRICES, M,
    HistoricalFixtures, context, meta, quota, receipt, stamp)

OBSERVATIONS = Path(__file__).parent / 'fixtures' / 'historical_epoch_observations.json'


class HistoricalEpochs(HistoricalFixtures):
    def trace(self, points, costs, *, resets=None, plans=None, models=None, efforts=None,
              step=900):
        self.assertEqual(len(costs), len(points) - 1)
        resets = resets or [RESET] * len(points)
        plans = plans or ['plus'] * len(points)
        rows = [meta(), context(), quota(0, points[0], reset=resets[0], plan=plans[0])]
        cumulative = 0
        for i, (used, cost) in enumerate(zip(points[1:], costs), 1):
            model = models[i - 1] if models else MODEL
            settings = {'reasoning_effort': efforts[i - 1]} if efforts else {}
            rows.append(context(model, **settings))
            if cost:
                amount = round(cost * 1_000_000)
                cumulative += amount
                rows.append(receipt(f'r{i}', i * step - step / 2, amount, cumulative=cumulative))
            rows.append(quota(i * step, used, reset=resets[i], plan=plans[i]))
        self.write('trace.jsonl', rows)
        return self.analyze(now=BASE + timedelta(hours=12))

    def positive(self, data, model=MODEL):
        return [r for r in self.scenarios(data, model) if r['observed']['known_api_cost_usd'] > 0]

    def assert_primary_unknown(self, row):
        self.assertIsNone(row['estimated_total_api_cost_usd'])
        self.assertEqual(row['total_quantization_range_usd'], {'lower': None, 'upper': None})
        self.assertIsNone(row['remaining']['estimated_remaining_api_cost_usd'])
        self.assertEqual(row['remaining']['quantization_range_usd'], {'lower': None, 'upper': None})
        self.assertEqual(row['status'], 'cannot_estimate')

    def metadata_replay(self, name, *, prefix=None):
        selected = json.loads(OBSERVATIONS.read_text())['cases'][name]
        if prefix is not None:
            selected = selected[:prefix]
        earliest = M['timestamp'](selected[0]['observed_at']) - timedelta(hours=1)
        grouped = {}
        for row in selected:
            sid = row['observer']
            grouped.setdefault(sid, [{'type': 'session_meta', 'timestamp': earliest.isoformat(),
                'payload': {'id': sid, 'timestamp': earliest.isoformat()}}, context()])
            pool, window, minutes, reset, plan = row['identity']
            grouped[sid].append({'type': 'event_msg', 'timestamp': row['observed_at'],
                'payload': {'type': 'token_count', 'rate_limits': {
                    'limit_id': pool, 'plan_type': plan,
                    window: {'used_percent': row['used_percent'], 'window_minutes': minutes,
                             'resets_at': int(M['timestamp'](reset).timestamp())}}}})
        for sid, records in grouped.items():
            self.write(sid + '.jsonl', records)
        now = M['timestamp'](selected[-1]['observed_at']) + timedelta(minutes=1)
        return self.analyze(now=now), selected

    def test_used_increase_consumes_quota_and_remaining_is_complement(self):
        data = self.trace([10, 30, 50], [20, 20])
        row = self.scenario(data)
        self.assertEqual(row['observed']['delta_percent'], 40)
        self.assertEqual(row['estimated_total_api_cost_usd'], 100)
        self.assertEqual(row['remaining']['used_percent'], 50)
        self.assertEqual(row['remaining']['remaining_percent'], 50)
        self.assertEqual(row['remaining']['estimated_remaining_api_cost_usd'], 50)
        self.assertEqual(len(data['quota_epochs']), 1)

    def test_supported_early_window_separates_cost_delta_and_remaining(self):
        resets = [RESET, RESET, RESET + 1800, RESET + 1800, RESET + 1800]
        data = self.trace([10, 30, 0, 15, 30], [20, 999, 30, 30], resets=resets)
        rows = sorted(self.positive(data), key=lambda r: r['historical_period']['start'])
        self.assertEqual(len(rows), 2)
        self.assertNotEqual(rows[0]['quota_epoch_id'], rows[1]['quota_epoch_id'])
        self.assertEqual([r['estimated_total_api_cost_usd'] for r in rows], [100, 200])
        self.assertEqual([r['observed']['known_api_cost_usd'] for r in rows], [20, 60])
        self.assertEqual([r['observed']['delta_percent'] for r in rows], [20, 30])
        self.assertEqual(rows[0]['remaining']['used_percent'], 30)
        self.assertEqual(rows[1]['remaining']['used_percent'], 30)
        self.assertEqual(rows[1]['remaining']['estimated_remaining_api_cost_usd'], 140)
        boundary = [a for a in data['attempts'] if 'quota_epoch_changed' in a['reasons']]
        self.assertEqual(sum(a['known_api_cost_usd'] for a in boundary), 999)
        self.assertTrue(data['quota_epochs'][-1]['supports_new_window'])
        self.assertEqual(data['quota_epochs'][0]['retired_at'], data['quota_epochs'][-1]['started_at'])

    def test_same_deadline_drop_is_ambiguous_and_keeps_below_peak_samples(self):
        data = self.trace([40, 60, 0, 10, 20], [20, 0, 20, 20])
        rows = sorted(self.positive(data), key=lambda r: r['historical_period']['start'])
        self.assertEqual(len(rows), 2)
        self.assertEqual([r['observed']['known_api_cost_usd'] for r in rows], [20, 40])
        self.assertEqual([r['observed']['delta_percent'] for r in rows], [20, 20])
        self.assertEqual([r['diagnostics']['nominal_total_api_cost_usd'] for r in rows], [100, 200])
        self.assertEqual(rows[0]['estimated_total_api_cost_usd'], 100)
        self.assert_primary_unknown(rows[1])
        epoch = next(e for e in data['quota_epochs'] if e['quota_epoch_id'] == rows[1]['quota_epoch_id'])
        self.assertEqual(epoch['status'], 'ambiguous')
        self.assertFalse(epoch['supports_new_window'])
        self.assertIn('quota_decrease_ambiguous', epoch['ambiguity_reasons'])
        self.assertEqual(rows[1]['observed']['boundary_coefficient_weight'], 2)
        self.assertEqual(rows[1]['diagnostics']['nominal_remaining_api_cost_usd'], 160)

    def test_recovery_above_old_peak_never_regroups_distinct_epochs(self):
        for pre_cost in (20, 80):
            with self.subTest(pre_cost=pre_cost):
                data = self.trace([10, 30, 0, 15, 30, 50], [pre_cost, 0, 30, 30, 40])
                rows = sorted(self.positive(data), key=lambda r: r['historical_period']['start'])
                self.assertEqual(len(rows), 2)
                self.assertEqual([r['observed']['known_api_cost_usd'] for r in rows], [pre_cost, 100])
                self.assertEqual([r['observed']['delta_percent'] for r in rows], [20, 50])
                self.assertEqual(rows[1]['diagnostics']['nominal_total_api_cost_usd'], 200)
                self.assert_primary_unknown(rows[1])
                self.assertNotEqual(rows[0]['quota_epoch_id'], rows[1]['quota_epoch_id'])
                self.assertEqual(rows[0]['remaining']['used_percent'], 30)

    def test_saturation_does_not_permanently_discard_later_ambiguous_epoch(self):
        data = self.trace([60, 80, 100, 0, 20, 40], [20, 20, 0, 40, 40])
        rows = sorted(self.positive(data), key=lambda r: r['historical_period']['start'])
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['observed']['known_api_cost_usd'], 20)
        self.assertEqual(rows[1]['observed']['known_api_cost_usd'], 80)
        self.assertEqual(rows[1]['observed']['delta_percent'], 40)
        self.assertEqual(rows[1]['diagnostics']['nominal_total_api_cost_usd'], 200)
        self.assert_primary_unknown(rows[1])
        self.assertIn('quota_saturated', json.dumps(data['attempts']))

    def test_fast_cache_compatible_return_is_not_confirmed_as_reset(self):
        data = self.trace([6, 0, 6, 7], [0, 1, 1], step=.01)
        self.assertEqual(len(data['quota_epochs']), 2)
        epoch = data['quota_epochs'][-1]
        self.assertEqual(epoch['status'], 'ambiguous')
        self.assertFalse(epoch['supports_new_window'])
        row, = self.positive(data)
        self.assert_primary_unknown(row)
        self.assertEqual(row['observed']['known_api_cost_usd'], 2)
        self.assertEqual(row['observed']['delta_percent'], 7)

    def test_old_window_return_is_quarantined_and_never_merged_or_current(self):
        resets = [RESET, RESET, RESET + 1800, RESET + 1800, RESET, RESET]
        data = self.trace([10, 30, 0, 20, 30, 50], [20, 0, 40, 0, 20], resets=resets)
        clean_old = [r for r in self.positive(data) if r['identity']['resets_at'] == stamp(604800).replace('Z', '+00:00')]
        self.assertEqual(len(clean_old), 1)
        self.assertEqual(clean_old[0]['observed']['delta_percent'], 20)
        self.assertEqual(clean_old[0]['observed']['known_api_cost_usd'], 20)
        self.assertEqual(clean_old[0]['remaining']['used_percent'], 30)
        self.assertEqual(clean_old[0]['remaining']['scope'], 'historical_as_of')
        returned = [e for e in data['quota_epochs'] if e['status'] == 'quarantined']
        self.assertTrue(returned)
        self.assertIn('retired_window_return', json.dumps(returned))
        self.assertFalse(any(r['remaining']['scope'] == 'current_cycle_as_of'
                             and r['remaining']['estimated_remaining_api_cost_usd'] is not None
                             for r in data['model_scenarios']))
        self.assertIn('retired_window_return', json.dumps(data['attempts']))

    def test_one_second_deadline_change_is_distinct_and_ambiguous(self):
        data = self.trace([10, 30, 40, 60], [20, 0, 40],
                          resets=[RESET, RESET, RESET + 1, RESET + 1])
        rows = sorted(self.positive(data), key=lambda r: r['historical_period']['start'])
        self.assertEqual(len(rows), 2)
        self.assertEqual(len({r['quota_epoch_id'] for r in rows}), 2)
        self.assertEqual([r['observed']['delta_percent'] for r in rows], [20, 20])
        self.assertEqual(rows[1]['diagnostics']['nominal_total_api_cost_usd'], 200)
        self.assert_primary_unknown(rows[1])
        self.assertIn('reset_alias_ambiguous', json.dumps(data['quota_epochs']))
        self.assertFalse(data['quota_epochs'][-1]['supports_new_window'])

    def test_small_forward_deadline_change_is_not_evidence_of_new_grant(self):
        data = self.trace([60, 80, 0, 20], [20, 0, 40],
                          resets=[RESET, RESET, RESET + 30, RESET + 30])
        self.assertFalse(data['quota_epochs'][-1]['supports_new_window'])
        post = max(self.positive(data), key=lambda r: r['historical_period']['start'])
        self.assertEqual(post['diagnostics']['nominal_total_api_cost_usd'], 200)
        self.assert_primary_unknown(post)

    def test_large_deadline_change_without_corresponding_used_drop_stays_ambiguous(self):
        data = self.trace([10, 30, 29, 49], [20, 0, 40],
                          resets=[RESET, RESET, RESET + 1800, RESET + 1800])
        self.assertFalse(data['quota_epochs'][-1]['supports_new_window'])
        post = max(self.positive(data), key=lambda r: r['historical_period']['start'])
        self.assert_primary_unknown(post)

    def test_plan_and_profile_boundaries_are_not_poolable(self):
        data = self.trace([10, 30, 0, 20, 40], [20, 0, 40, 60],
                          plans=['plus', 'plus', 'pro', 'pro', 'pro'],
                          models=[MODEL, MODEL, MODEL, OTHER],
                          efforts=['high', 'high', 'high', 'low'])
        rows = [r for r in data['model_scenarios'] if r['observed']['known_api_cost_usd'] > 0]
        self.assertEqual(len(rows), 3)
        self.assertEqual(sorted(r['diagnostics']['nominal_total_api_cost_usd'] for r in rows), [100, 200, 300])
        self.assertEqual(len({r['quota_epoch_id'] for r in rows}), 2)
        self.assertEqual({(r['identity']['plan_type'], r['profile']['model'], r['profile']['effort'])
                          for r in rows}, {('plus', MODEL, 'high'), ('pro', MODEL, 'high'), ('pro', OTHER, 'low')})

    def test_global_concurrent_observer_provenance_is_preserved_without_raw_ids(self):
        sid_a, sid_b = 'private-observer-A', 'private-observer-B'
        self.write('a.jsonl', [meta(sid_a), context(), quota(0, 10),
            receipt('a', 450, 1_000_000, owner=sid_a), quota(1800, 30),
            receipt('c', 2250, 3_000_000, owner=sid_a)])
        self.write('b.jsonl', [meta(sid_b), context(), quota(900, 20),
            receipt('b', 1350, 2_000_000, owner=sid_b), quota(2700, 40)])
        data = self.analyze()
        row = self.scenario(data)
        self.assertEqual(row['observed']['delta_percent'], 30)
        self.assertEqual(row['observed']['known_api_cost_usd'], 6)
        self.assertEqual(row['estimated_total_api_cost_usd'], 20)
        epoch, = data['quota_epochs']
        self.assertEqual(len(epoch['observer_labels']), 2)
        self.assertEqual(len(set(epoch['observer_labels'])), 2)
        exported = json.dumps(data)
        self.assertNotIn(sid_a, exported)
        self.assertNotIn(sid_b, exported)
        for attempt in data['attempts']:
            for endpoint in ('before', 'after'):
                self.assertIsNone(attempt[endpoint]['measurement_time'])
                self.assertTrue(attempt[endpoint]['observer_labels'])
                self.assertEqual(attempt[endpoint]['quota_epoch_id'], attempt['quota_epoch_id'])

    def test_equal_snapshot_from_two_observers_merges_without_losing_provenance(self):
        one = [meta('one'), context(), quota(0, 10), receipt('r', 450, 1_000_000), quota(900, 30)]
        self.write('a.jsonl', one)
        self.write('b.jsonl', [meta('two'), quota(0, 10), quota(900, 30)])
        row = self.scenario(self.analyze())
        self.assertEqual(row['observed']['known_api_cost_usd'], 1)
        self.assertEqual(row['observed']['delta_percent'], 20)
        data = self.analyze()
        self.assertEqual(len(data['quota_epochs'][0]['observer_labels']), 2)
        self.assertEqual(len(data['attempts'][0]['before']['observer_labels']), 2)
        self.assertEqual(len(data['attempts'][0]['after']['observer_labels']), 2)

    def test_synthetic_metadata_supports_early_new_windows(self):
        for name in ('synthetic_early_window_a', 'synthetic_early_window_b'):
            with self.subTest(name=name):
                for path in self.root.glob('*.jsonl'):
                    path.unlink()
                data, records = self.metadata_replay(name)
                self.assertEqual(len(data['quota_epochs']), 2)
                old, new = data['quota_epochs']
                self.assertTrue(new['supports_new_window'])
                self.assertEqual(new['status'], 'supported')
                self.assertEqual(old['retired_at'], records[1]['observed_at'])
                self.assertEqual(new['classification_as_of'], records[1]['observed_at'])
                self.assertNotEqual(old['quota_epoch_id'], new['quota_epoch_id'])
                self.assertIn('supported_new_window', json.dumps(data['attempts']))
                self.assertFalse(any(r['estimated_total_api_cost_usd'] is not None for r in data['model_scenarios']))

    def test_synthetic_metadata_old_window_return_is_quarantined(self):
        data, records = self.metadata_replay('synthetic_old_window_interleaving')
        self.assertGreaterEqual(len(data['quota_epochs']), 4)
        old_reset = records[0]['identity'][3]
        old = [e for e in data['quota_epochs'] if e['identity']['resets_at'] == old_reset]
        self.assertEqual(len(old), 2)
        self.assertIsNotNone(old[0]['retired_at'])
        self.assertEqual(old[1]['status'], 'quarantined')
        self.assertIn('retired_window_return', json.dumps(old[1]))
        self.assertEqual(old[1]['started_at'], records[2]['observed_at'])
        self.assertGreaterEqual(len({label for e in data['quota_epochs'] for label in e['observer_labels']}), 3)

    def test_unbounded_total_and_remaining_are_diagnostics_only(self):
        data = self.trace([10, 11], [1])
        row = self.scenario(data)
        self.assert_primary_unknown(row)
        self.assertEqual(row['diagnostics']['nominal_total_api_cost_usd'], 100)
        self.assertEqual(row['diagnostics']['nominal_remaining_api_cost_usd'], 89)
        self.assertIsNone(row['diagnostics']['total_quantization_range_usd']['upper'])
        self.assertIsNone(row['diagnostics']['remaining_quantization_range_usd']['upper'])
        json.dumps(data, allow_nan=False)

    def test_finite_but_wide_quantization_is_not_published_as_total(self):
        row = self.scenario(self.trace([10, 14], [1]))
        self.assert_primary_unknown(row)
        self.assertEqual(row['diagnostics']['nominal_total_api_cost_usd'], 25)
        self.assertIsNotNone(row['diagnostics']['total_quantization_range_usd']['upper'])

    def test_total_can_pass_while_remaining_range_is_too_wide(self):
        row = self.scenario(self.trace([90, 99], [9]))
        self.assertEqual(row['estimated_total_api_cost_usd'], 100)
        self.assertIsNone(row['remaining']['estimated_remaining_api_cost_usd'])
        self.assertEqual(row['remaining']['quantization_range_usd'], {'lower': None, 'upper': None})
        self.assertEqual(row['diagnostics']['nominal_remaining_api_cost_usd'], 1)
        self.assertAlmostEqual(row['diagnostics']['remaining_quantization_range_usd']['lower'], 0)
        self.assertAlmostEqual(row['diagnostics']['remaining_quantization_range_usd']['upper'], 18 / 7)

    def test_policy_alignment_divergence_withholds_all_primary_values(self):
        self.write('trace.jsonl', self.basic() + [receipt('late-expense', 1830, 50_000_000)])
        data = self.analyze(offsets=(0, 60))
        rows = self.scenarios(data)
        self.assertEqual(sorted(r['diagnostics']['nominal_total_api_cost_usd'] for r in rows), [75, 325])
        for row in rows:
            self.assert_primary_unknown(row)
            self.assertIn('sensitivity_divergence', row['reasons'])

    def test_sparse_current_epoch_never_borrows_old_coefficient(self):
        data = self.trace([10, 30, 0], [20, 0], resets=[RESET, RESET, RESET + 1800])
        current_id = data['quota_epochs'][-1]['quota_epoch_id']
        old, = self.positive(data)
        self.assertEqual(old['estimated_total_api_cost_usd'], 100)
        self.assertEqual(old['remaining']['scope'], 'historical_as_of')
        self.assertFalse(any(r['quota_epoch_id'] == current_id and
                             (r['estimated_total_api_cost_usd'] is not None or
                              r['remaining']['estimated_remaining_api_cost_usd'] is not None)
                             for r in data['model_scenarios']))

    def test_missing_reset_event_cannot_be_inferred_from_monotone_endpoints(self):
        row = self.scenario(self.trace([40, 60, 70], [20, 70]))
        self.assertEqual(row['diagnostics']['nominal_total_api_cost_usd'], 300)
        # These same observations admit hidden resets and external usage.  The
        # software must retain its conditional, unverified measurement basis.
        self.assertFalse(row['remaining']['is_live_lookup'])
        data = self.analyze()
        self.assertEqual(data['trust_level'], 'historical_low_trust')
        self.assertFalse(data['source']['account_verified'])
        self.assertFalse(data['quota_epochs'][0]['supports_new_window'])

    def test_zero_delta_cost_stays_in_new_epoch_and_shared_boundaries_cancel(self):
        data = self.trace([40, 60, 0, 5, 5, 20], [20, 0, 5, 1, 9])
        post = max(self.positive(data), key=lambda r: r['historical_period']['start'])
        self.assertEqual(post['observed']['known_api_cost_usd'], 15)
        self.assertEqual(post['observed']['delta_percent'], 20)
        self.assertEqual(post['observed']['boundary_coefficient_weight'], 2)
        self.assertEqual(post['observed']['delta_quantization_bounds'], {'lower': 18, 'upper': 21})
        self.assertEqual(post['counts']['zero_delta_intervals_retained'], 1)
        self.assertEqual(post['diagnostics']['nominal_total_api_cost_usd'], 75)
        self.assert_primary_unknown(post)

    def test_analysis_clock_is_not_cutoff_but_future_quota_cannot_publish(self):
        self.write('trace.jsonl', self.basic())
        data = self.analyze(now=BASE + timedelta(seconds=600))
        row = self.scenario(data)
        self.assertEqual(data['local_usage']['known_api_cost_usd'], 15)
        self.assertEqual(row['diagnostics']['nominal_total_api_cost_usd'], 75)
        self.assert_primary_unknown(row)
        self.assertIn('calibration_after_analysis_time', row['reasons'])
        self.assertNotEqual(row['remaining']['scope'], 'current_cycle_as_of')
        self.assertIn('not_log_cutoff', data['analysis_clock_policy'])

    def test_future_shifted_receipt_cannot_publish_at_earlier_clock(self):
        self.write('trace.jsonl', [meta(), context(), quota(0, 10), quota(600, 30),
                                  receipt('future-response', 650, 1_000_000)])
        policy = ({'id': 'shift', 'min_span_seconds': 600, 'max_gap_seconds': 900,
                   'min_delta_percent': 5},)
        data = self.analyze(now=BASE + timedelta(seconds=600), policies=policy, offsets=(60,))
        row = self.scenario(data)
        self.assertEqual(row['observed']['known_api_cost_usd'], 1)
        self.assertEqual(row['diagnostics']['nominal_total_api_cost_usd'], 5)
        self.assertEqual(data['local_usage']['known_api_cost_usd'], 1)
        self.assert_primary_unknown(row)
        self.assertIn('calibration_after_analysis_time', row['reasons'])
