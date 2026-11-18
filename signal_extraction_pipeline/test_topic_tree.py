import json
import os
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from signal_extraction_pipeline.core import load_prompt
from signal_extraction_pipeline.incremental import run_incremental
from signal_extraction_pipeline.topic_tree import validate_assignment, validate_new_topics, validate_tree

ROOT = Path(__file__).resolve().parents[1]
LABEL = load_prompt(ROOT / 'prompts/incremental_review_labels.yaml')
TOPICS = {'assign': load_prompt(ROOT / 'prompts/assign_label_topics.yaml'),
          'create': load_prompt(ROOT / 'prompts/create_label_topics.yaml')}
CONFIG = {'max_input_bytes': 300000}


def labels(existing=(), new=()):
    return {'existing_labels': list(existing), 'new_labels': list(new)}


def topics(*items):
    return {'new_topics': [{'topic': topic, 'labels': group} for topic, group in items]}


def call_payload(request):
    return json.loads(request[1]['content'].split('\n', 2)[2])


class TopicTreeTests(unittest.TestCase):
    def test_exact_stage_order_complete_context_and_earlier_reviews_unchanged(self):
        reviews = [{'review_id': str(i), 'text': f'Review {i}'} for i in range(4)]
        responses = iter([
            labels(new=['data leakage', 'inadequate validation']),
            topics(('data integrity', ['data leakage', 'inadequate validation'])),
            labels(['data leakage'], ['missing data handling', 'lack of generalizability']),
            {'assignments': [{'label': 'missing data handling', 'topic': 'data integrity'}],
             'remaining_labels': ['lack of generalizability']},
            topics(('external validity', ['lack of generalizability'])),
            labels(['data leakage']), labels(),
        ])
        calls = []
        def complete(request):
            payload = call_payload(request)
            calls.append(payload)
            self.assertIn('review', payload)
            self.assertIn('topic_label_tree', payload)
            return json.dumps(next(responses))
        with tempfile.TemporaryDirectory() as directory:
            state = run_incremental(reviews[:1], directory, LABEL, CONFIG, complete, TOPICS)
            original = deepcopy(state['reviews'][0])
            self.assertEqual(calls[0]['topic_label_tree'], {})
            self.assertIn('remaining_labels', calls[1])
            state = run_incremental(reviews, directory, LABEL, CONFIG, complete, TOPICS)
            self.assertEqual(len(calls), 7)
            self.assertEqual(calls[2]['topic_label_tree'], {'data integrity': ['data leakage', 'inadequate validation']})
            self.assertIn('new_labels', calls[3])
            self.assertEqual(calls[4]['remaining_labels'], ['lack of generalizability'])
            self.assertIn('missing data handling', calls[4]['topic_label_tree']['data integrity'])
            self.assertEqual(state['reviews'][0], original)
            self.assertEqual(state['reviews'][1]['topic_labels'], {
                'data integrity': ['data leakage', 'missing data handling'],
                'external validity': ['lack of generalizability']})
            self.assertEqual(state['reviews'][2]['topic_labels'], {'data integrity': ['data leakage']})
            self.assertEqual(state['reviews'][3]['topic_labels'], {})
            run_incremental(reviews, directory, LABEL, CONFIG, lambda _: self.fail('Must resume'), TOPICS)
            self.assertEqual(json.loads((Path(directory) / 'topic_label_tree.json').read_text()), state['topic_label_tree'])
            self.assertEqual(json.loads((Path(directory) / 'topics_by_review.json').read_text())['0'], original['topic_labels'])

    def test_all_new_labels_fit_existing_topic_without_creation_call(self):
        responses = iter([labels(new=['data leakage']), topics(('data integrity', ['data leakage'])),
                          labels(new=['missing data handling']),
                          {'assignments': [{'label': 'MISSING DATA HANDLING', 'topic': 'DATA INTEGRITY'}],
                           'remaining_labels': []}])
        calls = []
        def complete(request):
            calls.append(request)
            return json.dumps(next(responses))
        with tempfile.TemporaryDirectory() as directory:
            state = run_incremental([{'review_id': str(i), 'text': 'review'} for i in range(2)],
                                    directory, LABEL, CONFIG, complete, TOPICS)
            self.assertEqual(len(calls), 4)
            self.assertEqual(state['topic_label_tree'], {'data integrity': ['data leakage', 'missing data handling']})
            self.assertEqual(state['reviews'][1]['new_topics'], {})

    def test_failed_creation_rolls_back_assignments_and_retries_only_unfinished_review(self):
        reviews = [{'review_id': str(i), 'text': 'review'} for i in range(2)]
        responses = iter([labels(new=['data leakage']), topics(('data integrity', ['data leakage'])),
                          labels(new=['missing data handling', 'lack of generalizability']),
                          {'assignments': [{'label': 'missing data handling', 'topic': 'data integrity'}],
                           'remaining_labels': ['lack of generalizability']},
                          topics(('external validity', []))])
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                run_incremental(reviews, directory, LABEL, CONFIG, lambda _: json.dumps(next(responses)), TOPICS)
            state = json.loads((Path(directory) / 'state.json').read_text())
            self.assertEqual(state['topic_label_tree'], {'data integrity': ['data leakage']})
            self.assertEqual(state['label_space'], ['data leakage'])
            self.assertEqual(len(state['reviews']), 1)
            retry = iter([labels(new=['lack of generalizability']),
                          {'assignments': [], 'remaining_labels': ['lack of generalizability']},
                          topics(('external validity', ['lack of generalizability']))])
            state = run_incremental(reviews, directory, LABEL, CONFIG, lambda _: json.dumps(next(retry)), TOPICS)
            self.assertEqual(state['status'], 'completed')
            self.assertEqual(len(state['reviews']), 2)

    def test_invalid_assignments_rejected(self):
        cases = [
            {'assignments': [{'label': 'a', 'topic': 'unknown'}], 'remaining_labels': []},
            {'assignments': [], 'remaining_labels': []},
            {'assignments': [], 'remaining_labels': ['b']},
            {'assignments': [{'label': 'a', 'topic': 'topic'}], 'remaining_labels': ['a']},
            {'assignments': [{'label': 'a', 'topic': 'topic'}, {'label': 'a', 'topic': 'topic'}], 'remaining_labels': []},
        ]
        for answer in cases:
            with self.subTest(answer=answer), self.assertRaises(ValueError):
                validate_assignment(answer, ['a'], {'topic': ['existing']})

    def test_invalid_new_topics_and_duplicate_parentage_rejected(self):
        for answer in [topics(('old', ['a'])), topics(('new', [])), topics(),
                       topics(('new', ['unknown'])), topics(('one', ['a']), ('two', ['a'])),
                       topics(('new', ['a']), ('NEW', ['b']))]:
            with self.subTest(answer=answer), self.assertRaises(ValueError):
                validate_new_topics(answer, ['a'], {'old': ['existing']})
        for tree in [{'one': ['a'], 'two': ['a']}, {'one': []}, {'one': ['b']}]:
            with self.assertRaises(ValueError):
                validate_tree(tree, ['a'])

    def test_flat_checkpoint_cannot_be_silently_converted(self):
        reviews = [{'review_id': '1', 'text': 'review'}]
        with tempfile.TemporaryDirectory() as directory:
            run_incremental(reviews, directory, LABEL, CONFIG, lambda _: json.dumps(labels()))
            with self.assertRaisesRegex(ValueError, 'prompt changed'):
                run_incremental(reviews, directory, LABEL, CONFIG, lambda _: self.fail('Must reject'), TOPICS)

    def test_topic_stage_budget_guards_full_tree_without_committing_labels(self):
        prompt = {'system': 'labels', 'task': '{{input}}'}
        huge = {**TOPICS, 'create': {'system': 'x' * 10000, 'task': '{{input}}'}}
        calls = []
        def complete(request):
            calls.append(request)
            return json.dumps(labels(new=['data leakage']))
        with tempfile.TemporaryDirectory() as directory, self.assertRaisesRegex(ValueError, 'budget'):
            run_incremental([{'review_id': '1', 'text': 'review'}], directory, prompt,
                            {'max_input_bytes': 1000}, complete, huge)
        self.assertEqual(len(calls), 1)

    def test_cli_tree_mode_saves_each_stage_usage_without_live_calls(self):
        from signal_extraction_pipeline import run_incremental as cli
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'input.json'
            source.write_text(json.dumps([{'review_id': 'r1', 'text': 'A review.'}]))
            argv = ['run_incremental.py', '--topic-tree', '--input', str(source),
                    '--output', str(root / 'out'), '--env', str(root / 'absent')]
            responses = [{'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(value)}}],
                          'usage': {'total_tokens': 10}} for value in
                         [labels(new=['data leakage']), topics(('data integrity', ['data leakage']))]]
            with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'offline-only'}), \
                    patch.object(sys, 'argv', argv), patch.object(cli, 'post', side_effect=responses) as api:
                cli.main()
                self.assertEqual(api.call_count, 2)
                cli.main()
                self.assertEqual(api.call_count, 2)
            attempts = list((root / 'out/attempts').glob('*.json'))
            self.assertEqual(len(attempts), 2)
            self.assertEqual(sum(json.loads(p.read_text())['response']['usage']['total_tokens'] for p in attempts), 20)
            self.assertTrue(all('offline-only' not in p.read_text() for p in attempts))


if __name__ == '__main__':
    unittest.main()
