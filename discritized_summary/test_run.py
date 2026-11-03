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

    def test_elife_legacy_history_keeps_mixed_source_explicit(self):
        self.assertEqual(human_documents({'kind': 'elife_public_reviews', 'parts': {
            'public_reviews': '', 'author_response': 'Reviewer: limited evidence. Author: we disagree.'}}),
            [('reviewer_comments_quoted_in_author_response',
              'Reviewer: limited evidence. Author: we disagree.')])

    def test_accepts_bullets_and_rejects_json(self):
        self.assertEqual(validate('- Limited generalizability.\n- Strong evidence.'),
                         '- Limited generalizability.\n- Strong evidence.')
        with self.assertRaises(ValueError):
            validate('{"bullets": []}')

    def test_resume_and_fingerprint_cover_all_unicode(self):
        calls = []
        def complete(payload):
            calls.append(payload)
            self.assertNotIn('response_format', payload)
            return {'choices': [{'finish_reason': 'stop', 'message': {
                'content': '- The study has limited generalizability.'}}],
                    'usage': {'total_tokens': 12}}
        config = {'chunk_characters': 3, 'max_tokens': 100, 'model': 'test'}
        prompt = {'system': 'test', 'task': '{{source}}'}
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory)
            a = summarize('a😀b\ncdef', {'kind': 'ai', 'document': 'final'}, prompt, config, p, complete)
            self.assertTrue(a['text'].startswith('- '))
            self.assertEqual(''.join(c['messages'][1]['content'].split('\n\n', 1)[1]
                                     for c in calls), 'a😀b\ncdef')
            summarize('a😀b\ncdef', {'kind': 'ai', 'document': 'final'}, prompt, config, p, complete)
            self.assertEqual(len(calls), 3)
            summarize('x😀b\ncdef', {'kind': 'ai', 'document': 'final'}, prompt, config, p, complete)
            self.assertEqual(len(calls), 4)

    def test_truncated_response_not_cached_as_success(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory)
            with self.assertRaises(ValueError):
                summarize('text', {'kind': 'ai', 'document': 'final'}, {'system': 'test', 'task': '{{source}}'},
                          {'chunk_characters': 10, 'max_tokens': 100, 'model': 'test'}, p,
                          lambda _: {'choices': [{'finish_reason': 'length'}]})
            self.assertEqual(len(list(p.glob('*.attempt.json'))), 1)
            self.assertEqual(len(list(p.glob('*.json'))), 1)


if __name__ == '__main__':
    unittest.main()
