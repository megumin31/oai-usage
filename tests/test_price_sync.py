import copy
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from scripts import update_prices as sync

FIXTURES = Path(__file__).parent / 'fixtures'


def row(model='gpt-7-sol'):
    return {'id': model, 'modalities': {'input': ['text', 'image'], 'output': ['text']},
            'cost': {'input': 2, 'cache_read': 0.2, 'cache_write': 2.5, 'output': 10}}


def encoded(models, **providers):
    return json.dumps({'openai': {'models': models}, **providers}).encode()


class PriceSync(unittest.TestCase):
    def baseline(self):
        return json.loads((FIXTURES / 'prices.json').read_text())

    def snapshot(self):
        return sync.collect((FIXTURES / 'models-dev-openai-20260926.json').read_bytes())

    def current(self):
        return sync.updated_catalog(self.baseline(), self.snapshot(), '2026-09-26')

    def test_real_snapshots_preserve_all_ten_rates_and_rules(self):
        old = sync.catalog.parse_price_catalog(self.baseline(), 'old')
        raw = self.current()
        new = sync.catalog.parse_price_catalog(raw, 'new')
        self.assertTrue(old.prices.keys() <= new.prices.keys())
        for model, price in old.prices.items():
            for field in ('input', 'cached', 'write', 'output', 'long_input', 'long_cached',
                          'long_write', 'long_output', 'long_threshold'):
                self.assertEqual(getattr(price, field), getattr(new.prices[model], field), (model, field))
            self.assertEqual(new.prices[model].source, sync.PRICES_URL)
        self.assertEqual(raw['provider'], 'openai')
        sync.validate_transition(self.baseline(), raw, set(raw['models']))

    def test_exact_openai_keys_and_provider_only(self):
        good = row()
        hidden = row('gpt-7-hidden')
        other = row()
        other['cost']['input'] = 999
        values = sync.collect(encoded({'gpt-7-sol': good, 'gpt-7-hidden': hidden},
                                      anthropic={'models': {'gpt-7-sol': other, 'claude-test': other}}))
        self.assertEqual(set(values), {'gpt-7-sol', 'gpt-7-hidden'})
        self.assertEqual(values['gpt-7-sol']['input'], '2')

    def test_distinct_exact_alias_ids_are_kept_with_shared_canonical_model_id(self):
        models = {}
        for model in ('gpt-5.6', 'gpt-5.6-sol'):
            value = row(model)
            value['canonical_model_id'] = 'gpt-5.6-sol'
            models[model] = value
        values = sync.collect(encoded(models))
        self.assertEqual(set(values), {'gpt-5.6', 'gpt-5.6-sol'})
        self.assertEqual(values['gpt-5.6'], values['gpt-5.6-sol'])
        mismatched = copy.deepcopy(models)
        mismatched['gpt-5.6']['id'] = 'gpt-5.6-sol'
        with self.assertRaisesRegex(ValueError, 'does not match'):
            sync.collect(encoded(mismatched))

    def test_missing_or_invalid_openai_catalog_does_not_use_another_provider(self):
        for value in ({'openrouter': {'models': {'gpt-7-sol': row()}}}, [],
                      {'openai': None}, {'openai': {'models': []}}, {'openai': {'models': {}}},
                      {'openai': {'models': {str(index): row() for index in range(1001)}}}):
            with self.subTest(value=type(value)), self.assertRaisesRegex(ValueError, 'OpenAI model catalog'):
                sync.collect(json.dumps(value).encode())

    def test_unsupported_model_ids_are_filtered_without_guessing_prices(self):
        values = sync.collect(encoded({model: row(model)
                                       for model in ('gpt-5.3', 'claude-test', 'GPT-7-sol', 'gpt-7-sol')}))
        self.assertEqual(set(values), {'gpt-7-sol'})
        with self.assertRaisesRegex(ValueError, 'No supported OpenAI'):
            sync.collect(encoded({'gpt-5.3': row('gpt-5.3')}))

    def test_new_simple_text_model_is_discovered_without_code_change(self):
        value = row()
        del value['cost']['cache_write']
        result = sync.collect(encoded({'gpt-7-sol': value}))
        self.assertEqual(result['gpt-7-sol']['cached_input'], '0.2')
        self.assertIsNone(result['gpt-7-sol']['cache_write'])
        self.assertIsNone(result['gpt-7-sol']['long_context'])

    def test_models_without_cache_rates_import_and_new_cache_rates_update_automatically(self):
        value = row()
        value['cost'].pop('cache_read')
        value['cost'].pop('cache_write')
        discovered = sync.collect(encoded({'gpt-7-sol': value}))
        self.assertIsNone(discovered['gpt-7-sol']['cached_input'])
        self.assertIsNone(discovered['gpt-7-sol']['cache_write'])
        old = sync.updated_catalog(self.baseline(), discovered, '2026-09-30')
        sync.validate_transition(self.baseline(), old)
        new = copy.deepcopy(old)
        new['models']['gpt-7-sol']['cached_input'] = '0.2'
        sync.validate_transition(old, new)
        removed = copy.deepcopy(new)
        removed['models']['gpt-7-sol']['cached_input'] = None
        with self.assertRaisesRegex(ValueError, 'lost'):
            sync.validate_transition(new, removed)

    def test_gpt_61_and_future_context_tiers_need_no_local_model_rules(self):
        for model, threshold in (('gpt-6.1-sol', 272000), ('gpt-7-sol', 512000)):
            value = row(model)
            value['cost']['cache_read'] = 0.1
            value['cost']['tiers'] = [{'input': 4, 'cache_read': 0.2, 'cache_write': 5,
                                      'output': 15, 'tier': {'type': 'context', 'size': threshold}}]
            discovered = sync.collect(encoded({model: value}))
            imported = discovered[model]
            self.assertEqual(imported['cached_input'], '0.1')
            self.assertEqual(imported['long_context']['threshold'], threshold)
            candidate = sync.updated_catalog(self.baseline(), discovered, '2026-09-30')
            sync.validate_transition(self.baseline(), candidate)

    def test_unsupported_or_missing_prices_are_pending_not_zero(self):
        valid = row('gpt-7-good')
        for mutate in (
            lambda r: r['cost'].pop('input'),
            lambda r: r['cost'].update(output=None),
            lambda r: r['modalities'].update(output=['audio']),
            lambda r: r['cost'].update(input_audio=2),
            lambda r: r.update(cost=None),
        ):
            value = row()
            mutate(value)
            notes = []
            result = sync.collect(encoded({'gpt-7-good': valid, 'gpt-7-sol': value}), notes)
            self.assertEqual(set(result), {'gpt-7-good'})
            self.assertTrue(any('gpt-7-sol' in n for n in notes))

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
            sync.collect(encoded({'gpt-7-sol': bad}))

    def tiered(self):
        value = row()
        value['cost']['tiers'] = [{'input': 4, 'cache_read': 0.4, 'cache_write': 5,
                                  'output': 15, 'tier': {'type': 'context', 'size': 272000}}]
        return value

    def test_explicit_tier_supplies_threshold_and_request_prices(self):
        value = self.tiered()
        value['cost']['context_over_200k'] = {'input': 999}
        result = sync.collect(encoded({'gpt-7-sol': value}))
        long = result['gpt-7-sol']['long_context']
        self.assertEqual((long['threshold'], long['output']), (272000, '15'))
        value['cost']['tiers'][0]['tier']['size'] = 200000
        result = sync.collect(encoded({'gpt-7-sol': value}))
        self.assertEqual(result['gpt-7-sol']['long_context']['threshold'], 200000)

    def test_legacy_only_tiers_are_not_assigned_a_guessed_threshold(self):
        value = self.tiered()
        value['cost']['context_over_200k'] = value['cost'].pop('tiers')[0]
        notes = []
        result = sync.collect(encoded({'gpt-7-sol': value, 'gpt-7-good': row('gpt-7-good')}), notes)
        self.assertEqual(set(result), {'gpt-7-good'})
        self.assertTrue(any('explicit context tier' in note for note in notes))

    def test_unsupported_tiers_are_pending_and_existing_tier_cannot_disappear(self):
        value = self.tiered()
        for tiers in ([value['cost']['tiers'][0]] * 2,
                      [{'tier': {'type': 'batch', 'size': 272000}}]):
            candidate = row()
            candidate['cost']['tiers'] = tiers
            notes = []
            result = sync.collect(encoded({'gpt-7-sol': candidate, 'gpt-7-good': row('gpt-7-good')}), notes)
            self.assertNotIn('gpt-7-sol', result)
            self.assertTrue(notes)
        old = self.current()
        new = copy.deepcopy(old)
        new['models']['gpt-6-sol']['long_context'] = None
        with self.assertRaisesRegex(ValueError, 'threshold'):
            sync.validate_transition(old, new)

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
        new['verified_at'] = '2026-09-26'
        with self.assertRaisesRegex(ValueError, 'Unobserved prices'):
            sync.validate_transition(old, new, set(observed))

    def test_catalog_structure_rejects_unexpected_and_removed_fields(self):
        current = self.current()
        for mutate in (lambda value: value.update(extra='unexpected'),
                       lambda value: value.update(model_source='https://developers.openai.com/api/docs/models/all'),
                       lambda value: value['models']['gpt-6-sol'].update(extra='unexpected'),
                       lambda value: value['models']['gpt-6-sol'].update(source=sync.PRICES_URL),
                       lambda value: value['models']['gpt-6-sol'].update(model_source='removed'),
                       lambda value: value['models']['gpt-6-sol']['long_context'].update(extra='unexpected'),
                       lambda value: value['models']['gpt-6-sol']['long_context'].update(source=sync.PRICES_URL),
                       lambda value: value['models']['gpt-6-sol']['long_context'].update(scope='request')):
            candidate = copy.deepcopy(current)
            mutate(candidate)
            with self.assertRaises(ValueError):
                sync.validate_transition(current, candidate)

    def test_invalid_model_identifiers_are_rejected_in_the_finished_catalog(self):
        old = self.current()
        for model in ('', 'GPT-6-sol', '../gpt-6-sol', 'gpt-6-sol' + 'x' * 80):
            candidate = copy.deepcopy(old)
            candidate['models'][model] = candidate['models'].pop('gpt-6-sol')
            with self.subTest(model=model), self.assertRaisesRegex(ValueError, 'catalog model'):
                sync.validate_transition(old, candidate)

    def test_candidate_cannot_forge_upstream_source(self):
        old = self.current()
        new = copy.deepcopy(old)
        new['source'] = 'https://developers.openai.com/api/docs/pricing'
        with self.assertRaisesRegex(ValueError, 'upstream source'):
            sync.validate_transition(old, new)

    def test_unchanged_prices_refresh_only_after_thirty_days(self):
        old = self.current()
        self.assertIs(sync.updated_catalog(old, old['models'], '2026-09-27'), old)
        new = sync.updated_catalog(old, old['models'], '2026-10-26')
        self.assertEqual(new['verified_at'], '2026-10-26')
        self.assertEqual(new['models'], old['models'])

    def test_anomaly_gate_rejects_zero_extreme_changed_threshold_and_lost_models(self):
        old = self.current()
        for value in ('0', '100', '0.01'):
            new = copy.deepcopy(old)
            new['models']['gpt-6-sol']['input'] = value
            with self.assertRaisesRegex(ValueError, 'Review required'):
                sync.validate_transition(old, new)
        new = copy.deepcopy(old)
        new['models']['gpt-6-sol']['long_context']['threshold'] = 200000
        with self.assertRaisesRegex(ValueError, 'Review required'):
            sync.validate_transition(old, new)
        with self.assertRaisesRegex(ValueError, '25%'):
            sync.validate_transition(old, old, set(old['models']) - {'gpt-5.4', 'gpt-5.4-mini', 'gpt-5.4-nano'})
        new = copy.deepcopy(old)
        del new['models']['gpt-6-sol']
        with self.assertRaisesRegex(ValueError, 'retained'):
            sync.validate_transition(old, new)

    def test_new_zero_rates_are_blocked_and_valid_context_tiers_are_publishable(self):
        old = self.current()
        for field in ('input', 'cached_input', 'cache_write', 'output'):
            new = copy.deepcopy(old)
            new['models']['gpt-7-sol'] = sync.collect(encoded({'gpt-7-sol': row()}))['gpt-7-sol']
            new['models']['gpt-7-sol'][field] = '0'
            with self.assertRaisesRegex(ValueError, 'zero price'):
                sync.validate_transition(old, new)
        new = copy.deepcopy(old)
        new['models']['gpt-7-sol'] = sync.collect(encoded({'gpt-7-sol': self.tiered()}))['gpt-7-sol']
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

    def test_sync_fetches_models_dev_once(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline = Path(directory) / 'prices.json'
            baseline.write_text(json.dumps(self.baseline()))
            upstream = (FIXTURES / 'models-dev-openai-20260926.json').read_bytes()
            with patch.object(sync, 'CATALOG', baseline), patch.object(sync, 'fetch', return_value=upstream) as fetch, \
                 patch('sys.argv', ['sync', '--dry-run']), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(sync.main(), 0)
            fetch.assert_called_once_with(sync.PRICES_URL)

    def test_discovery_notes_reach_workflow_logs(self):
        old = self.current()
        observed = copy.deepcopy(old['models'])
        del observed['gpt-6-astra']
        def collect(_data, warnings):
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

    def test_producer_support_is_local_and_only_allows_upstream_downloads(self):
        worker = sync.ROOT / 'scripts' / 'price_support.py'
        self.assertEqual(Path(sync.catalog.__file__).resolve(), worker)
        self.assertEqual(sync.catalog.download_budget(sync.PRICES_URL), 8_000_000)
        for url in ('https://raw.githubusercontent.com/megumin31/oai-usage/main/prices.json',
                    'https://developers.openai.com/api/docs/models/all.md',
                    sync.PRICES_URL + '?extra=1', sync.PRICES_URL + '/extra', 'http://models.dev/api.json'):
            with self.subTest(url=url), self.assertRaises(sync.catalog.DownloadError), \
                 patch.object(sync.catalog.subprocess, 'run') as run:
                sync.fetch(url)
            run.assert_not_called()
        result = subprocess.CompletedProcess([], 0, b'upstream', b'')
        with patch.object(sync.catalog.subprocess, 'run', return_value=result) as run:
            self.assertEqual(sync.fetch(sync.PRICES_URL), b'upstream')
        self.assertEqual(run.call_args.args[0], [sys.executable, str(worker), '--download',
                                               sync.PRICES_URL, '8000000', '5'])
        self.assertEqual(run.call_args.kwargs['timeout'], 20)

    def test_producer_worker_rejects_excessive_budgets_and_maps_failures(self):
        for arguments in ({'limit': 8_000_001}, {'total_timeout': 21}, {'socket_timeout': 6},
                          {'total_timeout': 2, 'socket_timeout': 3}):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError), \
                 patch.object(sync.catalog.subprocess, 'run') as run:
                sync.catalog.fetch_https(sync.PRICES_URL, **arguments)
            run.assert_not_called()
        for failure in (subprocess.TimeoutExpired([], 20), OSError('worker cannot start')):
            with self.subTest(failure=failure), patch.object(sync.catalog.subprocess, 'run', side_effect=failure), \
                 self.assertRaises(sync.catalog.DownloadError):
                sync.fetch(sync.PRICES_URL)

    def test_upstream_transfer_rejects_redirects_and_invalid_responses(self):
        with self.assertRaises(sync.catalog.DownloadError):
            sync.catalog.NoRedirect().redirect_request(None, None, 302, '', {}, sync.PRICES_URL)

        class Response(io.BytesIO):
            status = 200

            def __init__(self, data, headers, url):
                super().__init__(data)
                self.headers, self.url = headers, url

            def geturl(self):
                return self.url

        cases = ((b'abc', {'Content-Encoding': 'gzip'}, sync.PRICES_URL),
                 (b'abc', {'Content-Length': 'invalid'}, sync.PRICES_URL),
                 (b'abc', {'Content-Length': '11'}, sync.PRICES_URL),
                 (b'abc', {'Content-Length': '4'}, sync.PRICES_URL),
                 (b'x' * 11, {}, sync.PRICES_URL),
                 (b'abc', {}, sync.PRICES_URL + '?redirect=1'))
        for data, headers, url in cases:
            with self.subTest(headers=headers, url=url), \
                 patch.object(sync.catalog.urllib.request, 'build_opener') as opener:
                opener.return_value.open.return_value = Response(data, headers, url)
                with self.assertRaises(sync.catalog.DownloadError):
                    sync.catalog._download(sync.PRICES_URL, 10, 5)

    def test_update_script_starts_without_cli_or_repository_imports(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, str(sync.ROOT / 'scripts' / 'update_prices.py'), '--help'],
                                    cwd=directory, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertIn(b'--dry-run', result.stdout)


if __name__ == '__main__':
    unittest.main()
