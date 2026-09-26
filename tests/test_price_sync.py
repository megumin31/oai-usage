import unittest

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


if __name__ == '__main__':
    unittest.main()
