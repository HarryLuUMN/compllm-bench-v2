import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from signal_extraction_pipeline.core import load_prompt
from signal_extraction_pipeline.incremental import load_reviews, run_incremental, validate_labels

ROOT = Path(__file__).resolve().parents[1]
PROMPT = load_prompt(ROOT / 'prompts/incremental_review_labels.yaml')
CONFIG = {'max_input_bytes': 300000}


def answer(existing=(), new=()):
    return json.dumps({'existing_labels': list(existing), 'new_labels': list(new)})


class IncrementalTests(unittest.TestCase):
    def test_empty_space_reuse_growth_and_resume_without_backfill(self):
        reviews = [{'review_id': str(i), 'text': 'Review ' + str(i)} for i in range(3)]
        calls = []
        def complete(request):
            data = json.loads(request[1]['content'].split('\n', 2)[2])
            calls.append(data['available_labels'])
            return [answer(new=['data leakage']), answer(['data leakage'], ['lack of generalizability']), answer()][len(calls)-1]
        with tempfile.TemporaryDirectory() as directory:
            state = run_incremental(reviews[:2], directory, PROMPT, CONFIG, complete)
            self.assertEqual(calls, [[], ['data leakage']])
            self.assertEqual(state['reviews'][0]['labels'], ['data leakage'])
            self.assertEqual(state['reviews'][1]['labels'], ['data leakage', 'lack of generalizability'])
            state = run_incremental(reviews, directory, PROMPT, CONFIG, complete)
            self.assertEqual(state['reviews'][0]['labels'], ['data leakage'])
            self.assertEqual(state['reviews'][2]['labels'], [])
            run_incremental(reviews, directory, PROMPT, CONFIG, lambda _: self.fail('Already processed'))
            self.assertEqual(len(calls), 3)

    def test_invalid_output_does_not_add_labels_and_resumes_failed_review(self):
        reviews = [{'review_id': '1', 'text': 'First'}, {'review_id': '2', 'text': 'Second'}]
        with tempfile.TemporaryDirectory() as directory:
            responses = iter([answer(new=['data leakage']), answer(['unknown'])])
            with self.assertRaises(ValueError):
                run_incremental(reviews, directory, PROMPT, CONFIG, lambda _: next(responses))
            state = json.loads((Path(directory) / 'state.json').read_text())
            self.assertEqual(state['label_space'], ['data leakage'])
            self.assertEqual(len(state['reviews']), 1)
            self.assertEqual(state['status'], 'failed')
            state = run_incremental(reviews, directory, PROMPT, CONFIG, lambda _: answer(['data leakage']))
            self.assertEqual(len(state['reviews']), 2)

    def test_rejects_new_duplicates_unknown_existing_and_wrong_schema(self):
        for value in [{'existing_labels': [], 'new_labels': ['DATA  LEAKAGE']},
                      {'existing_labels': ['missing'], 'new_labels': []},
                      {'existing_labels': [], 'new_labels': ['small sample', 'SMALL SAMPLE']},
                      {'existing_labels': 'data leakage', 'new_labels': []}, {}]:
            with self.assertRaises(ValueError):
                validate_labels(value, ['data leakage'])

    def test_changed_input_or_configuration_cannot_reuse_checkpoint(self):
        reviews = [{'review_id': '1', 'text': 'First'}]
        with tempfile.TemporaryDirectory() as directory:
            run_incremental(reviews, directory, PROMPT, CONFIG, lambda _: answer())
            for changed, config in [([{'review_id': '1', 'text': 'Edited'}], CONFIG),
                                    (reviews, {**CONFIG, 'model': 'different'})]:
                with self.assertRaises(ValueError):
                    run_incremental(changed, directory, PROMPT, config, lambda _: self.fail('Must reject'))

    def test_budget_failure_never_truncates_or_calls_model(self):
        with tempfile.TemporaryDirectory() as directory, self.assertRaisesRegex(ValueError, 'budget'):
            run_incremental([{'review_id': '1', 'text': 'Long review'}], directory, PROMPT,
                            {'max_input_bytes': 1}, lambda _: self.fail('Must not call'))

    def test_summary_loader_keeps_review_documents_whole(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / 'paper_0001'
            p.mkdir()
            (p / 'status.json').write_text('{"status":"completed"}')
            (p / 'review.txt').write_text('- One concern.\n- Another concern.')
            metadata = {'summaries': [{'source': {'kind': 'human', 'document': 'R1'}, 'file': 'review.txt'}]}
            (p / 'metadata.json').write_text(json.dumps(metadata))
            reviews = load_reviews(directory)
            self.assertEqual(len(reviews), 1)
            self.assertEqual(reviews[0]['text'], '- One concern.\n- Another concern.')
            metadata['summaries'][0]['file'] = '../escape.txt'
            (p / 'metadata.json').write_text(json.dumps(metadata))
            with self.assertRaises(ValueError):
                load_reviews(directory)

    def test_duplicate_review_ids_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'reviews.json'
            path.write_text(json.dumps([{'review_id': '1', 'text': 'a'}, {'review_id': '1', 'text': 'b'}]))
            with self.assertRaises(ValueError):
                load_reviews(path)

    def test_cli_saves_usage_and_resumes_without_a_tokenizer_or_api_call(self):
        from signal_extraction_pipeline import run_incremental as cli
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'reviews.json'
            source.write_text(json.dumps([{'review_id': 'r1', 'text': 'Data leaked into the test set.'}]))
            argv = ['run_incremental.py', '--input', str(source), '--output', str(root / 'out'),
                    '--env', str(root / 'no-env')]
            response = {'choices': [{'finish_reason': 'stop', 'message': {
                'content': answer(new=['data leakage'])}}], 'usage': {'total_tokens': 42}}
            with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'offline-test'}), \
                    patch.object(sys, 'argv', argv), patch.object(cli, 'post', return_value=response) as api:
                cli.main()
                self.assertEqual(api.call_count, 1)
                self.assertTrue(api.call_args.args[0].endswith('/chat/completions'))
                cli.main()
                self.assertEqual(api.call_count, 1)
            records = list((root / 'out/attempts').glob('*.json'))
            self.assertEqual(len(records), 1)
            saved = json.loads(records[0].read_text())
            self.assertEqual(saved['response']['usage']['total_tokens'], 42)
            self.assertNotIn('offline-test', records[0].read_text())


if __name__ == '__main__':
    unittest.main()
