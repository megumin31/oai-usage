"""Synthetic R4 frontier, measurement, selection, and frozen-validation tests.

No private logs, credentials, network calls, or real capacity targets are used.
The numbers describe invented costs and percentage-point observations only.
"""
import copy
from datetime import timedelta
import math
import random
import unittest
from unittest.mock import patch

from test_historical_segments import BASE, M, G


def trace(deltas, costs=None, *, initial=10, prefix='s', gaps=()):
    costs = [3] * len(deltas) if costs is None else costs
    blocks, used = [], initial
    for i, (delta, cost) in enumerate(zip(deltas, costs)):
        def endpoint(j, value):
            return {'id': f'{prefix}{j}', 'used_percent': value,
                    'observed_at': (BASE + timedelta(seconds=j * 300)).isoformat()}
        block = {'id': f'{prefix}-b{i}', 'known_api_cost_usd': cost,
                 'delta_percent': delta, 'span_seconds': 300,
                 'before': endpoint(i, used), 'after': endpoint(i + 1, used + delta)}
        if i in gaps:
            block['before']['id'] += '-gap'
        blocks.append(block)
        used += delta
    return blocks


def fit(blocks, envelope=1):
    return M['frontier_fit'](blocks, envelope=envelope, target_span=300)


def groups(blocks, rate=1, selected=None, envelope=1):
    ids = {b['id'] for b in blocks} if selected is None else selected
    weights = M['robust_weights'](blocks, envelope, 300)
    return M['frontier_support_groups'](blocks, ids, weights, rate, envelope)


class ConditionalFrontier(unittest.TestCase):
    def assert_contains(self, bounds, point):
        self.assertTrue(bounds['feasible'])
        self.assertIsNotNone(point)
        self.assertLessEqual(bounds['lower'], point + 1e-9)
        self.assertGreaterEqual(bounds['upper'], point - 1e-9)

    def test_four_clean_groups_supply_conditional_not_unconditional_capacity(self):
        result = fit(trace([3] * 4))
        self.assertEqual(result['status'], 'conditional_frontier_estimate')
        self.assertAlmostEqual(result['estimated_total_api_cost_usd'], 100)
        self.assertEqual(result['support']['group_count'], 4)
        self.assertEqual(result['support']['independent_support_count'], 2)
        self.assertFalse(result['support']['statistical_independence_established'])
        self.assert_contains(result['conditional_range_usd'], 100)
        self.assertIsNone(result['unconditional_identification']['upper_capacity_usd'])
        self.assertFalse(result['unconditional_identification']['finite_upper_identified'])
        self.assertIn('retrospective', result['evidence_basis'])

    def test_two_adjacent_groups_are_allowed_with_dependent_support_warning(self):
        result = fit(trace([3, 3]))
        self.assertEqual(result['status'], 'conditional_frontier_estimate')
        self.assertEqual(result['support']['group_count'], 2)
        self.assertEqual(result['support']['independent_support_count'], 1)
        self.assertIn('repeated_groups_share_endpoint_errors', result['quality_flags'])
        self.assertAlmostEqual(result['conditional_range_usd']['lower'], 75)
        self.assertAlmostEqual(result['conditional_range_usd']['upper'], 157.5)

    def test_single_outlier_and_all_noise_never_supply_repeated_support(self):
        for blocks in (trace([3]), trace([0] * 8), trace([1, -1, 1, -1]),
                       trace([3, 0, 0, 0], [30, .1, .1, .1])):
            with self.subTest(deltas=[b['delta_percent'] for b in blocks]):
                result = fit(blocks)
                self.assertEqual(result['status'], 'cannot_estimate')
                self.assertIsNone(result['estimated_total_api_cost_usd'])

    def test_predicted_signal_cannot_promote_a_noise_only_second_anchor(self):
        result = groups(trace([6, 0], [6, 3], gaps=(1,)))
        self.assertEqual(result['group_count'], 1)
        self.assertEqual(result['unused_low_signal_block_ids'], ['s-b1'])
        self.assertGreater(result['groups'][0]['positive_signal_lower_percent'], 0)

    def test_unhelpful_negative_prefix_does_not_swallow_two_informative_groups(self):
        blocks = trace([-1, 3, 3], [.1, 2, 2])
        physical = M['robust_rate_interval'](blocks, {b['id'] for b in blocks}, 1)
        self.assertTrue(physical['feasible'])
        result = fit(blocks)
        self.assertEqual(result['status'], 'conditional_frontier_estimate')
        self.assertGreaterEqual(result['support']['group_count'], 2)
        self.assertIn('s-b0', result['support']['unused_low_signal_block_ids'])

    def test_rare_clean_frontier_survives_many_higher_rate_dirty_blocks(self):
        blocks = trace([3] * 27, [30, 30] + [3] * 25)
        result = fit(blocks)
        self.assertEqual(result['status'], 'conditional_frontier_estimate')
        self.assertAlmostEqual(result['estimated_total_api_cost_usd'], 1000)
        self.assertEqual(result['selected_block_ids'], ['s-b0', 's-b1'])
        self.assertLess(result['support']['support_weight_fraction'], .25)
        self.assertIn('rare_frontier_support', result['quality_flags'])
        self.assertIsNone(result['unconditional_identification']['upper_capacity_usd'])

    def test_no_clean_proportional_world_is_observationally_indistinguishable(self):
        costs = [3] * 4
        clean = trace([1 * cost for cost in costs], costs)
        dirty = trace([.5 * cost + .5 * cost for cost in costs], costs)
        self.assertEqual(clean, dirty)
        clean_result, dirty_result = fit(clean), fit(dirty)
        self.assertEqual(clean_result, dirty_result)
        self.assertAlmostEqual(clean_result['estimated_total_api_cost_usd'], 100)
        self.assertNotEqual(dirty_result['estimated_total_api_cost_usd'], 100 / .5)
        self.assertFalse(dirty_result['unconditional_identification']['finite_upper_identified'])

    def test_atomic_constraints_prevent_aggregate_error_cancellation(self):
        # Each two-edge group totals 6pp, but a clean 6pp edge and a zero edge
        # cannot share one slope under E1 even though their average looks good.
        blocks = trace([6, 0, 6, 0], [1] * 4)
        interval = M['robust_rate_interval'](blocks, {b['id'] for b in blocks}, 1)
        self.assertFalse(interval['feasible'])
        self.assertEqual(fit(blocks)['status'], 'cannot_estimate')

    def test_overlapping_hypotheses_choose_information_not_maximum_capacity(self):
        result = fit(trace([3] * 8, [3, 3] + [2.5] * 6))
        supported = [h for h in result['hypotheses']
                     if h['status'] == 'supported_conditional_hypothesis']
        self.assertGreater(len(supported), 1)
        self.assertAlmostEqual(result['estimated_total_api_cost_usd'], 87.5)
        self.assertLess(result['estimated_total_api_cost_usd'],
                        max(h['total_api_cost_usd'] for h in supported))
        self.assertEqual(len(result['selected_block_ids']), 8)
        self.assert_contains(result['conditional_range_usd'], result['estimated_total_api_cost_usd'])

    def test_repeated_tilings_do_not_multiply_original_support(self):
        blocks = trace([3] * 4)
        memberships = M['frontier_memberships'](blocks, 1)
        keys = [tuple(h['selected_block_ids']) for h in memberships]
        self.assertEqual(len(keys), len(set(keys)))
        result = fit(blocks)
        self.assertEqual(result['support']['group_count'], 4)
        covered = [bid for g in result['support']['groups'] for bid in g['atomic_block_ids']]
        self.assertEqual(len(covered), len(set(covered)))
        self.assertTrue(result['tilings_are_not_independent_samples'])

    def test_replaying_one_original_block_cannot_manufacture_two_groups(self):
        blocks = trace([3])
        self.assertEqual(fit(blocks)['status'], 'cannot_estimate')
        replayed = fit(blocks + copy.deepcopy(blocks))
        self.assertEqual(replayed['status'], 'cannot_estimate')
        self.assertIsNone(replayed['estimated_total_api_cost_usd'])

    def test_replaying_unique_atoms_leaves_the_evidence_unchanged(self):
        blocks = trace([3] * 4)
        original, replayed = fit(blocks), fit(blocks + copy.deepcopy(blocks))
        self.assertEqual(original['estimated_total_api_cost_usd'], replayed['estimated_total_api_cost_usd'])
        self.assertEqual(original['support'], replayed['support'])

    def test_permuting_original_atoms_cannot_change_estimate_or_support(self):
        blocks = trace([3] * 4)
        self.assertEqual(fit(blocks), fit(list(reversed(blocks))))

    def test_conflicting_repeated_atom_id_is_rejected(self):
        blocks = trace([3])
        conflict = copy.deepcopy(blocks[0])
        conflict['known_api_cost_usd'] = 4
        with self.assertRaises(ValueError):
            fit(blocks + [conflict])

    def test_two_alias_ids_of_one_physical_observation_cannot_publish(self):
        blocks = trace([3])
        alias = copy.deepcopy(blocks[0])
        alias['id'] = 'alias'
        result = fit(blocks + [alias])
        self.assertEqual(result['status'], 'cannot_estimate')

    def test_conflicting_cost_for_same_physical_observation_is_rejected(self):
        blocks = trace([3])
        alias = copy.deepcopy(blocks[0])
        alias['id'] = 'alias'
        alias['known_api_cost_usd'] = 4
        with self.assertRaises(ValueError):
            fit(blocks + [alias])

    def test_different_ids_cannot_make_overlapping_time_spans_nonoverlapping(self):
        blocks = trace([3], prefix='first') + trace([3], prefix='second')
        with self.assertRaises(ValueError):
            fit(blocks)


class MeasurementAssumptions(unittest.TestCase):
    def test_quantization_e1_and_e2_ranges_nest_and_keep_main_point(self):
        result = fit(trace([3] * 4))
        quant = result['quantization_only']['capacity_range_usd']
        main = result['conditional_range_usd']
        stress = result['endpoint_error_stress']['capacity_range_usd']
        self.assertGreaterEqual(quant['lower'], main['lower'])
        self.assertLessEqual(quant['upper'], main['upper'])
        self.assertGreaterEqual(main['lower'], stress['lower'])
        self.assertLessEqual(main['upper'], stress['upper'])
        self.assertLessEqual(main['lower'], result['estimated_total_api_cost_usd'])
        self.assertLessEqual(result['estimated_total_api_cost_usd'], main['upper'])

    def test_quantization_range_uses_unknown_offset_without_zero_clipping(self):
        blocks = trace([3] * 4, initial=0)
        result = fit(blocks)
        ids = set(result['selected_block_ids'])
        quant = result['quantization_only']['rate_interval']
        expected = M['robust_rate_interval'](blocks, ids, .5, clip_endpoints=False)
        clipped = M['robust_rate_interval'](blocks, ids, .5, clip_endpoints=True)
        self.assertEqual(quant, expected)
        self.assertGreater(quant['upper'], clipped['upper'])

    def test_settlement_error_can_require_more_than_integer_quantization(self):
        # A clean 3pp-per-edge latent trace with alternating -1/+1pp display
        # errors yields 5/1/5/1. It is E1-feasible but not quantization-only.
        blocks = trace([5, 1, 5, 1])
        result = fit(blocks)
        self.assertEqual(result['status'], 'conditional_frontier_estimate')
        self.assertEqual(result['quantization_only']['status'], 'quantization_alone_insufficient')
        self.assertIn('selected_point_requires_more_than_quantization_only_error', result['quality_flags'])
        self.assertEqual(result['endpoint_error_stress']['status'], 'feasible')

    def test_stress_may_be_unbounded_without_refitting_membership(self):
        blocks = trace([3, 3], gaps=(1,))
        for side, second in (('before', 600), ('after', 900)):
            blocks[1][side]['observed_at'] = (BASE + timedelta(seconds=second)).isoformat()
        result = fit(blocks)
        self.assertEqual(result['status'], 'conditional_frontier_estimate')
        self.assertIsNotNone(result['conditional_range_usd']['upper'])
        self.assertIsNone(result['endpoint_error_stress']['capacity_range_usd']['upper'])
        self.assertIn('stress_upper_capacity_unbounded', result['quality_flags'])
        self.assertTrue(result['endpoint_error_stress']['frozen_membership'])
        expected = M['robust_rate_interval'](blocks, set(result['selected_block_ids']), 2)
        self.assertEqual(result['endpoint_error_stress']['rate_interval'], expected)

    def test_noninteger_and_saturated_displays_do_not_claim_integer_quantization(self):
        for blocks in (trace([3] * 4, initial=10.25), trace([3] * 4, initial=88)):
            with self.subTest(initial=blocks[0]['before']['used_percent']):
                result = fit(blocks)
                self.assertEqual(result['status'], 'conditional_frontier_estimate')
                self.assertEqual(result['quantization_only']['status'],
                                 'not_applicable_to_noninteger_or_saturated_display')

    def test_finite_positive_range_inversion_and_unbounded_upper(self):
        self.assertEqual(M['frontier_capacity_range']({'feasible': True, 'lower': .5, 'upper': 2})['lower'], 50)
        self.assertEqual(M['frontier_capacity_range']({'feasible': True, 'lower': .5, 'upper': 2})['upper'], 200)
        self.assertIsNone(M['frontier_capacity_range']({'feasible': True, 'lower': 0, 'upper': 2})['upper'])
        bad = M['frontier_capacity_range']({'feasible': False, 'lower': 2, 'upper': 1})
        self.assertIsNone(bad['lower'])
        self.assertIsNone(bad['upper'])

    def test_random_fixed_quantizer_clean_traces_keep_truth_and_nested_ranges(self):
        rng = random.Random(20261004)
        for case in range(40):
            costs = [rng.randint(1, 3) for _ in range(12)]
            actual = 10.37
            observations = [round(actual)]
            for cost in costs:
                # The original per-edge nuisance fraction remains <= .05.
                actual += cost * (1 + .05 * rng.random())
                observations.append(round(actual))
            blocks = trace([b - a for a, b in zip(observations, observations[1:])],
                           costs, initial=observations[0])
            result = fit(blocks)
            with self.subTest(case=case):
                self.assertEqual(result['status'], 'conditional_frontier_estimate')
                primary = result['conditional_range_usd']
                quant = result['quantization_only']['capacity_range_usd']
                stress = result['endpoint_error_stress']['capacity_range_usd']
                self.assertTrue(quant['feasible'])
                self.assertLessEqual(quant['lower'], 100 + 1e-8)
                self.assertGreaterEqual(quant['upper'], 100 - 1e-8)
                self.assertLessEqual(primary['lower'], result['estimated_total_api_cost_usd'] + 1e-8)
                self.assertGreaterEqual(primary['upper'], result['estimated_total_api_cost_usd'] - 1e-8)
                self.assertGreaterEqual(quant['lower'] + 1e-8, primary['lower'])
                self.assertLessEqual(quant['upper'] - 1e-8, primary['upper'])
                self.assertGreaterEqual(primary['lower'] + 1e-8, stress['lower'])
                if stress['upper'] is not None:
                    self.assertLessEqual(primary['upper'] - 1e-8, stress['upper'])


class GroupAndDiscoveryBoundaries(unittest.TestCase):
    def test_coarsening_preserves_zero_cost_atoms_and_trailing_units(self):
        blocks = trace([0, 1, 1, 1, 1], [0, 1, 1, 1, 1])
        units = M['frontier_coarsen'](blocks, 4)
        self.assertEqual([len(unit['atomic_block_ids']) for unit in units], [4, 1])
        self.assertEqual([bid for unit in units for bid in unit['atomic_block_ids']], [b['id'] for b in blocks])
        self.assertEqual(sum(unit['known_api_cost_usd'] for unit in units), 4)
        self.assertEqual(sum(unit['delta_percent'] for unit in units), 4)

    def test_gap_epoch_profile_policy_and_timing_changes_break_accumulation(self):
        changes = [('quota_epoch_id', ('a', 'b')),
                   ('profile', ({'model': 'a'}, {'model': 'b'})),
                   ('policy_id', ('5min', '15min')),
                   ('alignment_offset_seconds', (0, 60))]
        for field, values in changes:
            with self.subTest(field=field):
                blocks = trace([1, 2], [1, 2])
                for block, value in zip(blocks, values):
                    block[field] = value
                self.assertFalse(M['frontier_adjacent'](*blocks))
                self.assertEqual(len(M['frontier_coarsen'](blocks, 8)), 2)
                self.assertEqual(groups(blocks)['group_count'], 0)
                with self.assertRaises(ValueError):
                    fit(blocks)
        blocks = trace([1, 2], [1, 2], gaps=(1,))
        self.assertEqual(len(M['frontier_coarsen'](blocks, 8)), 2)
        self.assertEqual(groups(blocks)['group_count'], 0)

    def test_unselected_edge_breaks_a_support_path(self):
        blocks = trace([1, 1, 1, 1], [1] * 4)
        result = groups(blocks, selected={'s-b0', 's-b2', 's-b3'})
        self.assertEqual(result['group_count'], 0)

    def memberships_for_intervals(self, intervals):
        units = [{'atomic_block_ids': [name], 'known_api_cost_usd': 1,
                  'atoms': [{'id': name}]} for name in intervals]
        def interval(atoms, ids, envelope):
            low, high = intervals[atoms[0]['id']]
            return {'feasible': True, 'lower': low, 'upper': high}
        with patch.dict(G, frontier_coarsen=lambda blocks, count: units,
                        robust_rate_interval=interval):
            result = M['frontier_memberships']([], 1)
        return {frozenset(row['selected_block_ids']) for row in result}

    def test_closed_singleton_boundary_membership_is_retained(self):
        memberships = self.memberships_for_intervals({'a': (.5, 1), 'b': (1, 1), 'c': (1, 1.5)})
        self.assertIn(frozenset({'a', 'b', 'c'}), memberships)

    def test_open_cell_membership_is_retained_without_representable_midpoint(self):
        upper = math.nextafter(1.0, math.inf)
        memberships = self.memberships_for_intervals({'a': (1, upper), 'b': (1, 1), 'c': (upper, upper)})
        self.assertIn(frozenset({'a'}), memberships)

    def test_support_group_dp_matches_exhaustive_small_path_oracle(self):
        rng = random.Random(73193)
        for case in range(60):
            n = rng.randrange(1, 8)
            blocks = trace([rng.randint(-2, 5) for _ in range(n)],
                           [rng.randint(0, 4) for _ in range(n)], initial=25)
            selected = {b['id'] for b in blocks if rng.random() < .85}
            rate = rng.choice([.5, 1, 2])
            weights = M['robust_weights'](blocks, 1, 300)

            def oracle(start):
                if start >= n:
                    return (0, 0.0, 0.0)
                best = oracle(start + 1)
                cost = delta = weight = span = 0
                for end in range(start, n):
                    block = blocks[end]
                    if block['id'] not in selected:
                        break
                    cost += block['known_api_cost_usd']
                    delta += block['delta_percent']
                    weight += weights.get(block['id'], 0)
                    span += block['span_seconds']
                    if rate * cost >= 2 - 1e-10 and delta > 2 + 1e-10:
                        tail = oracle(end + 1)
                        best = max(best, (1 + tail[0], weight + tail[1], span + tail[2]))
                return best

            result = M['frontier_support_groups'](blocks, selected, weights, rate, 1)
            expected = oracle(0)
            with self.subTest(case=case):
                self.assertEqual(result['group_count'], expected[0])
                self.assertAlmostEqual(result['covered_information_weight'], expected[1])
                self.assertAlmostEqual(result['span_seconds'], expected[2])
                covered = [bid for group in result['groups'] for bid in group['atomic_block_ids']]
                self.assertEqual(len(covered), len(set(covered)))
                self.assertLessEqual(set(covered), selected)


class FrozenLaterEvidence(unittest.TestCase):
    def validate(self, training, holdout):
        return M['frontier_validate_frozen'](training, holdout, envelope=1, target_span=300)

    def test_clean_later_recurrence_validates_only_frozen_training_candidate(self):
        training = fit(trace([3] * 4))
        before = copy.deepcopy(training)
        validation = self.validate(training, trace([3] * 4, prefix='h'))
        self.assertEqual(validation['status'], 'later_recurrence_supported')
        self.assertFalse(validation['candidate_refitted_on_holdout'])
        self.assertTrue(validation['applies_only_to_training_candidate'])
        self.assertTrue(validation['not_accuracy_against_true_capacity'])
        self.assertEqual(training, before)

    def test_later_below_prediction_is_a_genuine_one_sided_contradiction(self):
        training = fit(trace([3] * 4))
        validation = self.validate(training, trace([0] * 4, prefix='h'))
        self.assertEqual(validation['status'], 'contradicted_under_endpoint_error_assumption')
        self.assertFalse(validation['one_sided_compatible'])
        self.assertEqual(validation['frozen_a_percent_per_usd'], 1)
        self.assertEqual(validation['training_candidate_usd'], 100)

    def test_later_excess_pollution_is_missing_corroboration_not_contradiction(self):
        training = fit(trace([3] * 4))
        validation = self.validate(training, trace([9] * 4, prefix='h'))
        self.assertEqual(validation['status'], 'limited_later_recurrence')
        self.assertTrue(validation['one_sided_compatible'])
        self.assertEqual(validation['support']['group_count'], 0)
        self.assertFalse(validation['candidate_refitted_on_holdout'])
        self.assertEqual(validation['training_candidate_usd'], 100)

    def test_missing_training_and_missing_later_data_are_explicit(self):
        self.assertEqual(self.validate(fit(trace([3])), trace([3] * 4, prefix='h'))['status'],
                         'training_frontier_unavailable')
        self.assertEqual(self.validate(fit(trace([3] * 4)), [])['status'], 'no_later_evidence')


if __name__ == '__main__':
    unittest.main()
