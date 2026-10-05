"""Metadata-only regression cases for the cloud quota extraction audit.

Every session and usage count below is invented. No real logs, chat bodies,
credentials, network, or statistical gate changes are involved.
"""
from datetime import timedelta
import json
from pathlib import Path
import tempfile
import unittest

from test_historical_segments import BASE, M, meta, context, quota, receipt, token
from test_robust_segments import ASTRA, OTHER, PRICES


POLICIES = ({'id': 'block_5m', 'target_span_seconds': 300,
             'min_span_seconds': 240, 'max_gap_seconds': 300,
             'min_delta_percent': 0},)


class CounterResetScope(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, name, records):
        path = self.root / name
        path.write_text(''.join(json.dumps(record) + '\n' for record in records))
        return path

    def owner(self):
        records = [meta('owner'), context(ASTRA), quota(0, 10)]
        cumulative = 0
        for grid in range(3):
            for j, second in enumerate((50, 150, 250)):
                cumulative += 1_000_000
                records.append(receipt(f'owner-{grid}-{j}', grid * 300 + second,
                                       1_000_000, owner='owner', cumulative=cumulative))
            records.append(quota((grid + 1) * 300, 13 + grid * 3))
        self.write('owner.jsonl', records)

    def companion(self, *, before=OTHER, after=OTHER, reset_at=180):
        records = [meta('companion')]
        if before is not None:
            records.append(context(before))
        records.append(token(75, 100, 100))
        if after is not None:
            records.append(context(after))
        records += [token(reset_at, 20, 20), token(500, 40, 20)]
        self.write('companion.jsonl', records)

    def analyze(self, *, robust=True, offsets=(0,)):
        options = dict(now=BASE + timedelta(days=1), policies=POLICIES, offsets=offsets)
        if robust:
            return M['robust_segments']([self.root], PRICES, **options)[0]
        return M['historical_segments']([self.root], PRICES,
            _block_span_seconds={'block_5m': 300}, _target_profile_ownership=True,
            _source_aware_epochs=True, **options)[0]

    @staticmethod
    def blocks(data):
        return data.get('blocks', data.get('attempts'))

    def test_known_other_model_reset_does_not_discard_complete_owner_grid(self):
        self.owner()
        original = self.blocks(self.analyze())
        self.companion()
        result = self.analyze()
        blocks = self.blocks(result)
        self.assertEqual([b['status'] for b in blocks], ['eligible'] * 3)
        self.assertEqual([b['known_api_cost_usd'] for b in blocks],
                         [b['known_api_cost_usd'] for b in original])
        self.assertTrue(all(b['profile']['model'] == ASTRA for b in blocks))
        self.assertEqual(sum(b['nuisance_local_event_count'] for b in blocks), 3)
        self.assertEqual(result['local_usage']['counter_resets'], 1)
        self.assertEqual([b['nuisance_counter_reset_count'] for b in blocks], [1, 0, 0])
        self.assertTrue(all('counter_reset' not in b['reasons'] for b in blocks))

    def test_nuisance_reset_on_boundary_does_not_discard_either_owner_grid(self):
        self.owner()
        self.companion(reset_at=300)
        blocks = self.blocks(self.analyze())
        self.assertEqual([b['status'] for b in blocks], ['eligible'] * 3)
        self.assertTrue(all(b['profile']['model'] == ASTRA for b in blocks))
        self.assertEqual([b['known_api_cost_usd'] for b in blocks], [3, 3, 3])
        self.assertEqual([b['nuisance_counter_reset_count'] for b in blocks], [1, 0, 0])

    def test_owner_reset_on_boundary_only_touches_half_open_cost_interval(self):
        records = [meta('owner'), context(ASTRA), quota(0, 10),
                   token(50, 100, 100), token(300, 20, 20), quota(300, 13),
                   token(500, 40, 20), quota(600, 16), token(800, 60, 20), quota(900, 19)]
        self.write('owner.jsonl', records)
        blocks = self.blocks(self.analyze())
        self.assertEqual(len(blocks), 3)
        self.assertIn('counter_reset', blocks[0]['reasons'])
        self.assertEqual(blocks[0]['status'], 'rejected')
        self.assertEqual([b['status'] for b in blocks[1:]], ['eligible', 'eligible'])
        self.assertAlmostEqual(sum(b['known_api_cost_usd'] for b in blocks), .00016)

    def test_owner_reset_half_open_rule_uses_shifted_cost_boundaries(self):
        for offset in (-60, 60):
            with self.subTest(offset=offset):
                reset_at = 300 + offset
                records = [meta('owner'), context(ASTRA), quota(0, 10),
                           token(75, 100, 100), token(reset_at, 20, 20), quota(300, 13),
                           token(500, 40, 20), quota(600, 16), token(800, 60, 20), quota(900, 19)]
                self.write('owner.jsonl', records)
                blocks = self.blocks(self.analyze(offsets=(offset,)))
                second = [b for b in blocks if b['fixed_grid'] and
                          b['fixed_grid']['grid_id'].endswith(':1')]
                self.assertEqual(len(second), 1)
                self.assertEqual(second[0]['status'], 'eligible')
                self.assertNotIn('counter_reset', second[0]['reasons'])

    def test_legacy_owner_model_replaced_by_receipt_still_prevents_nuisance_certification(self):
        self.owner()
        self.write('companion.jsonl', [meta('companion'), context(ASTRA),
            token(75, 100, 100), context(OTHER),
            receipt('matched-other-model', 75, 100, owner='companion', cumulative=100),
            token(180, 20, 20), token(500, 40, 20)])
        blocks = self.blocks(self.analyze())
        self.assertIn('counter_reset', blocks[0]['reasons'])
        self.assertEqual(blocks[0]['status'], 'rejected')

    def test_zero_unknown_snapshot_still_prevents_nuisance_certification(self):
        self.owner()
        self.write('companion.jsonl', [meta('companion'), token(25, 0, 0),
            context(OTHER), token(75, 100, 100), token(180, 20, 20), token(500, 40, 20)])
        blocks = self.blocks(self.analyze())
        self.assertIn('counter_reset', blocks[0]['reasons'])
        self.assertEqual(blocks[0]['status'], 'rejected')

    def test_reset_count_survives_atomic_interval_aggregation(self):
        self.owner()
        self.companion()
        self.write('more-quota.jsonl', [meta('samples'), quota(100, 11), quota(200, 12)])
        blocks = self.blocks(self.analyze())
        self.assertEqual(blocks[0]['atomic_interval_count'], 3)
        self.assertEqual(blocks[0]['status'], 'eligible')
        self.assertEqual(blocks[0]['nuisance_counter_reset_count'], 1)
        self.assertEqual(sum(b['nuisance_counter_reset_count'] for b in blocks), 1)

    def test_public_historical_counter_reset_boundary_behavior_remains_unchanged(self):
        self.write('owner.jsonl', [meta('owner'), context(ASTRA), quota(0, 10),
            token(50, 100, 100), token(300, 20, 20), quota(300, 13),
            token(500, 40, 20), quota(600, 16), token(800, 60, 20), quota(900, 19)])
        historical_policy = ({'id': 'test', 'min_span_seconds': 0,
                              'max_gap_seconds': 300, 'min_delta_percent': 0},)
        data = M['historical_segments']([self.root], PRICES,
            policies=historical_policy, offsets=(0,), now=BASE + timedelta(days=1))[0]
        rejected = [b for b in data['attempts'] if 'counter_reset' in b['reasons']]
        self.assertEqual(sum(b['atomic_interval_count'] for b in rejected), 2)

    def test_same_model_other_session_reset_remains_owner_relevant(self):
        self.owner()
        self.companion(before=ASTRA, after=ASTRA)
        blocks = self.blocks(self.analyze())
        self.assertIn('counter_reset', blocks[0]['reasons'])
        self.assertEqual(blocks[0]['status'], 'rejected')

    def test_session_that_changes_from_owner_to_companion_keeps_reset_veto(self):
        self.owner()
        self.companion(before=ASTRA, after=OTHER)
        blocks = self.blocks(self.analyze())
        self.assertIn('counter_reset', blocks[0]['reasons'])
        self.assertEqual(blocks[0]['status'], 'rejected')

    def test_unknown_prior_model_cannot_certify_non_owner_reset(self):
        self.owner()
        self.companion(before=None, after=OTHER)
        blocks = self.blocks(self.analyze())
        self.assertIn('counter_reset', blocks[0]['reasons'])
        self.assertEqual(blocks[0]['status'], 'rejected')

    def test_unknown_reset_model_cannot_certify_non_owner_reset(self):
        self.owner()
        self.companion(before=OTHER, after='unknown')
        blocks = self.blocks(self.analyze())
        self.assertIn('counter_reset', blocks[0]['reasons'])
        self.assertEqual(blocks[0]['status'], 'rejected')

    def test_nuisance_exemption_does_not_ignore_unattributed_fork_baseline(self):
        self.owner()
        self.write('fork.jsonl', [meta('fork', parent='missing-parent'), context(OTHER),
                                token(175, 100, None)])
        blocks = self.blocks(self.analyze())
        self.assertEqual(blocks[0]['status'], 'rejected')
        self.assertIn('unattributed_fork_baseline', blocks[0]['reasons'])

    def test_nuisance_exemption_does_not_ignore_malformed_session(self):
        self.owner()
        path = self.write('broken.jsonl', [meta('broken'), context(OTHER),
                                          token(75, 100, 100), token(250, 200, 100)])
        with path.open('a') as handle:
            handle.write('{"type":"token_usage_record", INVALID}\n')
        blocks = self.blocks(self.analyze())
        self.assertEqual(blocks[0]['status'], 'rejected')
        self.assertIn('session_ledger_incomplete', blocks[0]['reasons'])


if __name__ == '__main__':
    unittest.main()
