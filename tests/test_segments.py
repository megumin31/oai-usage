"""Synthetic-only coverage for opt-in segmented local-workload estimates.

All account labels, quota observations and receipts here are invented. No test
reads a real Codex session directory or obtains prices or quota over a network.
"""
import asyncio
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, Mock, patch

SCRIPT = Path(__file__).resolve().parents[1] / 'oai-usage'
M = runpy.run_path(str(SCRIPT), run_name='segment_tests')
G = M['main'].__globals__
FIXTURES = Path(__file__).parent / 'fixtures' / 'segments'
PRICE_FIXTURE = FIXTURES.parent / 'prices.json'
MODEL = 'gpt-5.4-mini'
BASE = datetime(2026, 9, 22, tzinfo=timezone.utc)
CONTEXT = dict(account_key='synthetic-account', plan_type='pro', limit_id='codex',
               window_kind='secondary', mode='standard')
ASSERTIONS = dict.fromkeys(M['SEGMENT_ASSERTIONS'], True)
PRICES = {MODEL: M['Price'](Decimal('1'), Decimal('0.2'), Decimal('2'), Decimal('1.5'))}


def stamp(seconds):
    return (BASE + timedelta(seconds=seconds)).isoformat().replace('+00:00', 'Z')


def snapshot(sid, seconds, percent):
    return dict(id=sid, context=copy.deepcopy(CONTEXT), capture_started_at=stamp(seconds),
                observed_at=stamp(seconds + 1), window_minutes=10080, resets_at=stamp(604800),
                used_percent=percent, percent_resolution=1, source='app_server_rpc',
                observation_basis='retrieved_not_measured')


def receipt(rid, start=60, end=300, tokens=100000, thread='local-a'):
    return dict(response_id=rid, thread_id=thread, owner_thread_id=thread,
                owner_created_at=stamp(0), started_at=stamp(start), completed_at=stamp(end),
                model=MODEL, usage=dict(input_tokens=tokens, cached_input_tokens=0,
                cache_write_input_tokens=0, output_tokens=0, reasoning_output_tokens=0,
                total_tokens=tokens))


def segment(sid, before, after):
    return dict(id=sid, before=before, after=after, assertions=copy.deepcopy(ASSERTIONS))


def sample():
    return json.loads((FIXTURES / 'complete.json').read_text())


def analyze(raw=None, prices=None):
    return M['analyze_segments'](sample() if raw is None else raw, PRICES if prices is None else prices)


class SegmentedEstimates(unittest.TestCase):
    def assert_blocked(self, raw, reason=None):
        result = analyze(raw)
        self.assertEqual(result['status'], 'cannot_estimate')
        for group in result['groups']:
            self.assertEqual(group['status'], 'cannot_estimate')
            self.assertIsNone(group['full_cycle_api_equivalent_usd'])
            self.assertIsNone(group['quantization_only_range_usd'])
            self.assertIsNone(group['statistical_confidence_interval'])
        if reason:
            self.assertIn(reason, result['rejection_reasons'])
        return result

    def test_valid_synthetic_fixture_retains_zero_delta_and_shared_endpoint_cancellation(self):
        raw = sample()
        original = copy.deepcopy(raw)
        result = analyze(raw)
        self.assertEqual(raw, original, 'analysis must not rewrite supplied observations')
        self.assertEqual(result['status'], 'conditional_estimate')
        self.assertEqual(result['counts']['eligible_segments'], 3)
        group, = result['groups']
        self.assertEqual(group['zero_delta_segment_count'], 1)
        self.assertEqual(group['receipt_count'], 3)
        self.assertAlmostEqual(group['local_api_cost_usd'], .9)
        self.assertEqual(group['delta_percent'], 20)
        self.assertAlmostEqual(group['full_cycle_api_equivalent_usd'], 4.5)
        self.assertEqual(group['quantization_delta_bounds'], [18, 22])
        self.assertEqual(group['quantization_only_range_usd'], [90 / 22, 90 / 18])
        self.assertIsNone(group['statistical_confidence_interval'])
        self.assertEqual(group['workload_by_model'][MODEL]['usage']['total_tokens'], 900000)
        self.assertEqual(group['workload_by_model'][MODEL]['cost_fraction'], 1)

    def test_recorded_percentage_never_borrows_cross_identity_endpoint(self):
        for field, value in [('resets_at', stamp(604801)), ('source', 'user_observation')]:
            raw = sample()
            raw['snapshots'][-1][field] = value
            result = analyze(raw)
            original_group = result['groups'][0]
            self.assertEqual(original_group['recorded_account_usage']['used_percent'], raw['snapshots'][-2]['used_percent'])
        raw = sample()
        for snap in raw['snapshots']:
            snap['context']['account_key'] = 'different-account'
        result = analyze(raw)
        self.assertIsNone(result['groups'][0]['recorded_account_usage'])
        self.assertIn('No account observation matches', M['render_segments'](result))

    def test_ratio_of_sums_is_not_unweighted_mean_of_segment_ratios(self):
        raw = sample()
        raw['segments'].pop(1)
        raw['receipts'].pop(1)
        group, = analyze(raw)['groups']
        self.assertAlmostEqual(group['full_cycle_api_equivalent_usd'], 100 * (.1 + .6) / (5 + 15))
        self.assertNotAlmostEqual(group['full_cycle_api_equivalent_usd'], (100 * .1 / 5 + 100 * .6 / 15) / 2)

    def test_gaps_and_external_jumps_never_bridge_the_next_baseline(self):
        raw = sample()
        raw['snapshots'] = [snapshot('a', 0, 10), snapshot('b', 600, 15),
                            snapshot('c', 1200, 75), snapshot('d', 1800, 90)]
        raw['segments'] = [segment('one', 'a', 'b'), segment('two', 'c', 'd')]
        raw['receipts'] = [receipt('one'), receipt('gap', 700, 900, 10000000),
                           receipt('two', 1260, 1500, 600000)]
        result = analyze(raw)
        group, = result['groups']
        self.assertEqual(result['counts']['outside_segment_receipts'], 1)
        self.assertEqual(group['delta_percent'], 20)
        self.assertAlmostEqual(group['local_api_cost_usd'], .7)
        self.assertAlmostEqual(group['full_cycle_api_equivalent_usd'], 3.5)
        self.assertEqual(group['quantization_delta_bounds'], [16, 24])
        self.assertLess(group['active_seconds'], group['span_seconds'])

    def test_concurrent_local_requests_are_valid_when_wholly_inside_one_segment(self):
        raw = sample()
        raw['receipts'].append(receipt('concurrent', 100, 400, 100000, 'local-b'))
        result = analyze(raw)
        self.assertEqual(result['status'], 'conditional_estimate')
        self.assertEqual(result['groups'][0]['receipt_count'], 4)
        self.assertAlmostEqual(result['groups'][0]['local_api_cost_usd'], 1)

    def test_all_explicit_assertions_are_required_and_unverified(self):
        for assertion in ASSERTIONS:
            raw = sample()
            raw['segments'][0]['assertions'][assertion] = False
            with self.subTest(assertion=assertion):
                result = self.assert_blocked(raw, 'unconfirmed_' + assertion)
                self.assertIn('group_contains_rejected_attempts', result['groups'][0]['reasons'])
        limitations = ' '.join(analyze()['limitations']).lower()
        for phrase in ('user assertions', 'cannot be verified', 'cached quota', 'hidden other-device',
                       'not a statistical confidence interval', 'no held-out accuracy validation'):
            self.assertIn(phrase, limitations)
        self.assertNotIn('verified', analyze()['segments'][0])

    def test_historical_cached_endpoints_are_never_eligible(self):
        raw = sample()
        for item in raw['snapshots']:
            item['source'] = 'historical_log'
        self.assert_blocked(raw, 'historical_cached_endpoint')

    def test_user_observations_remain_conditional(self):
        raw = sample()
        for item in raw['snapshots']:
            item['source'] = 'user_observation'
        result = analyze(raw)
        self.assertEqual(result['status'], 'conditional_estimate')
        self.assertEqual(result['groups'][0]['source'], 'user_observation')
        self.assertTrue(result['experimental'])

    def test_any_decrease_blocks_whole_identity_group(self):
        raw = sample()
        raw['snapshots'][2]['used_percent'] = 14
        result = self.assert_blocked(raw, 'quota_decreased_or_cached')
        self.assertEqual(result['counts']['rejected_segments'], 1)
        self.assertEqual(result['counts']['eligible_segments'], 2)

    def test_zero_and_small_integer_signals_cannot_claim_precision(self):
        for delta in (0, 1, 2, 4):
            raw = sample()
            raw['snapshots'] = [snapshot('a', 0, 10), snapshot('b', 600, 10 + delta)]
            raw['segments'] = [segment('one', 'a', 'b')]
            raw['receipts'] = [receipt('one')]
            with self.subTest(delta=delta):
                result = self.assert_blocked(raw)
                self.assertIn('insufficient_percentage_signal', result['groups'][0]['reasons'])

    def test_large_quantization_blocks_otherwise_sufficient_signal(self):
        raw = sample()
        for item in raw['snapshots']:
            item['percent_resolution'] = 10
        result = self.assert_blocked(raw)
        self.assertIn('quantization_too_large', result['groups'][0]['reasons'])

    def test_boundary_quantization_is_clipped_to_zero_and_one_hundred(self):
        raw = sample()
        raw['snapshots'] = [snapshot('a', 0, 0), snapshot('b', 600, 99.5)]
        raw['segments'] = [segment('one', 'a', 'b')]
        raw['receipts'] = [receipt('one')]
        result = analyze(raw)
        self.assertEqual(result['groups'][0]['quantization_delta_bounds'], [97.5, 100])
        self.assertIsNone(result['groups'][0]['statistical_confidence_interval'])

    def test_saturated_endpoint_blocks_its_group_including_zero_delta_at_one_hundred(self):
        raw = sample()
        raw['snapshots'][-1]['used_percent'] = 100
        result = self.assert_blocked(raw, 'quota_saturated')
        self.assertEqual(result['counts']['eligible_segments'], 2)
        self.assertIn('group_contains_rejected_attempts', result['groups'][0]['reasons'])
        for before, after in ((100, 100), (100, 90), (90, 100)):
            raw = sample()
            raw['snapshots'] = [snapshot('a', 0, before), snapshot('b', 600, after)]
            raw['segments'] = [segment('one', 'a', 'b')]
            raw['receipts'] = [receipt('one')]
            with self.subTest(before=before, after=after):
                result = self.assert_blocked(raw, 'quota_saturated')
                self.assertEqual(result['counts']['eligible_segments'], 0)

    def test_fractional_percentages_use_exact_five_point_signal_threshold(self):
        raw = sample()
        raw['snapshots'] = [snapshot('a', 0, 3.7), snapshot('b', 600, 8.7)]
        raw['segments'] = [segment('one', 'a', 'b')]
        raw['receipts'] = [receipt('one')]
        self.assertLess(8.7 - 3.7, 5, 'fixture must expose binary-float subtraction error')
        result = analyze(raw)
        self.assertEqual(result['status'], 'conditional_estimate')
        group, = result['groups']
        self.assertEqual(group['delta_percent'], 5)
        self.assertEqual(group['quantization_delta_bounds'], [3, 7])
        self.assertEqual(group['full_cycle_api_equivalent_usd'], 2)
        raw['snapshots'][1]['used_percent'] = 8.699999999999
        result = self.assert_blocked(raw)
        self.assertIn('insufficient_percentage_signal', result['groups'][0]['reasons'])

    def test_changed_reset_by_one_second_or_window_blocks_segment(self):
        for field, value in (('resets_at', stamp(604801)), ('window_minutes', 10079)):
            raw = sample()
            raw['snapshots'][1][field] = value
            with self.subTest(field=field):
                self.assert_blocked(raw, 'window_or_reset_changed')

    def test_changed_account_plan_pool_mode_or_window_kind_blocks_segment(self):
        for field, value in (('account_key', 'other'), ('plan_type', 'plus'), ('limit_id', 'other'),
                             ('mode', 'fast'), ('window_kind', 'primary')):
            raw = sample()
            raw['snapshots'][1]['context'][field] = value
            with self.subTest(field=field):
                self.assert_blocked(raw, 'context_mismatch')

    def test_changed_source_blocks_segment(self):
        raw = sample()
        raw['snapshots'][1]['source'] = 'user_observation'
        self.assert_blocked(raw, 'mixed_observation_sources')

    def test_distinct_reset_cycles_are_separate_and_never_pooled(self):
        raw = sample()
        raw['collection_period']['end'] = stamp(605500)
        raw['snapshots'] += [snapshot('next-a', 604800, 10), snapshot('next-b', 605400, 20)]
        for item in raw['snapshots'][-2:]:
            item['resets_at'] = stamp(1209600)
        raw['segments'].append(segment('next', 'next-a', 'next-b'))
        raw['receipts'].append(receipt('next', 604860, 605000, 100000))
        result = analyze(raw)
        self.assertEqual(result['status'], 'conditional_estimate')
        self.assertEqual(len(result['groups']), 2)
        self.assertEqual([g['delta_percent'] for g in result['groups']], [20, 10])
        self.assertEqual([g['full_cycle_api_equivalent_usd'] for g in result['groups']], [4.5, 1])

    def test_bad_cycle_does_not_suppress_independent_valid_cycle(self):
        raw = sample()
        raw['collection_period']['end'] = stamp(605500)
        raw['snapshots'] += [snapshot('next-a', 604800, 10), snapshot('next-b', 605400, 20)]
        for item in raw['snapshots'][-2:]:
            item['resets_at'] = stamp(1209600)
        raw['segments'].append(segment('next', 'next-a', 'next-b'))
        raw['segments'][-1]['assertions']['settlement_assumed'] = False
        raw['receipts'].append(receipt('next', 604860, 605000))
        result = analyze(raw)
        self.assertEqual(result['status'], 'conditional_estimate')
        self.assertEqual([g['status'] for g in result['groups']], ['conditional_estimate', 'cannot_estimate'])
        self.assertEqual(result['groups'][0]['full_cycle_api_equivalent_usd'], 4.5)
        self.assertIsNone(result['groups'][1]['full_cycle_api_equivalent_usd'])

    def test_overlapping_segments_and_double_assignment_are_rejected(self):
        for extra in (segment('duplicate-interval', 'a', 'b'), segment('spanning', 'a', 'd')):
            raw = sample()
            raw['segments'].append(extra)
            with self.subTest(segment=extra):
                result = self.assert_blocked(raw, 'overlapping_segments')
                self.assertIn('receipt_crosses_or_touches_boundary', result['rejection_reasons'])

    def test_same_time_renamed_observations_do_not_make_independent_intervals(self):
        raw = sample()
        aliases = copy.deepcopy(raw['snapshots'][:2])
        for item in aliases:
            item['id'] += '-alias'
        raw['snapshots'] += aliases
        raw['segments'].append(segment('alias', 'a-alias', 'b-alias'))
        self.assert_blocked(raw, 'overlapping_segments')

    def test_request_crossing_or_touching_capture_boundary_rejects(self):
        for start, end in ((-10, 300), (0, 300), (1, 300), (60, 600), (60, 601),
                           (60, 660), (600, 660), (601, 660)):
            raw = sample()
            row = raw['receipts'][0]
            row.update(started_at=stamp(start), completed_at=stamp(end), owner_created_at=stamp(-100))
            with self.subTest(start=start, end=end):
                self.assert_blocked(raw, 'receipt_crosses_or_touches_boundary')

    def test_missing_pre_start_observation_is_not_a_quota_baseline(self):
        raw = sample()
        raw['snapshots'][0].update(capture_started_at=stamp(120), observed_at=stamp(121))
        self.assert_blocked(raw, 'receipt_crosses_or_touches_boundary')

    def test_bad_snapshot_order_or_capture_overlap_is_rejected(self):
        for start, end in ((0, 2), (1, 2), (-10, 0)):
            raw = sample()
            raw['snapshots'][1].update(capture_started_at=stamp(start), observed_at=stamp(end))
            with self.subTest(start=start, end=end):
                self.assert_blocked(raw, 'unordered_or_overlapping_snapshots')

    def test_outside_collection_period_or_reset_cycle_is_rejected(self):
        raw = sample()
        raw['collection_period']['start'] = stamp(1)
        self.assert_blocked(raw, 'outside_collection_period')
        raw = sample()
        raw['snapshots'][0]['resets_at'] = stamp(0)
        self.assert_blocked(raw, 'snapshot_outside_cycle')

    def test_exact_duplicates_do_not_change_cost_or_delta(self):
        raw = sample()
        raw['snapshots'].append(copy.deepcopy(raw['snapshots'][0]))
        raw['receipts'].append(copy.deepcopy(raw['receipts'][0]))
        result = analyze(raw)
        self.assertEqual(result['counts']['duplicate_receipts_removed'], 1)
        self.assertEqual(result['counts']['duplicate_snapshots_removed'], 1)
        self.assertEqual(result['groups'], analyze()['groups'])

    def test_fork_inherited_receipts_are_excluded(self):
        raw = sample()
        inherited = receipt('fork', tokens=100000000)
        inherited['owner_thread_id'] = 'parent-thread'
        raw['receipts'].append(inherited)
        result = analyze(raw)
        self.assertEqual(result['counts']['inherited_receipts_excluded'], 1)
        self.assertEqual(result['groups'], analyze()['groups'])

    def test_request_crossing_owner_creation_is_error(self):
        raw = sample()
        raw['receipts'][0]['owner_created_at'] = stamp(120)
        with self.assertRaisesRegex(ValueError, 'owner creation'):
            analyze(raw)

    def test_conflicting_receipt_or_snapshot_identity_is_error(self):
        for collection, field, value in (('receipts', 'thread_id', 'other-local'),
                                         ('receipts', 'completed_at', stamp(301)),
                                         ('snapshots', 'used_percent', 11)):
            raw = sample()
            duplicate = copy.deepcopy(raw[collection][0])
            duplicate[field] = value
            if field == 'thread_id':
                duplicate['owner_thread_id'] = value
            raw[collection].append(duplicate)
            with self.subTest(collection=collection, field=field), self.assertRaisesRegex(ValueError, 'Conflicting'):
                analyze(raw)

    def test_conflicting_same_time_observations_are_error(self):
        raw = sample()
        duplicate = copy.deepcopy(raw['snapshots'][0])
        duplicate.update(id='alias', used_percent=12)
        raw['snapshots'].append(duplicate)
        with self.assertRaisesRegex(ValueError, 'Conflicting snapshots'):
            analyze(raw)

    def test_unknown_models_and_undeclared_workloads_fail_closed(self):
        for declared in (True, False):
            raw = sample()
            raw['receipts'][0]['model'] = 'unknown-synthetic-model'
            if declared:
                raw['workload']['models'].append('unknown-synthetic-model')
            with self.subTest(declared=declared):
                result = self.assert_blocked(raw, 'incomplete_price_unknown_model')
                if not declared:
                    self.assertIn('undeclared_workload_model', result['rejection_reasons'])

    def test_unknown_used_cache_price_blocks_but_unused_cache_price_does_not(self):
        for field, reason in (('cached_input_tokens', 'unknown_cache_read_price'),
                              ('cache_write_input_tokens', 'unknown_cache_write_price')):
            prices = {MODEL: M['Price'](Decimal('1'), None, Decimal('2'), None)}
            self.assertEqual(analyze(prices=prices)['status'], 'conditional_estimate')
            raw = sample()
            raw['receipts'][0]['usage'][field] = 1
            result = analyze(raw, prices)
            with self.subTest(field=field):
                self.assertEqual(result['status'], 'cannot_estimate')
                self.assertIn('incomplete_price_' + reason, result['rejection_reasons'])
                self.assertIsNone(result['groups'][0]['full_cycle_api_equivalent_usd'])

    def test_cost_uses_cache_components_and_exact_per_request_long_tier(self):
        raw = sample()
        raw['snapshots'] = raw['snapshots'][:2]
        raw['segments'] = raw['segments'][:1]
        row = receipt('components', tokens=100)
        row['usage'].update(cached_input_tokens=40, cache_write_input_tokens=10,
                            output_tokens=20, reasoning_output_tokens=5, total_tokens=120)
        raw['receipts'] = [row]
        prices = {MODEL: M['Price'](Decimal('1'), Decimal('.2'), Decimal('2'), Decimal('1.5'),
                                   99, 'synthetic', Decimal('3'), Decimal('.4'), Decimal('2'), Decimal('4'))}
        result = analyze(raw, prices)
        self.assertAlmostEqual(result['groups'][0]['local_api_cost_usd'], (50 * 3 + 40 * .4 + 10 * 2 + 20 * 4) / 1000000)
        self.assertEqual(result['groups'][0]['workload_by_model'][MODEL]['usage'], row['usage'])

    def test_known_but_undeclared_model_and_mixed_model_cost_breakdown(self):
        raw = sample()
        raw['receipts'][0]['model'] = 'other-synthetic-model'
        prices = dict(PRICES, **{'other-synthetic-model': M['Price'](Decimal('2'), None, Decimal('3'))})
        result = analyze(raw, prices)
        self.assertEqual(result['status'], 'cannot_estimate')
        self.assertIn('undeclared_workload_model', result['rejection_reasons'])
        self.assertNotIn('incomplete_price_unknown_model', result['rejection_reasons'])
        raw['workload']['models'].append('other-synthetic-model')
        result = analyze(raw, prices)
        self.assertEqual(result['status'], 'conditional_estimate')
        models = result['groups'][0]['workload_by_model']
        self.assertAlmostEqual(models[MODEL]['api_cost_usd'], .8)
        self.assertAlmostEqual(models['other-synthetic-model']['api_cost_usd'], .2)
        self.assertAlmostEqual(sum(row['cost_fraction'] for row in models.values()), 1)

    def test_receipt_fully_before_owner_creation_is_error(self):
        raw = sample()
        raw['receipts'][0]['owner_created_at'] = stamp(400)
        with self.assertRaisesRegex(ValueError, 'owner creation'):
            analyze(raw)

    def test_cross_identity_attempt_blocks_each_touched_group(self):
        raw = sample()
        raw['collection_period']['end'] = stamp(605500)
        raw['snapshots'] += [snapshot('next-a', 604800, 10), snapshot('next-b', 605400, 20)]
        for item in raw['snapshots'][-2:]:
            item['resets_at'] = stamp(1209600)
        raw['segments'] += [segment('next', 'next-a', 'next-b'), segment('cross-reset', 'd', 'next-a')]
        raw['receipts'].append(receipt('next', 604860, 605000))
        result = self.assert_blocked(raw, 'window_or_reset_changed')
        self.assertEqual(len(result['groups']), 2)
        for group in result['groups']:
            self.assertIn('group_contains_rejected_attempts', group['reasons'])

    def test_empty_and_unpaired_inputs_cannot_estimate(self):
        for snapshots in ([], sample()['snapshots']):
            raw = sample()
            raw.update(snapshots=snapshots, segments=[], receipts=[])
            with self.subTest(snapshots=len(snapshots)):
                result = self.assert_blocked(raw)
                self.assertEqual(result['groups'], [])
                self.assertIn('no paired segments', M['render_segments'](result))

    def test_percentage_increase_without_local_receipts_cannot_estimate(self):
        raw = sample()
        raw['receipts'] = []
        self.assert_blocked(raw, 'quota_increase_without_local_cost')

    def test_insufficient_public_fixture_is_honestly_reported(self):
        raw = json.loads((FIXTURES / 'insufficient.json').read_text())
        self.assert_blocked(raw)


class SegmentValidation(unittest.TestCase):
    def test_strict_top_level_structure_and_selection_policy(self):
        variants = []
        for key in sample():
            raw = sample()
            del raw[key]
            variants.append(raw)
        for field, value in (('schema_version', True), ('schema_version', 2),
                             ('selection_policy', 'best_segments'), ('snapshots', {}),
                             ('segments', None), ('receipts', 'discover-default-sessions')):
            raw = sample()
            raw[field] = value
            variants.append(raw)
        raw = sample()
        raw['extra'] = True
        variants.append(raw)
        for index, raw in enumerate(variants):
            with self.subTest(index=index), self.assertRaises(ValueError):
                analyze(raw)

    def test_missing_required_cache_usage_fields_never_default_to_zero(self):
        for field in M['FIELDS']:
            raw = sample()
            del raw['receipts'][0]['usage'][field]
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'receipt usage'):
                analyze(raw)

    def test_usage_nan_bool_negative_fractional_or_inconsistent_is_rejected(self):
        for field, value in (('input_tokens', True), ('cached_input_tokens', float('nan')),
                             ('cache_write_input_tokens', float('inf')), ('output_tokens', -1),
                             ('reasoning_output_tokens', .5), ('total_tokens', 999),
                             ('cached_input_tokens', 100001), ('reasoning_output_tokens', 1),
                             ('total_tokens', '100000')):
            raw = sample()
            raw['receipts'][0]['usage'][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                analyze(raw)

    def test_snapshot_numbers_reject_nan_bool_out_of_range_and_false_precision(self):
        for field, values in (('used_percent', (True, '10', float('nan'), float('inf'), -1, 101)),
                              ('percent_resolution', (True, 0, .1, 101, float('nan'))),
                              ('window_minutes', (True, 0, 1.5, 5256001, '10080'))):
            for value in values:
                raw = sample()
                raw['snapshots'][0][field] = value
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    analyze(raw)

    def test_naive_missing_or_malformed_timestamps_are_rejected_everywhere(self):
        locations = [('collection_period', None, 'start'), ('collection_period', None, 'end')]
        locations += [('snapshots', 0, field) for field in ('capture_started_at', 'observed_at', 'resets_at')]
        locations += [('receipts', 0, field) for field in ('owner_created_at', 'started_at', 'completed_at')]
        for collection, index, field in locations:
            for value in ('2026-09-22T00:00:00', 'not-a-time', None, 123):
                raw = sample()
                target = raw[collection] if index is None else raw[collection][index]
                target[field] = value
                with self.subTest(collection=collection, field=field, value=value), self.assertRaises(ValueError):
                    analyze(raw)

    def test_capture_reversed_and_receipt_reversed_or_empty_period_are_errors(self):
        raw = sample()
        raw['snapshots'][0]['capture_started_at'] = stamp(2)
        with self.assertRaises(ValueError):
            analyze(raw)
        raw = sample()
        raw['receipts'][0]['started_at'] = stamp(400)
        with self.assertRaises(ValueError):
            analyze(raw)
        raw = sample()
        raw['collection_period']['end'] = raw['collection_period']['start']
        with self.assertRaises(ValueError):
            analyze(raw)

    def test_assertions_require_exact_keys_and_actual_booleans(self):
        for value in (1, 0, 'true', None):
            raw = sample()
            raw['segments'][0]['assertions']['settlement_assumed'] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                analyze(raw)
        raw = sample()
        del raw['segments'][0]['assertions']['complete_local_receipts']
        with self.assertRaises(ValueError):
            analyze(raw)

    def test_invalid_identifiers_labels_sources_and_basis_are_errors(self):
        for section, key, value in (('workload', 'label', 'bad\x1b[31m'),
                                     ('context', 'account_key', ''), ('context', 'mode', 'x' * 201),
                                     ('context', 'window_kind', 'invented'),
                                     ('workload', 'models', []), ('workload', 'models', [MODEL, MODEL])):
            raw = sample()
            raw[section][key] = value
            with self.subTest(section=section, key=key), self.assertRaises(ValueError):
                analyze(raw)
        for key, value in (('source', 'authoritative-measurement'),
                            ('observation_basis', 'measured_now'), ('id', '')):
            raw = sample()
            raw['snapshots'][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                analyze(raw)

    def test_missing_endpoint_and_duplicate_segment_ids_are_errors(self):
        raw = sample()
        raw['segments'][0]['before'] = 'missing'
        with self.assertRaisesRegex(ValueError, 'Unknown before'):
            analyze(raw)
        raw = sample()
        raw['segments'].append(copy.deepcopy(raw['segments'][0]))
        with self.assertRaisesRegex(ValueError, 'Duplicate segment'):
            analyze(raw)

    def test_json_reader_rejects_duplicate_keys_nonfinite_invalid_encoding_and_nesting(self):
        for payload in (b'{"schema_version":1,"schema_version":1}', b'{"value":NaN}',
                         b'{"value":Infinity}', b'{"value":-Infinity}', b'\xff', b'[' * 2000,
                         b'{not-json}'):
            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'input.json'
                path.write_bytes(payload)
                with self.subTest(payload=payload[:50]), self.assertRaises(ValueError):
                    M['read_segment_json'](path)

    def test_json_reader_has_explicit_size_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'input.json'
            path.write_bytes(b' ' * (8 * M['MAX_BYTES'] + 1))
            with self.assertRaisesRegex(ValueError, '8 MB'):
                M['read_segment_json'](path)


class SegmentCli(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = dict(os.environ, HOME=str(self.root), CODEX_HOME=str(self.root / 'synthetic-codex-home'))
        # Site customization runs before the actual executable. Any accidental
        # network or default-session access fails rather than reading private logs.
        (self.root / 'sitecustomize.py').write_text('''import os, sys\ndef guard(event, args):\n    if event in ("socket.connect", "socket.getaddrinfo", "socket.gethostbyname"):\n        raise RuntimeError("offline test forbids network")\n    if event in ("open", "os.scandir", "os.listdir") and args:\n        path = str(args[0])\n        if "sessions" in path or "archived_sessions" in path:\n            raise RuntimeError("segment test forbids session access")\nsys.addaudithook(guard)\n''')
        self.env['PYTHONPATH'] = str(self.root)

    def cli(self, *args):
        return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=self.root, env=self.env,
                              text=True, capture_output=True, timeout=15)

    def command(self, *args):
        return self.cli('segments', 'analyze', '--input', str(FIXTURES / 'complete.json'),
                        '--price-catalog', str(PRICE_FIXTURE), *args)

    def test_offline_explicit_analysis_is_json_and_never_scans_or_downloads(self):
        result = self.command('--json')
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data['status'], 'conditional_estimate')
        self.assertEqual(data['price_catalog']['catalog_source'], str(PRICE_FIXTURE))
        self.assertAlmostEqual(data['groups'][0]['full_cycle_api_equivalent_usd'], 3.375)
        self.assertFalse((self.root / 'synthetic-codex-home').exists())

    def test_analysis_requires_input_and_explicit_price_file(self):
        for args in ((), ('--input', str(FIXTURES / 'complete.json')),
                      ('--price-catalog', str(PRICE_FIXTURE))):
            with self.subTest(args=args):
                result = self.cli('segments', 'analyze', *args)
                self.assertEqual(result.returncode, 2)
                self.assertIn('required', result.stderr)
                self.assertNotIn('Traceback', result.stderr)

    def test_help_describes_opt_in_offline_and_snapshot_provenance(self):
        for args, words in ((('--help',), ('segments', 'experimental')),
                            (('segments', '--help'), ('analyze', 'snapshot', 'offline')),
                            (('segments', 'analyze', '--help'), ('--input', '--price-catalog')),
                            (('segments', 'snapshot', '--help'), ('--account-key', '--mode'))):
            result = self.cli(*args)
            with self.subTest(args=args):
                self.assertEqual(result.returncode, 0, result.stderr)
                for word in words:
                    self.assertIn(word.lower(), result.stdout.lower())

    def test_default_report_arguments_do_not_implicitly_enable_segments(self):
        for args in (('segments',), ('segments', 'analyze', '--root', str(self.root)),
                      ('segments', 'snapshot', '--id', 'x')):
            result = self.cli(*args)
            with self.subTest(args=args):
                self.assertEqual(result.returncode, 2)
                self.assertNotIn('Traceback', result.stderr)

    def test_text_output_and_stdout_alias_label_limitations(self):
        result = self.command()
        self.assertEqual(result.returncode, 0, result.stderr)
        for phrase in ('EXPERIMENTAL', 'NOT a confidence interval', 'user assertions', 'zero-delta'):
            self.assertIn(phrase, result.stdout)
        result = self.command('--output', '-')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'], 'conditional_estimate')

    def test_new_output_file_is_json_and_existing_output_is_never_overwritten(self):
        output = self.root / 'new' / 'result.json'
        result = self.command('--json', '--output', str(output))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(output.read_text()), json.loads(result.stdout))
        previous = output.read_bytes()
        result = self.command('--output', str(output))
        self.assertEqual(result.returncode, 1)
        self.assertIn('already exists', result.stderr)
        self.assertEqual(output.read_bytes(), previous)
        self.assertEqual(list(output.parent.glob('.oai-usage-*')), [])

    def test_input_catalog_program_jsonl_targets_are_protected(self):
        ordinary = self.root / 'ordinary.json'
        ordinary.write_text('keep me')
        for output in (FIXTURES / 'complete.json', PRICE_FIXTURE, SCRIPT, ordinary,
                        self.root / 'new-output.jsonl'):
            original = output.read_bytes() if output.exists() else None
            result = self.command('--output', str(output))
            with self.subTest(output=output):
                self.assertEqual(result.returncode, 1)
                self.assertNotIn('Traceback', result.stderr)
                if original is not None:
                    self.assertEqual(output.read_bytes(), original)
                else:
                    self.assertFalse(output.exists())

    def test_symlink_output_target_is_protected(self):
        ordinary = self.root / 'ordinary.json'
        ordinary.write_text('keep me')
        link = self.root / 'output-link.json'
        try:
            link.symlink_to(ordinary)
        except OSError as exc:
            self.skipTest(f'Symlink creation is unavailable: {exc}')
        result = self.command('--output', str(link))
        self.assertEqual(result.returncode, 1)
        self.assertEqual(ordinary.read_text(), 'keep me')
        self.assertTrue(link.is_symlink())

    def test_malformed_manifest_or_price_file_and_missing_files_fail_without_traceback(self):
        bad = self.root / 'bad.json'
        bad.write_text('{"value":NaN}')
        for input_path, price_path in ((bad, PRICE_FIXTURE), (FIXTURES / 'complete.json', bad),
                                      (self.root / 'missing.json', PRICE_FIXTURE)):
            result = self.cli('segments', 'analyze', '--input', str(input_path), '--price-catalog', str(price_path))
            with self.subTest(input=input_path, price=price_path):
                self.assertEqual(result.returncode, 1)
                self.assertIn('oai-usage segments:', result.stderr)
                self.assertNotIn('Traceback', result.stderr)

    def test_cannot_estimate_is_valid_successful_analysis_with_null_values(self):
        result = self.cli('segments', 'analyze', '--input', str(FIXTURES / 'insufficient.json'),
                          '--price-catalog', str(PRICE_FIXTURE), '--json')
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data['status'], 'cannot_estimate')
        self.assertIsNone(data['groups'][0]['full_cycle_api_equivalent_usd'])


class SegmentSnapshot(unittest.IsolatedAsyncioTestCase):
    def live(self):
        return {'fetched_at': stamp(2), 'result': {'rateLimitsByLimitId': {'codex': {
            'limitId': 'codex', 'planType': 'pro', 'secondary': {
            'windowDurationMins': 10080, 'resetsAt': int((BASE + timedelta(days=7)).timestamp()),
            'usedPercent': 10}}}}}

    async def invoke(self, live, error=None, extra=(), expected_calls=1):
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return BASE
        fetch = AsyncMock(return_value=(live, error))
        no_prices = AsyncMock(side_effect=AssertionError('must not fetch prices'))
        no_scheduler = Mock(side_effect=AssertionError('must not construct query scheduler'))
        no_scanner = Mock(side_effect=AssertionError('must not scan sessions'))
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.dict(G, {'datetime': Clock, 'fetch_live': fetch, 'load_price_catalog_async': no_prices,
                            'QueryScheduler': no_scheduler, 'Scanner': no_scanner}), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = await M['async_main'](['segments', 'snapshot', '--id', 'observed-1',
                '--account-key', 'local-opaque-label', '--mode', 'standard',
                '--codex-binary', 'synthetic-codex', '--timeout', '2', *extra])
        if expected_calls:
            fetch.assert_awaited_once_with('synthetic-codex', 2, _raw_result=True)
        else:
            fetch.assert_not_awaited()
        no_prices.assert_not_awaited()
        no_scheduler.assert_not_called()
        no_scanner.assert_not_called()
        return status, stdout.getvalue(), stderr.getvalue()

    async def test_one_shot_snapshot_preserves_rpc_receipt_provenance(self):
        status, stdout, stderr = await self.invoke(self.live())
        self.assertEqual(status, 0, stderr)
        data = json.loads(stdout)
        self.assertEqual(data['capture_started_at'], BASE.isoformat())
        self.assertEqual(data['observed_at'], stamp(2))
        self.assertEqual(data['source'], 'app_server_rpc')
        self.assertEqual(data['observation_basis'], 'retrieved_not_measured')
        self.assertEqual(data['percent_resolution'], 1)
        self.assertEqual(data['context']['account_key'], 'local-opaque-label')
        self.assertEqual(data['context']['mode'], 'standard')
        self.assertNotIn('verified', data)
        self.assertNotIn('price_catalog', data)

    async def test_snapshot_can_write_only_a_new_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'snapshot.json'
            status, stdout, stderr = await self.invoke(self.live(), extra=('--output', str(path)))
            self.assertEqual(status, 0, stderr)
            self.assertEqual(json.loads(path.read_text()), json.loads(stdout))
            old = path.read_bytes()
            status, stdout, stderr = await self.invoke(self.live(), extra=('--output', str(path)), expected_calls=0)
            self.assertEqual(status, 1)
            self.assertIn('already exists', stderr)
            self.assertEqual(path.read_bytes(), old)

    async def test_rpc_failure_missing_pool_or_missing_plan_fail_closed(self):
        missing_plan = self.live()
        del missing_plan['result']['rateLimitsByLimitId']['codex']['planType']
        for live, error in ((None, 'synthetic RPC failure'), ({'result': {}}, None), (missing_plan, None)):
            with self.subTest(live=live):
                status, stdout, stderr = await self.invoke(live, error)
                self.assertEqual(status, 1)
                self.assertEqual(stdout, '')
                self.assertIn('oai-usage segments:', stderr)

    async def test_raw_fractional_bool_or_nonpositive_identity_cannot_be_normalized_away(self):
        for field, values in (('windowDurationMins', (300.9, True, 0, -1, '300', 5256001)),
                              ('resetsAt', (1.5, True, 0, -1, '1790630400')),
                              ('usedPercent', (True, float('nan'), float('inf'), -1, 101, '10'))):
            for value in values:
                live = self.live()
                live['result']['rateLimitsByLimitId']['codex']['secondary'][field] = value
                with self.subTest(field=field, value=value):
                    status, stdout, stderr = await self.invoke(live)
                    self.assertEqual(status, 1)
                    self.assertEqual(stdout, '')
                    self.assertIn('oai-usage segments:', stderr)

    async def test_returned_pool_identity_mismatch_is_rejected(self):
        for value in ('another-pool', None, True):
            live = self.live()
            live['result']['rateLimitsByLimitId']['codex']['limitId'] = value
            with self.subTest(value=value):
                status, stdout, stderr = await self.invoke(live)
                self.assertEqual(status, 1)
                self.assertEqual(stdout, '')
                self.assertIn('pool identity', stderr)

    async def test_selected_individual_limit_retains_exact_identity(self):
        live = self.live()
        bucket = live['result']['rateLimitsByLimitId']['codex']
        bucket['individualLimit'] = bucket.pop('secondary')
        status, stdout, stderr = await self.invoke(live, extra=('--window', 'individual_limit'))
        self.assertEqual(status, 0, stderr)
        self.assertEqual(json.loads(stdout)['context']['window_kind'], 'individual_limit')

    async def test_rpc_raw_mode_preserves_payload_and_normal_mode_preserves_api(self):
        # A local stdio transport exercises the real handshake without Codex,
        # credentials, quota activity, default-session reads, or price access.
        raw_payload = self.live()['result']
        raw_payload['rateLimitsByLimitId']['codex']['secondary']['windowDurationMins'] = 300.9
        program = r"""import json, sys
payload = json.loads(sys.argv[1])
for line in sys.stdin:
    message = json.loads(line)
    if message['method'] == 'initialize':
        print(json.dumps({'id': 0, 'result': {}}), flush=True)
    elif message['method'] == 'account/rateLimits/read':
        print(json.dumps({'id': 1, 'result': payload}), flush=True)
"""
        command = [sys.executable, '-c', program, json.dumps(raw_payload)]
        raw, error = await M['fetch_live'](None, 2, _command=command, _raw_result=True)
        self.assertIsNone(error)
        self.assertEqual(raw['result'], raw_payload)
        self.assertEqual(raw['result']['rateLimitsByLimitId']['codex']['secondary']['windowDurationMins'], 300.9)
        self.assertIsNotNone(M['segment_time'](raw['fetched_at'], 'fetched_at'))
        normal, error = await M['fetch_live'](None, 2, _command=command)
        self.assertIsNone(error)
        self.assertEqual(normal['source'], 'app_server')
        self.assertEqual(normal['limits']['codex']['secondary']['window_minutes'], 300)
        self.assertNotIn('result', normal)

    async def test_capture_ending_before_rpc_started_is_rejected(self):
        live = self.live()
        live['fetched_at'] = stamp(-1)
        status, stdout, stderr = await self.invoke(live)
        self.assertEqual(status, 1)
        self.assertEqual(stdout, '')
        self.assertIn('capture_started_at', stderr)


if __name__ == '__main__':
    unittest.main()
