"""R5 exact-profile projections of synthetic original quota edges, offline only."""
import copy
from datetime import timedelta
from decimal import Decimal
import json
import unittest
from unittest.mock import patch

from test_historical_segments import (BASE, G, M, MODEL, OTHER, PRICES, RESET,
                                      HistoricalFixtures, context, meta, quota,
                                      receipt, token)


class RawQuotaEdges(HistoricalFixtures):
    def analyze(self, **kwargs):
        options = dict(now=BASE + timedelta(hours=1), offsets=(0,), _raw_graph_only=True)
        options.update(kwargs)
        return M['historical_segments']([self.root], PRICES, **options)[0]

    def scenario(self, data, model=MODEL, offset=0):
        rows = [row for row in data['raw_model_scenarios']
                if row['profile']['model'] == model and row['alignment_offset_seconds'] == offset]
        self.assertEqual(len(rows), 1)
        return rows[0]

    def basic_raw(self):
        return [meta(), context(), quota(0, 10),
                receipt('a', 50, 1_000_000, cumulative=1_000_000), quota(100, 11),
                quota(200, 11), receipt('b', 250, 2_000_000, cumulative=3_000_000),
                quota(300, 13)]

    def test_raw_interior_observations_and_zero_edges_are_never_coarsened(self):
        self.write('one.jsonl', self.basic_raw())
        with patch.dict(G, historical_fixed_grid=lambda *args: self.fail('raw path built a grid'),
                        historical_target_owner=lambda *args: self.fail('raw path assigned an owner')):
            data = self.analyze()
        edges = self.scenario(data)['edges']
        self.assertEqual(data['kind'], 'raw_quota_edge_graph')
        self.assertEqual(data['counts']['quota_observations'], 4)
        self.assertEqual(data['counts']['raw_edges'], 3)
        self.assertEqual([e['known_api_cost_usd'] for e in edges], [1, 0, 2])
        self.assertEqual([e['delta_percent'] for e in edges], [1, 0, 2])
        self.assertTrue(all(e['status'] == 'eligible' for e in edges))
        self.assertEqual(edges[0]['after'], edges[1]['before'])
        self.assertEqual(edges[1]['after'], edges[2]['before'])
        self.assertEqual(edges[1]['target_cost_contract'], 'exact')
        self.assertFalse(edges[1]['eligible_for_clean_anchor'])
        self.assertTrue(edges[1]['clean_budget_eligible'])
        self.assertEqual(len({s['id'] for s in data['raw_observations']}), 4)

    def test_mixed_models_keep_exact_cost_per_edge_with_no_grid_owner(self):
        self.write('one.jsonl', [meta(), context(), quota(0, 10),
            receipt('a', 50, 1_000_000, cumulative=1_000_000), context(OTHER),
            receipt('b', 75, 2_000_000, cumulative=3_000_000), quota(100, 13),
            receipt('c', 150, 4_000_000, cumulative=7_000_000), quota(200, 17)])
        data = self.analyze()
        target, other = self.scenario(data)['edges'], self.scenario(data, OTHER)['edges']
        self.assertEqual([e['known_api_cost_usd'] for e in target], [1, 0])
        self.assertEqual([e['known_api_cost_usd'] for e in other], [2, 4])
        for a, b in zip(target, other):
            self.assertEqual(a['id'], b['id'])
            self.assertEqual(a['before'], b['before'])
            self.assertEqual(a['after'], b['after'])
            self.assertEqual(a['delta_percent'], b['delta_percent'])
            self.assertEqual(a['nuisance_local_known_api_cost_usd'], b['known_api_cost_usd'])
            self.assertEqual(a['known_api_cost_usd'] + b['known_api_cost_usd'], a['total_local_known_api_cost_usd'])
            self.assertEqual(a['status'], 'eligible')
            self.assertEqual(b['status'], 'eligible')
            self.assertNotIn('ownership', a)
            self.assertTrue(a['quota_delta_is_not_apportioned'])
        self.assertEqual(data['local_usage']['known_api_cost_usd'], 7)

    def test_exact_mode_and_effort_profiles_are_not_combined(self):
        self.write('one.jsonl', [meta(), context(service_tier='default', effort='high'), quota(0, 10),
            receipt('a', 50, 1_000_000, cumulative=1_000_000),
            context(service_tier='fast', effort='low'),
            receipt('b', 75, 2_000_000, cumulative=3_000_000), quota(100, 13)])
        rows = self.analyze()['raw_model_scenarios']
        self.assertEqual(len(rows), 2)
        costs = {(r['profile']['mode'], r['profile']['effort']): r['edges'][0]['known_api_cost_usd'] for r in rows}
        self.assertEqual(costs, {('default', 'high'): 1, ('fast', 'low'): 2})

    def test_receipts_obey_shifted_start_exclusive_end_inclusive(self):
        records = [meta(), context(), quota(100, 10)]
        for i, second in enumerate((100, 160, 200, 260, 300, 360)):
            records.append(receipt(str(i), second, 1_000_000, cumulative=(i + 1) * 1_000_000))
        records.extend([quota(200, 12), quota(300, 14)])
        self.write('one.jsonl', records)
        data = self.analyze(offsets=(0, 60))
        a, b = self.scenario(data, offset=0)['edges'], self.scenario(data, offset=60)['edges']
        self.assertEqual([e['known_api_cost_usd'] for e in a], [2, 2])
        self.assertEqual([e['known_api_cost_usd'] for e in b], [2, 2])
        self.assertEqual([e['id'] for e in a], [e['id'] for e in b])
        self.assertEqual(b[0]['target_receipt_interval']['start_exclusive'], (BASE + timedelta(seconds=160)).isoformat())
        self.assertEqual(b[0]['latest_target_event_at'], (BASE + timedelta(seconds=260)).isoformat())

    def test_zero_target_positive_quota_edge_is_kept_as_nuisance_path(self):
        records = self.basic_raw()
        records[5] = quota(200, 12)
        records[-1] = quota(300, 14)
        self.write('one.jsonl', records)
        zero = self.scenario(self.analyze())['edges'][1]
        self.assertEqual(zero['known_api_cost_usd'], 0)
        self.assertEqual(zero['delta_percent'], 1)
        self.assertEqual(zero['status'], 'eligible')

    def test_zero_price_target_receipt_is_still_an_original_edge(self):
        records = self.basic_raw()
        records[3]['payload']['usage']['cached_input_tokens'] = 1_000_000
        self.write('one.jsonl', records)
        prices = {MODEL: M['Price'](Decimal('1'), Decimal('0'), Decimal('2'))}
        data = M['historical_segments']([self.root], prices, offsets=(0,), _raw_graph_only=True,
                                       now=BASE + timedelta(hours=1))[0]
        edge = self.scenario(data)['edges'][0]
        self.assertEqual(edge['known_api_cost_usd'], 0)
        self.assertEqual(edge['local_event_count'], 1)
        self.assertEqual(edge['status'], 'eligible')

    def test_duplicate_files_and_same_time_observers_do_not_create_edges(self):
        records = self.basic_raw()
        self.write('one.jsonl', records)
        before = self.analyze()
        self.write('copy.jsonl', copy.deepcopy(records))
        self.write('observer.jsonl', [meta('observer'), *[r for r in records if r['type'] == 'event_msg']])
        after = self.analyze()
        self.assertEqual(self.scenario(before)['edges'], self.scenario(after)['edges'])
        self.assertEqual(after['counts']['raw_edges'], 3)
        self.assertEqual(after['counts']['quota_observations'], 4)
        self.assertEqual(after['local_usage']['known_api_cost_usd'], 3)
        self.assertGreater(after['issues']['duplicate_global_quota_observations_removed'], 0)
        self.assertTrue(all(s['source_record_count'] == 3 for s in after['raw_observations']))

    def test_same_time_conflict_stays_explicit_and_both_touched_edges_reject(self):
        self.write('one.jsonl', self.basic_raw())
        self.write('conflict.jsonl', [meta('conflict'), quota(100, 15)])
        data = self.analyze()
        conflict, = [s for s in data['raw_observations'] if s['conflict']]
        touched = [e for e in data['raw_edges'] if conflict['id'] in (e['before']['id'], e['after']['id'])]
        self.assertEqual(len(touched), 2)
        self.assertTrue(all(e['status'] == 'rejected' for e in touched))
        self.assertTrue(all('conflicting_quota_observation' in e['reasons'] for e in touched))

    def test_saturation_outside_cycle_and_long_gap_are_explicit(self):
        for records, reason in (
            ([meta(), context(), quota(0, 10), receipt('a', 50, 1_000_000), quota(301, 13)],
             'observation_gap_exceeds_policy'),
            ([meta(), context(), quota(0, 99), receipt('a', 50, 1_000_000), quota(100, 100)],
             'quota_saturated'),
            ([meta(), context(), quota(0, 10, reset=RESET + 100), receipt('a', 50, 1_000_000),
              quota(100, 13, reset=RESET + 100)], 'observation_outside_declared_cycle')):
            with self.subTest(reason=reason):
                self.write('one.jsonl', records)
                data = self.analyze()
                self.assertIn(reason, data['raw_edges'][0]['reasons'])
                self.assertTrue(all(e['status'] == 'rejected' for s in data['raw_model_scenarios'] for e in s['edges']))

    def test_future_observation_remains_audited_but_cannot_enter_fit(self):
        self.write('one.jsonl', self.basic_raw())
        data = self.analyze(now=BASE + timedelta(seconds=150))
        self.assertEqual(data['counts']['quota_observations'], 4)
        edges = self.scenario(data)['edges']
        self.assertEqual(edges[0]['status'], 'eligible')
        self.assertIn('observation_after_analysis_time', edges[1]['reasons'])
        self.assertIn('local_usage_after_analysis_time', edges[2]['reasons'])
        self.assertIsNone(data['current_quota_epochs'][0]['quota_epoch_id'])

    def test_shifted_interval_cannot_claim_complete_cost_beyond_analysis_time(self):
        self.write('one.jsonl', self.basic_raw())
        data = self.analyze(now=BASE + timedelta(seconds=300), offsets=(0, 60))
        unshifted = self.scenario(data, offset=0)['edges'][-1]
        shifted = self.scenario(data, offset=60)['edges'][-1]
        self.assertEqual(unshifted['status'], 'eligible')
        self.assertIn('alignment_after_analysis_time', shifted['reasons'])
        self.assertEqual(shifted['status'], 'rejected')
        self.assertNotIn('local_usage_after_analysis_time', shifted['reasons'])

    def test_offset_crossing_epoch_does_not_borrow_receipts(self):
        self.write('one.jsonl', [meta(), context(), quota(100, 10),
            receipt('a', 125, 1_000_000, cumulative=1_000_000), quota(150, 11),
            receipt('b', 175, 1_000_000, cumulative=2_000_000), quota(200, 8), quota(250, 9)])
        data = self.analyze(offsets=(0, 60))
        shifted = [e for s in data['raw_model_scenarios'] if s['alignment_offset_seconds'] == 60 for e in s['edges']]
        self.assertIn('alignment_crosses_quota_epoch', shifted[0]['reasons'])
        self.assertEqual(shifted[0]['status'], 'rejected')
        self.assertTrue(any('quota_epoch_changed' in e['reasons'] for e in data['raw_edges']))

    def test_supported_source_cache_drop_keeps_the_signed_interior_edge(self):
        self.write('b.jsonl', [meta('b'), context(), quota(0, 10),
            receipt('b1', 50, 1_000_000, owner='b', cumulative=1_000_000), quota(100, 10),
            receipt('b2', 150, 1_000_000, owner='b', cumulative=2_000_000),
            quota(180, 10), quota(200, 10),
            receipt('b3', 250, 1_000_000, owner='b', cumulative=3_000_000), quota(300, 11)])
        self.write('a.jsonl', [meta('a'), context(), quota(190, 11)])
        data = self.analyze()
        self.assertEqual(len(data['quota_epochs']), 1)
        edges = self.scenario(data)['edges']
        self.assertEqual(len(edges), 5)
        negative, = [e for e in edges if e['delta_percent'] < 0]
        self.assertEqual(negative['delta_percent'], -1)
        self.assertEqual(negative['status'], 'eligible')
        self.assertEqual(sum(e['delta_percent'] for e in edges), 1)
        self.assertIn('bounded_cross_observer_reordering', data['quota_epochs'][0]['continuity_assumptions'])

    def test_unsupported_single_source_drop_still_starts_an_epoch(self):
        self.write('one.jsonl', [meta(), context(), quota(0, 10),
            receipt('a', 50, 1_000_000), quota(100, 11), quota(150, 10), quota(200, 12)])
        data = self.analyze()
        self.assertEqual(len(data['quota_epochs']), 2)
        edge = data['raw_edges'][1]
        self.assertIn('quota_epoch_changed', edge['reasons'])
        self.assertEqual(edge['status'], 'rejected')

    def test_nuisance_only_known_model_reset_does_not_reject_target(self):
        self.write('one.jsonl', self.basic_raw())
        self.write('other.jsonl', [meta('other'), context(OTHER), token(25, 100, 100),
                                 token(100, 20, 20), token(150, 40, 20)])
        edges = self.scenario(self.analyze())['edges']
        self.assertEqual([e['status'] for e in edges], ['eligible'] * 3)
        self.assertEqual([e['nuisance_counter_reset_count'] for e in edges], [1, 0, 0])
        self.assertEqual([e['known_api_cost_usd'] for e in edges], [1, 0, 2])

    def test_target_counter_reset_weakens_only_half_open_affected_edge(self):
        self.write('one.jsonl', [meta(), context(), quota(0, 10), token(50, 100, 100),
            token(100, 20, 20), quota(100, 11), token(150, 40, 20), quota(200, 12)])
        edges = self.scenario(self.analyze())['edges']
        self.assertIn('counter_reset', edges[0]['target_defect_reasons'])
        self.assertFalse(edges[0]['eligible_for_clean_anchor'])
        self.assertFalse(edges[0]['clean_budget_eligible'])
        self.assertEqual(edges[0]['target_cost_contract'], 'lower_bound_only_zero')
        self.assertEqual(edges[0]['known_api_cost_usd'], 0)
        self.assertEqual(edges[0]['status'], 'eligible')
        self.assertEqual(edges[0]['reasons'], [])
        self.assertEqual(edges[1]['status'], 'eligible')

    def test_unknown_or_target_model_prior_prevents_nuisance_reset_exemption(self):
        for prior in (None, MODEL):
            self.write('one.jsonl', self.basic_raw())
            records = [meta('other')]
            if prior:
                records.append(context(prior))
            records += [token(25, 100, 100), context(OTHER), token(100, 20, 20)]
            self.write('other.jsonl', records)
            edges = self.scenario(self.analyze())['edges']
            self.assertIn('counter_reset', edges[0]['target_defect_reasons'])
            self.assertEqual(edges[0]['status'], 'eligible')
            self.assertEqual(edges[0]['known_api_cost_usd'], 0)
            self.assertFalse(edges[0]['clean_budget_eligible'])

    def test_incomplete_nuisance_price_does_not_contaminate_exact_target_cost(self):
        self.write('one.jsonl', self.basic_raw())
        self.write('other.jsonl', [meta('other'), context('unpriced-model'),
                                 receipt('nuisance', 50, 9_000_000, owner='other')])
        data = self.analyze()
        edge = self.scenario(data)['edges'][0]
        self.assertEqual(edge['known_api_cost_usd'], 1)
        self.assertEqual(edge['nuisance_unpriced_event_count'], 1)
        self.assertEqual(edge['status'], 'eligible')
        bad = self.scenario(data, 'unpriced-model')['edges'][0]
        self.assertEqual(bad['status'], 'eligible')
        self.assertIn('incomplete_local_price', bad['target_defect_reasons'])
        self.assertEqual(bad['known_api_cost_usd'], 0)
        self.assertFalse(bad['clean_budget_eligible'])

    def test_target_incomplete_cache_metadata_is_not_an_exact_cost_anchor(self):
        records = self.basic_raw()
        del records[3]['payload']['usage']['cached_input_tokens']
        self.write('one.jsonl', records)
        edge = self.scenario(self.analyze())['edges'][0]
        self.assertFalse(edge['cache_mix_complete'])
        self.assertIn('incomplete_target_cache_mix', edge['target_defect_reasons'])
        self.assertEqual(edge['status'], 'eligible')
        self.assertEqual(edge['known_api_cost_usd'], 0)
        self.assertFalse(edge['clean_budget_eligible'])
        self.assertEqual(edge['diagnostic_target_known_api_cost_usd'], 1)
        self.assertEqual(edge['known_api_cost_decimal_usd'], '0')
        self.assertEqual(Decimal(edge['diagnostic_target_known_api_cost_decimal_usd']), Decimal(1))

    def test_counter_component_correction_never_supplies_clean_anchor(self):
        first, second = token(50, 100, 100), token(150, 200, 100)
        first['payload']['info']['total_token_usage']['cached_input_tokens'] = 50
        second['payload']['info']['total_token_usage']['cached_input_tokens'] = 25
        self.write('one.jsonl', [meta(), context(), quota(0, 10), first,
                                quota(100, 11), second, quota(200, 12)])
        data = self.analyze()
        self.assertGreater(data['issues']['counter_component_corrections'], 0)
        edges = self.scenario(data)['edges']
        self.assertTrue(all(e['status'] == 'eligible' for e in edges))
        self.assertTrue(all('session_ledger_incomplete' in e['target_defect_reasons'] for e in edges))
        self.assertTrue(all(e['known_api_cost_usd'] == 0 for e in edges))
        self.assertTrue(all(not e['clean_budget_eligible'] for e in edges))

    def test_defective_cost_bridge_preserves_one_sided_shared_endpoint_contradiction(self):
        self.write('one.jsonl', [meta(), context(), quota(0, 10),
            receipt('a', 50, 1_000_000, cumulative=1_000_000), quota(100, 11),
            quota(200, 11), receipt('b', 250, 1_000_000, cumulative=2_000_000), quota(300, 12)])
        incomplete = receipt('bridge', 150, 1_000_000, owner='bridge')
        del incomplete['payload']['usage']['cached_input_tokens']
        self.write('bridge.jsonl', [meta('bridge'), context(), incomplete])
        data = self.analyze()
        edges = self.scenario(data)['edges']
        self.assertTrue(all(e['status'] == 'eligible' for e in edges))
        self.assertEqual([e['known_api_cost_usd'] for e in edges], [1, 0, 1])
        self.assertEqual([e['clean_budget_eligible'] for e in edges], [True, False, True])
        self.assertEqual(edges[1]['diagnostic_target_known_api_cost_usd'], 1)
        self.assertEqual(edges[1]['nuisance_local_known_api_cost_usd'], 0)
        self.assertEqual(edges[1]['total_local_known_api_cost_usd'], 1)
        self.assertEqual(edges[1]['reasons'], [])
        physical = M['robust_rate_interval'](edges, set(), 1)
        disconnected = M['robust_rate_interval']([edges[0], edges[2]], set(), 1)
        self.assertFalse(M['robust_rate_contains'](physical, 3))
        self.assertTrue(M['robust_rate_contains'](disconnected, 3))
        self.assertEqual(physical['chain_count'], 1)
        self.assertEqual(disconnected['chain_count'], 2)

    def test_exact_raw_target_cost_has_separate_complete_ledger_audit(self):
        self.write('one.jsonl', self.basic_raw())
        edges = self.scenario(self.analyze())['edges']
        for edge in edges:
            self.assertEqual(edge['target_cost_contract'], 'exact')
            self.assertTrue(edge['clean_budget_eligible'])
            self.assertEqual(edge['diagnostic_target_known_api_cost_usd'], edge['known_api_cost_usd'])
            self.assertEqual(edge['diagnostic_target_known_api_cost_decimal_usd'], edge['known_api_cost_decimal_usd'])
            self.assertEqual(edge['target_defect_reasons'], [])

    def test_unprojected_edges_and_sparse_single_snapshot_evidence_are_retained(self):
        self.write('one.jsonl', [meta(), quota(0, 10), quota(100, 11)])
        data = self.analyze()
        self.assertEqual(data['counts']['raw_edges'], 1)
        self.assertEqual(data['raw_model_scenarios'], [])
        self.write('one.jsonl', [meta(), context(), quota(0, 10), receipt('a', 50, 1_000_000)])
        data = self.analyze()
        self.assertEqual(self.scenario(data)['edges'], [])
        self.assertEqual(data['counts']['quota_observations'], 1)

    def test_metadata_only_source_read_is_unchanged_no_network_or_default_roots(self):
        records = self.basic_raw() + [{'type': 'response_item', 'payload': {
            'content': 'PRIVATE_CHAT_SENTINEL', 'arguments': 'PRIVATE_TOOL_SENTINEL'}}]
        path = self.write('one.jsonl', records)
        before = path.read_bytes()
        with patch.dict(G, default_roots=lambda *a, **kw: self.fail('default roots touched'),
                        download=lambda *a, **kw: self.fail('network touched')):
            data = self.analyze(source_label='fixture-roots', account_label='synthetic')
        self.assertNotIn('PRIVATE_', json.dumps(data))
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(data['source']['label'], 'fixture-roots')
        self.assertEqual(data['source']['account_label'], 'synthetic')
        self.assertFalse(data['source']['account_verified'])
        self.assertEqual(data['source']['selected_root_count'], 1)
        self.assertEqual(len(data['source']['root_set_fingerprint']), 64)
        json.dumps(data, allow_nan=False)

    def test_raw_flag_default_does_not_change_legacy_historical_shape(self):
        self.write('one.jsonl', self.basic_raw())
        data = super().analyze()
        self.assertEqual(data['kind'], 'historical_heuristic_quota_scenarios')
        self.assertNotIn('raw_edges', data)
        self.assertNotIn('raw_model_scenarios', data)


if __name__ == '__main__':
    unittest.main()
