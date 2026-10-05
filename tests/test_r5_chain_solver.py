"""Exact shared-endpoint chain bounds, checked using synthetic data only.

The quadratic reference is deliberately confined to small tests. Production
must handle long raw-snapshot chains without enumerating their subpaths.
"""
from datetime import timedelta
import math
import random
import time
import unittest

from test_historical_segments import BASE, M


def make_chain(deltas, costs, *, initial=10, prefix='raw', gaps=(), span=1e-6):
    used, blocks = initial, []
    for i, (delta, cost) in enumerate(zip(deltas, costs)):
        before = {'id': f'{prefix}-s{i}', 'used_percent': used,
                  'observed_at': (BASE + timedelta(seconds=i * span)).isoformat()}
        used += delta
        after = {'id': f'{prefix}-s{i+1}', 'used_percent': used,
                 'observed_at': (BASE + timedelta(seconds=(i + 1) * span)).isoformat()}
        if i in gaps:
            before['id'] += '-gap'
        blocks.append({'id': f'{prefix}-b{i}', 'before': before, 'after': after,
                       'known_api_cost_usd': cost, 'delta_percent': delta,
                       'span_seconds': span})
    return blocks


def chains_of(blocks):
    chains = []
    for block in blocks:
        if not chains or chains[-1][-1]['after']['id'] != block['before']['id']:
            chains.append([])
        chains[-1].append(block)
    return chains


def portion_membership(portions):
    return {key: portion for portion in portions for key in portion['atomic_block_ids']}


def brute_bounds(blocks, clean, envelope, *, clip=True, portions=None):
    """R4's all-subpath reference, extended with whole-portion budgets."""
    low, high, feasible = 0., math.inf, True
    membership = None if portions is None else portion_membership(portions)
    rho = M['ROBUST_PROTOCOL']['near_clean_external_fraction']
    for chain in chains_of(blocks):
        snapshots = [chain[0]['before']] + [b['after'] for b in chain]
        costs, quotas = [0.], [0.]
        for b in chain:
            costs.append(costs[-1] + b['known_api_cost_usd'])
            quotas.append(quotas[-1] + b['delta_percent'])
        positions = {b['id']: i for i, b in enumerate(chain)}
        for i in range(len(chain)):
            li, ui = M['robust_endpoint_error'](snapshots[i], envelope, clip_endpoints=clip)
            for j in range(i + 1, len(chain) + 1):
                lj, uj = M['robust_endpoint_error'](snapshots[j], envelope, clip_endpoints=clip)
                cost, quota = costs[j] - costs[i], quotas[j] - quotas[i]
                upper_num, lower_num = quota - (lj - ui), quota - (uj - li)
                if cost > 0:
                    high = min(high, upper_num / cost)
                elif upper_num < -1e-10:
                    feasible = False
                if all(b['id'] in clean for b in chain[i:j]):
                    if membership is None:
                        denominator = (1 + rho) * cost
                    else:
                        first = membership[chain[i]['id']]['atomic_block_ids'][0]
                        last = membership[chain[j-1]['id']]['atomic_block_ids'][-1]
                        budget_cost = costs[positions[last] + 1] - costs[positions[first]]
                        denominator = cost + rho * budget_cost
                    if denominator > 0:
                        low = max(low, lower_num / denominator)
                    elif lower_num > 1e-10:
                        feasible = False
    if high < low and math.isclose(high, low, rel_tol=1e-12, abs_tol=0):
        low = high = (low + high) / 2
    return {'lower': low, 'upper': high if math.isfinite(high) else None,
            'feasible': feasible and high >= low and high > 0}


def graph_feasible(blocks, portions, envelope, rate, *, clip=True):
    """Independent Bellman-Ford oracle on true endpoint usages and a ground.

    Raw edges impose a*C <= true_after - true_before. A clean portion adds
    the opposite directed chord, true_end - true_start <= (1+rho)*a*C.
    This never enumerates or uses the optimized subpath-cover formula.
    """
    rho = M['ROBUST_PROTOCOL']['near_clean_external_fraction']
    for chain in chains_of(blocks):
        snapshots = [chain[0]['before']] + [b['after'] for b in chain]
        ground, edges = len(snapshots), []
        quota = 0.
        for i, snapshot in enumerate(snapshots):
            if i:
                quota += chain[i-1]['delta_percent']
            lo, hi = M['robust_endpoint_error'](snapshot, envelope, clip_endpoints=clip)
            edges.extend([(ground, i, quota - lo), (i, ground, hi - quota)])
        for i, b in enumerate(chain):
            edges.append((i + 1, i, -rate * b['known_api_cost_usd']))
        indices = {b['id']: i for i, b in enumerate(chain)}
        for portion in portions:
            members = portion['atomic_block_ids']
            if members[0] in indices:
                start, end = indices[members[0]], indices[members[-1]] + 1
                cost = sum(b['known_api_cost_usd'] for b in chain[start:end])
                edges.append((start, end, (1 + rho) * rate * cost))
        distance = [0.] * (ground + 1)
        for iteration in range(len(distance)):
            changed = False
            for source, target, weight in edges:
                candidate = distance[source] + weight
                if candidate < distance[target] - 1e-9:
                    distance[target] = candidate
                    changed = True
            if not changed:
                break
        else:
            return False
    return True


class RawChainSolver(unittest.TestCase):
    def compare_reference(self, blocks, clean, envelope, *, clip=True, portions=None):
        actual = M['robust_rate_interval'](blocks, clean, envelope, clip_endpoints=clip,
                                           clean_portions=portions)
        expected = brute_bounds(blocks, clean, envelope, clip=clip, portions=portions)
        self.assertEqual(actual['feasible'], expected['feasible'], (actual, expected))
        for side in ('lower', 'upper'):
            if expected[side] is None:
                self.assertIsNone(actual[side])
            else:
                self.assertTrue(math.isclose(actual[side], expected[side], rel_tol=5e-12, abs_tol=1e-12),
                                (side, actual, expected))
        self.assertEqual(actual['chain_lengths'], [len(c) for c in chains_of(blocks)])
        self.assertEqual(actual['chain_count'], len(chains_of(blocks)))
        self.assertEqual(actual['interval_kind'], 'conditional_feasible_rate_bounds')
        self.assertTrue(actual['not_statistical_confidence_interval'])
        for name, lower in (('rate_lower', True), ('rate_upper', False)):
            witness = actual['binding_constraints'][name]
            if witness is None:
                continue
            self.assertEqual(witness['block_count'], len(witness['block_ids']))
            self.assertEqual(witness['all_near_clean'], all(i in clean for i in witness['block_ids']))
            self.assertEqual(witness['capacity_bound_side'], 'upper' if lower else 'lower')
            self.assertEqual(witness['near_clean_external_fraction'], .05 if lower else None)
            self.assertTrue(math.isclose(witness['rate_bound'], actual['lower' if lower else 'upper'],
                                        rel_tol=1e-12, abs_tol=1e-12))
        return actual

    def test_legacy_random_subchains_with_clipping_zeros_tiny_costs_and_gaps(self):
        rng = random.Random(1958407)
        for case in range(1800):
            size = rng.randrange(1, 22)
            initial, used = rng.choice([0., .01, 10., 50., 99.99, 100.]), None
            used, deltas = initial, []
            for _ in range(size):
                after = min(100., max(0., used + rng.choice([-3., -.01, 0., .001, .25, 1., 4.])))
                deltas.append(after - used)
                used = after
            costs = [rng.choice([0., 0., 1e-12, 1e-6, .25, 1., 2., 1000.]) for _ in deltas]
            gaps = {i for i in range(1, size) if rng.random() < .1}
            blocks = make_chain(deltas, costs, initial=initial, gaps=gaps)
            clean = {b['id'] for b in blocks if rng.random() < .7}
            with self.subTest(case=case):
                self.compare_reference(blocks, clean, rng.choice([.5, 1., 2.]), clip=case % 3 != 0)

    def test_aggregate_random_bounds_and_full_difference_graph(self):
        rng = random.Random(518031)
        for case in range(1200):
            size = rng.randrange(1, 15)
            deltas = [rng.choice([-1., 0., .25, .5, 1., 3., 6.]) for _ in range(size)]
            costs = [rng.choice([0., 0., .25, .5, 1., 4., 10.]) for _ in deltas]
            gaps = {i for i in range(1, size) if rng.random() < .12}
            blocks = make_chain(deltas, costs, initial=20., gaps=gaps)
            portions, i = [], 0
            while i < size:
                if rng.random() < .3:
                    i += 1
                    continue
                end = min(size, i + rng.randrange(1, 5))
                end = min([g for g in gaps if i < g < end] or [end])
                portions.append({'id': f'p{i}', 'atomic_block_ids': [b['id'] for b in blocks[i:end]]})
                i = end
            clean = set(portion_membership(portions))
            envelope, clip = rng.choice([.5, 1., 2.]), case % 2 == 0
            with self.subTest(case=case):
                result = self.compare_reference(blocks, clean, envelope, clip=clip, portions=portions)
                for _ in range(4):
                    rate = rng.uniform(.001, 8.)
                    self.assertEqual(M['robust_rate_contains'](result, rate),
                                     graph_feasible(blocks, portions, envelope, rate, clip=clip))

    def test_generated_latent_clean_cases_remain_feasible_with_distributed_nuisance(self):
        rng = random.Random(16061983)
        for case in range(700):
            size = rng.randrange(2, 20)
            costs = [rng.choice([0., 0., .1, .2, .5, 1.]) for _ in range(size)]
            rate, envelope = rng.uniform(.1, 3.), rng.choice([.5, 1., 2.])
            portions, contaminated, i = [], [0.] * size, 0
            while i < size:
                if rng.random() < .25:
                    contaminated[i] = rng.uniform(0., 2.)
                    i += 1
                    continue
                end = min(size, i + rng.randrange(1, 5))
                portions.append({'id': f'p{i}', 'atomic_block_ids': [f'raw-b{k}' for k in range(i, end)]})
                target = rng.choice([k for k in range(i, end) if not costs[k]] or list(range(i, end)))
                contaminated[target] = .05 * rate * sum(costs[i:end]) * rng.choice([0., .25, .5, .99])
                i = end
            true, observed = rng.choice([0., 10.]), []
            observed.append(max(0., true + rng.uniform(-envelope, envelope)))
            for cost, nuisance in zip(costs, contaminated):
                true += rate * cost + nuisance
                observed.append(max(0., true + rng.uniform(-envelope, envelope)))
            blocks = make_chain([b - a for a, b in zip(observed, observed[1:])], costs,
                                initial=observed[0])
            clean = set(portion_membership(portions))
            with self.subTest(case=case):
                actual = self.compare_reference(blocks, clean, envelope, portions=portions)
                self.assertTrue(M['robust_rate_contains'](actual, rate))
                self.assertTrue(graph_feasible(blocks, portions, envelope, rate))

    def test_equal_cost_starts_can_replace_an_already_inserted_aggregate_point(self):
        blocks = make_chain([-1., 1., 6., -1.], [0., 0., 10., .25], initial=20.)
        portions = [{'id': 'first', 'atomic_block_ids': ['raw-b0', 'raw-b1', 'raw-b2']},
                    {'id': 'second', 'atomic_block_ids': ['raw-b3']}]
        actual = self.compare_reference(blocks, set(portion_membership(portions)), 2.,
                                        clip=False, portions=portions)
        self.assertAlmostEqual(actual['lower'], 3 / 10.5)
        self.assertEqual(actual['binding_constraints']['rate_lower']['before_id'], 'raw-s1')

    def test_contaminated_clean_portions_distribute_budget_over_zero_cost_edges(self):
        blocks = make_chain([40., 2.], [40., 0.])
        clean = {b['id'] for b in blocks}
        portions = [{'id': 'anchor', 'atomic_block_ids': [b['id'] for b in blocks]}]
        actual = self.compare_reference(blocks, clean, .5, portions=portions)
        self.assertTrue(M['robust_rate_contains'](actual, 1.))
        self.assertAlmostEqual(actual['lower'], 41 / 42)
        self.assertAlmostEqual(actual['upper'], 41 / 40)
        self.assertFalse(M['robust_rate_interval'](blocks, clean, .5)['feasible'])
        witness = actual['binding_constraints']['rate_lower']
        self.assertEqual(witness['clean_portion_ids'], ['anchor'])
        self.assertEqual(witness['near_clean_budget_cost_usd'], 40.)
        self.assertEqual(witness['near_clean_denominator_cost_usd'], 42.)

    def test_internal_entry_constraint_rejects_endpoint_only_false_positive(self):
        blocks = make_chain([7., 3.], [10., 0.])
        portions = [{'id': 'anchor', 'atomic_block_ids': [b['id'] for b in blocks]}]
        actual = self.compare_reference(blocks, set(portion_membership(portions)), 1., portions=portions)
        self.assertFalse(actual['feasible'])
        self.assertEqual(actual['lower'], 2.)
        self.assertEqual(actual['upper'], .9)
        witness = actual['binding_constraints']['rate_lower']
        self.assertEqual(witness['cost_usd'], 0.)
        self.assertEqual(witness['block_ids'], ['raw-b1'])
        self.assertEqual(witness['near_clean_budget_cost_usd'], 10.)

    def test_touching_portion_segmentation_matters(self):
        blocks = make_chain([40., 2.], [40., 0.])
        portions = [{'id': f'p{i}', 'atomic_block_ids': [b['id']]} for i, b in enumerate(blocks)]
        actual = self.compare_reference(blocks, set(portion_membership(portions)), .5, portions=portions)
        self.assertFalse(actual['feasible'])

    def test_zero_cost_tolerance_and_no_cost_bounds(self):
        for delta, feasible in [(1e-10, True), (1.01e-10, False), (-1e-10, True), (-1.01e-10, False)]:
            blocks = make_chain([delta], [0.])
            for portions in (None, [{'id': 'p', 'atomic_block_ids': ['raw-b0']}]):
                actual = self.compare_reference(blocks, {'raw-b0'}, 0., portions=portions)
                self.assertEqual(actual['feasible'], feasible)
                self.assertIsNone(actual['upper'])

    def test_empty_and_one_sided_only(self):
        self.compare_reference([], set(), 1., portions=[])
        blocks = make_chain([1., 2., 3.], [1., 1., 1.])
        actual = self.compare_reference(blocks, set(), 1., portions=[])
        self.assertEqual(actual['lower'], 0.)
        self.assertIsNone(actual['binding_constraints']['rate_lower'])

    def test_near_equality_reconciliation_preserved(self):
        blocks = make_chain([1.05, 1 - 5e-13], [1., 1.], gaps={1})
        actual = self.compare_reference(blocks, {'raw-b0'}, 0.)
        self.assertTrue(actual['feasible'])
        self.assertEqual(actual['lower'], actual['upper'])

    def test_equal_bindings_keep_earliest_start_then_end(self):
        blocks = make_chain([21.] * 4, [20.] * 4, initial=0.)
        actual = self.compare_reference(blocks, {b['id'] for b in blocks}, 0.)
        for witness in actual['binding_constraints'].values():
            self.assertEqual(witness['block_ids'], ['raw-b0'])

    def test_subnormal_and_large_coordinates_do_not_break_hull_predicates(self):
        for costs in ([1e-300, 1e-300, 0., 1e-300], [1e200, 1e200, 0., 1e200],
                      [1., 1e-30, 0., 1e-30]):
            blocks = make_chain([1., 1., 0., 1.], costs)
            self.compare_reference(blocks, {b['id'] for b in blocks}, .5)
        blocks = make_chain([0., 1.], [5e-324, 0.])
        portions = [{'id': 'p', 'atomic_block_ids': [b['id'] for b in blocks]}]
        actual = M['robust_rate_interval'](blocks, {'raw-b0', 'raw-b1'}, 0., clean_portions=portions)
        self.assertFalse(actual['feasible'])
        self.assertTrue(math.isinf(actual['lower']))

    def test_invalid_aggregate_memberships_are_rejected(self):
        blocks = make_chain([1.] * 4, [1.] * 4)
        cases = [
            ([{'id': 'p', 'atomic_block_ids': []}], set()),
            ([{'id': 'p', 'atomic_block_ids': ['missing']}], {'missing'}),
            ([{'id': 'p', 'atomic_block_ids': ['raw-b0', 'raw-b0']}], {'raw-b0'}),
            ([{'id': 'p', 'atomic_block_ids': ['raw-b0', 'raw-b2']}], {'raw-b0', 'raw-b2'}),
            ([{'id': 'p', 'atomic_block_ids': ['raw-b1', 'raw-b0']}], {'raw-b0', 'raw-b1'}),
            ([{'id': 'p', 'atomic_block_ids': ['raw-b0']}], set()),
            ([], {'raw-b0'}),
            ([{'id': 'p', 'atomic_block_ids': ['raw-b0']}, {'id': 'p', 'atomic_block_ids': ['raw-b1']}],
             {'raw-b0', 'raw-b1'}),
            ([{'id': 'p', 'atomic_block_ids': ['raw-b0']}, {'id': 'q', 'atomic_block_ids': ['raw-b0']}],
             {'raw-b0'}),
        ]
        for portions, clean in cases:
            with self.subTest(portions=portions), self.assertRaises(ValueError):
                M['robust_rate_interval'](blocks, clean, 1., clean_portions=portions)
        blocks[1]['before']['id'] += '-gap'
        with self.assertRaises(ValueError):
            M['robust_rate_interval'](blocks, {'raw-b0', 'raw-b1'}, 1.,
                                      clean_portions=[{'id': 'p', 'atomic_block_ids': ['raw-b0', 'raw-b1']}])
        blocks[1]['id'] = blocks[0]['id']
        with self.assertRaises(ValueError):
            M['robust_rate_interval'](blocks, set(), 1., clean_portions=[])

    def test_negative_and_nonfinite_costs_rejected(self):
        for cost in (-1., math.inf, math.nan):
            with self.subTest(cost=cost), self.assertRaises(ValueError):
                M['robust_rate_interval'](make_chain([1.], [cost]), set(), 1.)

    def test_large_chain_avoids_quadratic_subpath_or_binding_work(self):
        count = 100000
        blocks = make_chain([.0005] * count, [.01] * count)
        clean = {b['id'] for b in blocks}
        portions = [{'id': 'large-anchor', 'atomic_block_ids': [b['id'] for b in blocks]}]
        start = time.perf_counter()
        actual = M['robust_rate_interval'](blocks, clean, 1., clean_portions=portions)
        elapsed = time.perf_counter() - start
        self.assertLess(elapsed, 15., f'100,000-edge aggregate solver took {elapsed:.3f}s')
        self.assertTrue(actual['feasible'])
        self.assertEqual(actual['chain_lengths'], [count])
        self.assertEqual(actual['binding_constraints']['rate_lower']['block_count'], count)
        self.assertAlmostEqual(actual['lower'], (50 - 2) / 1050, places=10)
        self.assertAlmostEqual(actual['upper'], (50 + 2) / 1000, places=10)


if __name__ == '__main__':
    unittest.main()
