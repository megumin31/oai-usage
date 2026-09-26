import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from scripts import update_prices as sync

FIXTURES = Path(__file__).parent / 'fixtures'


def listing(*models):
    return '# Models\n' + '\n'.join(f'- [{m}](/api/docs/models/{m}.md)' for m in models)


def row(model='gpt-7-sol'):
    return {'id': model, 'modalities': {'input': ['text', 'image'], 'output': ['text']},
            'cost': {'input': 2, 'cache_read': 0.2, 'cache_write': 2.5, 'output': 10}}


def encoded(models, **providers):
    return json.dumps({'openai': {'models': models}, **providers}).encode()


class PriceSync(unittest.TestCase):
    def baseline(self):
        return json.loads((FIXTURES / 'prices.json').read_text())

    def snapshot(self):
        return sync.collect((FIXTURES / 'openai-models-20260926.md').read_text(),
                            (FIXTURES / 'models-dev-openai-20260926.json').read_bytes(), sync.load_rules())

    def current(self):
        return sync.updated_catalog(self.baseline(), self.snapshot(), '2026-09-26')

    def test_real_snapshots_preserve_all_ten_rates_and_rules(self):
        old = sync.catalog.parse_price_catalog(self.baseline(), 'old')
        raw = self.current()
        new = sync.catalog.parse_price_catalog(raw, 'new')
        self.assertEqual(set(old.prices), set(new.prices))
        for model, price in old.prices.items():
            for field in ('input', 'cached', 'write', 'output', 'long_input', 'long_cached',
                          'long_write', 'long_output', 'long_threshold', 'long_scope'):
                self.assertEqual(getattr(price, field), getattr(new.prices[model], field), (model, field))
            self.assertEqual(new.prices[model].source, sync.PRICES_URL)
            self.assertEqual(new.prices[model].model_source, sync.MODEL_URL.format(model))
        self.assertEqual(raw['schema_version'], 2)
        self.assertEqual(raw['provider'], 'openai')
        sync.validate_transition(self.baseline(), raw, set(raw['models']))

    def test_exact_official_join_and_openai_provider_only(self):
        good = row()
        hidden = row('gpt-7-hidden')
        other = row()
        other['cost']['input'] = 999
        values = sync.collect(listing('gpt-7-sol', 'claude-test'),
                              encoded({'gpt-7-sol': good, 'gpt-7-hidden': hidden},
                                      anthropic={'models': {'gpt-7-sol': other, 'claude-test': other}}), {})
        self.assertEqual(set(values), {'gpt-7-sol'})
        self.assertEqual(values['gpt-7-sol']['input'], '2')
        with self.assertRaisesRegex(ValueError, 'OpenAI model catalog'):
            sync.upstream_models(json.dumps({'openrouter': {'models': {'gpt-7-sol': good}}}).encode())

    def test_official_links_are_exact_and_no_alias_prices_are_guessed(self):
        document = listing('gpt-7-sol') + '\n[Bad](https://evil.test/api/docs/models/gpt-7-fake.md)'
        self.assertEqual(sync.official_models(document), {'gpt-7-sol'})
        aliased = row('gpt-7-sol-2027-01-01')
        with self.assertRaisesRegex(ValueError, 'No supported OpenAI'):
            sync.collect(document, encoded({aliased['id']: aliased}), {})
        with self.assertRaises(ValueError):
            sync.official_models('A mention of gpt-7-sol without a model link.')
        with self.assertRaises(ValueError):
            sync.official_models(listing('gpt-7-sol') + '\n' + 'x' * 8193)

    def test_new_simple_text_model_is_discovered_without_code_change(self):
        value = row()
        del value['cost']['cache_write']
        result = sync.collect(listing('gpt-7-sol'), encoded({'gpt-7-sol': value}), {})
        self.assertEqual(result['gpt-7-sol']['cached_input'], '0.2')
        self.assertIsNone(result['gpt-7-sol']['cache_write'])
        self.assertIsNone(result['gpt-7-sol']['long_context'])

    def test_unsupported_or_missing_prices_are_pending_not_zero(self):
        valid = row('gpt-7-good')
        for mutate in (
            lambda r: r['cost'].pop('cache_read'),
            lambda r: r['cost'].update(cache_read=None),
            lambda r: r['modalities'].update(output=['audio']),
            lambda r: r['cost'].update(input_audio=2),
            lambda r: r.update(cost=None),
        ):
            value = row()
            mutate(value)
            notes = []
            result = sync.collect(listing('gpt-7-good', 'gpt-7-sol', 'gpt-7-missing'),
                                  encoded({'gpt-7-good': valid, 'gpt-7-sol': value}), {}, notes)
            self.assertEqual(set(result), {'gpt-7-good'})
            self.assertTrue(any('gpt-7-sol' in n for n in notes))
            self.assertTrue(any('gpt-7-missing' in n for n in notes))

    def test_upstream_json_limits_duplicate_keys_and_numeric_validation(self):
        bad_json = [b'{"openai":{},"openai":{}}', b'{"x":NaN}', b'{"x":1e99999}',
                    b'[' * 17 + b'0' + b']' * 17, b'{"x":"' + b'x' * 16385 + b'"}',
                    b'x' * 8_000_001]
        for data in bad_json:
            with self.subTest(size=len(data)), self.assertRaises(ValueError):
                sync.upstream_models(data)
        for value in (True, -1, '0.2', 1000000001, Decimal('NaN'), Decimal('1e-13')):
            with self.subTest(value=value), self.assertRaises(ValueError):
                sync.rate(value)
        self.assertEqual(sync.rate(Decimal('0.125')), '0.125')
        self.assertEqual(sync.rate(Decimal('2.00')), '2')
        bad = row()
        bad['id'] = 'gpt-7-other'
        with self.assertRaisesRegex(ValueError, 'does not match'):
            sync.collect(listing('gpt-7-sol'), encoded({'gpt-7-sol': bad}), {})

    def tiered(self):
        value = row()
        value['cost']['tiers'] = [{'input': 4, 'cache_read': 0.4, 'cache_write': 5,
                                  'output': 15, 'tier': {'type': 'context', 'size': 272000}}]
        rules = {'gpt-7-sol': {'threshold': 272000, 'scope': 'session',
                             'source': sync.MODEL_URL.format('gpt-7-sol')}}
        return value, rules

    def test_explicit_tier_and_reviewed_scope_control_long_context(self):
        value, rules = self.tiered()
        value['cost']['context_over_200k'] = {k: v for k, v in value['cost']['tiers'][0].items() if k != 'tier'}
        result = sync.collect(listing('gpt-7-sol'), encoded({'gpt-7-sol': value}), rules)
        long = result['gpt-7-sol']['long_context']
        self.assertEqual((long['threshold'], long['scope'], long['output']), (272000, 'session', '15'))
        value['cost']['tiers'][0]['tier']['size'] = 200000
        with self.assertRaisesRegex(ValueError, 'changed long-context threshold'):
            sync.collect(listing('gpt-7-sol'), encoded({'gpt-7-sol': value}), rules)

    def test_unknown_long_rules_and_legacy_only_tiers_are_not_guessed(self):
        value, rules = self.tiered()
        for supplied in ({}, rules):
            candidate = copy.deepcopy(value)
            if supplied:
                candidate['cost']['context_over_200k'] = candidate['cost'].pop('tiers')[0]
            notes = []
            result = sync.collect(listing('gpt-7-sol', 'gpt-7-good'),
                                  encoded({'gpt-7-sol': candidate, 'gpt-7-good': row('gpt-7-good')}),
                                  supplied, notes)
            self.assertEqual(set(result), {'gpt-7-good'})
            self.assertTrue(any('gpt-7-sol' in n for n in notes))
        value['cost']['context_over_200k'] = {'input': 999, 'cache_read': .4, 'cache_write': 5, 'output': 15}
        result = sync.collect(listing('gpt-7-sol'), encoded({'gpt-7-sol': value}), rules)
        self.assertEqual(result['gpt-7-sol']['long_context']['input'], '4')

    def test_unsupported_tiers_and_missing_existing_tier_are_pending(self):
        value, rules = self.tiered()
        for tiers in ([], [value['cost']['tiers'][0]] * 2,
                      [{'tier': {'type': 'batch', 'size': 272000}}]):
            value['cost']['tiers'] = tiers
            notes = []
            result = sync.collect(listing('gpt-7-sol', 'gpt-7-good'),
                                  encoded({'gpt-7-sol': value, 'gpt-7-good': row('gpt-7-good')}), rules, notes)
            self.assertNotIn('gpt-7-sol', result)
            self.assertTrue(notes)

    def test_history_retained_without_advancing_check_date(self):
        old = self.current()
        old['verified_at'] = '2026-08-01'
        observed = copy.deepcopy(old['models'])
        del observed['gpt-6-astra']
        self.assertIs(sync.updated_catalog(old, observed, '2026-09-26'), old)
        observed['gpt-6-sol']['input'] = '3'
        new = sync.updated_catalog(old, observed, '2026-09-26')
        self.assertEqual(new['models']['gpt-6-astra'], old['models']['gpt-6-astra'])
        self.assertEqual(new['verified_at'], old['verified_at'])
        sync.validate_transition(old, new, set(observed))

    def test_old_catalog_formats_are_rejected_without_migration(self):
        current = self.current()
        old = copy.deepcopy(current)
        old['schema_version'] = 1
        for baseline, candidate in ((old, current), (current, old), (old, old)):
            with self.assertRaisesRegex(ValueError, 'schema'):
                sync.validate_transition(baseline, candidate)
        with self.assertRaisesRegex(ValueError, 'schema'):
            sync.updated_catalog(old, current['models'], '2026-09-26')

    def test_candidate_cannot_forge_official_source_or_bypass_rule_review(self):
        old = self.current()
        new = copy.deepcopy(old)
        forged = copy.deepcopy(old['models']['gpt-6-sol'])
        forged.update(source='https://developers.openai.com/api/docs/pricing',
                      model_source=sync.MODEL_URL.format('gpt-7-sol'))
        forged['long_context'].update(threshold=1, source=sync.MODEL_URL.format('gpt-7-sol'))
        new['models']['gpt-7-sol'] = forged
        with self.assertRaisesRegex(ValueError, 'model price source'):
            sync.validate_transition(old, new)
        new['source'] = 'https://developers.openai.com/api/docs/pricing'
        with self.assertRaisesRegex(ValueError, 'upstream source'):
            sync.validate_transition(old, new)
    def test_unchanged_prices_refresh_only_after_thirty_days(self):
        old = self.current()
        self.assertIs(sync.updated_catalog(old, old['models'], '2026-09-27'), old)
        new = sync.updated_catalog(old, old['models'], '2026-10-26')
        self.assertEqual(new['verified_at'], '2026-10-26')
        self.assertEqual(new['models'], old['models'])

    def test_anomaly_gate_rejects_zero_extreme_changed_scope_and_lost_models(self):
        old = self.current()
        for value in ('0', '100', '0.01'):
            new = copy.deepcopy(old)
            new['models']['gpt-6-sol']['input'] = value
            with self.assertRaisesRegex(ValueError, 'Review required'):
                sync.validate_transition(old, new)
        new = copy.deepcopy(old)
        new['models']['gpt-6-sol']['long_context']['scope'] = 'session'
        with self.assertRaisesRegex(ValueError, 'Review required'):
            sync.validate_transition(old, new)
        with self.assertRaisesRegex(ValueError, '25%'):
            sync.validate_transition(old, old, set(old['models']) - {'gpt-5.4', 'gpt-5.4-mini', 'gpt-5.4-nano'})
        new = copy.deepcopy(old)
        del new['models']['gpt-6-sol']
        with self.assertRaisesRegex(ValueError, 'retained'):
            sync.validate_transition(old, new)

    def test_new_zero_rate_model_and_unreviewed_long_rule_are_blocked(self):
        old = self.current()
        for field in ('input', 'cached_input', 'cache_write', 'output'):
            new = copy.deepcopy(old)
            new['models']['gpt-7-sol'] = sync.collect(listing('gpt-7-sol'), encoded({'gpt-7-sol': row()}), {})['gpt-7-sol']
            new['models']['gpt-7-sol'][field] = '0'
            with self.assertRaisesRegex(ValueError, 'zero price'):
                sync.validate_transition(old, new)
        value, rules = self.tiered()
        new['models']['gpt-7-sol'] = sync.collect(listing('gpt-7-sol'), encoded({'gpt-7-sol': value}), rules)['gpt-7-sol']
        with self.assertRaisesRegex(ValueError, 'local rule'):
            sync.validate_transition(old, new)

    def test_failure_dry_run_and_offline_publisher_validation(self):
        old = self.baseline()
        with tempfile.TemporaryDirectory() as directory:
            baseline, candidate = Path(directory) / 'prices.json', Path(directory) / 'candidate.json'
            baseline.write_text(json.dumps(old))
            before = baseline.read_bytes()
            observed = self.snapshot()
            broken = copy.deepcopy(observed)
            broken['gpt-6-sol']['input'] = '0'
            with patch.object(sync, 'CATALOG', baseline), patch.object(sync, 'fetch', return_value=b''), \
                 patch.object(sync, 'collect', return_value=broken), patch('sys.argv', ['sync']), self.assertRaises(ValueError):
                sync.main()
            self.assertEqual(baseline.read_bytes(), before)
            with patch.object(sync, 'CATALOG', baseline), patch.object(sync, 'fetch', return_value=b''), \
                 patch.object(sync, 'collect', return_value=observed), redirect_stdout(io.StringIO()):
                with patch('sys.argv', ['sync', '--dry-run', '--output', str(candidate)]):
                    sync.main()
                self.assertFalse(candidate.exists())
                with patch('sys.argv', ['sync', '--output', str(candidate)]):
                    sync.main()
            with patch.object(sync, 'CATALOG', baseline), patch.object(sync, 'fetch', side_effect=AssertionError('No network')), \
                 patch('sys.argv', ['sync', '--validate', str(candidate)]), redirect_stdout(io.StringIO()):
                self.assertEqual(sync.main(), 0)
            self.assertEqual(baseline.read_bytes(), before)

    def test_discovery_notes_reach_workflow_logs(self):
        old = self.current()
        observed = copy.deepcopy(old['models'])
        del observed['gpt-6-astra']
        def collect(_markdown, _data, _rules, warnings):
            warnings.append('Pending gpt-7-sol: missing price.')
            return observed
        with tempfile.TemporaryDirectory() as directory:
            baseline = Path(directory) / 'prices.json'
            baseline.write_text(json.dumps(old))
            output = io.StringIO()
            with patch.object(sync, 'CATALOG', baseline), patch.object(sync, 'fetch', return_value=b''), \
                 patch.object(sync, 'collect', side_effect=collect), patch('sys.argv', ['sync', '--dry-run']), \
                 redirect_stderr(output), redirect_stdout(io.StringIO()):
                self.assertEqual(sync.main(), 0)
            self.assertIn('gpt-7-sol', output.getvalue())
            self.assertIn('date will not advance', output.getvalue())


if __name__ == '__main__':
    unittest.main()
