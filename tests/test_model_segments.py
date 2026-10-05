"""Offline schema-v2 regressions using only fictional models, rates and receipts.

No real account/session data or published model prices are used. Fixture catalog
source metadata is required syntax, not provenance for the invented $1/M rate.
"""
import copy
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

SCRIPT = Path(__file__).resolve().parents[1] / 'oai-usage'
M = runpy.run_path(str(SCRIPT), run_name='model_segment_tests')
FIXTURES = Path(__file__).parent / 'fixtures' / 'model_segments'
PRICE_FIXTURE = FIXTURES / 'fictional_prices.json'
PRICE_RAW = json.loads(PRICE_FIXTURE.read_text())
PRICES = M['parse_price_catalog'](PRICE_RAW, 'fictional fixture, not published pricing').prices
BASE = datetime(2026, 9, 22, tzinfo=timezone.utc)
A, B = 'astra-standard', 'sol-standard'


def stamp(seconds):
    return (BASE + timedelta(seconds=seconds)).isoformat().replace('+00:00', 'Z')


def sample(name='two_models'):
    return json.loads((FIXTURES / (name + '.json')).read_text())


def analyze(raw=None, prices=None):
    return M['analyze_segments'](sample() if raw is None else raw,
                                 PRICES if prices is None else prices)


def scenario(result, profile=A, source='app_server_rpc', reset=None, window=10080):
    rows = [row for row in result['model_scenarios']
            if row['workload_id'] == profile and row['source'] == source and row['window_minutes'] == window
            and (reset is None or row['resets_at'] == reset)]
    if len(rows) != 1:
        raise AssertionError('Expected one matching scenario, got %r' % rows)
    return rows[0]


def current(raw):
    return next(row for row in raw['snapshots'] if row['id'] == 'current')


def one_profile(delta=20):
    raw = sample()
    raw['workloads'] = raw['workloads'][:1]
    raw['segments'] = raw['segments'][:1]
    raw['receipts'] = raw['receipts'][:1]
    raw['snapshots'] = [raw['snapshots'][0], raw['snapshots'][1], current(raw)]
    raw['snapshots'][1]['used_percent'] = 10 + delta
    return raw


class ModelScenarios(unittest.TestCase):
    def assert_unknown(self, row, reason=None):
        self.assertEqual(row['status'], 'cannot_estimate')
        self.assertIsNone(row['estimated_total_api_cost_usd'])
        self.assertIsNone(row['statistical_confidence_interval'])
        if reason:
            self.assertIn(reason, row['reasons'])

    def test_two_models_produce_alternative_75_100_and_remaining_30_40(self):
        raw = sample()
        before = copy.deepcopy(raw)
        result = analyze(raw)
        self.assertEqual(raw, before, 'Analysis must not mutate evidence')
        self.assertEqual(result['schema_version'], 2)
        self.assertEqual(result['kind'], 'per_model_conditional_quota_scenarios')
        self.assertEqual(result['status'], 'conditional_estimate')
        self.assertTrue(result['alternatives_not_additive'])
        self.assertTrue(result['experimental'])
        self.assertEqual(result['trust_level'], 'conditional_on_user_assertions')
        self.assertEqual(result['current_snapshot_id'], 'current')
        for pid, model, cost, total, remaining in (
                (A, 'synthetic-astra', 15, 75, 30),
                (B, 'synthetic-sol', 20, 100, 40)):
            with self.subTest(profile=pid):
                row = scenario(result, pid)
                self.assertEqual(row['model'], model)
                self.assertEqual(row['local_api_cost_usd'], cost)
                self.assertEqual(row['delta_percent'], 20)
                self.assertEqual(row['estimated_total_api_cost_usd'], total)
                self.assertEqual(row['remaining']['estimated_remaining_api_cost_usd'], remaining)
                self.assertEqual(row['remaining']['recorded_account_usage']['remaining_percent'], 40)
                self.assertFalse(row['remaining']['recorded_account_usage']['is_live_lookup'])
                self.assertEqual(row['remaining']['recorded_account_usage']['snapshot_id'], 'current')
                self.assertIsNone(row['statistical_confidence_interval'])
                self.assertEqual(row['applicability']['price_fingerprint'], result['price_fingerprint'])
        self.assertNotIn('estimated_total_api_cost_usd', result)
        self.assertNotIn('estimated_remaining_api_cost_usd', result)

    def test_zero_delta_retains_cost_and_cancels_only_shared_endpoints(self):
        row = scenario(analyze(sample('continuous_zero_delta')))
        self.assertEqual(row['eligible_segment_count'], 3)
        self.assertEqual(row['zero_delta_segment_count'], 1)
        self.assertEqual(row['receipt_count'], 3)
        self.assertEqual(row['local_api_cost_usd'], 15)
        self.assertEqual(row['delta_percent'], 20)
        self.assertEqual(row['estimated_total_api_cost_usd'], 75)
        self.assertEqual(row['quantization_delta_bounds'], [18, 22])
        self.assertEqual(row['uncancelled_endpoint_count'], 2)
        self.assertEqual(row['boundary_coefficient_weight'], 2)
        self.assertNotAlmostEqual(row['estimated_total_api_cost_usd'], 100 * (5 + 9) / 20)

    def test_ratio_of_sums_is_not_mean_of_interval_ratios(self):
        raw = sample('continuous_zero_delta')
        raw['snapshots'][1]['used_percent'] = 15
        raw['snapshots'][2]['used_percent'] = 15
        row = scenario(analyze(raw))
        self.assertEqual(row['estimated_total_api_cost_usd'], 75)
        self.assertNotEqual(row['estimated_total_api_cost_usd'], (5 / .05 + 9 / .15) / 2)

    def test_fragments_have_four_uncancelled_endpoints_and_wider_bounds(self):
        continuous = scenario(analyze(sample('continuous_zero_delta')))
        fragments = scenario(analyze(sample('independent_fragments')))
        self.assertEqual(fragments['estimated_total_api_cost_usd'], continuous['estimated_total_api_cost_usd'])
        self.assertEqual(fragments['quantization_delta_bounds'], [16, 24])
        self.assertEqual(fragments['uncancelled_endpoint_count'], 4)
        self.assertEqual(fragments['boundary_coefficient_weight'], 4)
        self.assertGreater(fragments['relative_total_range_width'], continuous['relative_total_range_width'])

    def test_a_b_a_never_cancels_uncertainty_across_profiles(self):
        result = analyze(sample('alternating_models'))
        a, b = scenario(result), scenario(result, B)
        self.assertEqual(a['delta_percent'], 20)
        self.assertEqual(a['quantization_delta_bounds'], [16, 24])
        self.assertEqual(a['uncancelled_endpoint_count'], 4)
        self.assertEqual(b['quantization_delta_bounds'], [18, 22])
        self.assertEqual(b['uncancelled_endpoint_count'], 2)
        self.assertEqual(a['estimated_total_api_cost_usd'], 75)
        self.assertEqual(b['estimated_total_api_cost_usd'], 100)

    def test_gap_receipts_do_not_bridge_or_change_calibration(self):
        raw = sample('independent_fragments')
        gap = copy.deepcopy(raw['receipts'][0])
        gap.update(response_id='gap', started_at=stamp(700), completed_at=stamp(900))
        gap['usage']['input_tokens'] = gap['usage']['total_tokens'] = 99999999
        raw['receipts'].append(gap)
        result = analyze(raw)
        self.assertEqual(result['counts']['outside_segment_receipts'], 1)
        self.assertEqual(scenario(result)['local_api_cost_usd'], 15)
        self.assertEqual(scenario(result)['delta_percent'], 20)

    def test_declared_mixed_interval_is_unsupported_without_cost_apportionment(self):
        result = analyze(sample('mixed_workload'))
        mixed = next(row for row in result['segments'] if row['id'] == 'mixed')
        self.assertEqual(mixed['status'], 'unsupported_mixed_workload')
        self.assertIn('mixed_workload_attribution_unsupported', mixed['reasons'])
        self.assertEqual(mixed['observed_workload_ids'], [A, B])
        self.assertEqual(mixed['known_api_cost_usd'], 10)
        self.assertEqual(result['counts']['unsupported_mixed_segments'], 1)
        self.assertFalse(result['policy']['mixed_workload_fitting_supported'])
        self.assertEqual(scenario(result)['local_api_cost_usd'], 15)
        self.assertEqual(scenario(result, B)['local_api_cost_usd'], 20)
        self.assertEqual(scenario(result)['estimated_total_api_cost_usd'], 75)
        self.assertEqual(scenario(result, B)['estimated_total_api_cost_usd'], 100)

    def test_unexpected_receipt_profile_blocks_both_touched_cells(self):
        raw = sample()
        wrong = copy.deepcopy(raw['receipts'][1])
        wrong.update(response_id='unexpected-sol', started_at=stamp(100), completed_at=stamp(400))
        raw['receipts'].append(wrong)
        result = analyze(raw)
        for pid in (A, B):
            row = scenario(result, pid)
            self.assert_unknown(row, 'profile_group_contains_rejected_attempts')
            self.assertIn('unexpected_mixed_workload', row['rejected_attempt_reasons'])
            self.assertIsNone(row['quantization_only_total_bounds_usd'])
            self.assertIsNone(row['remaining']['quantization_only_bounds_usd'])

    def test_model_profile_mismatch_blocks_calibration(self):
        raw = sample()
        raw['receipts'][0]['model'] = 'synthetic-sol'
        result = analyze(raw)
        row = scenario(result)
        self.assert_unknown(row, 'profile_group_contains_rejected_attempts')
        self.assertIn('receipt_model_profile_mismatch', row['rejected_attempt_reasons'])
        self.assertIsNone(row['quantization_only_total_bounds_usd'])

    def test_uncalibrated_profile_cannot_borrow_another_models_coefficient(self):
        raw = sample()
        raw['segments'] = raw['segments'][:1]
        raw['receipts'] = raw['receipts'][:1]
        result = analyze(raw)
        self.assertEqual(scenario(result)['estimated_total_api_cost_usd'], 75)
        row = scenario(result, B)
        self.assert_unknown(row, 'no_eligible_single_workload_segments')
        self.assertEqual(row['local_api_cost_usd'], 0)
        self.assertEqual(row['receipt_count'], 0)
        self.assertIsNone(row['remaining']['estimated_remaining_api_cost_usd'])

    def test_speed_effort_cache_variants_stay_separate(self):
        for field, value in [('speed', 'fast'), ('effort', 'low'), ('cache_workload', 'cache-heavy')]:
            with self.subTest(field=field):
                raw = sample()
                raw['workloads'][1] = copy.deepcopy(raw['workloads'][0])
                raw['workloads'][1].update(id=B, **{field: value})
                raw['receipts'][1]['model'] = 'synthetic-astra'
                result = analyze(raw)
                self.assertEqual(len(result['model_scenarios']), 2)
                self.assertEqual(scenario(result)['estimated_total_api_cost_usd'], 75)
                self.assertEqual(scenario(result, B)['estimated_total_api_cost_usd'], 100)
                self.assertEqual(scenario(result, B)['applicability'][field], value)

    def test_observed_cache_and_output_ratios_are_reported(self):
        raw = one_profile()
        raw['receipts'][0]['usage'].update(input_tokens=10000000, cached_input_tokens=2000000,
                                          cache_write_input_tokens=1000000, output_tokens=3000000,
                                          reasoning_output_tokens=1000000, total_tokens=13000000)
        row = scenario(analyze(raw))
        self.assertEqual(row['observed_cache_read_fraction'], .2)
        self.assertEqual(row['observed_cache_write_fraction'], .1)
        self.assertEqual(row['observed_output_to_input_ratio'], .3)
        self.assertEqual(row['observed_usage']['reasoning_output_tokens'], 1000000)

    def test_safety_limitations_state_conditional_provenance(self):
        result = analyze()
        text = ' '.join(result['limitations']).lower()
        for phrase in ('not an official quota', 'user assertions', 'cannot be verified',
                       'not a statistical confidence interval', 'not a current balance',
                       'must not be added', 'mixed-workload', 'no held-out accuracy validation'):
            self.assertIn(phrase, text)


class ModelQuantization(unittest.TestCase):
    def test_true_1_99pp_displayed_1pp_has_unknown_total_and_unbounded_upper(self):
        result = analyze(sample('quantization_trap'))
        row = scenario(result)
        self.assertEqual(row['delta_percent'], 1)
        self.assertEqual(row['quantization_delta_bounds'], [0, 2])
        self.assertEqual(row['status'], 'cannot_estimate')
        self.assertIsNone(row['estimated_total_api_cost_usd'])
        self.assertIsNone(row['quantization_only_total_bounds_usd'][1])
        self.assertAlmostEqual(row['quantization_only_total_bounds_usd'][0], 74.625)
        self.assertIn('insufficient_percentage_signal', row['reasons'])
        self.assertIn('quantization_too_large', row['reasons'])
        self.assertIsNone(row['remaining']['estimated_remaining_api_cost_usd'])
        json.dumps(result, allow_nan=False)

    def test_many_tiny_fragments_have_enough_total_delta_but_unbounded_total(self):
        result = analyze(sample('many_small_fragments'))
        row = scenario(result)
        self.assertEqual(row['eligible_segment_count'], 10)
        self.assertEqual(row['delta_percent'], 20)
        self.assertEqual(row['uncancelled_endpoint_count'], 20)
        self.assertEqual(row['boundary_coefficient_weight'], 20)
        self.assertEqual(row['quantization_delta_bounds'], [0, 40])
        self.assertNotIn('insufficient_percentage_signal', row['reasons'])
        self.assertIn('quantization_too_large', row['reasons'])
        self.assertIn('total_range_too_wide', row['reasons'])
        self.assertIsNone(row['estimated_total_api_cost_usd'])
        self.assertEqual(row['quantization_only_total_bounds_usd'], [37.5, None])
        self.assertIsNone(row['remaining']['estimated_remaining_api_cost_usd'])
        json.dumps(result, allow_nan=False)

    def test_all_rounding_assumptions_produce_conservative_closed_bounds(self):
        expected = {'unknown': [18, 22], 'floor': [19, 21], 'nearest': [19, 21], 'ceil': [19, 21]}
        for rounding, bounds in expected.items():
            with self.subTest(rounding=rounding):
                raw = one_profile()
                raw['quantization']['rounding'] = rounding
                result = analyze(raw)
                row = scenario(result)
                self.assertEqual(row['quantization_delta_bounds'], bounds)
                self.assertTrue(result['quantization']['endpoint_bounds_are_closed_outer_bounds'])
                self.assertEqual(row['quantization_only_total_bounds_usd'], [1500 / bounds[1], 1500 / bounds[0]])
                lo, hi = M['quantized_percent_bounds'](current(raw), rounding)
                total_lo, total_hi = row['quantization_only_total_bounds_usd']
                self.assertAlmostEqual(row['remaining']['quantization_only_bounds_usd'][0], total_lo * float((100-hi)/100))
                self.assertAlmostEqual(row['remaining']['quantization_only_bounds_usd'][1], total_hi * float((100-lo)/100))

    def test_zero_and_hundred_clip_endpoint_bounds_for_every_rounding(self):
        expected = {
            'unknown': [(0, 1), (99, 100)], 'floor': [(0, 1), (100, 100)],
            'nearest': [(0, .5), (99.5, 100)], 'ceil': [(0, 0), (99, 100)]}
        for rounding, pairs in expected.items():
            for used, pair in zip((0, 100), pairs):
                with self.subTest(rounding=rounding, used=used):
                    snap = dict(used_percent=used, percent_resolution=1)
                    self.assertEqual(M['quantized_percent_bounds'](snap, rounding), tuple(Decimal(str(x)) for x in pair))

    def test_explicit_rounding_rejects_off_grid_observations(self):
        for rounding in ('floor', 'nearest', 'ceil'):
            for index in (0, 1, 2):
                with self.subTest(rounding=rounding, index=index):
                    raw = one_profile()
                    raw['quantization']['rounding'] = rounding
                    raw['snapshots'][index]['used_percent'] += .25
                    with self.assertRaisesRegex(ValueError, 'quantization grid'):
                        analyze(raw)

    def test_unknown_rounding_accepts_fractional_grid_without_claiming_finer_resolution(self):
        raw = one_profile()
        raw['snapshots'][0]['used_percent'] = 3.7
        raw['snapshots'][1]['used_percent'] = 23.7
        row = scenario(analyze(raw))
        self.assertEqual(row['delta_percent'], 20)
        self.assertEqual(row['quantization_delta_bounds'], [18, 22])
        self.assertEqual(row['estimated_total_api_cost_usd'], 75)

    def test_range_width_gate_is_stricter_than_percentage_error_gate(self):
        row = scenario(analyze(one_profile(delta=8)))
        self.assertEqual(row['quantization_delta_bounds'], [6, 10])
        self.assertNotIn('quantization_too_large', row['reasons'])
        self.assertIn('total_range_too_wide', row['reasons'])
        self.assertGreater(row['relative_total_range_width'], .5)
        self.assertIsNone(row['estimated_total_api_cost_usd'])
        row = scenario(analyze(one_profile(delta=9)))
        self.assertEqual(row['status'], 'conditional_estimate')
        self.assertLessEqual(row['relative_total_range_width'], .5)

    def test_weak_or_zero_signal_never_returns_nonfinite_json(self):
        for delta in (0, 1, 2, 4, 5, 8, 9, 20):
            with self.subTest(delta=delta):
                result = analyze(one_profile(delta))
                encoded = json.dumps(result, allow_nan=False)
                self.assertNotIn('Infinity', encoded)
                self.assertNotIn('NaN', encoded)
                row = scenario(result)
                if delta < 5:
                    self.assertEqual(row['status'], 'cannot_estimate')
                    self.assertIsNone(row['estimated_total_api_cost_usd'])

    def test_calibration_at_100_is_saturated_but_start_at_zero_is_valid(self):
        raw = one_profile()
        raw['snapshots'][0]['used_percent'] = 0
        raw['snapshots'][1]['used_percent'] = 20
        self.assertEqual(scenario(analyze(raw))['estimated_total_api_cost_usd'], 75)
        for before, after in [(80, 100), (100, 100), (100, 90)]:
            raw = one_profile()
            raw['snapshots'][0]['used_percent'], raw['snapshots'][1]['used_percent'] = before, after
            row = scenario(analyze(raw))
            self.assertEqual(row['status'], 'cannot_estimate')
            self.assertIn('quota_saturated', row['rejected_attempt_reasons'])


class ModelRemaining(unittest.TestCase):
    def assert_only_remaining_unknown(self, raw, reason):
        result = analyze(raw)
        # A differing current source/reset can create another identity with no
        # calibration; select the original calibrated identity explicitly.
        for pid, total in [(A, 75), (B, 100)]:
            row = scenario(result, pid, reset=stamp(604800).replace('Z', '+00:00'))
            self.assertEqual(row['status'], 'conditional_estimate')
            self.assertEqual(row['estimated_total_api_cost_usd'], total)
            self.assertIsNotNone(row['quantization_only_total_bounds_usd'])
            rem = row['remaining']
            self.assertEqual(rem['status'], 'cannot_estimate')
            self.assertIsNone(rem['estimated_remaining_api_cost_usd'])
            self.assertIn(reason, rem['reasons'])
        return result

    def test_current_selection_is_required_and_never_implicitly_uses_latest_endpoint(self):
        raw = sample()
        raw['current_snapshot'] = None
        result = self.assert_only_remaining_unknown(raw, 'no_current_snapshot_selected')
        self.assertIsNone(scenario(result)['remaining']['recorded_account_usage'])

    def test_missing_current_reference_keeps_totals_and_reports_unknown_remaining(self):
        raw = sample()
        raw['current_snapshot'] = 'missing'
        result = self.assert_only_remaining_unknown(raw, 'unknown_current_snapshot')
        self.assertIsNone(scenario(result)['remaining']['recorded_account_usage'])

    def test_omitted_current_selection_keeps_totals_without_mutating_input(self):
        raw = sample()
        del raw['current_snapshot']
        self.assert_only_remaining_unknown(raw, 'no_current_snapshot_selected')
        self.assertNotIn('current_snapshot', raw)

    def test_current_capture_overlapping_calibration_is_unusable(self):
        raw = sample()
        current(raw).update(capture_started_at=stamp(1190), observed_at=stamp(1210))
        result = self.assert_only_remaining_unknown(raw, 'current_capture_overlaps_calibration')
        self.assertFalse(scenario(result)['remaining']['recorded_account_usage']['usable_for_remaining'])

    def test_latest_calibration_endpoint_can_explicitly_be_current(self):
        raw = sample()
        raw['current_snapshot'] = 'c'
        result = analyze(raw)
        for pid, expected in [(A, 37.5), (B, 50)]:
            rem = scenario(result, pid)['remaining']
            self.assertEqual(rem['status'], 'conditional_estimate')
            self.assertEqual(rem['estimated_remaining_api_cost_usd'], expected)
            self.assertTrue(rem['recorded_account_usage']['usable_for_remaining'])
            self.assertNotIn('current_capture_overlaps_calibration', rem['reasons'])

    def test_historical_current_invalidates_remaining_only(self):
        raw = sample()
        current(raw)['source'] = 'historical_log'
        self.assert_only_remaining_unknown(raw, 'historical_current_snapshot')

    def test_wrong_reset_or_source_or_window_current_invalidates_remaining_only(self):
        for field, value in [('resets_at', stamp(604900)), ('source', 'user_observation'), ('window_minutes', 10079)]:
            with self.subTest(field=field):
                raw = sample()
                current(raw)[field] = value
                self.assert_only_remaining_unknown(raw, 'current_snapshot_identity_mismatch')

    def test_wrong_account_plan_pool_mode_or_window_kind_current_invalidates_remaining_only(self):
        for field in ('account_key', 'plan_type', 'limit_id', 'mode', 'window_kind'):
            with self.subTest(field=field):
                raw = sample()
                current(raw)['context'][field] = 'primary' if field == 'window_kind' else 'different-' + field
                self.assert_only_remaining_unknown(raw, 'current_snapshot_identity_mismatch')

    def test_current_must_be_later_than_every_profiles_calibration(self):
        raw = sample()
        raw['current_snapshot'] = 'b'  # Good for A alone, but B calibrates after it.
        self.assert_only_remaining_unknown(raw, 'current_snapshot_precedes_calibration')

    def test_current_before_all_calibration_does_not_call_max_on_empty_history(self):
        raw = sample()
        for snap in raw['snapshots']:
            if snap['id'] == 'current':
                snap.update(capture_started_at=stamp(0), observed_at=stamp(1), used_percent=0)
            else:
                for field in ('capture_started_at', 'observed_at'):
                    at = datetime.fromisoformat(snap[field].replace('Z', '+00:00')) + timedelta(seconds=300)
                    snap[field] = at.isoformat()
        for receipt in raw['receipts']:
            for field in ('started_at', 'completed_at'):
                at = datetime.fromisoformat(receipt[field].replace('Z', '+00:00')) + timedelta(seconds=300)
                receipt[field] = at.isoformat()
        self.assert_only_remaining_unknown(raw, 'current_snapshot_precedes_calibration')

    def test_current_decrease_is_checked_against_all_profiles_not_only_one(self):
        raw = sample()
        current(raw)['used_percent'] = 40  # Above A's endpoint, below B's endpoint.
        self.assert_only_remaining_unknown(raw, 'current_snapshot_decreased_or_cached')

    def test_expired_current_invalidates_remaining_only(self):
        raw = sample()
        current(raw).update(capture_started_at=stamp(604800), observed_at=stamp(604801))
        self.assert_only_remaining_unknown(raw, 'current_snapshot_outside_cycle')

    def test_remaining_width_can_fail_while_total_is_sound(self):
        raw = sample()
        current(raw)['used_percent'] = 99
        result = self.assert_only_remaining_unknown(raw, 'remaining_range_too_wide')
        rem = scenario(result)['remaining']
        self.assertEqual(rem['quantization_only_bounds_usd'][0], 0)
        self.assertGreater(rem['relative_range_width'], .5)

    def test_unknown_rounding_at_100_keeps_nonzero_possible_remaining_and_unknown_point(self):
        raw = sample()
        current(raw)['used_percent'] = 100
        result = self.assert_only_remaining_unknown(raw, 'remaining_range_too_wide')
        rem = scenario(result)['remaining']
        self.assertEqual(rem['recorded_account_usage']['remaining_percent'], 0)
        self.assertEqual(rem['quantization_only_bounds_usd'][0], 0)
        self.assertGreater(rem['quantization_only_bounds_usd'][1], 0)
        self.assertIsNone(rem['relative_range_width'])
        json.dumps(result, allow_nan=False)

    def test_floor_rounding_at_100_allows_exact_zero_remaining(self):
        raw = sample()
        raw['quantization']['rounding'] = 'floor'
        current(raw)['used_percent'] = 100
        result = analyze(raw)
        for pid in (A, B):
            rem = scenario(result, pid)['remaining']
            self.assertEqual(rem['status'], 'conditional_estimate')
            self.assertEqual(rem['estimated_remaining_api_cost_usd'], 0)
            self.assertEqual(rem['quantization_only_bounds_usd'], [0, 0])
            self.assertEqual(rem['relative_range_width'], 0)

    def test_current_zero_after_positive_calibration_is_a_decrease_not_a_new_full_balance(self):
        raw = sample()
        current(raw)['used_percent'] = 0
        self.assert_only_remaining_unknown(raw, 'current_snapshot_decreased_or_cached')


class ModelIntegrity(unittest.TestCase):
    def test_duplicate_and_case_aliased_profiles_are_rejected(self):
        for change in ({}, {'id': 'alias'}, {'id': 'alias', 'speed': 'STANDARD', 'effort': 'HIGH',
                                             'cache_workload': 'UNCACHED-INPUT-ONLY'}):
            with self.subTest(change=change):
                raw = sample()
                alias = copy.deepcopy(raw['workloads'][0])
                alias.update(change)
                raw['workloads'].append(alias)
                with self.assertRaisesRegex(ValueError, 'Duplicate workload ID or aliased applicability profile'):
                    analyze(raw)

    def test_profile_whitespace_cannot_evade_alias_detection(self):
        for field in ('id', 'model', 'speed', 'effort', 'cache_workload'):
            raw = sample()
            raw['workloads'][0][field] += ' '
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'surrounding whitespace'):
                analyze(raw)

    def test_unknown_profile_reference_is_rejected(self):
        for kind in ('segments', 'receipts'):
            raw = sample()
            raw[kind][0]['workload_id'] = 'not-declared'
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, 'Unknown workload_id'):
                analyze(raw)

    def test_missing_workload_id_is_not_silently_inferred(self):
        for kind in ('segments', 'receipts'):
            raw = sample()
            del raw[kind][0]['workload_id']
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, 'requires workload_id'):
                analyze(raw)

    def test_null_receipt_profile_is_not_allowed_even_for_mixed_segment(self):
        raw = sample('mixed_workload')
        raw['receipts'][-1]['workload_id'] = None
        with self.assertRaisesRegex(ValueError, 'Unknown workload_id'):
            analyze(raw)

    def test_identical_duplicates_are_deduplicated_globally(self):
        raw = sample()
        raw['receipts'].append(copy.deepcopy(raw['receipts'][0]))
        raw['snapshots'].append(copy.deepcopy(raw['snapshots'][0]))
        result = analyze(raw)
        self.assertEqual(result['counts']['duplicate_receipts_removed'], 1)
        self.assertEqual(result['counts']['duplicate_snapshots_removed'], 1)
        self.assertEqual(result['counts']['unique_local_receipts'], 2)
        self.assertEqual(scenario(result)['estimated_total_api_cost_usd'], 75)

    def test_duplicate_receipt_cannot_reassign_profile_or_usage(self):
        for field, value, reason in [('workload_id', B, 'Conflicting duplicate receipt workload_id'),
                                     ('thread_id', 'different', 'Conflicting duplicate response receipt'),
                                     ('model', 'synthetic-sol', 'Conflicting duplicate response receipt')]:
            raw = sample()
            duplicate = copy.deepcopy(raw['receipts'][0])
            duplicate[field] = value
            if field == 'thread_id':
                duplicate['owner_thread_id'] = value
            raw['receipts'].append(duplicate)
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, reason):
                analyze(raw)

    def test_inherited_fork_receipt_is_excluded_before_counting_or_pricing(self):
        raw = sample()
        inherited = copy.deepcopy(raw['receipts'][0])
        inherited.update(thread_id='fictional-child', workload_id=B, model='synthetic-sol')
        raw['receipts'].append(inherited)
        result = analyze(raw)
        self.assertEqual(result['counts']['inherited_receipts_excluded'], 1)
        self.assertEqual(scenario(result)['estimated_total_api_cost_usd'], 75)
        self.assertEqual(scenario(result, B)['estimated_total_api_cost_usd'], 100)

    def test_cross_boundary_receipt_rejects_every_affected_profile(self):
        raw = sample()
        crossing = copy.deepcopy(raw['receipts'][0])
        crossing.update(response_id='crossing', started_at=stamp(550), completed_at=stamp(650))
        raw['receipts'].append(crossing)
        result = analyze(raw)
        for pid in (A, B):
            row = scenario(result, pid)
            self.assertEqual(row['status'], 'cannot_estimate')
            self.assertIn('receipt_crosses_or_touches_boundary', row['rejected_attempt_reasons'])
            self.assertIsNone(row['quantization_only_total_bounds_usd'])

    def test_crossing_b_receipt_at_later_a_interval_blocks_earlier_b_cell(self):
        raw = sample('alternating_models')
        crossing = copy.deepcopy(raw['receipts'][1])
        crossing.update(response_id='late-crossing-b', started_at=stamp(1750), completed_at=stamp(1850))
        raw['receipts'].append(crossing)
        result = analyze(raw)
        for pid in (A, B):
            row = scenario(result, pid)
            self.assertEqual(row['status'], 'cannot_estimate')
            self.assertIn('receipt_crosses_or_touches_boundary', row['rejected_attempt_reasons'])
            self.assertIsNone(row['estimated_total_api_cost_usd'])
            self.assertIsNone(row['quantization_only_total_bounds_usd'])
        last = next(row for row in result['segments'] if row['id'] == 'three')
        self.assertEqual(last['observed_models'], ['synthetic-astra', 'synthetic-sol'])
        self.assertEqual(last['observed_workload_ids'], [A, B])

    def test_mismatched_model_blocks_all_actual_model_variants(self):
        raw = sample()
        variant = copy.deepcopy(raw['workloads'][1])
        variant.update(id='sol-fast', speed='fast')
        raw['workloads'].append(variant)
        raw['receipts'][0]['model'] = 'synthetic-sol'
        result = analyze(raw)
        for pid in (A, B, 'sol-fast'):
            row = scenario(result, pid)
            self.assertEqual(row['status'], 'cannot_estimate')
            self.assertIn('receipt_model_profile_mismatch', row['rejected_attempt_reasons'])
            self.assertIsNone(row['quantization_only_total_bounds_usd'])

    def test_crossing_mixed_to_pure_interval_is_still_validated_globally(self):
        raw = sample('alternating_models')
        raw['segments'][1]['workload_id'] = None
        crossing = copy.deepcopy(raw['receipts'][1])
        crossing.update(response_id='crossing-mixed-pure', started_at=stamp(1150), completed_at=stamp(1250))
        raw['receipts'].append(crossing)
        result = analyze(raw)
        mixed = next(row for row in result['segments'] if row['id'] == 'two')
        self.assertEqual(mixed['status'], 'unsupported_mixed_workload')
        self.assertIn('receipt_crosses_or_touches_boundary', mixed['reasons'])
        row = scenario(result)
        self.assertEqual(row['eligible_segment_count'], 1)
        self.assertIn('receipt_crosses_or_touches_boundary', row['rejected_attempt_reasons'])
        self.assertIsNone(row['estimated_total_api_cost_usd'])
        self.assertIsNone(row['quantization_only_total_bounds_usd'])

    def test_overlap_is_not_hidden_by_partitioning_profiles(self):
        raw = sample()
        raw['segments'][1]['before'] = 'a'
        result = analyze(raw)
        for pid in (A, B):
            row = scenario(result, pid)
            self.assertEqual(row['status'], 'cannot_estimate')
            self.assertIn('overlapping_segments', row['rejected_attempt_reasons'])

    def test_rejected_attempt_does_not_poison_untouched_profile(self):
        raw = sample()
        raw['segments'][0]['assertions']['settlement_assumed'] = False
        result = analyze(raw)
        self.assertEqual(scenario(result)['status'], 'cannot_estimate')
        self.assertEqual(scenario(result, B)['estimated_total_api_cost_usd'], 100)

    def test_changed_reset_blocks_touched_old_and_new_identity_cells(self):
        raw = sample()
        raw['snapshots'][1]['resets_at'] = stamp(604700)
        result = analyze(raw)
        self.assertTrue(any('window_or_reset_changed' in row['reasons'] for row in result['segments']))
        for row in result['model_scenarios']:
            self.assertIsNone(row['estimated_total_api_cost_usd'])

    def test_nonfinite_boolean_and_overprecise_observations_are_invalid(self):
        for field, values in [('used_percent', [float('nan'), float('inf'), float('-inf'), True, -1, 101]),
                              ('percent_resolution', [0, .1, float('nan'), float('inf'), True, 101])]:
            for value in values:
                raw = sample()
                raw['snapshots'][0][field] = value
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    analyze(raw)

    def test_unknown_rounding_and_selection_policy_are_rejected(self):
        for field, value in [('quantization', {'rounding': 'auto'}), ('selection_policy', 'best_fit_only')]:
            raw = sample()
            raw[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                analyze(raw)


class ModelPriceIdentity(unittest.TestCase):
    def test_price_origin_and_decimal_spelling_do_not_change_fingerprint(self):
        equivalent = copy.deepcopy(PRICE_RAW)
        for rates in equivalent['models'].values():
            for key in ('input', 'cached_input', 'cache_write', 'output'):
                rates[key] = rates[key] + ('0' if '.' in rates[key] else '.00')
        other = M['parse_price_catalog'](equivalent, 'another-fictional-origin').prices
        other = {model: replace(price, source='different fictional provenance') for model, price in other.items()}
        self.assertEqual(M['numeric_price_fingerprint'](PRICES), M['numeric_price_fingerprint'](other))
        self.assertEqual(analyze(prices=PRICES)['price_fingerprint'], analyze(prices=other)['price_fingerprint'])

    def test_each_numeric_rate_change_changes_the_basis(self):
        original = M['numeric_price_fingerprint'](PRICES)
        for field in ('input', 'cached', 'write', 'output'):
            changed = dict(PRICES)
            changed['synthetic-astra'] = replace(changed['synthetic-astra'], **{field: Decimal('3')})
            with self.subTest(field=field):
                self.assertNotEqual(original, M['numeric_price_fingerprint'](changed))

    def test_long_context_threshold_and_cache_tier_rates_change_basis(self):
        tiered = dict(PRICES)
        tiered['synthetic-astra'] = replace(tiered['synthetic-astra'], long_threshold=100000,
            long_input=Decimal('2'), long_cached=Decimal('.4'), long_write=Decimal('3'), long_output=Decimal('4'))
        original = M['numeric_price_fingerprint'](tiered)
        self.assertNotEqual(original, M['numeric_price_fingerprint'](PRICES))
        for field, value in [('long_threshold', 100001), ('long_input', Decimal('5')),
                             ('long_cached', Decimal('.5')), ('long_write', Decimal('5')), ('long_output', Decimal('5'))]:
            changed = dict(tiered)
            changed['synthetic-astra'] = replace(changed['synthetic-astra'], **{field: value})
            with self.subTest(field=field):
                self.assertNotEqual(original, M['numeric_price_fingerprint'](changed))

    def test_missing_prices_block_only_touched_model_cells(self):
        result = analyze(prices={'synthetic-sol': PRICES['synthetic-sol']})
        self.assertEqual(scenario(result)['status'], 'cannot_estimate')
        self.assertTrue(any(reason.startswith('incomplete_price_') for reason in scenario(result)['rejected_attempt_reasons']))
        self.assertEqual(scenario(result, B)['estimated_total_api_cost_usd'], 100)


class ModelSegmentCli(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = dict(os.environ, HOME=str(self.root), CODEX_HOME=str(self.root / 'fictional-codex-home'),
                        PYTHONPATH=str(self.root))
        (self.root / 'sitecustomize.py').write_text('''import sys
# Windows asyncio creates a loopback socketpair for its internal wakeup pipe.
# Build that test infrastructure before forbidding every application connection.
# asyncio.run owns and closes this one precreated loop as usual.
import asyncio
_offline_loop = asyncio.new_event_loop()
asyncio.events.new_event_loop = lambda: _offline_loop

def guard(event, args):
    if event in ('socket.connect', 'socket.getaddrinfo', 'socket.gethostbyname'):
        raise RuntimeError('offline fixture test forbids network')
    if event in ('open', 'os.scandir', 'os.listdir') and args:
        path = str(args[0])
        if 'sessions' in path or 'archived_sessions' in path:
            raise RuntimeError('fixture test forbids private session access')
sys.addaudithook(guard)
''')

    def command(self, name='two_models', *args, input_path=None):
        return subprocess.run([sys.executable, str(SCRIPT), 'segments', 'analyze',
            '--input', str(input_path or (FIXTURES / (name + '.json'))),
            '--price-catalog', str(PRICE_FIXTURE), *args], cwd=self.root, env=self.env,
            text=True, capture_output=True, timeout=15)

    def test_actual_two_model_fixture_cli_json_is_offline_and_has_required_amounts(self):
        process = self.command('two_models', '--json')
        self.assertEqual(process.returncode, 0, process.stderr)
        result = json.loads(process.stdout, parse_constant=lambda value: self.fail('Nonfinite JSON: ' + value))
        self.assertEqual(result['schema_version'], 2)
        self.assertEqual(scenario(result)['estimated_total_api_cost_usd'], 75)
        self.assertEqual(scenario(result, B)['estimated_total_api_cost_usd'], 100)
        self.assertEqual(scenario(result)['remaining']['estimated_remaining_api_cost_usd'], 30)
        self.assertEqual(scenario(result, B)['remaining']['estimated_remaining_api_cost_usd'], 40)
        self.assertEqual(result['price_catalog']['catalog_source'], str(PRICE_FIXTURE))
        self.assertFalse((self.root / 'fictional-codex-home').exists())

    def test_actual_fixture_cli_text_labels_alternatives_provenance_and_remaining(self):
        process = self.command()
        self.assertEqual(process.returncode, 0, process.stderr)
        for phrase in ('EXPERIMENTAL', 'NOT additive', 'synthetic-astra', 'synthetic-sol',
                       'Estimated total: $75.00', 'Estimated total: $100.00',
                       'Estimated remaining for this model: $30.00',
                       'Estimated remaining for this model: $40.00',
                       '40% as of', 'not live', 'Quantization-only', 'Numeric price basis: sha256:',
                       'FICTIONAL'):
            self.assertIn(phrase, process.stdout)
        self.assertNotIn('Estimated total: $175.00', process.stdout)
        self.assertNotIn('Estimated remaining for this model: $70.00', process.stdout)

    def test_all_committed_manifests_execute_via_real_offline_cli(self):
        for name in ('continuous_zero_delta', 'independent_fragments', 'alternating_models',
                     'quantization_trap', 'mixed_workload', 'many_small_fragments'):
            with self.subTest(name=name):
                process = self.command(name, '--json')
                self.assertEqual(process.returncode, 0, process.stderr)
                result = json.loads(process.stdout)
                self.assertEqual(result['schema_version'], 2)
                json.dumps(result, allow_nan=False)
                self.assertFalse((self.root / 'fictional-codex-home').exists())

    def test_unbounded_fixture_text_says_unknown_instead_of_false_precision(self):
        process = self.command('quantization_trap')
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertIn('Estimated total: unknown', process.stdout)
        self.assertIn('unbounded', process.stdout)
        self.assertNotIn('Estimated total: $149.25', process.stdout)
        self.assertNotIn('nan', process.stdout.lower())
        self.assertNotIn('infinity', process.stdout.lower())

    def test_json_reader_rejects_duplicate_keys_and_nonfinite_constants_in_v2_manifest(self):
        text = (FIXTURES / 'two_models.json').read_text()
        invalid = [text.replace('"schema_version": 2,', '"schema_version": 2, "schema_version": 2,', 1)]
        invalid += [text.replace('"used_percent": 10,', '"used_percent": ' + value + ',', 1)
                    for value in ('NaN', 'Infinity', '-Infinity')]
        for index, payload in enumerate(invalid):
            path = self.root / ('invalid-%d.json' % index)
            path.write_text(payload)
            process = self.command(input_path=path)
            with self.subTest(index=index):
                self.assertNotEqual(process.returncode, 0)
                self.assertNotIn('Traceback', process.stderr)
                self.assertNotIn('Estimated total: $', process.stdout)

    def test_explicit_grid_error_is_actionable_cli_failure(self):
        raw = sample()
        raw['quantization']['rounding'] = 'floor'
        current(raw)['used_percent'] = 60.25
        path = self.root / 'off-grid.json'
        path.write_text(json.dumps(raw))
        process = self.command(input_path=path)
        self.assertNotEqual(process.returncode, 0)
        self.assertIn('quantization grid', process.stderr)
        self.assertNotIn('Traceback', process.stderr)


if __name__ == '__main__':
    unittest.main()
