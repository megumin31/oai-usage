import base64
import copy
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import Mock, patch

from scripts import publish_prices as publish


class PublishPrices(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.old = json.loads((Path(__file__).parent / 'fixtures/prices.json').read_text())
        self.new = copy.deepcopy(self.old)
        self.new['models']['gpt-6-sol']['input'] = '3'
        self.new['models']['gpt-6-sol']['long_context']['input'] = '6'
        self.baseline = self.root / 'prices.json'
        self.candidate = self.root / 'candidate.json'
        self.baseline.write_text(json.dumps(self.old))
        self.candidate.write_text(json.dumps(self.new))
        self.head = 'a' * 40
        for context in (patch.object(publish, 'CATALOG', self.baseline),
                        patch.dict(os.environ, {'GITHUB_REPOSITORY': publish.REPOSITORY,
                                                'GITHUB_REF': 'refs/heads/main', 'GITHUB_SHA': self.head})):
            context.start()
            self.addCleanup(context.stop)

    def test_valid_change_updates_only_fixed_file_with_head_precondition(self):
        with patch.object(publish, 'gh', side_effect=[self.head.encode(), b'b' * 40]) as gh, redirect_stdout(io.StringIO()):
            self.assertTrue(publish.publish(self.candidate))
        call = gh.call_args
        self.assertEqual(call.args[0], 'graphql')
        payload = json.loads(call.kwargs['data'])['variables']['input']
        self.assertEqual(payload['branch'], {'repositoryNameWithOwner': publish.REPOSITORY, 'branchName': 'main'})
        self.assertEqual(payload['expectedHeadOid'], self.head)
        additions = payload['fileChanges']['additions']
        self.assertEqual(len(additions), 1)
        self.assertEqual(additions[0]['path'], 'prices.json')
        self.assertEqual(json.loads(base64.b64decode(additions[0]['contents'])), self.new)

    def test_unchanged_or_invalid_candidate_never_calls_write_api(self):
        self.candidate.write_text(json.dumps(self.old))
        with patch.object(publish, 'gh') as gh, redirect_stdout(io.StringIO()):
            self.assertFalse(publish.publish(self.candidate))
            gh.assert_not_called()
        self.new['models']['gpt-6-sol']['input'] = '0'
        self.candidate.write_text(json.dumps(self.new))
        with patch.object(publish, 'gh') as gh, self.assertRaises(ValueError):
            publish.publish(self.candidate)
        gh.assert_not_called()

    def test_ref_or_head_change_stops_publication(self):
        with patch.dict(os.environ, {'GITHUB_REF': 'refs/heads/untrusted'}), patch.object(publish, 'gh') as gh, self.assertRaises(ValueError):
            publish.publish(self.candidate)
        gh.assert_not_called()
        with patch.object(publish, 'gh', return_value=b'c' * 40) as gh, self.assertRaisesRegex(ValueError, 'Main advanced'):
            publish.publish(self.candidate)
        self.assertEqual(gh.call_count, 1)

    def test_symlinked_candidate_is_rejected(self):
        self.candidate.unlink()
        self.candidate.symlink_to(self.baseline)
        with patch.object(publish, 'gh') as gh, self.assertRaises(ValueError):
            publish.publish(self.candidate)
        gh.assert_not_called()


if __name__ == '__main__':
    unittest.main()
