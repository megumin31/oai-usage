"""Frozen-rate grouped information diagnostics; entirely synthetic evidence."""
from datetime import timedelta
from decimal import Decimal
import random
import unittest

from test_historical_segments import BASE, M
from test_robust_segments import oracle_trace


def block(i, before, after, cost=4):
    return {'id': f'b{i}', 'known_api_cost_usd': cost,
            'delta_percent': after-before, 'span_seconds': 100,
            'before': {'id': f's{i}', 'used_percent': before,
                       'observed_at': (BASE + timedelta(seconds=i*100)).isoformat()},
            'after': {'id': f's{i+1}', 'used_percent': after,
                      'observed_at': (BASE + timedelta(seconds=(i+1)*100)).isoformat()}}


def clean_trace():
    return [block(i, round(10+.4*i), round(10+.4*(i+1))) for i in range(91)]


def group(blocks, *, selected=None, rate=.1, envelope=1):
    if selected is None:
        selected = {b['id'] for b in blocks if b['known_api_cost_usd'] > 0}
    weights = M['robust_weights'](blocks, envelope, 100)
    return M['robust_grouped_support'](blocks, selected, weights, rate, envelope)


class FrozenAggregation(unittest.TestCase):
    def test_collective_clean_signal_is_visible_without_primary_promotion_or_refit(self):
        blocks = clean_trace()
        training, holdout = blocks[:60], blocks[61:]
        for envelope in (1, 2):
            with self.subTest(envelope=envelope):
                result = M['robust_fit_blocks'](training, holdout, blocks,
                                               envelope=envelope, target_span=100)
                diagnostic = result['aggregation_diagnostic']
                self.assertEqual(diagnostic['status'], 'supports_frozen_candidate')
                self.assertFalse(diagnostic['changes_primary_estimate'])
                self.assertFalse(diagnostic['candidate_refitted'])
                self.assertTrue(diagnostic['internal_atomic_constraints_retained'])
                self.assertEqual(result['status'], 'diagnostic_candidate')
                self.assertIn('frozen_core_information_gate_failed', result['reasons'])
                self.assertAlmostEqual(result['candidate']['a_percent_per_usd'], .1)
                self.assertAlmostEqual(result['candidate']['total_api_cost_usd'], 1000)
                self.assertEqual(set(result['selected_block_ids']), {b['id'] for b in training})
                physical = M['robust_rate_interval'](
                    training, set(result['selected_block_ids']), envelope)
                self.assertEqual(result['rate_interval'], physical)
                self.assertAlmostEqual(result['candidate']['conditional_range_usd']['lower'],
                                       100/physical['upper'])
                self.assertAlmostEqual(result['candidate']['conditional_range_usd']['upper'],
                                       100/physical['lower'])
                self.assertAlmostEqual(diagnostic['training']['clean_predicted_delta_percent'], 24)
                self.assertAlmostEqual(diagnostic['holdout']['clean_predicted_delta_percent'], 12)
                self.assertTrue(diagnostic['holdout_joint_feasible'])
                self.assertIsNone(result['one_sided_capacity_bounds']['upper'])
                self.assertFalse(result['one_sided_capacity_bounds']['finite_upper_bound_identified'])

    def test_individually_compatible_holdout_cannot_hide_joint_contradiction(self):
        blocks = clean_trace()
        for b in blocks[61:]:
            b['before']['used_percent'] = 34
            b['after']['used_percent'] = 34
            b['delta_percent'] = 0
        for envelope in (1, 2):
            with self.subTest(envelope=envelope):
                fit = M['robust_fit_blocks'](blocks[:60], blocks[61:], blocks,
                                            envelope=envelope, target_span=100)
                diagnostic = fit['aggregation_diagnostic']
                # Every .4pp prediction fits the individual ±2E envelope, but
                # the held-out chain predicts 12pp against a flat display.
                self.assertGreater(diagnostic['holdout']['support_block_count'], 0)
                self.assertFalse(diagnostic['holdout_joint_feasible'])
                self.assertEqual(diagnostic['status'], 'insufficient_grouped_evidence')
                self.assertIn('grouped_holdout_shared_endpoint_infeasible', diagnostic['reasons'])
                self.assertIn('one_sided_model_contradiction', diagnostic['reasons'])
                self.assertAlmostEqual(fit['candidate']['a_percent_per_usd'], .1)

    def test_grouping_cannot_cross_train_purge_holdout_frontier(self):
        blocks = clean_trace()
        fit = M['robust_fit_blocks'](blocks[:61], blocks[62:], blocks,
                                    envelope=1, target_span=100)
        diagnostic = fit['aggregation_diagnostic']
        train_ids, hold_ids = {b['id'] for b in blocks[:61]}, {b['id'] for b in blocks[62:]}
        train_seen, hold_seen = set(), set()
        for anchor in diagnostic['training']['groups']:
            self.assertLessEqual(set(anchor['atomic_block_ids']), train_ids)
            train_seen.update(anchor['atomic_block_ids'])
        for anchor in diagnostic['holdout']['groups']:
            self.assertLessEqual(set(anchor['atomic_block_ids']), hold_ids)
            hold_seen.update(anchor['atomic_block_ids'])
        self.assertFalse(train_seen & hold_seen)
        self.assertNotIn('b61', train_seen | hold_seen)

    def test_proportional_contamination_remains_observationally_unidentified(self):
        costs = [4] * 91
        clean_records, clean_truth = oracle_trace(costs, a='.1', initial=10, step=100)
        dirty_records, dirty_truth = oracle_trace(
            costs, a='.06', external=[Decimal('.04')*c for c in costs], initial=10, step=100)
        self.assertEqual(clean_records, dirty_records)
        self.assertNotEqual(clean_truth['true_B_usd'], dirty_truth['true_B_usd'])
        blocks = clean_trace()
        fit = M['robust_fit_blocks'](blocks[:60], blocks[61:], blocks,
                                    envelope=1, target_span=100)
        self.assertEqual(fit['aggregation_diagnostic']['status'], 'supports_frozen_candidate')
        self.assertNotEqual(fit['candidate']['total_api_cost_usd'], dirty_truth['true_B_usd'])
        self.assertTrue(fit['identifiability_assumptions'])
        self.assertFalse(fit['aggregation_diagnostic']['training']['statistical_independence_established'])
        self.assertIsNone(fit['one_sided_capacity_bounds']['upper'])


class GroupConservation(unittest.TestCase):
    def test_disconnected_tiny_fragments_never_form_a_signal_group(self):
        blocks = clean_trace()[:10]
        for i, b in enumerate(blocks):
            b['before']['id'] = f'disconnected-before-{i}'
            b['after']['id'] = f'disconnected-after-{i}'
        result = group(blocks)
        self.assertEqual(result['groups'], [])
        self.assertEqual(result['covered_atomic_block_count'], 0)
        self.assertEqual(set(result['unused_low_signal_block_ids']), {b['id'] for b in blocks})
        self.assertEqual(result['clean_predicted_delta_percent'], 0)

    def test_dirty_zero_cost_bridge_stays_a_constraint_but_not_a_clean_connector(self):
        blocks = [block(0, 10, 10, 6), block(1, 10, 11, 0), block(2, 11, 11, 6)]
        result = group(blocks, selected={'b0', 'b2'})
        self.assertEqual(result['groups'], [])
        self.assertEqual(set(result['unused_low_signal_block_ids']), {'b0', 'b2'})
        physical = M['robust_rate_interval'](blocks, {'b0', 'b2'}, 1)
        self.assertEqual(physical['chain_count'], 1)
        self.assertEqual(physical['chain_lengths'], [3])

    def test_profile_epoch_or_timing_change_breaks_pending_information_group(self):
        for field, values in (
                ('quota_epoch_id', ('epoch-a', 'epoch-b')),
                ('profile', ({'model': 'model-a'}, {'model': 'model-b'})),
                ('policy_id', ('five-minute', 'fifteen-minute')),
                ('alignment_offset_seconds', (0, 60))):
            with self.subTest(field=field):
                blocks = [block(0, 10, 11, 6), block(1, 11, 11, 6)]
                for b, value in zip(blocks, values):
                    b[field] = value
                result = group(blocks)
                self.assertEqual(result['groups'], [])
                self.assertEqual(set(result['unused_low_signal_block_ids']), {'b0', 'b1'})

    def test_trailing_low_signal_does_not_count_as_information_or_weight(self):
        result = group(clean_trace()[:5])
        self.assertEqual(len(result['groups']), 1)
        self.assertEqual(result['groups'][0]['atomic_block_ids'], ['b0', 'b1', 'b2'])
        self.assertEqual(result['unused_low_signal_block_ids'], ['b3', 'b4'])
        self.assertEqual(result['covered_atomic_block_count'], 3)
        self.assertAlmostEqual(result['clean_predicted_delta_percent'], 1.2)
        self.assertAlmostEqual(result['support_weight_fraction'], .6)

    def test_no_positive_frozen_rate_cannot_create_groups(self):
        for rate in (None, 0):
            with self.subTest(rate=rate):
                result = group(clean_trace()[:20], rate=rate)
                self.assertEqual(result['groups'], [])
                self.assertEqual(result['covered_atomic_block_count'], 0)

    def test_random_grouping_conserves_membership_and_closes_at_first_signal_crossing(self):
        rng = random.Random(20261004)
        for _ in range(100):
            blocks = [block(i, 10+i, 11+i, rng.choice([0, 1, 2, 4, 8])) for i in range(30)]
            selected = {b['id'] for b in blocks if b['known_api_cost_usd'] > 0 and rng.random() < .8}
            for i, b in enumerate(blocks):
                if rng.random() < .1:
                    b['before']['id'] = f'new-frontier-{i}'
            rate, envelope = rng.choice([.1, .2, .4]), rng.choice([1, 2])
            result = group(blocks, selected=selected, rate=rate, envelope=envelope)
            by_id = {b['id']: b for b in blocks}
            seen = []
            for anchor in result['groups']:
                ids = anchor['atomic_block_ids']
                self.assertLessEqual(set(ids), selected)
                members = [by_id[bid] for bid in ids]
                self.assertTrue(all(a['after']['id'] == b['before']['id']
                                    for a, b in zip(members, members[1:])))
                signal = rate*sum(b['known_api_cost_usd'] for b in members)
                self.assertGreaterEqual(signal, envelope-1e-10)
                self.assertLess(rate*sum(b['known_api_cost_usd'] for b in members[:-1]),
                                envelope-1e-10)
                seen.extend(ids)
            self.assertEqual(len(seen), len(set(seen)))
            self.assertFalse(set(seen) & set(result['unused_low_signal_block_ids']))
            self.assertEqual(set(seen) | set(result['unused_low_signal_block_ids']), selected)


class AtomicConstraintOracle(unittest.TestCase):
    @staticmethod
    def direct_feasibility(blocks, selected, envelope, rate):
        low, high = None, None
        previous = None
        rho = M['ROBUST_PROTOCOL']['near_clean_external_fraction']
        for b in blocks:
            if previous != b['before']['id']:
                p = b['before']['used_percent']
                low, high = max(0, p-envelope), min(100, p+envelope)
            low += rate*b['known_api_cost_usd']
            high = (high + (1+rho)*rate*b['known_api_cost_usd']
                    if b['id'] in selected else float('inf'))
            p = b['after']['used_percent']
            low, high = max(low, max(0, p-envelope)), min(high, min(100, p+envelope))
            if low > high+1e-10:
                return False
            previous = b['after']['id']
        return True

    def test_shared_interval_solver_matches_independent_latent_endpoint_oracle(self):
        rng = random.Random(20261004)
        for case in range(128):
            blocks, selected, used = [], set(), rng.uniform(0, 40)
            for i in range(rng.randint(1, 12)):
                after = min(100, max(0, used+rng.randint(-4, 9)))
                b = block(i, used, after, rng.choice([0, .2, 1, 3, 10]))
                blocks.append(b)
                if rng.random() < .7:
                    selected.add(b['id'])
                used = after
            for envelope in (1, 2):
                interval = M['robust_rate_interval'](blocks, selected, envelope)
                for rate in (.001, .01, .03, .1, .2, .4, 1, 2, 5):
                    with self.subTest(case=case, envelope=envelope, rate=rate):
                        self.assertEqual(M['robust_rate_contains'](interval, rate),
                                         self.direct_feasibility(blocks, selected, envelope, rate))


if __name__ == '__main__':
    unittest.main()
