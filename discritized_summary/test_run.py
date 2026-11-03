import json
import tempfile
import unittest
from pathlib import Path

from discritized_summary.run import human_documents, summarize, validate


class SummaryTests(unittest.TestCase):
    def test_excludes_author_and_editor(self):
        self.assertEqual(human_documents({'kind': 'bmc_peer_review_reports', 'parts': {'assets': [
            {'label': 'AuthorComment_V1', 'text': 'author'},
            {'label': 'ReviewerReport_V0_R1', 'text': 'review'}]}}), [('ReviewerReport_V0_R1', 'review')])
        self.assertEqual(human_documents({'kind': 'elife_public_reviews', 'parts': {
            'public_reviews': 'review', 'author_response': 'author', 'elife_assessment': 'editor'}}),
            [('public_reviews', 'review')])

    def test_quote_must_be_real(self):
        with self.assertRaises(ValueError):
            validate({'bullets': [{'text': 'claim', 'quote': 'invented', 'reviewer': 'R1'}]}, 'source')

    def test_resume_and_fingerprint_cover_all_unicode(self):
        calls = []
        def complete(payload):
            calls.append(payload)
            source = json.loads(payload['messages'][1]['content'])['text']
            return {'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps({
                'bullets': [{'text': source, 'quote': source, 'reviewer': 'AI'}]})}}],
                    'usage': {'total_tokens': 12}}
        config = {'chunk_characters': 3, 'max_tokens': 100, 'model': 'test'}
        prompt = {'system': 'test', 'task': '{{source}}'}
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory)
            a = summarize('a😀b\ncdef', {'kind': 'ai'}, prompt, config, p, complete)
            self.assertEqual(''.join(b['quote'] for b in a['bullets']), 'a😀b\ncdef')
            summarize('a😀b\ncdef', {'kind': 'ai'}, prompt, config, p, complete)
            self.assertEqual(len(calls), 3)
            summarize('x😀b\ncdef', {'kind': 'ai'}, prompt, config, p, complete)
            self.assertEqual(len(calls), 4)

    def test_truncated_response_not_cached_as_success(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory)
            with self.assertRaises(ValueError):
                summarize('text', {}, {'system': 'test', 'task': '{{source}}'},
                          {'chunk_characters': 10, 'max_tokens': 100, 'model': 'test'}, p,
                          lambda _: {'choices': [{'finish_reason': 'length'}]})
            self.assertEqual(len(list(p.glob('*.attempt.json'))), 1)
            self.assertEqual(len(list(p.glob('*.json'))), 1)


if __name__ == '__main__':
    unittest.main()
