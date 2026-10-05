"""Offline, synthetic regressions for opt-in historical quota heuristics.

These fixtures contain metadata and invented quota observations only.  They do
not read default log roots, credentials, private conversations, or the network.
"""
import asyncio
import contextlib
import copy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import io
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

SCRIPT = Path(__file__).resolve().parents[1] / 'oai-usage'
M = runpy.run_path(str(SCRIPT), run_name='historical_heuristic_tests')
G = M['main'].__globals__
CATALOG = Path(__file__).parent / 'fixtures' / 'prices.json'
BASE = datetime(2026, 9, 22, tzinfo=timezone.utc)
RESET = int((BASE + timedelta(days=7)).timestamp())
MODEL = 'gpt-5.4-mini'
OTHER = 'gpt-5.4-nano'
PRICES = {model: M['Price'](Decimal('1'), Decimal('.2'), Decimal('2'), Decimal('1.5'))
          for model in (MODEL, OTHER)}
POLICIES = ({'id': 'test', 'min_span_seconds': 0, 'max_gap_seconds': 3600,
             'min_delta_percent': 5},)


def stamp(seconds):
    return (BASE + timedelta(seconds=seconds)).isoformat().replace('+00:00', 'Z')


def usage(n):
    return dict(input_tokens=n, cached_input_tokens=0, cache_write_input_tokens=0,
                output_tokens=0, reasoning_output_tokens=0, total_tokens=n)


def meta(sid='one', parent=None, created=0):
    return {'type': 'session_meta', 'timestamp': stamp(created),
            'payload': {'id': sid, 'timestamp': stamp(created), 'forked_from_id': parent}}


def context(model=MODEL, **extra):
    return {'type': 'turn_context', 'payload': {'model': model, **extra}}


def quota(seconds, percent, *, reset=RESET, plan='plus', pool='codex',
          window='secondary', minutes=10080):
    return {'type': 'event_msg', 'timestamp': stamp(seconds),
            'payload': {'type': 'token_count', 'rate_limits': {
                'limit_id': pool, 'plan_type': plan,
                window: {'used_percent': percent, 'window_minutes': minutes,
                         'resets_at': reset}}}}


def receipt(rid, seconds, amount, *, owner='one', cumulative=None):
    return {'type': 'token_usage_record', 'timestamp': stamp(seconds),
            'payload': {'thread_id': owner, 'response_id': rid,
                        'usage': usage(amount),
                        'thread_token_usage': usage(amount if cumulative is None else cumulative)}}


def token(seconds, cumulative, last):
    return {'type': 'event_msg', 'timestamp': stamp(seconds),
            'payload': {'type': 'token_count', 'info': {
                'total_token_usage': usage(cumulative), 'last_token_usage': usage(last)}}}


class HistoricalFixtures(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'logs'
        self.root.mkdir()

    def write(self, name, records):
        path = self.root / name
        path.write_text(''.join(json.dumps(row) + '\n' for row in records), encoding='utf-8')
        return path

    def analyze(self, **kwargs):
        options = dict(now=BASE + timedelta(hours=1), policies=POLICIES, offsets=(0,))
        options.update(kwargs)
        return M['historical_segments']([self.root], PRICES, **options)[0]

    def basic(self):
        return [meta(), context(), quota(0, 10), receipt('a', 100, 5_000_000),
                quota(600, 15), receipt('b', 700, 1_000_000), quota(1200, 15),
                receipt('c', 1300, 9_000_000), quota(1800, 30)]

    def scenarios(self, data, model=MODEL):
        return [row for row in data['model_scenarios'] if row['profile']['model'] == model]

    def scenario(self, data, model=MODEL):
        row, = self.scenarios(data, model)
        return row


class HistoricalSegments(HistoricalFixtures):
    def test_metadata_only_automatic_extraction_and_low_trust(self):
        records = self.basic() + [{'type': 'response_item', 'payload': {
            'text': 'PRIVATE_CHAT_SENTINEL', 'instructions': 'PRIVATE_TOOL_SENTINEL'}}]
        path = self.write('one.jsonl', records)
        before = path.read_bytes()
        data = self.analyze()
        row = self.scenario(data)
        self.assertEqual(data['kind'], 'historical_heuristic_quota_scenarios')
        self.assertEqual(data['trust_level'], 'historical_low_trust')
        self.assertTrue(data['alternatives_not_additive'])
        self.assertEqual(row['status'], 'conditional_estimate')
        self.assertAlmostEqual(row['estimated_total_api_cost_usd'], 75)
        self.assertNotIn('PRIVATE_', json.dumps(data))
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(row['profile']['model'], MODEL)
        self.assertIn('unknown', str(row['profile']).lower())

    def test_zero_delta_cost_is_retained_and_shared_endpoints_telescope(self):
        self.write('one.jsonl', self.basic())
        row = self.scenario(self.analyze())
        self.assertEqual(row['observed']['known_api_cost_usd'], 15)
        self.assertEqual(row['observed']['delta_percent'], 20)
        self.assertEqual(row['observed']['boundary_coefficient_weight'], 2)
        self.assertEqual(row['observed']['delta_quantization_bounds'], {'lower': 18, 'upper': 22})
        self.assertEqual(row['total_quantization_range_usd'], {'lower': 1500 / 22, 'upper': 1500 / 18})
        self.assertNotAlmostEqual(row['estimated_total_api_cost_usd'], 100 * (5 + 9) / 20)

    def test_global_timeline_does_not_add_overlapping_session_deltas(self):
        self.write('a.jsonl', [meta('one'), context(), quota(0, 10),
                              receipt('a', 100, 2_000_000), quota(600, 20),
                              receipt('c', 700, 10_000_000)])
        self.write('b.jsonl', [meta('two'), context(), quota(300, 15),
                              receipt('b', 400, 3_000_000, owner='two'), quota(900, 30)])
        row = self.scenario(self.analyze())
        self.assertEqual(row['observed']['known_api_cost_usd'], 15)
        self.assertEqual(row['observed']['delta_percent'], 20)
        self.assertEqual(row['estimated_total_api_cost_usd'], 75)
        self.assertEqual(row['observed']['boundary_coefficient_weight'], 2)

    def test_same_time_quota_observers_and_duplicate_files_are_idempotent(self):
        records = self.basic()
        self.write('a.jsonl', records)
        before = self.scenario(self.analyze())
        self.write('b-copy.jsonl', records)
        self.write('c-observer.jsonl', [meta('observer'), *[
            copy.deepcopy(r) for r in records if r['type'] == 'event_msg']])
        after = self.scenario(self.analyze())
        self.assertEqual(after['observed'], before['observed'])
        self.assertEqual(after['estimated_total_api_cost_usd'], before['estimated_total_api_cost_usd'])

    def test_ledger_deduplicates_response_fork_and_compaction_copies(self):
        first = receipt('a', 100, 1_000_000, cumulative=1_000_000)
        parent = [meta(), context(), quota(0, 10), first, token(110, 1_000_000, 1_000_000),
                  receipt('a', 150, 1_000_000, cumulative=1_000_000),
                  {'type': 'compacted', 'payload': {'latest_token_usage_record': first['payload']}},
                  quota(600, 20), receipt('b', 700, 2_000_000, cumulative=3_000_000),
                  token(710, 3_000_000, 2_000_000), quota(1200, 30)]
        self.write('a.jsonl', parent)
        self.write('a-copy.jsonl', parent)
        self.write('child.jsonl', [meta('child', 'one', 1200), context(), first,
                                  quota(600, 20), receipt('child', 1300, 3_000_000,
                                                         owner='child', cumulative=6_000_000),
                                  token(1310, 6_000_000, 3_000_000), quota(1800, 40)])
        data = self.analyze()
        row = self.scenario(data)
        self.assertEqual(row['observed']['known_api_cost_usd'], 6)
        self.assertEqual(row['observed']['delta_percent'], 30)
        self.assertEqual(row['estimated_total_api_cost_usd'], 20)

    def test_mixed_model_delta_is_not_split_by_cost_or_borrowed(self):
        self.write('one.jsonl', [meta(), context(), quota(0, 10),
            receipt('a', 100, 5_000_000), quota(600, 15),
            receipt('mixed-a', 700, 40_000_000), context(OTHER),
            receipt('mixed-b', 800, 10_000_000), quota(1200, 65), context(),
            receipt('c', 1300, 10_000_000), quota(1800, 80)])
        data = self.analyze()
        row = self.scenario(data)
        self.assertEqual(row['observed']['delta_percent'], 20)
        self.assertEqual(row['observed']['known_api_cost_usd'], 15)
        self.assertEqual(row['estimated_total_api_cost_usd'], 75)
        self.assertFalse(any(s['estimated_total_api_cost_usd'] is not None
                             for s in self.scenarios(data, OTHER)))
        self.assertIn('mixed', json.dumps(data['attempts']).lower())

    def test_independent_fragments_keep_four_uncancelled_boundaries(self):
        self.write('one.jsonl', [meta(), context(), quota(0, 10),
            receipt('a', 100, 5_000_000), quota(600, 15), context('unpriced-model'),
            receipt('unknown', 700, 1_000_000), quota(1200, 20), context(),
            receipt('c', 1300, 10_000_000), quota(1800, 35)])
        row = self.scenario(self.analyze())
        self.assertEqual(row['estimated_total_api_cost_usd'], 75)
        self.assertEqual(row['observed']['boundary_coefficient_weight'], 4)
        self.assertEqual(row['observed']['delta_quantization_bounds'], {'lower': 16, 'upper': 24})
        self.assertEqual(row['total_quantization_range_usd'], {'lower': 1500 / 24, 'upper': 1500 / 16})

    def test_negative_jump_starts_ambiguous_epoch_without_claiming_fresh_quota(self):
        rows = [meta(), context(), quota(0, 40)]
        for i, percent in enumerate((30, 41, 42, 52, 62), 1):
            rows += [receipt(f'r{i}', i * 600 - 300, 1_000_000), quota(i * 600, percent)]
        self.write('one.jsonl', rows)
        data = self.analyze()
        self.assertIn('quota_decrease_ambiguous', json.dumps(data['attempts']))
        row, = [r for r in self.scenarios(data) if r['observed']['known_api_cost_usd'] > 0]
        self.assertEqual(row['observed']['delta_percent'], 32)
        self.assertEqual(row['observed']['known_api_cost_usd'], 4)
        self.assertEqual(row['observed']['boundary_coefficient_weight'], 2)
        self.assertEqual(row['diagnostics']['nominal_total_api_cost_usd'], 12.5)
        self.assertIsNone(row['estimated_total_api_cost_usd'])
        self.assertIsNone(row['remaining']['estimated_remaining_api_cost_usd'])
        self.assertEqual(len(data['quota_epochs']), 2)

    def test_ambiguous_epoch_survives_gap_and_keeps_profiles_separate(self):
        policy = ({'id': 'gap', 'min_span_seconds': 0, 'max_gap_seconds': 600,
                   'min_delta_percent': 5},)
        for explicit_drop in (False, True):
            with self.subTest(explicit_drop=explicit_drop):
                rows = [meta(), context(), quota(0, 40)]
                if explicit_drop:
                    rows += [receipt('drop', 300, 1_000_000), quota(600, 30)]
                rows += [context(OTHER), quota(7200, 35),
                         receipt('still-low', 7500, 1_000_000), quota(7800, 36),
                         context(), receipt('recovery', 8100, 1_000_000), quota(8400, 41),
                         receipt('clean', 8700, 1_000_000), quota(9000, 50)]
                self.write('one.jsonl', rows)
                data = self.analyze(policies=policy, now=BASE + timedelta(hours=3))
                row, = [r for r in self.scenarios(data) if r['observed']['known_api_cost_usd'] > 0]
                self.assertEqual(row['observed']['delta_percent'], 14)
                self.assertEqual(row['observed']['known_api_cost_usd'], 2)
                self.assertIsNone(row['estimated_total_api_cost_usd'])
                other, = [r for r in self.scenarios(data, OTHER)
                          if r['observed']['known_api_cost_usd'] > 0]
                self.assertEqual(other['observed']['delta_percent'], 1)
                self.assertEqual(other['observed']['known_api_cost_usd'], 1)
                self.assertEqual(row['quota_epoch_id'], other['quota_epoch_id'])
                self.assertIsNone(other['estimated_total_api_cost_usd'])
                self.assertIn('quota_decrease_ambiguous', json.dumps(data['attempts']))

    def test_saturation_cost_is_visible_but_cannot_inflate_clean_estimate(self):
        self.write('one.jsonl', [meta(), context(), quota(0, 89),
            receipt('clean', 100, 1_000_000), quota(600, 99),
            receipt('saturates', 700, 10_000_000), quota(1200, 100),
            receipt('censored', 1300, 10_000_000), quota(1800, 100)])
        data = self.analyze()
        row = self.scenario(data)
        self.assertEqual(row['observed']['known_api_cost_usd'], 1)
        self.assertEqual(row['observed']['delta_percent'], 10)
        self.assertEqual(row['estimated_total_api_cost_usd'], 10)
        self.assertIn('quota_saturated', json.dumps(data['attempts']))
        self.assertEqual(sum(a['known_api_cost_usd'] for a in data['attempts']
                             if 'quota_saturated' in a['reasons']), 20)

    def test_conflicting_observation_retains_later_samples_in_ambiguous_epoch(self):
        rows = [meta(), context(), quota(0, 10)]
        for i, percent in enumerate((20, 30, 40, 50), 1):
            rows += [receipt(f'r{i}', i * 600 - 300, i * 1_000_000), quota(i * 600, percent)]
        self.write('one.jsonl', rows)
        self.write('observer.jsonl', [meta('observer'), quota(600, 25)])
        data = self.analyze()
        row, = [r for r in self.scenarios(data) if r['observed']['known_api_cost_usd'] > 0]
        self.assertEqual(row['observed']['delta_percent'], 20)
        self.assertEqual(row['observed']['known_api_cost_usd'], 7)
        self.assertIn('conflict', json.dumps(data['attempts']).lower())
        self.assertEqual(row['diagnostics']['nominal_total_api_cost_usd'], 35)
        self.assertIsNone(row['estimated_total_api_cost_usd'])
        self.assertIsNone(row['remaining']['estimated_remaining_api_cost_usd'])
        self.assertEqual(row['observed']['boundary_coefficient_weight'], 2)
        self.assertFalse(data['global_reasons'])
        self.assertEqual(len(data['quota_epochs']), 3)

    def test_counter_reset_does_not_globally_poison_unrelated_clean_profile(self):
        self.write('one.jsonl', [meta(), context(), quota(0, 10),
            token(100, 1_000_000, 1_000_000), quota(600, 20),
            token(700, 100_000, 100_000), quota(1200, 30), context(OTHER),
            token(1300, 1_100_000, 1_000_000), quota(1800, 40)])
        data = self.analyze()
        self.assertEqual(self.scenario(data, OTHER)['observed']['known_api_cost_usd'], 1)
        self.assertEqual(self.scenario(data, OTHER)['estimated_total_api_cost_usd'], 10)
        self.assertIn('reset', json.dumps(data['attempts']).lower())
        self.assertEqual(self.scenario(data)['observed']['known_api_cost_usd'], 1)

    def test_exact_reset_plan_pool_and_window_identities_never_pool(self):
        rows = [meta(), context()]
        variations = ({}, {'plan': 'pro'}, {'plan': 'pro', 'reset': RESET + 1},
                      {'pool': 'other-pool'}, {'window': 'primary', 'minutes': 300,
                                               'reset': int((BASE + timedelta(hours=5)).timestamp())})
        for index, kwargs in enumerate(variations):
            seconds = index * 1200
            rows += [quota(seconds, 10, **kwargs), receipt(f'r{index}', seconds + 300,
                                                          (index + 1) * 1_000_000),
                     quota(seconds + 600, 20, **kwargs)]
        self.write('one.jsonl', rows)
        scenarios = self.scenarios(self.analyze())
        self.assertEqual(len(scenarios), 5)
        self.assertEqual(sorted(s['observed']['delta_percent'] for s in scenarios), [10] * 5)
        self.assertEqual(sorted(s['diagnostics']['nominal_total_api_cost_usd'] for s in scenarios),
                         [10, 20, 30, 40, 50])
        alias, = [s for s in scenarios if s['identity']['resets_at'] == stamp(604801).replace('Z', '+00:00')]
        self.assertIsNone(alias['estimated_total_api_cost_usd'])
        resets = {s['identity']['resets_at'] for s in scenarios if s['identity']['plan_type'] == 'pro'}
        self.assertEqual(len(resets), 2, 'one-second reset drift is a distinct identity')

    def test_unknown_price_exclusion_leaves_clean_profile_estimate(self):
        rows = self.basic()
        rows[5:5] = [context('unpriced-model')]
        rows[7:7] = [context()]
        self.write('one.jsonl', rows)
        data = self.analyze()
        row = self.scenario(data)
        self.assertEqual(row['observed']['known_api_cost_usd'], 14)
        self.assertEqual(row['observed']['delta_percent'], 20)
        self.assertEqual(row['estimated_total_api_cost_usd'], 70)
        self.assertIn('price', json.dumps(data['attempts']).lower())

    def test_remaining_uses_own_period_and_never_newer_other_plan_percentage(self):
        self.write('one.jsonl', [meta(), context(), quota(0, 10),
            receipt('a', 100, 1_000_000), quota(600, 20), quota(1200, 70, plan='pro'),
            receipt('b', 1300, 2_000_000), quota(1800, 80, plan='pro')])
        data = self.analyze()
        by_plan = {s['identity']['plan_type']: s for s in self.scenarios(data)}
        self.assertEqual(by_plan['plus']['remaining']['scope'], 'historical_as_of')
        self.assertEqual(by_plan['pro']['remaining']['scope'], 'current_cycle_as_of')
        self.assertEqual(by_plan['plus']['remaining']['used_percent'], 20)
        self.assertEqual(by_plan['plus']['remaining']['estimated_remaining_api_cost_usd'], 8)
        self.assertEqual(by_plan['pro']['remaining']['used_percent'], 80)
        self.assertIsNone(by_plan['pro']['remaining']['estimated_remaining_api_cost_usd'])
        self.assertEqual(by_plan['pro']['diagnostics']['nominal_remaining_api_cost_usd'], 4)
        self.assertFalse(by_plan['pro']['remaining']['is_live_lookup'])
        historical = self.analyze(now=BASE + timedelta(days=8))
        for row in self.scenarios(historical):
            self.assertEqual(row['remaining']['scope'], 'historical_as_of')
            self.assertFalse(row['remaining']['is_live_lookup'])

    def test_remaining_never_borrows_percentage_from_later_ambiguous_epoch(self):
        self.write('one.jsonl', [meta(), context(), quota(0, 10),
            receipt('a', 100, 1_000_000), quota(600, 20), context(OTHER),
            receipt('b', 700, 2_000_000), quota(1200, 40),
            receipt('cached', 1300, 1_000_000), quota(1800, 30)])
        data = self.analyze()
        row = self.scenario(data)
        self.assertEqual(row['estimated_total_api_cost_usd'], 10)
        self.assertEqual(row['remaining']['used_percent'], 40)
        self.assertEqual(row['remaining']['scope'], 'historical_as_of')
        self.assertEqual(row['remaining']['estimated_remaining_api_cost_usd'], 6)
        self.assertEqual(M['timestamp'](row['remaining']['observed_at']), BASE + timedelta(seconds=1200))
        self.assertFalse(any(r['remaining']['scope'] == 'current_cycle_as_of'
                             and r['remaining']['estimated_remaining_api_cost_usd'] is not None
                             for r in data['model_scenarios']))
        self.assertEqual(len(data['quota_epochs']), 2)

    def test_current_cycle_as_of_is_not_a_live_quota_balance(self):
        self.write('one.jsonl', self.basic())
        row = self.scenario(self.analyze())
        self.assertEqual(row['remaining']['scope'], 'current_cycle_as_of')
        self.assertFalse(row['remaining']['is_live_lookup'])
        self.assertEqual(row['remaining']['used_percent'], 30)
        self.assertEqual(row['remaining']['estimated_remaining_api_cost_usd'], 52.5)
        self.assertEqual(M['timestamp'](row['remaining']['observed_at']), BASE + timedelta(seconds=1800))

    def test_small_signal_keeps_nominal_and_unbounded_quantization_range(self):
        self.write('one.jsonl', [meta(), context(), quota(0, 10),
                               receipt('a', 100, 1_000_000), quota(600, 11)])
        row = self.scenario(self.analyze())
        self.assertEqual(row['status'], 'cannot_estimate')
        self.assertIsNone(row['estimated_total_api_cost_usd'])
        self.assertEqual(row['diagnostics']['nominal_total_api_cost_usd'], 100)
        self.assertIsNone(row['diagnostics']['total_quantization_range_usd']['upper'])
        self.assertEqual(row['total_quantization_range_usd'], {'lower': None, 'upper': None})
        self.assertIsNone(row['remaining']['estimated_remaining_api_cost_usd'])
        self.assertTrue(row['reasons'])
        self.assertEqual(row['observed']['delta_percent'], 1)

    def test_zero_total_delta_never_divides_by_zero(self):
        self.write('one.jsonl', [meta(), context(), quota(0, 10),
                               receipt('a', 100, 1_000_000), quota(600, 10)])
        data = self.analyze()
        row = self.scenario(data)
        self.assertIsNone(row['estimated_total_api_cost_usd'])
        self.assertEqual(row['status'], 'cannot_estimate')
        self.assertEqual(row['observed']['known_api_cost_usd'], 1)
        json.dumps(data, allow_nan=False)

    def test_threshold_and_time_offsets_remain_visible_without_ratio_selection(self):
        self.write('one.jsonl', self.basic() + [receipt('delayed', 1830, 5_000_000)])
        policies = ({'id': 'wide', 'min_span_seconds': 600, 'max_gap_seconds': 900,
                     'min_delta_percent': 5},
                    {'id': 'strict', 'min_span_seconds': 600, 'max_gap_seconds': 900,
                     'min_delta_percent': 30})
        data = self.analyze(policies=policies, offsets=(0, 60))
        rows = {(r['policy_id'], r['alignment_offset_seconds']): r for r in self.scenarios(data)}
        self.assertEqual(set(rows), {('wide', 0), ('wide', 60), ('strict', 0), ('strict', 60)})
        for policy in ('wide', 'strict'):
            self.assertEqual(rows[(policy, 0)]['diagnostics']['nominal_total_api_cost_usd'], 75)
            self.assertEqual(rows[(policy, 60)]['diagnostics']['nominal_total_api_cost_usd'], 100)
        self.assertEqual(rows[('strict', 0)]['status'], 'cannot_estimate')
        self.assertIsNone(rows[('strict', 0)]['estimated_total_api_cost_usd'])
        self.assertEqual(rows[('wide', 0)]['estimated_total_api_cost_usd'], 75)
        self.assertEqual(rows[('wide', 0)]['status'], 'conditional_estimate')
        self.assertTrue(data['alternatives_not_additive'])
        self.assertEqual(data, self.analyze(policies=policies, offsets=(0, 60)))

    def test_alignment_offsets_recheck_model_purity(self):
        self.write('one.jsonl', self.basic() + [context(OTHER),
                                              receipt('other-late', 1830, 5_000_000)])
        data = self.analyze(offsets=(0, 60))
        rows = {row['alignment_offset_seconds']: row for row in self.scenarios(data)}
        self.assertEqual(rows[0]['observed']['known_api_cost_usd'], 15)
        self.assertEqual(rows[0]['observed']['delta_percent'], 20)
        self.assertEqual(rows[60]['observed']['known_api_cost_usd'], 6)
        self.assertEqual(rows[60]['observed']['delta_percent'], 5)
        mixed = [a for a in data['attempts'] if a['alignment_offset_seconds'] == 60
                 and 'mixed_workload_interval' in a['reasons']]
        self.assertEqual(len(mixed), 1)
        self.assertEqual(mixed[0]['known_api_cost_usd'], 14)

    def test_alignment_offsets_cannot_cross_plan_or_reset_identity(self):
        for variation in ({'plan': 'pro'}, {'reset': RESET + 1}):
            with self.subTest(variation=variation):
                self.write('one.jsonl', [meta(), context(), quota(0, 10),
                    receipt('a', 100, 1_000_000), quota(600, 20), quota(620, 50, **variation),
                    receipt('b', 640, 2_000_000), quota(1200, 60, **variation)])
                data = self.analyze(offsets=(0, 60))
                original = [r for r in self.scenarios(data) if r['identity']['plan_type'] == 'plus'
                            and r['identity']['resets_at'] == stamp(604800).replace('Z', '+00:00')]
                rows = {r['alignment_offset_seconds']: r for r in original}
                self.assertEqual(rows[0]['estimated_total_api_cost_usd'], 10)
                self.assertIsNone(rows[60]['estimated_total_api_cost_usd'])
                self.assertIn('alignment_crosses_quota_identity', json.dumps(data['attempts']))

    def test_default_policy_and_alignment_grid_retains_all_nine_choices(self):
        self.write('one.jsonl', self.basic())
        data = self.analyze(policies=None, offsets=(-60, 0, 60))
        expected = {(policy['id'], offset) for policy in M['HISTORICAL_POLICIES']
                    for offset in (-60, 0, 60)}
        self.assertEqual({(r['policy_id'], r['alignment_offset_seconds'])
                          for r in self.scenarios(data)}, expected)
        self.assertEqual(data['selection_policy'], 'all_attempts_all_policies_no_ratio_selection')
        self.assertTrue(any(r['estimated_total_api_cost_usd'] is None for r in self.scenarios(data)))
        self.assertTrue(any(r['estimated_total_api_cost_usd'] is not None for r in self.scenarios(data)))

    def test_observed_effort_profiles_are_separate_without_inventing_settings(self):
        self.write('one.jsonl', [meta(), context(reasoning_effort='high', service_tier='standard'),
            quota(0, 10), receipt('high', 100, 1_000_000), quota(600, 20),
            context(reasoning_effort='low', service_tier='fast'),
            receipt('low', 700, 2_000_000), quota(1200, 30)])
        rows = {r['profile']['effort']: r for r in self.scenarios(self.analyze())}
        self.assertEqual(set(rows), {'high', 'low'})
        self.assertEqual(rows['high']['profile']['mode'], 'standard')
        self.assertEqual(rows['low']['profile']['mode'], 'fast')
        self.assertEqual(rows['high']['estimated_total_api_cost_usd'], 10)
        self.assertEqual(rows['low']['estimated_total_api_cost_usd'], 20)
        self.assertEqual(rows['high']['observed']['boundary_coefficient_weight'], 2)
        self.assertEqual(rows['low']['observed']['boundary_coefficient_weight'], 2)

    def test_quantization_range_never_claims_statistical_confidence(self):
        self.write('one.jsonl', self.basic())
        data = self.analyze()
        def inspect(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if 'confidence' in key:
                        self.assertIsNone(item)
                    inspect(item)
            elif isinstance(value, list):
                for item in value:
                    inspect(item)
        inspect(data)
        warnings = ' '.join(data['limitations']).lower()
        self.assertRegex(warnings, r'not [^.]*confidence interval')
        self.assertIn('cached', warnings)
        self.assertIn('account', warnings)

    def test_missing_input_does_not_fabricate_estimates(self):
        data = self.analyze()
        self.assertFalse(any(r['estimated_total_api_cost_usd'] is not None
                             for r in data['model_scenarios']))
        data, _ = M['historical_segments']([self.root / 'missing'], PRICES,
                                           now=BASE, policies=POLICIES, offsets=(0,))
        self.assertFalse(any(r['estimated_total_api_cost_usd'] is not None
                             for r in data['model_scenarios']))


class HistoricalCLI(HistoricalFixtures):
    # Keep the actual process offline, including regressions that accidentally
    # re-enter ordinary price/quota lookup or credential/default-root discovery.
    def test_cli_requires_explicit_roots_and_catalog(self):
        for args in ([], ['--root', str(self.root)], ['--price-catalog', str(CATALOG)]):
            proc = subprocess.run([sys.executable, str(SCRIPT), 'segments', 'historical', *args],
                                  capture_output=True, text=True)
            self.assertEqual(proc.returncode, 2, proc.stderr)

    def test_actual_cli_is_offline_and_never_reads_credentials(self):
        self.write('one.jsonl', self.basic())
        guard = Path(self.temp.name) / 'guard'
        guard.mkdir()
        (guard / 'sitecustomize.py').write_text('''import os, sys
# Windows asyncio creates a loopback socketpair for its internal wakeup pipe.
# Build that test infrastructure before forbidding every application connection.
# asyncio.run owns and closes this one precreated loop as usual.
import asyncio
_offline_loop = asyncio.new_event_loop()
asyncio.events.new_event_loop = lambda: _offline_loop

def forbid(event, args):
    if event in ('socket.connect', 'socket.getaddrinfo', 'subprocess.Popen', 'os.system'):
        raise AssertionError('Offline historical mode attempted ' + event)
    if event == 'open' and args and isinstance(args[0], (str, bytes)):
        name = os.path.basename(os.fsdecode(args[0]))
        if name in ('auth.json', 'credentials.json', '.env'):
            raise AssertionError('Historical mode attempted credential read')
sys.addaudithook(forbid)
''')
        fake_home = Path(self.temp.name) / 'fake-home'
        fake_home.mkdir()
        (fake_home / 'auth.json').write_text('PRIVATE_CREDENTIAL_SENTINEL')
        env = dict(os.environ, PYTHONPATH=str(guard), CODEX_HOME=str(fake_home), HOME=str(fake_home))
        proc = subprocess.run([sys.executable, str(SCRIPT), 'segments', 'historical',
            '--root', str(self.root), '--price-catalog', str(CATALOG), '--json'],
            capture_output=True, text=True, env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(data['kind'], 'historical_heuristic_quota_scenarios')
        self.assertNotIn('PRIVATE_', proc.stdout)
        self.assertFalse(proc.stderr, proc.stderr)

    def test_dispatch_does_not_call_live_prices_quota_or_default_roots(self):
        self.write('one.jsonl', self.basic())
        forbidden = AsyncMock(side_effect=AssertionError('online route forbidden'))
        with patch.dict(G, {'fetch_live': forbidden, 'load_price_catalog_async': forbidden}):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                code = asyncio.run(M['async_main'](['segments', 'historical', '--root', str(self.root),
                                                   '--price-catalog', str(CATALOG), '--json']))
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())['kind'], 'historical_heuristic_quota_scenarios')
        forbidden.assert_not_called()

    def test_normal_report_and_one_frame_watch_keep_production_usage(self):
        self.write('one.jsonl', self.basic())
        catalog = M['parse_price_catalog'](json.loads(CATALOG.read_text()), 'fixture')
        for command in ('report', 'watch'):
            with self.subTest(command=command):
                with patch.dict(G, {'load_price_catalog_async': AsyncMock(return_value=catalog),
                                    'fetch_live': AsyncMock(side_effect=AssertionError('quota disabled'))}):
                    with contextlib.redirect_stdout(io.StringIO()) as output:
                        code = asyncio.run(M['async_main']([command, '--root', str(self.root),
                             '--days', 'all', '--quota', 'off', '--json', '--count', '1']))
                self.assertEqual(code, 0)
                data = json.loads(output.getvalue())
                self.assertEqual(data['summary']['usage']['input_tokens'], 15_000_000)
                self.assertNotIn('model_scenarios', data)
                self.assertNotIn('historical_heuristic', json.dumps(data))


if __name__ == '__main__':
    unittest.main()
