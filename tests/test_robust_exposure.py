"""Exposure-based chronology, with synthetic records and no external sources.

These tests exercise geometry only. Overlapping timing alternatives must not
create extra observation time, and profile-inactive wall time must not decide
which eligible workload is withheld.
"""
from datetime import timedelta

from test_historical_segments import BASE, M, context, quota, stamp
from test_robust_segments import ASTRA, OTHER, PRICES, RobustFixtures, oracle_trace
import unittest


def at(seconds):
    return BASE + timedelta(seconds=seconds)


class ExposurePartition(unittest.TestCase):
    def partition(self, intervals, **kwargs):
        return M['robust_exposure_partition'](
            [(at(start), at(end)) for start, end in intervals], **kwargs)

    def assert_partition(self, actual, *, start, end, cutoff, midpoint, exposure, count):
        self.assertEqual(actual['start'], at(start))
        self.assertEqual(actual['end'], at(end))
        self.assertEqual(actual['cutoff'], at(cutoff))
        self.assertEqual(actual['training_midpoint'], at(midpoint))
        self.assertAlmostEqual(actual['observed_exposure_seconds'], exposure)
        self.assertEqual(actual['interval_count'], count)

    def test_empty_input_has_no_partition(self):
        self.assertIsNone(self.partition([]))

    def test_uniform_geometry_uses_two_thirds_and_training_exposure_half(self):
        self.assert_partition(self.partition([(0, 90)]), start=0, end=90,
                              cutoff=60, midpoint=30, exposure=90, count=1)

    def test_holes_do_not_count_as_observed_workload(self):
        self.assert_partition(self.partition([(0, 30), (90, 150)]),
                              start=0, end=150, cutoff=120, midpoint=30,
                              exposure=90, count=2)

    def test_overlapping_variants_duplicates_and_touching_edges_count_once(self):
        intervals = [(100, 130), (20, 50), (0, 30), (50, 60),
                     (0, 30), (10, 40), (100, 130)]
        self.assert_partition(self.partition(intervals), start=0, end=130,
                              cutoff=60, midpoint=30, exposure=90, count=2)
        self.assertEqual(self.partition(intervals),
                         self.partition([(0, 60), (100, 130)]))

    def test_fraction_applies_to_exposure_and_its_training_half(self):
        self.assert_partition(self.partition([(0, 30), (90, 150)], fraction=.5),
                              start=0, end=150, cutoff=105, midpoint=22.5,
                              exposure=90, count=2)

    def test_translating_geometry_translates_cutoffs_without_changing_exposure(self):
        original = self.partition([(0, 30), (90, 150)])
        shifted = self.partition([(1000, 1030), (1090, 1150)])
        for key in ('start', 'end', 'cutoff', 'training_midpoint'):
            self.assertEqual(shifted[key], original[key] + timedelta(seconds=1000))
        self.assertEqual(shifted['observed_exposure_seconds'],
                         original['observed_exposure_seconds'])
        self.assertEqual(shifted['interval_count'], original['interval_count'])


class ExposureIntegration(RobustFixtures):
    def analyze(self):
        return M['robust_segments']([self.root], PRICES,
                                   now=BASE + timedelta(days=1))[0]

    @staticmethod
    def rows(data, model=ASTRA):
        return [row for row in data['model_scenarios']
                if row['profile']['model'] == model]

    @staticmethod
    def key(row):
        return (row['quota_epoch_id'], tuple(sorted(row['profile'].items())),
                row['policy_id'], row['alignment_offset_seconds'])

    def expected_partition(self, data, row):
        geometry = [
            (M['timestamp'](b['before']['observed_at']),
             M['timestamp'](b['after']['observed_at']))
            for b in data['blocks']
            if (b['quota_epoch_id'] == row['quota_epoch_id']
                and b['profile'] == row['profile']
                and b['status'] == 'eligible'
                and b.get('local_event_count', 0) > 0
                and not b.get('constraint_only'))
        ]
        return M['robust_exposure_partition'](geometry)

    def test_quota_only_inactive_tail_cannot_move_split_or_held_out_workload(self):
        records, _ = oracle_trace([20] * 36, a='.1', initial=0, step=300)
        self.write('trace.jsonl', records)
        baseline_data = self.analyze()
        baseline = {self.key(row): row for row in self.rows(baseline_data)}
        self.assertEqual(len(baseline), 9)

        # Three extra inactive hours would move a full-epoch wall-clock split
        # beyond every receipt. It must not consume observed-workload exposure.
        self.write('trace.jsonl', records + [quota(21600, 72)])
        extended_data = self.analyze()
        extended = {self.key(row): row for row in self.rows(extended_data)}
        self.assertEqual(set(baseline), set(extended))
        def signature(data, ids):
            # Attempt IDs are serial across policies, so a new rejected tail
            # may renumber later variants without changing their evidence.
            by_id = {b['id']: b for b in data['blocks']}
            return [(by_id[bid]['before']['observed_at'],
                     by_id[bid]['after']['observed_at'],
                     by_id[bid]['before']['used_percent'],
                     by_id[bid]['after']['used_percent'],
                     by_id[bid]['known_api_cost_usd']) for bid in ids]
        for key, before in baseline.items():
            after = extended[key]
            self.assertEqual(before['split']['cutoff_at'], after['split']['cutoff_at'])
            for field in ('training_block_ids', 'holdout_block_ids', 'purged_block_ids'):
                self.assertEqual(signature(baseline_data, before['split'][field]),
                                 signature(extended_data, after['split'][field]))
            self.assertEqual(before['candidate'], after['candidate'])
            self.assertTrue(after['split']['holdout_block_ids'])

    def test_all_timing_variants_use_one_canonical_profile_cutoff(self):
        records, _ = oracle_trace([20] * 36, a='.1', initial=0, step=300)
        # Different grid lengths lose different windows around this missing
        # observation; their available interval geometries therefore differ.
        records = [r for r in records
                   if not (r['type'] == 'event_msg' and r['timestamp'] == stamp(900))]
        self.write('trace.jsonl', records)
        data = self.analyze()
        rows = self.rows(data)
        self.assertEqual(len(rows), 9)
        expected = self.expected_partition(data, rows[0])
        self.assertIsNotNone(expected)
        self.assertAlmostEqual(expected['observed_exposure_seconds'], 10200)
        self.assertEqual(expected['cutoff'], at(7400))
        self.assertEqual({M['timestamp'](row['split']['cutoff_at']) for row in rows},
                         {expected['cutoff']})
        by_id = {block['id']: block for block in data['blocks']}
        for row in rows:
            cutoff = M['timestamp'](row['split']['cutoff_at'])
            train, hold = row['split']['training_block_ids'], row['split']['holdout_block_ids']
            self.assertTrue(all(M['timestamp'](by_id[b]['after']['observed_at']) <= cutoff
                                for b in train))
            self.assertTrue(all(M['timestamp'](by_id[b]['before']['observed_at']) > cutoff
                                for b in hold))
            train_endpoints = {by_id[b][side]['id'] for b in train for side in ('before', 'after')}
            hold_endpoints = {by_id[b][side]['id'] for b in hold for side in ('before', 'after')}
            self.assertFalse(train_endpoints & hold_endpoints)

    def test_separate_profiles_do_not_share_an_activity_split(self):
        records, _ = oracle_trace([20] * 36, a='.1', initial=0, step=300)
        changed = []
        for record in records:
            if (record['type'] == 'token_usage_record'
                    and record['payload']['response_id'] == 'r13'):
                changed.append(context(OTHER))
            changed.append(record)
        self.write('trace.jsonl', changed)
        data = self.analyze()
        expected_cutoffs = {ASTRA: at(2400), OTHER: at(8400)}
        for model, cutoff in expected_cutoffs.items():
            rows = self.rows(data, model)
            self.assertEqual(len(rows), 9)
            self.assertEqual({M['timestamp'](row['split']['cutoff_at']) for row in rows}, {cutoff})
            self.assertEqual(self.expected_partition(data, rows[0])['cutoff'], cutoff)
        self.assertNotEqual(expected_cutoffs[ASTRA], expected_cutoffs[OTHER])


if __name__ == '__main__':
    unittest.main()
