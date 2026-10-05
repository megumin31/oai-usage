"""Synthetic oracle tests for the opt-in one-sided contamination experiment.

For each invented block, displayed quota approximates y = a*C + e, e >= 0.
Here a is percentage points per API-equivalent dollar, so B = 100/a.
The generator knows a and e; the estimator receives ordinary metadata only.
No real model, account, credential, price download, or user log is consulted.
"""
import asyncio
import contextlib
from datetime import timedelta
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from test_historical_segments import (BASE, RESET, CATALOG, M, G, meta, context,
                                      quota, receipt, usage, stamp)

ASTRA = 'gpt-6-astra'
OTHER = 'gpt-6-sol'
PRICES = {m: M['Price'](Decimal('1'), Decimal('.2'), Decimal('2'), Decimal('1.5'))
          for m in (ASTRA, OTHER)}


def oracle_trace(costs, *, a='0.1', external=None, initial='10', step=600,
                 model=ASTRA, rounding=None, owner='one'):
    """Create ordinary local records, retaining hidden truth separately.

    a and external are never written into quota, model context, or receipts.
    Endpoint quantization uses one fixed rule, not independent per-edge errors.
    """
    costs = [Decimal(str(value)) for value in costs]
    coefficient = Decimal(str(a))
    external = [Decimal(str(value)) for value in (external or [0] * len(costs))]
    if len(external) != len(costs) or any(value < 0 for value in external):
        raise ValueError('The synthetic one-sided oracle needs nonnegative contamination per interval')
    latent = Decimal(str(initial))
    def display(value):
        return float(value if rounding is None else value.quantize(Decimal('1'), rounding=rounding))
    records = [meta(owner), context(model), quota(0, display(latent))]
    truth, cumulative = [], 0
    for i, (cost, pollution) in enumerate(zip(costs, external), 1):
        amount = cost * 1_000_000
        if amount != amount.to_integral_value():
            raise ValueError('Synthetic costs must map exactly to whole input tokens')
        cumulative += int(amount)
        if amount:
            records.append(receipt(f'r{i}', (i - .5) * step, int(amount),
                                   owner=owner, cumulative=cumulative))
        before = latent
        latent += coefficient * cost + pollution
        if not 0 <= latent < 100:
            raise ValueError('Choose a trace that stays inside one unsaturated quota window')
        records.append(quota(i * step, display(latent)))
        truth.append({'cost_usd': float(cost), 'external_pp': float(pollution),
                      'actual_delta_pp': float(latent - before), 'actual_used_pp': float(latent)})
    return records, {'a_pp_per_usd': float(coefficient), 'true_B_usd': float(100 / coefficient),
                     'intervals': truth}


class RobustFixtures(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'logs'
        self.root.mkdir()

    def write(self, name, records):
        path = self.root / name
        path.write_text(''.join(json.dumps(row) + '\n' for row in records), encoding='utf-8')
        return path


class OneSidedOracle(unittest.TestCase):
    def test_proportional_variable_pollution_is_observationally_indistinguishable(self):
        costs = [10, 20, 30, 15, 25, 10, 20, 30, 15, 25]
        clean, clean_truth = oracle_trace(costs, a='.1')
        contaminated, polluted_truth = oracle_trace(costs, a='.06', external=[Decimal('.04') * c for c in costs])
        self.assertEqual(clean, contaminated)
        self.assertNotEqual(clean_truth['true_B_usd'], polluted_truth['true_B_usd'])
        self.assertGreater(len({r['external_pp'] for r in polluted_truth['intervals']}), 1)
        self.assertTrue(all(r['external_pp'] > 0 for r in polluted_truth['intervals']))

    def test_constant_full_pollution_also_has_an_equivalent_clean_world(self):
        clean, truth_a = oracle_trace([20] * 12, a='.1')
        contaminated, truth_b = oracle_trace([20] * 12, a='.05', external=[1] * 12)
        self.assertEqual(clean, contaminated)
        self.assertEqual(truth_a['true_B_usd'], 1000)
        self.assertEqual(truth_b['true_B_usd'], 2000)

    def test_integer_display_ratio_is_not_an_unconditional_capacity_lower_bound(self):
        records, truth = oracle_trace([1], a='1.99', initial=0, rounding=ROUND_FLOOR)
        observations = [r['payload']['rate_limits']['secondary']['used_percent']
                        for r in records if r['type'] == 'event_msg']
        naive = 100 / (observations[-1] - observations[0])
        self.assertEqual(observations, [0, 1])
        self.assertEqual(naive, 100)
        self.assertLess(truth['true_B_usd'], 51)
        self.assertGreater(naive, truth['true_B_usd'])


class RobustCLI(RobustFixtures):
    def test_cli_requires_explicit_roots_and_local_catalog(self):
        script = Path(__file__).resolve().parents[1] / 'oai-usage'
        for args in ([], ['--root', str(self.root)], ['--price-catalog', str(CATALOG)]):
            run = subprocess.run([sys.executable, '-B', str(script), 'segments', 'robust', *args],
                                 capture_output=True, text=True, timeout=20)
            self.assertEqual(run.returncode, 2, run.stderr)

    def test_actual_cli_forbids_network_processes_and_credential_reads(self):
        records, _ = oracle_trace([40] * 18, initial=0, step=900)
        self.write('trace.jsonl', records)
        guard = Path(self.temp.name) / 'guard'
        guard.mkdir()
        (guard / 'sitecustomize.py').write_text('''import os, sys
# Windows asyncio creates a loopback socketpair for its internal wakeup pipe.
# Build that test infrastructure before forbidding every application connection.
# asyncio.run owns and closes this one precreated loop as usual.
import asyncio
_offline_loop = asyncio.new_event_loop()
asyncio.events.new_event_loop = lambda: _offline_loop

def prohibit(event, args):
    if event in ('socket.connect', 'socket.getaddrinfo', 'socket.bind', 'socket.sendto',
                  'subprocess.Popen', 'os.system', 'os.exec', 'os.posix_spawn'):
        raise AssertionError('Offline robust analysis attempted ' + event)
    if event == 'open' and args and isinstance(args[0], (str, bytes)):
        if os.path.basename(os.fsdecode(args[0])) in ('auth.json', 'credentials.json', '.env'):
            raise AssertionError('Offline robust analysis attempted a credential read')
sys.addaudithook(prohibit)
''')
        fake_home = Path(self.temp.name) / 'fake-home'
        fake_home.mkdir()
        (fake_home / 'auth.json').write_text('SYNTHETIC_CREDENTIAL_SENTINEL')
        env = dict(os.environ, PYTHONPATH=str(guard), PYTHONDONTWRITEBYTECODE='1',
                   CODEX_HOME=str(fake_home), HOME=str(fake_home))
        script = Path(__file__).resolve().parents[1] / 'oai-usage'
        run = subprocess.run([sys.executable, '-B', str(script), 'segments', 'robust',
                              '--root', str(self.root), '--price-catalog', str(CATALOG), '--json'],
                             capture_output=True, text=True, env=env, timeout=30)
        self.assertEqual(run.returncode, 0, run.stderr)
        result = json.loads(run.stdout)
        self.assertIn('robust', result['kind'])
        self.assertNotIn('SYNTHETIC_CREDENTIAL_SENTINEL', run.stdout)
        self.assertFalse(run.stderr)

    def test_dispatch_does_not_reenter_online_quota_or_price_routes(self):
        records, _ = oracle_trace([40] * 18, initial=0, step=900)
        self.write('trace.jsonl', records)
        forbidden = AsyncMock(side_effect=AssertionError('Online route is forbidden'))
        with patch.dict(G, {'fetch_live': forbidden, 'load_price_catalog_async': forbidden}):
            with contextlib.redirect_stdout(io.StringIO()) as output:
                code = asyncio.run(M['async_main'](['segments', 'robust', '--root', str(self.root),
                                                   '--price-catalog', str(CATALOG), '--json']))
        self.assertEqual(code, 0)
        self.assertIn('robust', json.loads(output.getvalue())['kind'])
        forbidden.assert_not_called()


POLICIES = ({'id': 'test900', 'target_span_seconds': 900, 'min_span_seconds': 900,
             'max_gap_seconds': 900, 'min_delta_percent': 0},)


class RobustSegments(RobustFixtures):
    def analyze(self, **kwargs):
        options = dict(now=BASE + timedelta(hours=12), policies=POLICIES, offsets=(0,))
        options.update(kwargs)
        return M['robust_segments']([self.root], PRICES, **options)[0]

    def make(self, costs=None, **kwargs):
        records, truth = oracle_trace([40] * 18 if costs is None else costs,
                                      initial=0, step=900, **kwargs)
        self.write('trace.jsonl', records)
        return self.analyze(), truth

    def scenarios(self, data, model=ASTRA):
        return [r for r in data['model_scenarios'] if r['profile']['model'] == model]

    def scenario(self, data, model=ASTRA):
        row, = self.scenarios(data, model)
        return row

    def assert_no_primary(self, row):
        self.assertIsNone(row['estimated_total_api_cost_usd'])
        self.assertEqual(row['total_conditional_range_usd'], {'lower': None, 'upper': None})
        self.assertIsNone(row['remaining']['estimated_remaining_api_cost_usd'])

    def test_repeated_clean_blocks_recover_conditioned_rate_and_frozen_holdout(self):
        data, truth = self.make()
        row = self.scenario(data)
        self.assertAlmostEqual(row['candidate']['a_percent_per_usd'], truth['a_pp_per_usd'])
        self.assertAlmostEqual(row['candidate']['total_api_cost_usd'], truth['true_B_usd'])
        self.assertEqual(len(row['split']['training_block_ids']), 12)
        self.assertEqual(len(row['split']['purged_block_ids']), 1)
        self.assertEqual(len(row['split']['holdout_block_ids']), 5)
        for fit in row['fits'].values():
            self.assertGreaterEqual(fit['training']['independent_support_count'], 3)
            self.assertGreaterEqual(fit['training']['effective_support'], 3)
            self.assertTrue(fit['training']['two_halves_supported'])
            self.assertGreaterEqual(fit['holdout']['independent_support_count'], 2)
            self.assertTrue(fit['holdout']['joint_feasible'])
        self.assertAlmostEqual(row['estimated_total_api_cost_usd'], truth['true_B_usd'])
        self.assertIsNone(row['strict_validation']['estimated_total_api_cost_usd'])
        self.assertEqual(row['frontier']['confidence_label'], 'provisional_conditional')
        self.assertIsNone(row['remaining']['estimated_remaining_api_cost_usd'])
        self.assertIn('unverified_snapshot_freshness', row['remaining']['reasons'])

    def test_minority_nonnegative_contamination_does_not_raise_lower_clean_rate(self):
        pollution = [6 if i in (2, 7, 14) else 0 for i in range(18)]
        data, truth = self.make(external=pollution)
        row = self.scenario(data)
        self.assertAlmostEqual(row['candidate']['a_percent_per_usd'], truth['a_pp_per_usd'], delta=.015)
        self.assertGreater(row['fits']['base']['holdout']['positive_excess_percent'], 0)
        self.assertEqual(row['fits']['base']['holdout']['negative_residual_count'], 0)
        self.assertLess(row['fits']['base']['training']['support_weight_fraction'], 1)
        self.assertGreaterEqual(row['fits']['base']['training']['support_weight_fraction'], .25)
        naive_rate = sum(r['actual_delta_pp'] for r in truth['intervals']) / sum(r['cost_usd'] for r in truth['intervals'])
        self.assertGreater(naive_rate, row['candidate']['a_percent_per_usd'])

    def test_all_polluted_equivalent_world_cannot_be_claimed_as_verified_truth(self):
        costs = [20, 30, 40, 50] * 5
        clean, clean_truth = oracle_trace(costs, a='.1', initial=0, step=900)
        all_polluted, contaminated_truth = oracle_trace(costs, a='.06',
            external=[Decimal('.04') * c for c in costs], initial=0, step=900)
        self.assertEqual(clean, all_polluted)
        self.write('trace.jsonl', all_polluted)
        data = self.analyze()
        row = self.scenario(data)
        self.assertAlmostEqual(row['candidate']['a_percent_per_usd'], .1, delta=.015)
        self.assertNotAlmostEqual(row['candidate']['a_percent_per_usd'], contaminated_truth['a_pp_per_usd'])
        protocol = json.dumps(data['protocol']).lower()
        self.assertIn('clean', protocol)
        self.assertTrue('assum' in protocol or 'conditional' in protocol)
        self.assertNotEqual(clean_truth['true_B_usd'], contaminated_truth['true_B_usd'])
        self.assertNotEqual(row['status'], 'verified')
        self.assertIsNone(row['remaining']['estimated_remaining_api_cost_usd'])

    def test_1_99_to_displayed_one_is_not_promoted_to_a_capacity_lower_bound(self):
        records, truth = oracle_trace([1], a='1.99', initial=0, rounding=ROUND_FLOOR, step=900)
        self.write('trace.jsonl', records)
        data = self.analyze()
        row = self.scenario(data)
        self.assert_no_primary(row)
        self.assertLess(truth['true_B_usd'], 100)
        # No data-derived claim of B >= naive C/q is valid under this rounding.
        self.assertNotIn('unconditional_lower_bound', json.dumps(row).lower())

    def test_duplicate_files_and_same_time_observers_do_not_multiply_support(self):
        records, _ = oracle_trace([40] * 18, initial=0, step=900)
        self.write('trace.jsonl', records)
        before = self.scenario(self.analyze())
        self.write('copy.jsonl', records)
        quota_only = [r for r in records if r['type'] == 'event_msg']
        self.write('observer.jsonl', [meta('observer'), *quota_only])
        after = self.scenario(self.analyze())
        self.assertEqual(before['candidate'], after['candidate'])
        self.assertEqual(before['split'], after['split'])
        self.assertEqual(before['fits'], after['fits'])

    def test_training_and_holdout_do_not_share_an_observation_after_purge(self):
        data, _ = self.make()
        row = self.scenario(data)
        blocks = {b['id']: b for b in data['blocks']}
        train_ids = row['split']['training_block_ids']
        held_ids = row['split']['holdout_block_ids']
        def endpoints(ids):
            return {blocks[bid][which]['id'] for bid in ids for which in ('before', 'after')}
        self.assertFalse(endpoints(train_ids) & endpoints(held_ids))
        self.assertFalse(set(train_ids) & set(held_ids))
        self.assertLessEqual(max(M['timestamp'](blocks[i]['after']['observed_at']) for i in train_ids),
                             min(M['timestamp'](blocks[i]['before']['observed_at']) for i in held_ids))
        ordered = sorted([blocks[i] for i in train_ids + row['split']['purged_block_ids'] + held_ids],
                         key=lambda b: b['before']['observed_at'])
        for a, b in zip(ordered, ordered[1:]):
            self.assertLessEqual(M['timestamp'](a['after']['observed_at']), M['timestamp'](b['before']['observed_at']))

    def test_holdout_drift_cannot_refit_training_coefficient(self):
        records, _ = oracle_trace([40] * 18, initial=0, step=900)
        self.write('trace.jsonl', records)
        baseline = self.scenario(self.analyze())
        for item in records:
            if item['type'] == 'event_msg':
                index = round((M['timestamp'](item['timestamp']) - BASE).total_seconds() / 900)
                if index > 12:
                    item['payload']['rate_limits']['secondary']['used_percent'] = 48 + .4 * (index - 12)
        self.write('trace.jsonl', records)
        changed = self.scenario(self.analyze())
        self.assertEqual(baseline['split'], changed['split'])
        for key in ('base', 'settlement_stress'):
            self.assertEqual(baseline['fits'][key]['candidate'], changed['fits'][key]['candidate'])
            self.assertEqual(baseline['fits'][key]['selected_block_ids'], changed['fits'][key]['selected_block_ids'])
        self.assertGreater(changed['fits']['base']['holdout']['negative_residual_count'], 0)
        self.assert_no_primary(changed)
        self.assertIsNotNone(changed['candidate']['total_api_cost_usd'])

    def test_every_predefined_policy_offset_is_retained(self):
        records, _ = oracle_trace([20] * 36, initial=0, step=300)
        self.write('trace.jsonl', records)
        data = self.analyze(policies=None, offsets=(-60, 0, 60))
        variants = {(r['policy_id'], r['alignment_offset_seconds']) for r in self.scenarios(data)}
        self.assertEqual(len(variants), 9)
        self.assertEqual({offset for _, offset in variants}, {-60, 0, 60})
        self.assertTrue(any(r['candidate']['total_api_cost_usd'] is not None for r in self.scenarios(data)))
        self.assertTrue(all(r['sensitivity']['missing_variants'] == [] for r in self.scenarios(data)))

    def test_missing_scheme_evidence_is_explicit_in_sensitivity(self):
        records, _ = oracle_trace([20] * 36, initial=0, step=300)
        self.write('trace.jsonl', records)
        original = G['historical_segments']
        # Exercise the robust assembler's explicit missing-upstream-evidence
        # contract without inventing an estimate for an unavailable scheme.
        def unavailable(*args, **kwargs):
            data, paths = original(*args, **kwargs)
            for key in ('attempts', 'model_scenarios'):
                data[key] = [r for r in data[key] if not
                             (r['policy_id'] == 'block_30m' and r['alignment_offset_seconds'] == 60)]
            return data, paths
        with patch.dict(G, historical_segments=unavailable):
            data = self.analyze(policies=None, offsets=(-60, 0, 60))
        rows = self.scenarios(data)
        self.assertEqual(len(rows), 8)
        for row in rows:
            self.assertEqual(row['sensitivity']['expected_variants'], 9)
            self.assertEqual(row['sensitivity']['missing_variants'], [
                {'policy_id': 'block_30m', 'alignment_offset_seconds': 60,
                 'reason': 'no_quota_paired_profile_evidence'}])

    def test_mixed_model_grid_has_one_owner_without_cost_apportionment(self):
        # Authorized semantic replacement: robust now selects one target by
        # event-count ownership; ordinary historical retains strict rejection.
        records, _ = oracle_trace([40] * 18, initial=0, step=900)
        mixed = []
        for item in records:
            if item['type'] == 'event_msg' and item['timestamp'] == stamp(900):
                mixed.extend([context(OTHER), receipt('other', 800, 40_000_000), context(ASTRA)])
            mixed.append(item)
        self.write('trace.jsonl', mixed)
        data = self.analyze()
        mixed, = [b for b in data['blocks'] if b['status'] == 'eligible' and b['nuisance_local_event_count']]
        self.assertEqual(mixed['profile']['model'], ASTRA)
        self.assertEqual(mixed['known_api_cost_usd'], 40)
        self.assertEqual(mixed['nuisance_local_known_api_cost_usd'], 40)
        self.assertEqual(mixed['total_local_known_api_cost_usd'], 80)
        self.assertEqual(mixed['delta_percent'], 4)
        self.assertEqual(sum(b['delta_percent'] for b in data['blocks'] if b['status'] == 'eligible'), 72)
        self.assertTrue(mixed['ownership']['quota_delta_is_not_apportioned'])
        self.assertFalse(any(r['estimated_total_api_cost_usd'] is not None for r in self.scenarios(data, OTHER)))

    def test_metadata_only_never_retains_conversation_or_tool_content(self):
        records, _ = oracle_trace([40] * 18, initial=0, step=900)
        records.append({'type': 'response_item', 'payload': {'content': 'PRIVATE_PROMPT_SENTINEL',
                                                           'arguments': 'PRIVATE_TOOL_SENTINEL'}})
        path = self.write('trace.jsonl', records)
        before = path.read_bytes()
        data = self.analyze()
        self.assertNotIn('PRIVATE_', json.dumps(data))
        self.assertEqual(path.read_bytes(), before)

    def test_current_without_freshness_never_borrows_a_published_remaining_value(self):
        data, _ = self.make()
        row = self.scenario(data)
        self.assertIsNotNone(row['candidate']['total_api_cost_usd'])
        self.assertIsNone(row['remaining']['estimated_remaining_api_cost_usd'])
        self.assertIn('unverified_snapshot_freshness', row['remaining']['reasons'])
        self.assertNotEqual(row['status'], 'verified')

    def test_zero_delta_blocks_keep_their_positive_cost_in_fit_support(self):
        records, truth = oracle_trace([20] * 36, a='.02', initial=0, step=450,
                                      rounding=ROUND_HALF_UP)
        self.write('trace.jsonl', records)
        data = self.analyze()
        row = self.scenario(data)
        zeros = [b for b in data['blocks'] if b['status'] == 'eligible' and b['delta_percent'] == 0]
        self.assertTrue(zeros)
        self.assertTrue(all(b['known_api_cost_usd'] > 0 for b in zeros))
        training = set(row['split']['training_block_ids'])
        zero_training = {b['id'] for b in zeros} & training
        self.assertTrue(zero_training)
        self.assertTrue(zero_training <= set(row['fits']['base']['selected_block_ids']))
        self.assertAlmostEqual(row['candidate']['a_percent_per_usd'], truth['a_pp_per_usd'], delta=.004)
        self.assertEqual(data['local_usage']['known_api_cost_usd'], 720)

    def test_zero_cost_receipt_retains_shared_endpoint_path_without_rate_support(self):
        records, _ = oracle_trace([40] * 18, initial=0, step=900)
        # A wholly cached receipt has real tokens but zero price in this invented
        # catalog. Its quota movement can be external; the path edge still exists.
        record = next(r for r in records if r['type'] == 'token_usage_record'
                      and r['payload']['response_id'] == 'r6')
        record['payload']['usage']['cached_input_tokens'] = 40_000_000
        self.write('trace.jsonl', records)
        prices = {ASTRA: M['Price'](Decimal('1'), Decimal('0'), Decimal('2'), Decimal('1.5'))}
        data = M['robust_segments']([self.root], prices, now=BASE + timedelta(hours=12),
                                    policies=POLICIES, offsets=(0,))[0]
        row = self.scenario(data)
        zero, = [b for b in data['blocks'] if b['status'] == 'eligible'
                  and b['known_api_cost_usd'] == 0]
        self.assertIn(zero['id'], row['split']['training_block_ids'])
        for fit in row['fits'].values():
            self.assertEqual(fit['rate_interval']['chain_count'], 1)
            self.assertNotIn(zero['id'], fit['selected_block_ids'])
            self.assertNotIn(zero['id'], fit['training']['capped_weights'])

    def test_no_receipt_zero_cost_interval_still_connects_observation_path(self):
        # Same exact epoch and model context, but no local receipt in the middle
        # interval. Its zero cost/zero quota movement is still a shared edge.
        records, _ = oracle_trace([100, 0, 100], a='.01', initial=10, step=900)
        self.write('trace.jsonl', records)
        data = self.analyze()
        row = self.scenario(data)
        zero, = [b for b in data['blocks'] if b['known_api_cost_usd'] == 0
                  and b['before']['observed_at'] == (BASE + timedelta(seconds=900)).isoformat()
                  and b['after']['observed_at'] == (BASE + timedelta(seconds=1800)).isoformat()]
        self.assertEqual(zero['status'], 'eligible')
        self.assertEqual(zero['profile']['model'], ASTRA)
        self.assertEqual(zero['delta_percent'], 0)
        self.assertIn(zero['id'], row['split']['training_block_ids'])
        self.assertEqual(row['counts']['zero_cost_blocks'], 1)
        for fit in row['fits'].values():
            self.assertEqual(fit['rate_interval']['chain_count'], 1)
            self.assertNotIn(zero['id'], fit['selected_block_ids'])

    def test_independent_fragment_quantization_does_not_average_down_like_shared_endpoints(self):
        data, _ = self.make()
        continuous = self.scenario(data)['fits']['base']['rate_interval']
        records = [meta(), context(ASTRA), quota(0, 0)]
        cumulative, used = 0, 0
        for i in range(18):
            start = i * 1800
            cumulative += 40_000_000
            records.extend([context(ASTRA), receipt(f'clean{i}', start + 450, 40_000_000,
                                                    cumulative=cumulative)])
            used += 4
            records.append(quota(start + 900, used))
            if i < 17:
                cumulative += 1_000_000
                records.extend([context('unpriced-gap-model'),
                                receipt(f'gap{i}', start + 1350, 1_000_000, cumulative=cumulative),
                                quota(start + 1800, used)])
        self.write('trace.jsonl', records)
        fragmented = self.scenario(self.analyze())['fits']['base']['rate_interval']
        self.assertIsNotNone(continuous['lower'])
        self.assertIsNotNone(fragmented['lower'])
        self.assertGreater(fragmented['upper'] - fragmented['lower'],
                           continuous['upper'] - continuous['lower'])

    def test_many_tiny_polluted_blocks_do_not_outvote_large_cost_support(self):
        costs, external = [], []
        for _ in range(6):
            costs.append(400)
            external.append(0)
            costs.extend([Decimal('.1')] * 10)
            external.extend([Decimal('.999')] * 10)
        records, truth = oracle_trace(costs, a='.01', external=external, initial=0, step=900)
        self.write('trace.jsonl', records)
        data = self.analyze(now=BASE + timedelta(days=1))
        row = self.scenario(data)
        self.assertAlmostEqual(row['candidate']['a_percent_per_usd'], truth['a_pp_per_usd'], delta=.002)
        self.assertLess(row['candidate']['a_percent_per_usd'], 1)
        # Sixty blocks have q/C=10; the six larger clean blocks have q/C=.01.
        # An unweighted 25th-percentile would choose the polluted population.
        self.assertGreater(len(costs) - 6, .75 * len(costs))
        self.assertIsNotNone(row['candidate']['total_api_cost_usd'])

    def test_two_separated_rate_populations_are_not_one_verified_capacity(self):
        records, _ = oracle_trace([40] * 18, initial=0, step=900)
        used = 0
        for item in records:
            if item['type'] == 'event_msg':
                index = round((M['timestamp'](item['timestamp']) - BASE).total_seconds() / 900)
                if index:
                    used += 1 if index % 2 else 7
                    item['payload']['rate_limits']['secondary']['used_percent'] = used
        self.write('trace.jsonl', records)
        row = self.scenario(self.analyze())
        self.assert_no_primary(row)
        self.assertIsNotNone(row['candidate']['total_api_cost_usd'])
        self.assertGreater(len(row['fits']['base']['components']), 1)

    def test_chronological_rate_drift_keeps_only_provisional_conditional_frontier(self):
        records, _ = oracle_trace([40] * 18, initial=0, step=900)
        used = 0
        for item in records:
            if item['type'] == 'event_msg':
                index = round((M['timestamp'](item['timestamp']) - BASE).total_seconds() / 900)
                if index:
                    used += 1 + index / 3
                    item['payload']['rate_limits']['secondary']['used_percent'] = used
        self.write('trace.jsonl', records)
        row = self.scenario(self.analyze())
        self.assertIsNone(row['strict_validation']['estimated_total_api_cost_usd'])
        self.assertTrue(row['strict_validation']['reasons'])
        self.assertEqual(row['frontier']['confidence_label'], 'provisional_conditional')
        self.assertNotEqual(row['frontier']['historical_validation']['status'], 'later_recurrence_supported')

    def test_cache_mix_drift_keeps_conditional_frontier_and_explicit_warning(self):
        records, _ = oracle_trace([40] * 18, initial=0, step=900)
        for item in records:
            if item['type'] == 'token_usage_record':
                index = int(item['payload']['response_id'][1:])
                if index > 12:
                    item['payload']['usage']['cached_input_tokens'] = 32_000_000
        self.write('trace.jsonl', records)
        row = self.scenario(self.analyze())
        self.assertIsNone(row['strict_validation']['estimated_total_api_cost_usd'])
        self.assertIsNotNone(row['estimated_total_api_cost_usd'])
        self.assertTrue(row['strict_validation']['reasons'])
        self.assertEqual(row['frontier']['confidence_label'], 'provisional_conditional')
        self.assertIn('observed_workload_cache_mix_shift', row['frontier']['quality_flags'])

    def test_same_deadline_decrease_isolates_conditioned_fits_in_separate_epochs(self):
        records, _ = oracle_trace([40] * 18, initial=0, step=900)
        second, _ = oracle_trace([20] * 18, initial=0, step=900)
        for item in second[2:]:
            item['timestamp'] = (M['timestamp'](item['timestamp']) + timedelta(seconds=18000)).isoformat()
            if item['type'] == 'token_usage_record':
                item['payload']['response_id'] = 'second-' + item['payload']['response_id']
        self.write('trace.jsonl', records + second[2:])
        data = self.analyze(now=BASE + timedelta(days=1))
        rows = self.scenarios(data)
        self.assertEqual(len({r['quota_epoch_id'] for r in rows}), 2)
        self.assertEqual(len(rows), 2)
        blocks = {b['id']: b for b in data['blocks']}
        for row in rows:
            for fit in row['fits'].values():
                self.assertTrue(all(blocks[bid]['quota_epoch_id'] == row['quota_epoch_id']
                                    for bid in fit['selected_block_ids']))
            self.assertIsNone(row['remaining']['estimated_remaining_api_cost_usd'])
        self.assertIn('quota_decrease_ambiguous', json.dumps(data['quota_epochs']))

    def test_sparse_new_current_window_cannot_borrow_old_candidate(self):
        records, _ = oracle_trace([40] * 18, initial=0, step=900)
        new_at = 18000
        records.append(quota(new_at, 0, reset=RESET + new_at))
        self.write('trace.jsonl', records)
        data = self.analyze(now=BASE + timedelta(days=1))
        current = data['quota_epochs'][-1]['quota_epoch_id']
        self.assertTrue(data['quota_epochs'][-1]['supports_new_window'])
        self.assertFalse(any(r['quota_epoch_id'] == current and
                             (r['estimated_total_api_cost_usd'] is not None or
                              r['remaining']['estimated_remaining_api_cost_usd'] is not None)
                             for r in data['model_scenarios']))
        old = next(r for r in self.scenarios(data) if r['quota_epoch_id'] != current)
        self.assertIsNotNone(old['candidate']['total_api_cost_usd'])

    def test_old_window_return_cannot_reactivate_or_fit_retired_blocks(self):
        records, _ = oracle_trace([40] * 18, initial=0, step=900)
        records.extend([quota(18000, 0, reset=RESET + 18000), quota(18900, 72),
                        receipt('returned', 19350, 40_000_000), quota(19800, 76)])
        self.write('trace.jsonl', records)
        data = self.analyze(now=BASE + timedelta(days=1))
        quarantined = {e['quota_epoch_id'] for e in data['quota_epochs'] if e['status'] == 'quarantined'}
        self.assertTrue(quarantined)
        for row in self.scenarios(data):
            if row['quota_epoch_id'] in quarantined:
                self.assert_no_primary(row)
                self.assertIsNone(row['candidate']['total_api_cost_usd'])
            self.assertIsNone(row['remaining']['estimated_remaining_api_cost_usd'])

    def test_one_second_deadline_alias_does_not_merge_estimation_samples(self):
        records, _ = oracle_trace([40] * 18, initial=0, step=900)
        for item in records:
            if item['type'] == 'event_msg' and M['timestamp'](item['timestamp']) >= BASE + timedelta(seconds=9000):
                item['payload']['rate_limits']['secondary']['resets_at'] += 1
        self.write('trace.jsonl', records)
        data = self.analyze()
        rows = self.scenarios(data)
        self.assertEqual(len({r['quota_epoch_id'] for r in rows}), 2)
        self.assertIn('reset_alias_ambiguous', json.dumps(data['quota_epochs']))
        blocks = {b['id']: b for b in data['blocks']}
        for row in rows:
            self.assertTrue(all(blocks[bid]['quota_epoch_id'] == row['quota_epoch_id']
                                for bid in row['split']['training_block_ids'] + row['split']['holdout_block_ids']))

    def test_clean_coverage_across_lengths_and_offsets_can_publish_conditional_total(self):
        records, truth = oracle_trace([20] * 36, a='.1', initial=0, step=300)
        self.write('trace.jsonl', records)
        data = self.analyze(policies=None, offsets=(-60, 0, 60))
        published = [r for r in self.scenarios(data) if r['estimated_total_api_cost_usd'] is not None]
        self.assertGreaterEqual(len(published), 3)
        self.assertGreaterEqual(len({r['policy_id'] for r in published}), 2)
        self.assertGreaterEqual(len({r['alignment_offset_seconds'] for r in published}), 2)
        for row in published:
            self.assertAlmostEqual(row['estimated_total_api_cost_usd'], truth['true_B_usd'])
            self.assertIsNone(row['remaining']['estimated_remaining_api_cost_usd'])
            self.assertNotEqual(row['status'], 'verified')
            self.assertEqual(row['identifiability_assumptions'], data['protocol']['assumptions'])
            for fit in row['fits'].values():
                self.assertEqual(fit['identifiability_assumptions'], data['protocol']['assumptions'])

    def test_positive_holdout_residuals_need_repeated_near_clean_support(self):
        records, _ = oracle_trace([10] * 18, a='.3', initial=0, step=900)
        for item in records:
            if item['type'] == 'event_msg':
                index = round((M['timestamp'](item['timestamp']) - BASE).total_seconds() / 900)
                if index > 12:
                    item['payload']['rate_limits']['secondary']['used_percent'] = 36 + 6 * (index - 12)
        self.write('trace.jsonl', records)
        row = self.scenario(self.analyze())
        self.assertAlmostEqual(row['candidate']['a_percent_per_usd'], .3)
        self.assertEqual(row['fits']['base']['holdout']['negative_residual_count'], 0)
        self.assertGreater(row['fits']['base']['holdout']['positive_excess_percent'], 0)
        self.assertLess(row['fits']['base']['holdout']['independent_support_count'], 2)
        self.assertIsNone(row['strict_validation']['estimated_total_api_cost_usd'])
        self.assertAlmostEqual(row['estimated_total_api_cost_usd'], 100 / .3)
        self.assertEqual(row['frontier']['historical_validation']['status'], 'limited_later_recurrence')
        self.assertTrue(row['frontier']['historical_validation']['one_sided_compatible'])

    def test_missing_grid_endpoint_is_not_interpolated_or_bridged(self):
        records, _ = oracle_trace([40] * 18, initial=0, step=900)
        records = [r for r in records if not (r['type'] == 'event_msg' and r['timestamp'] == stamp(900))]
        self.write('trace.jsonl', records)
        data = self.analyze()
        eligible = [b for b in data['blocks'] if b['status'] == 'eligible']
        self.assertTrue(eligible)
        self.assertTrue(all(.8 * 900 <= b['span_seconds'] <= 1.2 * 900 for b in eligible))
        self.assertEqual(sum(b['known_api_cost_usd'] for b in eligible), 640)
        self.assertEqual(data['local_usage']['known_api_cost_usd'], 720)

    def test_profile_and_plan_changes_never_pool_block_support(self):
        records, _ = oracle_trace([40] * 18, initial=0, step=900)
        changed = []
        for item in records:
            if item['type'] == 'token_usage_record':
                index = int(item['payload']['response_id'][1:])
                changed.append(context(ASTRA, service_tier='fast' if index > 9 else 'standard',
                                       reasoning_effort='low' if index > 9 else 'high'))
            if item['type'] == 'event_msg' and M['timestamp'](item['timestamp']) >= BASE + timedelta(seconds=8100):
                item['payload']['rate_limits']['plan_type'] = 'pro'
            changed.append(item)
        self.write('trace.jsonl', changed)
        data = self.analyze()
        rows = self.scenarios(data)
        self.assertGreaterEqual(len(rows), 2)
        blocks = {b['id']: b for b in data['blocks']}
        for row in rows:
            for bid in row['split']['training_block_ids'] + row['split']['holdout_block_ids']:
                self.assertEqual(blocks[bid]['quota_epoch_id'], row['quota_epoch_id'])
                self.assertEqual(blocks[bid]['profile'], row['profile'])

    def test_future_full_input_cannot_publish_at_earlier_analysis_clock(self):
        records, _ = oracle_trace([20] * 36, a='.1', initial=0, step=300)
        self.write('trace.jsonl', records)
        data = self.analyze(policies=None, offsets=(-60, 0, 60), now=BASE + timedelta(seconds=6000))
        self.assertEqual(data['local_usage']['known_api_cost_usd'], 720)
        candidates = [r for r in self.scenarios(data) if r['candidate']['total_api_cost_usd'] is not None]
        self.assertTrue(candidates)
        for row in candidates:
            self.assert_no_primary(row)
        self.assertRegex(json.dumps(candidates).lower(), r'future|analysis_time')

    def test_missing_early_exposure_moves_frozen_workload_holdout_without_reordering(self):
        records, _ = oracle_trace([40] * 18, initial=0, step=900)
        self.write('trace.jsonl', records)
        baseline = self.scenario(self.analyze())
        missing = {stamp(t) for t in (900, 1800, 2700, 3600)}
        records = [r for r in records if not (r['type'] == 'event_msg' and r['timestamp'] in missing)]
        self.write('trace.jsonl', records)
        data = self.analyze()
        changed = self.scenario(data)
        self.assertEqual(M['timestamp'](baseline['split']['cutoff_at']), BASE + timedelta(seconds=10800))
        # Revision 3 partitions actual target exposure, not inactive epoch time.
        # Missing five early 15m intervals leaves 195m: 2/3 falls at t=205m.
        cutoff = BASE + timedelta(seconds=12300)
        self.assertEqual(M['timestamp'](changed['split']['cutoff_at']), cutoff)
        self.assertEqual(changed['split']['observed_exposure_seconds'], 11700)
        self.assertEqual(changed['split']['basis'], 'canonical_union_of_eligible_target_workload_exposure')
        blocks = {b['id']: b for b in data['blocks']}
        self.assertTrue(all(M['timestamp'](blocks[bid]['after']['observed_at']) <= cutoff
                            for bid in changed['split']['training_block_ids']))
        self.assertTrue(all(M['timestamp'](blocks[bid]['before']['observed_at']) > cutoff
                            for bid in changed['split']['holdout_block_ids']))


class SharedErrorOracle(unittest.TestCase):
    @staticmethod
    def blocks(deltas, cost=10):
        used, result = 10, []
        for i, delta in enumerate(deltas):
            before = {'id': f's{i}', 'used_percent': used}
            used += delta
            after = {'id': f's{i+1}', 'used_percent': used}
            result.append({'id': f'b{i}', 'before': before, 'after': after,
                           'known_api_cost_usd': cost, 'delta_percent': delta})
        return result

    def fit_blocks(self, deltas):
        blocks = self.blocks(deltas, cost=40)
        for i, block in enumerate(blocks):
            block['span_seconds'] = 900
            block['before']['observed_at'] = stamp(i * 900)
            block['after']['observed_at'] = stamp((i + 1) * 900)
        return blocks

    def test_empty_fit_consistency_is_not_evaluable_not_a_contradiction(self):
        fit = M['robust_fit_blocks']([], [], [], envelope=1, target_span=900)
        self.assertIsNone(fit['candidate']['a_percent_per_usd'])
        self.assertIsNone(fit['holdout']['joint_feasible'])
        self.assertIsNone(fit['all_blocks_one_sided_feasible'])
        self.assertEqual(fit['holdout']['consistency_assessment'], 'not_evaluable')
        self.assertEqual(fit['all_blocks_consistency_assessment'], 'not_evaluable')
        self.assertNotIn('one_sided_model_contradiction', fit['reasons'])
        self.assertNotIn('holdout_shared_endpoint_infeasible', fit['reasons'])
        self.assertIn('no_positive_candidate_rate', fit['reasons'])

    def test_candidate_without_holdout_does_not_claim_holdout_failure_or_success(self):
        blocks = self.fit_blocks([4] * 12)
        fit = M['robust_fit_blocks'](blocks, [], blocks, envelope=1, target_span=900)
        self.assertAlmostEqual(fit['candidate']['a_percent_per_usd'], .1)
        self.assertIsNone(fit['holdout']['joint_feasible'])
        self.assertEqual(fit['holdout']['consistency_assessment'], 'not_evaluable')
        self.assertTrue(fit['all_blocks_one_sided_feasible'])
        self.assertEqual(fit['all_blocks_consistency_assessment'], 'feasible')
        self.assertNotIn('holdout_shared_endpoint_infeasible', fit['reasons'])
        self.assertIn('insufficient_holdout_endpoint_disjoint_support', fit['reasons'])

    def test_observed_contradiction_remains_infeasible_when_candidate_exists(self):
        blocks = self.fit_blocks([4] * 12 + [0] * 6)
        fit = M['robust_fit_blocks'](blocks[:12], blocks[12:], blocks, envelope=1, target_span=900)
        self.assertAlmostEqual(fit['candidate']['a_percent_per_usd'], .1)
        self.assertFalse(fit['holdout']['joint_feasible'])
        self.assertFalse(fit['all_blocks_one_sided_feasible'])
        self.assertEqual(fit['holdout']['consistency_assessment'], 'infeasible')
        self.assertEqual(fit['all_blocks_consistency_assessment'], 'infeasible')
        self.assertIn('one_sided_model_contradiction', fit['reasons'])
        self.assertIn('holdout_shared_endpoint_infeasible', fit['reasons'])

    def test_all_subchains_sharpen_bounds_without_treating_errors_as_independent(self):
        blocks = self.blocks([1, 2] * 8)
        result = M['robust_rate_interval'](blocks, {b['id'] for b in blocks}, 1)
        # The tight 15-edge subchains give [.14/(1+rho), .16].
        # These are algebraic feasible bounds, not an interval divided by sqrt(N).
        self.assertTrue(result['feasible'])
        self.assertAlmostEqual(result['lower'], .14 / 1.05)
        self.assertAlmostEqual(result['upper'], .16)
        self.assertEqual(result['chain_count'], 1)

    def test_dirty_edges_keep_one_sided_constraint_without_getting_clean_lower_bound(self):
        blocks = self.blocks([1, 2] * 8)
        low_ids = {b['id'] for b in blocks if b['delta_percent'] == 1}
        result = M['robust_rate_interval'](blocks, low_ids, 1)
        self.assertTrue(result['feasible'])
        self.assertEqual(result['lower'], 0)
        self.assertAlmostEqual(result['upper'], .16)
        disconnected = [b for b in blocks if b['id'] in low_ids]
        loose = M['robust_rate_interval'](disconnected, low_ids, 1)
        self.assertEqual(loose['chain_count'], 8)
        self.assertEqual(loose['lower'], 0)
        self.assertAlmostEqual(loose['upper'], .3)

    def test_positive_cost_zero_delta_chain_still_constrains_slope(self):
        blocks = self.blocks([0] * 16)
        result = M['robust_rate_interval'](blocks, set(), 1)
        self.assertTrue(result['feasible'])
        self.assertEqual(result['lower'], 0)
        self.assertAlmostEqual(result['upper'], 2 / 160)

    def test_feasibility_is_invariant_to_cost_units_even_for_tiny_slopes(self):
        # The early and late six-edge subchains require incompatible rates.
        # Multiplying C by 10^12 divides both bounds by 10^12, but cannot make
        # a contradiction disappear through an absolute tolerance on a.
        for envelope in (1, 2):
            for cost in (1, 10**12):
                with self.subTest(envelope=envelope, cost=cost):
                    blocks = self.blocks([3] * 6 + [5] * 6, cost=cost)
                    result = M['robust_rate_interval'](blocks, {b['id'] for b in blocks}, envelope)
                    self.assertGreater(result['lower'], result['upper'])
                    self.assertFalse(result['feasible'])
