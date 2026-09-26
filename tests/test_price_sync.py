import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from scripts import update_prices as sync


HEADER = '| Model | Short context input | Short context cached input | Short context cache writes | Short context output | Long context input | Long context cached input | Long context cache writes | Long context output |'
SEPARATOR = '| --- | --- | --- | --- | --- | --- | --- | --- | --- |'


def markdown(extra=''):
    return '\n'.join([
        '# Pricing', '### Standard pricing data', HEADER, SEPARATOR,
        '| gpt-6-astra | $10 | $1 | $12.5 | $50 | $20 | $2 | $25 | $75 |',
        '| gpt-6-sol | $2 | $0.2 | $2.5 | $10 | $4 | $0.4 | $5 | $15 |',
        '| gpt-6-luna | $0.1 | $0.01 | $0.125 | $0.5 | $0.2 | $0.02 | $0.25 | $0.75 |',
        extra, '', 'Batch',
    ])


class PriceSync(unittest.TestCase):
    def baseline(self):
        return json.loads((Path(__file__).parent / 'fixtures/prices.json').read_text())

    def test_discovers_new_standard_model_and_scope(self):
        row = '| gpt-7-sol | $3 | $0.3 | $3.75 | $12 | $6 | $0.6 | $7.5 | $18 |'
        page = '- Prompts with more than 272K input tokens are priced for the full request.'
        models = sync.collect(markdown(row), lambda url: page)
        self.assertEqual(models['gpt-7-sol']['input'], '3')
        self.assertEqual(models['gpt-7-sol']['long_context'], {
            'threshold': 272000, 'scope': 'request', 'input': '6', 'cached_input': '0.6',
            'cache_write': '7.5', 'output': '18'})

    def test_accepts_changed_long_context_rate_from_official_table(self):
        changed = markdown().replace('$4 | $0.4 | $5 | $15', '$4 | $0.4 | $5 | $16')
        models = sync.collect(changed, lambda url: '- Prompts with more than 272K input tokens for the full request.')
        self.assertEqual(models['gpt-6-sol']['long_context']['output'], '16')

    def test_normalizes_dated_model_and_rejects_conflicting_alias(self):
        dated = '| gpt-7-2027-01-01 | $3 | $0.3 | - | $12 | - | - | - | - |'
        page = '- Prompts with more than 272K input tokens are priced for the full request.'
        models = sync.collect(markdown(dated), lambda url: page)
        self.assertEqual(models['gpt-7']['input'], '3')
        self.assertNotIn('gpt-7-2027-01-01', models)
        preview = dated.replace('gpt-7-2027-01-01', 'gpt-7-preview')
        self.assertEqual(sync.collect(markdown(preview), lambda url: page)['gpt-7']['input'], '3')
        base = '| gpt-7 | $4 | $0.4 | - | $16 | - | - | - | - |'
        with self.assertRaisesRegex(ValueError, 'Conflicting Standard prices'):
            sync.collect(markdown('\n'.join((dated, base))), lambda url: page)

    def test_keeps_prior_catalog_when_official_prices_are_unchanged(self):
        observed = {'gpt-6-sol': {'input': '2'}}
        old = {'schema_version': 1, 'basis': 'standard_api_equivalent',
               'verified_at': '2026-09-26', 'models': observed}
        self.assertIs(sync.updated_catalog(old, observed, '2026-09-27'), old)
        refreshed = sync.updated_catalog(old, observed, '2026-10-26')
        self.assertEqual(refreshed['verified_at'], '2026-10-26')
        self.assertEqual(refreshed['models'], observed)
        self.assertEqual(old['verified_at'], '2026-09-26')

    def test_accepts_normal_price_change_and_new_model(self):
        old = self.baseline()
        new = copy.deepcopy(old)
        new['models']['gpt-6-sol']['input'] = '3'
        new['models']['gpt-6-sol']['long_context']['input'] = '6'
        new['models']['gpt-7-sol'] = copy.deepcopy(new['models']['gpt-6-sol'])
        sync.validate_transition(old, new)

    def test_blocks_zero_extreme_prices_rule_changes_and_missing_models(self):
        old = self.baseline()
        for value in ('0', '100', '0.01'):
            new = copy.deepcopy(old)
            new['models']['gpt-6-sol']['input'] = value
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'Review required'):
                sync.validate_transition(old, new)
        new = copy.deepcopy(old)
        new['models']['gpt-6-sol']['long_context']['scope'] = 'session'
        with self.assertRaisesRegex(ValueError, 'Review required'):
            sync.validate_transition(old, new)
        observed = set(old['models']) - {'gpt-5.4', 'gpt-5.4-mini', 'gpt-5.4-nano'}
        with self.assertRaisesRegex(ValueError, '25%'):
            sync.validate_transition(old, old, observed)

    def test_new_models_require_review_for_any_zero_rate(self):
        old = self.baseline()
        for long_context in (False, True):
            for field in ('input', 'cached_input', 'cache_write', 'output'):
                new = copy.deepcopy(old)
                new['models']['gpt-7-sol'] = copy.deepcopy(old['models']['gpt-6-sol'])
                row = new['models']['gpt-7-sol']
                if long_context:
                    row = row['long_context']
                row[field] = '0'
                with self.subTest(long_context=long_context, field=field), self.assertRaisesRegex(ValueError, 'zero price'):
                    sync.validate_transition(old, new)
        new = copy.deepcopy(old)
        new['models']['gpt-7-sol'] = copy.deepcopy(old['models']['gpt-6-sol'])
        new['models']['gpt-7-sol']['cache_write'] = None
        new['models']['gpt-7-sol']['long_context']['cache_write'] = None
        sync.validate_transition(old, new)

    def test_failed_sync_and_dry_run_never_replace_baseline(self):
        old = self.baseline()
        with tempfile.TemporaryDirectory() as root:
            baseline = Path(root) / 'prices.json'
            candidate = Path(root) / 'candidate.json'
            baseline.write_text(json.dumps(old))
            before = baseline.read_bytes()
            observed = copy.deepcopy(old['models'])
            observed['gpt-6-sol']['input'] = '0'
            with patch.object(sync, 'CATALOG', baseline), patch.object(sync, 'fetch', return_value=''), \
                 patch.object(sync, 'collect', return_value=observed), patch('sys.argv', ['sync']), self.assertRaises(ValueError):
                sync.main()
            self.assertEqual(baseline.read_bytes(), before)
            with patch.object(sync, 'CATALOG', baseline), patch.object(sync, 'fetch', return_value=''), \
                 patch.object(sync, 'collect', return_value=old['models']), redirect_stdout(io.StringIO()):
                with patch('sys.argv', ['sync', '--dry-run', '--output', str(candidate)]):
                    sync.main()
                self.assertFalse(candidate.exists())
                with patch('sys.argv', ['sync', '--output', str(candidate)]):
                    sync.main()
                self.assertEqual(json.loads(candidate.read_text())['models'], old['models'])
            with patch.object(sync, 'CATALOG', baseline), patch.object(sync, 'fetch', side_effect=AssertionError('No network')), \
                 patch('sys.argv', ['sync', '--validate', str(candidate)]), redirect_stdout(io.StringIO()):
                self.assertEqual(sync.main(), 0)


if __name__ == '__main__':
    unittest.main()
