"""Independent metadata-only regressions for the Astra evidence funnel repair.

All sessions, responses, costs, observer names, and shared-endpoint math
examples are wholly invented synthetic test data.
No real log, conversation, credential, network, or model call is used.
"""
from datetime import datetime, timedelta
from decimal import Decimal
import copy
import json
from pathlib import Path
import tempfile
import unittest

from test_historical_segments import BASE, RESET, M, context, meta, quota, receipt, stamp

ASTRA = 'gpt-6-astra'
REVIEW = 'gpt-6-sol'
PRICES = {name: M['Price'](Decimal('1'), Decimal('.2'), Decimal('2'), Decimal('1.5'))
          for name in (ASTRA, REVIEW)}
POLICIES = ({'id': 'test', 'target_span_seconds': 300, 'min_span_seconds': 240,
             'max_gap_seconds': 300, 'min_delta_percent': 0},)


def observation(index, used, *, seconds=None, plan='prolite', reset=RESET,
                observer='observer-a', minutes=10080, conflict=False):
    at = BASE + timedelta(seconds=index * 10 if seconds is None else seconds)
    return at, {'id': f'quota-{index}', 'identity_key': (
        'codex', 'primary', minutes, datetime.fromtimestamp(reset, BASE.tzinfo).isoformat(), plan),
        'observed_at': at.isoformat(), 'used_percent': used, 'conflict': conflict,
        'observer_labels': list(observer) if isinstance(observer, (tuple, list)) else [observer]}


def classify(rows, **kwargs):
    return M['historical_quota_epochs']({('codex', 'primary'): rows}, **kwargs)


class FunnelFixtures(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, name, rows):
        path = self.root / name
        path.write_text(''.join(json.dumps(row) + '\n' for row in rows))
        return path

    def analyze(self, **kwargs):
        options = dict(now=BASE + timedelta(hours=12), policies=POLICIES, offsets=(0,))
        options.update(kwargs)
        return M['robust_segments']([self.root], PRICES, **options)[0]


class MissingPlanSemantics(FunnelFixtures):
    def test_missing_plan_is_not_evidence_of_a_change_or_retirement(self):
        rows = [observation(0, 10), observation(1, 10, plan=None),
                observation(2, 10), observation(3, 11)]
        epochs, _, _, _ = classify(rows)
        first = epochs[rows[0][1]['quota_epoch_id']]
        unknown = epochs[rows[1][1]['quota_epoch_id']]
        returned = epochs[rows[2][1]['quota_epoch_id']]
        self.assertIsNone(first['retired_at'])
        self.assertIsNone(unknown['identity_key'][4])
        self.assertNotEqual(unknown['status'], 'supported')
        self.assertNotEqual(unknown['classification'], 'supported_context_change')
        self.assertNotEqual(returned['status'], 'quarantined')
        self.assertNotEqual(first['quota_epoch_id'], returned['quota_epoch_id'])
        self.assertEqual(rows[2][1]['quota_epoch_id'], rows[3][1]['quota_epoch_id'])

    def test_repeated_missing_records_keep_unknown_plan_and_cannot_retire_known_identity(self):
        rows = [observation(0, 10), observation(1, 10, plan=None),
                observation(2, 11, plan=None), observation(3, 11), observation(4, 12)]
        epochs, _, _, _ = classify(rows)
        self.assertTrue(all(e['retired_at'] is None for e in epochs.values()))
        for _, snap in rows[1:3]:
            self.assertIsNone(snap['identity_key'][4])
            self.assertIsNone(epochs[snap['quota_epoch_id']]['identity_key'][4])
        self.assertEqual(rows[3][1]['quota_epoch_id'], rows[4][1]['quota_epoch_id'])

    def test_first_known_plan_after_missing_metadata_is_not_a_confirmed_plan_change(self):
        rows = [observation(0, 10, plan=None), observation(1, 10), observation(2, 11)]
        epochs, _, _, _ = classify(rows)
        self.assertFalse(any(e['classification'] == 'supported_context_change' for e in epochs.values()))
        self.assertTrue(all(e['retired_at'] is None for e in epochs.values()))
        self.assertNotEqual(rows[0][1]['quota_epoch_id'], rows[1][1]['quota_epoch_id'])

    def test_explicit_different_known_plans_keep_their_retirement_boundary(self):
        rows = [observation(0, 10, plan='plus'), observation(1, 10, plan='prolite'),
                observation(2, 11, plan='prolite'), observation(3, 11, plan='plus')]
        epochs, _, current, _ = classify(rows)
        self.assertEqual(epochs[rows[1][1]['quota_epoch_id']]['classification'], 'supported_context_change')
        self.assertIsNotNone(epochs[rows[0][1]['quota_epoch_id']]['retired_at'])
        self.assertEqual(epochs[rows[-1][1]['quota_epoch_id']]['status'], 'quarantined')
        self.assertIsNone(current[('codex', 'primary')]['quota_epoch_id'])

    def test_explicit_post_missing_baseline_can_accumulate_later_usage_without_gap_bridging(self):
        records = [meta(), context(ASTRA), quota(0, 10, plan='prolite'),
                   quota(300, 10, plan=None), quota(600, 10, plan='prolite'),
                   receipt('after-known', 750, 2_000_000), quota(900, 11, plan='prolite')]
        self.write('metadata.jsonl', records)
        data = self.analyze()
        eligible = [b for b in data['blocks'] if b['status'] == 'eligible' and b['known_api_cost_usd'] > 0]
        self.assertEqual(len(eligible), 1)
        block = eligible[0]
        self.assertEqual(block['known_api_cost_usd'], 2)
        self.assertEqual(block['delta_percent'], 1)
        self.assertEqual(M['timestamp'](block['before']['observed_at']), BASE + timedelta(seconds=600))
        self.assertEqual(block['identity']['plan_type'], 'prolite')
        self.assertFalse(any(e['status'] == 'quarantined' for e in data['quota_epochs']))

    def test_known_plan_different_after_unknown_is_compared_with_last_known_plan(self):
        rows = [observation(0, 10, plan='plus'), observation(1, 10, plan=None),
                observation(2, 10, plan='prolite')]
        epochs, _, _, _ = classify(rows)
        self.assertNotEqual(epochs[rows[1][1]['quota_epoch_id']]['status'], 'supported')
        self.assertEqual(epochs[rows[2][1]['quota_epoch_id']]['classification'], 'supported_context_change')
        self.assertIsNotNone(epochs[rows[0][1]['quota_epoch_id']]['retired_at'])


class SourceAwareContinuity(unittest.TestCase):
    @staticmethod
    def epoch_ids(rows, enabled=True):
        classify(rows, source_aware=enabled)
        return [snap['quota_epoch_id'] for _, snap in rows]

    def test_cross_observer_short_one_pp_drop_preserves_signed_telescoping_path(self):
        rows = [observation(0, 16, observer='b'), observation(1, 17, observer='a'),
                observation(2, 16, observer='b'), observation(3, 17, observer='b')]
        before = [s['used_percent'] for _, s in rows]
        ids = self.epoch_ids(rows)
        self.assertEqual(len(set(ids)), 1)
        self.assertEqual([s['used_percent'] for _, s in rows], before)
        deltas = [b-a for a, b in zip(before, before[1:])]
        self.assertEqual(deltas, [1, -1, 1])
        self.assertEqual(sum(deltas), 1)
        strict = [observation(0, 16, observer='b'), observation(1, 17, observer='a'),
                  observation(2, 16, observer='b')]
        strict_ids = self.epoch_ids(strict, enabled=False)
        self.assertNotEqual(strict_ids[1], strict_ids[2])

    def test_future_recovery_cannot_supply_a_missing_observer_prefix(self):
        rows = [observation(0, 17, observer='a'), observation(1, 16, observer='b'),
                observation(2, 17, observer='b')]
        prefix_ids = self.epoch_ids(copy.deepcopy(rows[:2]))
        full_ids = self.epoch_ids(copy.deepcopy(rows))
        self.assertEqual(prefix_ids, full_ids[:2])
        self.assertNotEqual(full_ids[0], full_ids[1])

    def test_same_source_drop_large_drop_stale_prior_and_missing_plan_still_split(self):
        cases = {
            'same_source': [observation(0, 16), observation(1, 17), observation(2, 16)],
            'large_drop': [observation(0, 15, observer='b'), observation(1, 18, observer='a'), observation(2, 15, observer='b')],
            'stale_prior': [observation(0, 16, seconds=0, observer='b'), observation(1, 17, seconds=61, observer='a'), observation(2, 16, seconds=62, observer='b')],
            'unknown_plan': [observation(0, 16, observer='b', plan=None), observation(1, 17, observer='a', plan=None), observation(2, 16, observer='b', plan=None)],
            'own_decrease': [observation(0, 17, observer='b'), observation(1, 17, observer='a'), observation(2, 16, observer='b')],
        }
        for name, rows in cases.items():
            with self.subTest(case=name):
                ids = self.epoch_ids(rows)
                self.assertNotEqual(ids[-2], ids[-1])

    def test_every_incoming_observer_needs_nonconflicting_own_history(self):
        for observers, conflict in ((('b', 'new-c'), False), ('b', True)):
            rows = [observation(0, 16, observer='b'), observation(1, 17, observer='a'),
                    observation(2, 16, observer=observers, conflict=conflict)]
            ids = self.epoch_ids(rows)
            self.assertNotEqual(ids[1], ids[2])

    def test_same_source_prefix_can_support_one_second_alias_without_rewriting_raw_reset(self):
        rows = [observation(0, 22, seconds=100), observation(1, 22, seconds=113, reset=RESET-1),
                observation(2, 23, seconds=125, reset=RESET)]
        raw = [s['identity_key'] for _, s in rows]
        ids = self.epoch_ids(rows)
        self.assertEqual(len(set(ids)), 1)
        self.assertEqual([s['identity_key'] for _, s in rows], raw)
        strict = copy.deepcopy([observation(0, 22, seconds=100), observation(1, 22, seconds=113, reset=RESET-1)])
        self.assertNotEqual(*self.epoch_ids(strict, enabled=False))

    def test_alias_without_source_evidence_or_outside_shared_window_does_not_merge(self):
        cases = (
            [observation(0, 10, seconds=100, observer='a'), observation(1, 10, seconds=110, observer='b', reset=RESET+1)],
            [observation(0, 10, seconds=100), observation(1, 12, seconds=110, reset=RESET+1)],
            [observation(0, 10, seconds=100), observation(1, 9, seconds=110, reset=RESET+1)],
            [observation(0, 10, seconds=100, plan=None), observation(1, 10, seconds=110, plan=None, reset=RESET+1)],
            [observation(0, 10, seconds=0), observation(1, 10, seconds=10, reset=RESET+1)],
        )
        for rows in cases:
            with self.subTest(rows=rows):
                ids = self.epoch_ids(rows)
                self.assertNotEqual(ids[0], ids[1])

    def test_alias_anchor_is_fixed_and_cannot_walk_forward_one_second_at_a_time(self):
        rows = [observation(0, 10, seconds=100), observation(1, 10, seconds=110, reset=RESET+1),
                observation(2, 10, seconds=120, reset=RESET+2)]
        ids = self.epoch_ids(rows)
        self.assertEqual(ids[0], ids[1])
        self.assertNotEqual(ids[1], ids[2])

    def test_each_observers_own_prefix_must_be_inside_both_raw_windows(self):
        # Global predecessor B at +2s is inside R and R+1. A's own prior at
        # t0 is outside the later window, so B cannot lend A that evidence.
        rows = [observation(0, 10, seconds=0, observer='a'),
                observation(1, 10, seconds=2, observer='b'),
                observation(2, 10, seconds=3, observer='a', reset=RESET+1)]
        ids = self.epoch_ids(rows)
        self.assertEqual(ids[0], ids[1])
        self.assertNotEqual(ids[1], ids[2])

    def test_prior_generation_source_cannot_rejoin_after_a_large_reset(self):
        rows = [observation(0, 40, observer='b'), observation(1, 40, observer='a'),
                observation(2, 0, observer='a'), observation(3, 40, observer='b')]
        ids = self.epoch_ids(rows)
        self.assertNotEqual(ids[1], ids[2])
        self.assertNotEqual(ids[2], ids[3])
        self.assertNotEqual(ids[0], ids[3])

    def test_old_generation_deadline_alias_cannot_escape_rebound_isolation(self):
        for repeated_reset in (False, True):
            with self.subTest(repeated_reset=repeated_reset):
                rows = [observation(0, 80, seconds=100, observer='a'),
                        observation(1, 80, seconds=110, observer='b'),
                        observation(2, 80, seconds=120, observer='b', reset=RESET+1),
                        observation(3, 80, seconds=130, observer='a'),
                        observation(4, 10, seconds=140, observer='a')]
                if repeated_reset:
                    rows.extend([observation(5, 12, seconds=150, observer='a'),
                                 observation(6, 0, seconds=160, observer='a')])
                rows.append(observation(7, 80, seconds=170, observer='b'))
                raw_keys = [s['identity_key'] for _, s in rows]
                epochs, _, _, _ = classify(rows, source_aware=True)
                ids = [s['quota_epoch_id'] for _, s in rows]
                self.assertEqual(len(set(ids[:4])), 1)
                self.assertNotEqual(ids[3], ids[4])
                self.assertNotEqual(ids[-2], ids[-1])
                self.assertEqual(epochs[ids[-1]]['classification'], 'prior_generation_observer_return')
                self.assertEqual([s['identity_key'] for _, s in rows], raw_keys)

    def test_true_early_window_and_retired_old_window_stay_separate(self):
        rows = [observation(0, 20, seconds=10), observation(1, 40, seconds=100),
                observation(2, 0, seconds=1800, reset=RESET+1800),
                observation(3, 1, seconds=1810, reset=RESET+1800),
                observation(4, 41, seconds=1820, reset=RESET)]
        epochs, _, current, _ = classify(rows, source_aware=True)
        self.assertEqual(epochs[rows[2][1]['quota_epoch_id']]['classification'], 'supported_new_window')
        self.assertEqual(rows[2][1]['quota_epoch_id'], rows[3][1]['quota_epoch_id'])
        self.assertEqual(epochs[rows[-1][1]['quota_epoch_id']]['status'], 'quarantined')
        self.assertIsNone(current[('codex', 'primary')]['quota_epoch_id'])

    def test_further_declines_cannot_escape_a_retired_identity_quarantine(self):
        rows = [observation(0, 40, seconds=100),
                observation(1, 0, seconds=1800, reset=RESET+1800),
                observation(2, 10, seconds=1810, reset=RESET),
                observation(3, 9, seconds=1820, reset=RESET),
                observation(4, 8, seconds=1830, reset=RESET)]
        epochs, _, current, edges = classify(rows, source_aware=True)
        for _, snap in rows[2:]:
            self.assertEqual(epochs[snap['quota_epoch_id']]['status'], 'quarantined')
        for a, b in zip(rows[2:], rows[3:]):
            self.assertIn('retired_window_return', edges[(a[1]['id'], b[1]['id'])])
        self.assertIsNone(current[('codex', 'primary')]['quota_epoch_id'])


class RejectedUsageConservation(FunnelFixtures):
    def test_rejected_compaction_sums_each_token_field_and_cost(self):
        records = [meta(), context(ASTRA), quota(0, 10, plan='prolite')]
        cumulative = dict(input_tokens=0, cached_input_tokens=0, cache_write_input_tokens=0,
                          output_tokens=0, reasoning_output_tokens=0, total_tokens=0)
        expected = dict(cumulative)
        for index, (inp, cached, out, reasoning) in enumerate(((10, 4, 2, 1), (20, 6, 3, 2), (30, 7, 5, 3)), 1):
            usage = dict(input_tokens=inp, cached_input_tokens=cached, cache_write_input_tokens=0,
                         output_tokens=out, reasoning_output_tokens=reasoning, total_tokens=inp+out)
            for key, value in usage.items():
                cumulative[key] += value
                expected[key] += value
            record = receipt(f'r{index}', index * 300 - 150, inp)
            record['payload']['usage'] = usage
            record['payload']['thread_token_usage'] = dict(cumulative)
            records.extend([record, quota(index * 300, 10 + index, plan='prolite')])
        self.write('rejected.jsonl', records)
        policy = ({'id': 'reject-gap', 'target_span_seconds': 900, 'min_span_seconds': 720,
                   'max_gap_seconds': 100, 'min_delta_percent': 0},)
        data = self.analyze(policies=policy)
        blocks = data['blocks']
        self.assertEqual(len(blocks), 1)
        self.assertTrue(all(b['status'] == 'rejected' for b in blocks))
        self.assertEqual(sum(b['atomic_interval_count'] for b in blocks), 3)
        for key, value in expected.items():
            self.assertEqual(sum(b['usage'][key] for b in blocks), value, key)
            self.assertEqual(sum(b['total_local_usage'][key] for b in blocks), value, key)
            self.assertEqual(sum(b['nuisance_local_usage'][key] for b in blocks), 0, key)
        self.assertAlmostEqual(sum(b['known_api_cost_usd'] for b in blocks), data['local_usage']['known_api_cost_usd'])

    def test_rejected_compaction_cannot_turn_missing_cache_metadata_into_complete(self):
        records = [meta(), context(ASTRA), quota(0, 10, plan='prolite')]
        for index in range(1, 4):
            record = receipt(f'r{index}', index * 300 - 150, 1_000_000, cumulative=index * 1_000_000)
            if index == 2:
                del record['payload']['usage']['cached_input_tokens']
            records.extend([record, quota(index * 300, 10 + index, plan='prolite')])
        self.write('incomplete-cache.jsonl', records)
        policy = ({'id': 'reject-gap', 'target_span_seconds': 900, 'min_span_seconds': 720,
                   'max_gap_seconds': 100, 'min_delta_percent': 0},)
        block, = self.analyze(policies=policy)['blocks']
        self.assertEqual(block['atomic_interval_count'], 3)
        self.assertEqual(block['usage']['total_tokens'], 3_000_000)
        self.assertFalse(block['cache_mix_complete'])


class SignedQuotaExtraction(FunnelFixtures):
    def test_source_aware_grid_keeps_small_negative_edge_in_total_delta(self):
        # B's own t180 -> t200 history stays at 10 while A reports 11 in between.
        # The raw +1,-1,+1 path sums to one, not two, quota percentage points.
        self.write('b.jsonl', [meta('b'), context(ASTRA), quota(0, 10, plan='prolite'),
            receipt('b1', 50, 1_000_000, owner='b', cumulative=1_000_000),
            quota(100, 10, plan='prolite'),
            receipt('b2', 150, 1_000_000, owner='b', cumulative=2_000_000),
            quota(180, 10, plan='prolite'), quota(200, 10, plan='prolite'),
            receipt('b3', 250, 1_000_000, owner='b', cumulative=3_000_000),
            quota(300, 11, plan='prolite')])
        self.write('a.jsonl', [meta('a'), context(ASTRA), quota(190, 11, plan='prolite')])
        data = self.analyze()
        block, = [b for b in data['blocks'] if b['status'] == 'eligible']
        self.assertEqual(block['known_api_cost_usd'], 3)
        self.assertEqual(block['delta_percent'], 1)
        self.assertEqual(block['atomic_interval_count'], 5)
        self.assertEqual(block['before']['used_percent'], 10)
        self.assertEqual(block['after']['used_percent'], 11)
        self.assertEqual(len(data['quota_epochs']), 1)


class UniqueGridOwnership(FunnelFixtures):
    def trace(self, events, samples=((0, 10), (100, 11), (200, 12), (300, 13))):
        """events are (second, model, input-token count, optional context)."""
        records = [meta()]
        ordered = [(t, 0, ('quota', used)) for t, used in samples]
        ordered += [(event[0], 1, ('event', event)) for event in events]
        cumulative = 0
        for t, _, (kind, value) in sorted(ordered, key=lambda x: (x[0], x[1])):
            if kind == 'quota':
                records.append(quota(t, value, plan='prolite'))
            else:
                _, model, amount, *settings = value
                records.append(context(model, **(settings[0] if settings else {})))
                cumulative += amount
                records.append(receipt(f'receipt-{len(records)}', t, amount, cumulative=cumulative))
        self.write('mixed.jsonl', records)
        return records

    def eligible(self, data):
        return [b for b in data['blocks'] if b['status'] == 'eligible']

    def test_event_count_owns_whole_grid_despite_more_expensive_review_companion(self):
        self.trace([(50, ASTRA, 1_000_000), (150, REVIEW, 100_000_000), (250, ASTRA, 1_000_000)])
        data = self.analyze()
        blocks = self.eligible(data)
        self.assertEqual(len(blocks), 1)
        block = blocks[0]
        self.assertEqual(block['profile']['model'], ASTRA)
        self.assertEqual(block['known_api_cost_usd'], 2)
        self.assertEqual(block['total_local_known_api_cost_usd'], 102)
        self.assertEqual(block['nuisance_local_known_api_cost_usd'], 100)
        self.assertEqual((block['local_event_count'], block['total_local_event_count'], block['nuisance_local_event_count']), (2, 3, 1))
        self.assertEqual((block['usage']['total_tokens'], block['total_local_usage']['total_tokens'], block['nuisance_local_usage']['total_tokens']), (2_000_000, 102_000_000, 100_000_000))
        self.assertEqual(block['delta_percent'], 3)
        self.assertTrue(block['ownership']['quota_delta_is_not_apportioned'])
        self.assertFalse(block['ownership']['selection_uses_quota_or_cost'])
        self.assertTrue(block['ownership']['no_fallback'])
        self.assertEqual(len({(b['ownership']['grid_id'], b['policy_id'], b['alignment_offset_seconds']) for b in blocks}), len(blocks))

    def test_owner_is_selected_for_each_shifted_grid(self):
        self.trace([(50, ASTRA, 1_000_000), (250, ASTRA, 1_000_000),
                    (310, REVIEW, 1_000_000), (320, REVIEW, 1_000_000)])
        blocks = self.eligible(self.analyze(offsets=(0, 60)))
        self.assertEqual({b['alignment_offset_seconds']: b['profile']['model'] for b in blocks}, {0: ASTRA, 60: REVIEW})
        self.assertEqual([b['delta_percent'] for b in blocks], [3, 3])

    def test_ties_use_earliest_event_then_lexical_profile_not_cost_or_record_order(self):
        for events in (
            [(50, ASTRA, 1_000_000), (150, REVIEW, 100_000_000)],
            [(50, REVIEW, 100_000_000), (50, ASTRA, 1_000_000)],
        ):
            with self.subTest(events=events):
                self.trace(events)
                block, = self.eligible(self.analyze())
                self.assertEqual(block['profile']['model'], ASTRA)
                self.assertEqual(block['known_api_cost_usd'], 1)

    def test_unpriced_companion_is_nuisance_but_unpriced_owner_never_falls_back(self):
        unknown = 'unpriced-review-model'
        self.trace([(50, ASTRA, 1_000_000), (150, unknown, 100_000_000), (250, ASTRA, 1_000_000)])
        block, = self.eligible(self.analyze())
        self.assertEqual(block['profile']['model'], ASTRA)
        self.assertEqual(block['nuisance_unpriced_event_count'], 1)
        self.assertEqual(block['nuisance_local_usage']['total_tokens'], 100_000_000)
        self.trace([(50, unknown, 1_000_000), (150, ASTRA, 100_000_000), (250, unknown, 1_000_000)])
        data = self.analyze()
        self.assertFalse(self.eligible(data))
        owned = [b for b in data['blocks'] if b.get('ownership', {}).get('owner_profile')]
        self.assertTrue(owned)
        self.assertTrue(all(b['profile']['model'] == unknown for b in owned))
        self.assertTrue(any('incomplete_local_price' in b['reasons'] for b in owned))

    def test_same_model_different_configuration_is_not_one_owner_profile(self):
        self.trace([(50, ASTRA, 1_000_000, {'reasoning_effort': 'high'}),
                    (150, ASTRA, 100_000_000, {'reasoning_effort': 'low'}),
                    (250, ASTRA, 1_000_000, {'reasoning_effort': 'high'})])
        block, = self.eligible(self.analyze())
        self.assertEqual(block['profile']['effort'], 'high')
        self.assertEqual(block['known_api_cost_usd'], 2)
        self.assertEqual(block['nuisance_local_known_api_cost_usd'], 100)

    def test_duplicate_ledger_and_observers_cannot_multiply_ownership_votes(self):
        records = self.trace([(50, ASTRA, 1_000_000), (150, REVIEW, 100_000_000), (250, ASTRA, 1_000_000)])
        before, = self.eligible(self.analyze())
        self.write('duplicate.jsonl', records)
        after, = self.eligible(self.analyze())
        for key in ('known_api_cost_usd', 'total_local_known_api_cost_usd', 'delta_percent', 'ownership'):
            self.assertEqual(before[key], after[key])

    def test_incomplete_grid_has_no_target_but_preserves_all_local_usage(self):
        self.trace([(50, ASTRA, 1_000_000), (150, REVIEW, 100_000_000)], samples=((0, 10), (100, 11), (200, 12)))
        data = self.analyze()
        self.assertFalse(self.eligible(data))
        self.assertTrue(data['blocks'])
        for block in data['blocks']:
            self.assertIsNone(block['profile'])
            self.assertEqual(block['known_api_cost_usd'], 0)
            self.assertEqual(block['local_event_count'], 0)
            self.assertEqual(block['usage']['total_tokens'], 0)
        self.assertEqual(sum(b['total_local_usage']['total_tokens'] for b in data['blocks']), 101_000_000)

    def test_ordinary_historical_still_rejects_mixed_model_intervals(self):
        self.trace([(50, ASTRA, 1_000_000), (150, REVIEW, 100_000_000), (250, ASTRA, 1_000_000)], samples=((0, 10), (300, 13)))
        data = M['historical_segments']([self.root], PRICES, now=BASE + timedelta(hours=1),
            policies=POLICIES, offsets=(0,))[0]
        mixed = [b for b in data['attempts'] if 'mixed_workload_interval' in b['reasons']]
        self.assertTrue(mixed)
        self.assertEqual(sum(b['known_api_cost_usd'] for b in mixed), 102)
        self.assertFalse(any(b['status'] == 'eligible' for b in data['attempts']))


class SolSharedEndpointRegression(unittest.TestCase):
    # Each chain starts at an observed used value, then gives C, Delta, selected.
    # Separate chains have disjoint endpoint IDs, even when adjacent in this list.
    CASES = (
        {'name': 'disconnected-synthetic-chains', 'chains': (
            (10, ((3, 3, True), (3, 3, True))),
            (20, ((2, 2, True), (2, 2, True)))),
         'point': 100, 'bounds': (75, 157.5)},
        {'name': 'synthetic-unselected-prefix', 'chains': (
            (30, ((1, 1, False), (2, 2, True), (2, 2, True), (2, 2, True))),),
         'point': 100, 'bounds': (700 / 9, 157.5)},
        {'name': 'synthetic-eight-point-chain', 'chains': (
            (40, ((4, 4, True), (4, 4, True))),),
         'point': 100, 'bounds': (80, 140)},
    )

    @staticmethod
    def blocks(case):
        blocks, clean = [], set()
        for chain_index, (used, rows) in enumerate(case['chains']):
            for j, (cost, delta, selected) in enumerate(rows):
                key = f'chain-{chain_index}-block-{j}'
                before = {'id': f'chain-{chain_index}-q-{j}', 'used_percent': used,
                          'observed_at': stamp(chain_index * 3600 + j * 300)}
                used += delta
                after = {'id': f'chain-{chain_index}-q-{j+1}', 'used_percent': used,
                         'observed_at': stamp(chain_index * 3600 + (j + 1) * 300)}
                blocks.append({'id': key, 'before': before, 'after': after,
                               'known_api_cost_usd': cost, 'delta_percent': delta})
                if selected:
                    clean.add(key)
        return blocks, clean

    def test_three_synthetic_points_and_ranges_match_hand_calculation(self):
        for case in self.CASES:
            with self.subTest(name=case['name']):
                blocks, clean = self.blocks(case)
                cost = sum(b['known_api_cost_usd'] for b in blocks if b['id'] in clean)
                delta = sum(b['delta_percent'] for b in blocks if b['id'] in clean)
                interval = M['robust_rate_interval'](blocks, clean, 1)
                self.assertTrue(interval['feasible'])
                self.assertEqual(interval['chain_count'], len(case['chains']))
                self.assertAlmostEqual(100 * cost / delta, case['point'])
                self.assertAlmostEqual(100 / interval['upper'], case['bounds'][0])
                self.assertAlmostEqual(100 / interval['lower'], case['bounds'][1])

    def test_sol_binding_witnesses_recompute_bounds_from_shared_outer_endpoints(self):
        for case in self.CASES:
            with self.subTest(name=case['name']):
                blocks, clean = self.blocks(case)
                by_id = {b['id']: b for b in blocks}
                interval = M['robust_rate_interval'](blocks, clean, 1)
                self.assertEqual(interval['interval_kind'], 'conditional_feasible_rate_bounds')
                self.assertTrue(interval['not_statistical_confidence_interval'])
                self.assertEqual(interval['chain_lengths'], [len(rows) for _, rows in case['chains']])
                for side in ('lower', 'upper'):
                    witness = interval['binding_constraints']['rate_' + side]
                    members = [by_id[bid] for bid in witness['block_ids']]
                    self.assertEqual(witness['block_count'], len(members))
                    self.assertEqual(len(set(witness['block_ids'])), len(members))
                    self.assertEqual(witness['before_id'], members[0]['before']['id'])
                    self.assertEqual(witness['after_id'], members[-1]['after']['id'])
                    self.assertTrue(all(a['after']['id'] == b['before']['id']
                                        for a, b in zip(members, members[1:])))
                    cost = sum(b['known_api_cost_usd'] for b in members)
                    delta = members[-1]['after']['used_percent'] - members[0]['before']['used_percent']
                    self.assertAlmostEqual(witness['cost_usd'], cost)
                    self.assertEqual(witness['observed_delta_percent'], delta)
                    self.assertEqual(witness['endpoint_error_difference'], {'lower': -2, 'upper': 2})
                    self.assertEqual(witness['all_near_clean'], all(b['id'] in clean for b in members))
                    self.assertEqual(witness['near_clean_external_fraction'], .05 if side == 'lower' else None)
                    rate = (delta - 2) / (1.05 * cost) if side == 'lower' else (delta + 2) / cost
                    self.assertAlmostEqual(rate, witness['rate_bound'])
                    self.assertAlmostEqual(rate, interval[side])
                    self.assertAlmostEqual(100 / rate, witness['capacity_bound_usd'])
                    self.assertTrue(witness['formula'])
                    if side == 'lower':
                        self.assertTrue(witness['all_near_clean'])
