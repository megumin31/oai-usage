"""Synthetic support-selection and closed frontier regressions; no private logs."""
import copy
from datetime import timedelta
import itertools
import random
import unittest
from unittest.mock import patch

from test_historical_segments import BASE, M, G


def block(i, *, cost=1, delta=1, prefix='s', start=None):
    start = i * 100 if start is None else start
    return {'id': f'b{i}', 'known_api_cost_usd': cost,
            'delta_percent': delta, 'span_seconds': 100,
            'before': {'id': f'{prefix}{i}', 'used_percent': 10,
                       'observed_at': (BASE + timedelta(seconds=start)).isoformat()},
            'after': {'id': f'{prefix}{i+1}', 'used_percent': 10 + delta,
                      'observed_at': (BASE + timedelta(seconds=start+100)).isoformat()}}


def support(blocks, values=None, selected=None):
    weights = dict(zip((b['id'] for b in blocks), values or [1] * len(blocks)))
    return M['robust_support'](blocks, selected or {b['id'] for b in blocks}, weights)


def oracle(blocks, weights):
    best_count, best_kish = -1, -1
    for count in range(len(blocks) + 1):
        for subset in itertools.combinations(blocks, count):
            ids = [endpoint for b in subset for endpoint in (b['before']['id'], b['after']['id'])]
            if len(ids) != len(set(ids)):
                continue
            values = [weights[b['id']] for b in subset]
            kish = sum(values) ** 2 / sum(w*w for w in values) if any(values) else 0
            if count > best_count or count == best_count and kish > best_kish:
                best_count, best_kish = count, kish
    return best_count, best_kish


class PhaseNeutralSupport(unittest.TestCase):
    def test_first_or_last_heavy_edge_does_not_choose_an_arbitrary_phase(self):
        blocks = [block(i) for i in range(6)]
        for weights, expected in (([4, 1, 1, 1, 1, 1], ['b1', 'b3', 'b5']),
                                  ([1, 1, 1, 1, 1, 4], ['b0', 'b2', 'b4'])):
            with self.subTest(weights=weights):
                result = support(blocks, weights)
                self.assertEqual(result['independent_support_count'], 3)
                self.assertEqual(result['effective_support'], 3)
                self.assertEqual(result['endpoint_disjoint_block_ids'], expected)
                self.assertFalse(result['statistical_independence_established'])
                self.assertTrue(result['endpoint_disjoint_selection']['maximum_cardinality_guaranteed'])
                self.assertTrue(result['endpoint_disjoint_selection']['maximum_concentration_guaranteed'])

    def test_best_maximum_matching_can_mix_both_parities(self):
        blocks = [block(i) for i in range(6)]
        result = support(blocks, [1, 4, 1, 1, 4, 1])
        self.assertEqual(result['independent_support_count'], 3)
        self.assertEqual(result['effective_support'], 3)
        self.assertEqual(result['endpoint_disjoint_block_ids'], ['b0', 'b2', 'b5'])

    def test_disconnected_runs_are_optimized_jointly_not_one_phase_each(self):
        blocks = [block(i, prefix='left' if i < 2 else 'right') for i in range(4)]
        result = support(blocks, [1, 2, 2, 3])
        self.assertEqual(result['independent_support_count'], 2)
        self.assertEqual(result['effective_support'], 2)
        self.assertEqual(result['endpoint_disjoint_block_ids'], ['b1', 'b2'])
        self.assertEqual(result['endpoint_disjoint_selection']['component_count'], 2)

    def test_equal_weights_and_odd_chain_preserve_maximum_count(self):
        for n in range(1, 20):
            result = support([block(i) for i in range(n)])
            self.assertEqual(result['independent_support_count'], (n + 1) // 2)
            self.assertEqual(result['effective_support'], (n + 1) // 2)
            self.assertEqual(result['endpoint_disjoint_block_ids'], [f'b{i}' for i in range(0, n, 2)])

    def test_permutation_and_repeated_blocks_do_not_change_support(self):
        blocks = [block(i) for i in range(6)]
        weights = {b['id']: w for b, w in zip(blocks, [4, 1, 1, 1, 1, 1])}
        baseline = M['robust_support'](blocks, set(weights), weights)
        self.assertEqual(M['robust_support'](list(reversed(blocks)), set(weights), weights), baseline)
        self.assertEqual(M['robust_support'](blocks + copy.deepcopy(blocks), set(weights), weights), baseline)

    def test_a_shared_snapshot_is_never_counted_twice(self):
        blocks = [block(i) for i in range(6)]
        result = support(blocks, [4, 1, 1, 1, 1, 1])
        selected = [b for b in blocks if b['id'] in result['endpoint_disjoint_block_ids']]
        endpoints = [b[end]['id'] for b in selected for end in ('before', 'after')]
        self.assertEqual(len(endpoints), len(set(endpoints)))
        self.assertEqual(result['support_block_count'], 6)

    def test_missing_selected_edge_breaks_a_chain_without_manufactured_support(self):
        blocks = [block(i) for i in range(7)]
        result = support(blocks, selected={'b0', 'b1', 'b3', 'b4', 'b6'})
        self.assertEqual(result['independent_support_count'], 3)
        self.assertEqual(result['endpoint_disjoint_selection']['component_count'], 3)
        self.assertEqual(set(result['endpoint_disjoint_block_ids']), {'b0', 'b3', 'b6'})
        self.assertEqual(result['span_seconds'], 500)
        self.assertEqual(result['observation_span_seconds'], 700)

    def test_selector_does_not_optimize_rate_or_capacity(self):
        blocks = [block(i) for i in range(6)]
        weights = {b['id']: 1 for b in blocks}
        before = M['robust_support'](blocks, set(weights), weights)
        modified = copy.deepcopy(blocks)
        for i, b in enumerate(modified):
            b['known_api_cost_usd'] = 10 ** (i + 1)
            b['delta_percent'] = 10 ** (6 - i)
        after = M['robust_support'](modified, set(weights), weights)
        self.assertEqual(before, after)
        self.assertEqual(before['endpoint_disjoint_block_ids'], ['b0', 'b2', 'b4'])

    def test_maximum_count_precedes_a_more_favorable_kish(self):
        result = support([block(i) for i in range(5)], [100, 1, 1, 1, 1])
        self.assertEqual(result['independent_support_count'], 3)
        self.assertLess(result['effective_support'], 2)
        self.assertEqual(result['endpoint_disjoint_block_ids'], ['b0', 'b2', 'b4'])

    def test_path_hull_matches_exhaustive_synthetic_oracle(self):
        rng = random.Random(1741)
        for case in range(100):
            n = rng.randrange(1, 12)
            blocks = [block(i) for i in range(n)]
            for i in range(1, n):
                if rng.random() < .3:
                    blocks[i]['before']['id'] += '-gap'
            values = [rng.randrange(0, 21) for _ in blocks]
            weights = dict(zip((b['id'] for b in blocks), values))
            expected_count, expected_kish = oracle(blocks, weights)
            result = M['robust_support'](blocks, set(weights), weights)
            with self.subTest(case=case, values=values):
                self.assertEqual(result['independent_support_count'], expected_count)
                self.assertAlmostEqual(result['effective_support'], expected_kish, places=11)
                self.assertFalse(result['endpoint_disjoint_selection']['bounded_heuristic_used'])

    def test_many_disconnected_phases_do_not_need_exponential_search(self):
        blocks = [block(i, prefix=f'run{i//2}:') for i in range(100)]
        result = support(blocks, [1, 2] * 50)
        self.assertEqual(result['independent_support_count'], 50)
        self.assertEqual(result['effective_support'], 50)
        selection = result['endpoint_disjoint_selection']
        self.assertTrue(selection['maximum_concentration_guaranteed'])
        self.assertFalse(selection['bounded_heuristic_used'])
        self.assertEqual(selection['nonpath_search_states'], 0)

    def test_cycle_is_exact_and_nonpath_fallback_discloses_its_search(self):
        cycle = [block(i) for i in range(5)]
        cycle[-1]['after']['id'] = cycle[0]['before']['id']
        weights = {b['id']: w for b, w in zip(cycle, [1, 4, 3, 2, 1])}
        expected = oracle(cycle, weights)
        result = M['robust_support'](cycle, set(weights), weights)
        self.assertEqual(result['independent_support_count'], expected[0])
        self.assertAlmostEqual(result['effective_support'], expected[1])
        branch = [block(i) for i in range(8)]
        for b in branch:
            b['before']['id'] = 'shared-center'
        result = support(branch)
        self.assertEqual(result['independent_support_count'], 1)
        self.assertEqual(result['endpoint_disjoint_selection']['nonpath_component_count'], 1)
        self.assertGreater(result['endpoint_disjoint_selection']['nonpath_search_states'], 0)
        self.assertTrue(result['endpoint_disjoint_selection']['maximum_cardinality_guaranteed'])

    def test_bounded_nonpath_fallback_does_not_claim_optimality(self):
        blocks = [block(i) for i in range(35)]
        blocks[-1]['before']['id'] = 's5'
        result = support(blocks)
        metadata = result['endpoint_disjoint_selection']
        self.assertTrue(metadata['bounded_heuristic_used'])
        self.assertFalse(metadata['maximum_cardinality_guaranteed'])
        self.assertFalse(metadata['maximum_concentration_guaranteed'])
        self.assertFalse(metadata['statistical_independence_established'])
        self.assertEqual(metadata['nonpath_search_states'], metadata['nonpath_search_state_limit'])
        self.assertEqual(result, support(list(reversed(blocks))))

    def test_aliases_of_the_same_snapshot_pair_do_not_multiply_disjoint_count(self):
        blocks = [block(0)]
        alias = copy.deepcopy(blocks[0])
        alias['id'] = 'alias'
        result = support(blocks + [alias])
        self.assertEqual(result['independent_support_count'], 1)
        self.assertEqual(result['effective_support'], 1)
        self.assertEqual(len(result['endpoint_disjoint_block_ids']), 1)


class ClosedSupportCells(unittest.TestCase):
    def discover(self, blocks, weights=None, envelope=1):
        return M['robust_discover_components'](blocks, weights or {b['id']: 1 for b in blocks}, envelope)

    def test_single_closed_feasible_support_interval_is_not_lost(self):
        b = block(0, delta=0)
        b['before']['used_percent'] = b['after']['used_percent'] = 0
        result = self.discover([b])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['rate_interval'], {'lower': 1, 'upper': 1})
        self.assertEqual(result[0]['selected_block_ids'], ['b0'])
        self.assertTrue(M['robust_rate_contains'](M['robust_rate_interval']([b], {'b0'}, 1), 1))

    def test_boundary_only_overlap_can_be_a_singleton_physical_component(self):
        blocks = [block(0, cost=1, delta=0, prefix='a'),
                  block(1, cost=20, delta=44, prefix='b'),
                  block(2, delta=10, prefix='c'),
                  block(3, delta=20, prefix='d'),
                  block(4, delta=30, prefix='e')]
        result = self.discover(blocks)
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0]['qualified'])
        self.assertEqual(result[0]['rate_interval'], {'lower': 2, 'upper': 2})
        self.assertEqual(result[0]['selected_block_ids'], ['b0', 'b1'])
        self.assertEqual(result[0]['support']['independent_support_count'], 2)
        physical = M['robust_rate_interval'](blocks, {'b0', 'b1'}, 1)
        self.assertTrue(physical['feasible'])
        self.assertEqual(physical['lower'], physical['upper'])
        self.assertEqual(physical['lower'], 2)

    def test_touching_closed_intervals_join_but_freeze_the_boundary_peak(self):
        blocks = [block(0, delta=0, prefix='a'), block(1, cost=20, delta=44, prefix='b')]
        result = self.discover(blocks)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['rate_interval'], {'lower': 1, 'upper': 2.3})
        self.assertEqual(result[0]['selected_block_ids'], ['b0', 'b1'])
        self.assertEqual(result[0]['weight'], 2)

    def test_positive_gap_does_not_join_distinct_components(self):
        blocks = [block(0, delta=0, prefix='a'), block(1, cost=20, delta=45, prefix='b')]
        result = self.discover(blocks)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]['rate_interval'], {'lower': 1, 'upper': 2})
        self.assertGreater(result[1]['rate_interval']['lower'], 2)
        self.assertEqual(result[0]['selected_block_ids'], ['b0'])
        self.assertEqual(result[1]['selected_block_ids'], ['b1'])

    def test_empty_physical_intervals_supply_no_point_support(self):
        b = block(0, delta=-10)
        self.assertEqual(self.discover([b]), [])

    def test_duplicate_block_ids_do_not_create_boundary_support_weight(self):
        b = block(0, delta=0)
        b['before']['used_percent'] = b['after']['used_percent'] = 0
        self.assertEqual(self.discover([b]), self.discover([b, copy.deepcopy(b)]))

    def test_one_ulp_gap_does_not_invent_an_open_cell_or_join_points(self):
        # Pin interval arithmetic to adjacent representable endpoints, so no
        # floating midpoint can accidentally borrow singleton membership.
        import math
        blocks = [block(0, prefix='a'), block(1, prefix='b')]
        right = math.nextafter(1.0, math.inf)
        blocks[1]['known_api_cost_usd'] = 1 / right
        for b in blocks:
            b['delta_percent'] = 0
        with patch.dict(G, {'robust_endpoint_error': lambda snapshot, envelope: (-1, 0)}):
            result = self.discover(blocks)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]['rate_interval'], {'lower': 1.0, 'upper': 1.0})
        self.assertEqual(result[1]['rate_interval'], {'lower': right, 'upper': right})


if __name__ == '__main__':
    unittest.main()
