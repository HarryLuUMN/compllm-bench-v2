import json
import os
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from signal_extraction_pipeline.core import cache_key, load_prompt
from signal_extraction_pipeline.incremental import run_incremental
from signal_extraction_pipeline.topic_tree import tree_messages, validate_new_topics, validate_placement, validate_tree

ROOT = Path(__file__).resolve().parents[1]
LABEL = load_prompt(ROOT / 'prompts/tree_review_labels.yaml')
TOPICS = {'context': load_prompt(ROOT / 'prompts/topic_tree_context.yaml'),
          'place': load_prompt(ROOT / 'prompts/place_label_topics.yaml')}
CONFIG = {'max_input_bytes': 300000}


def labels(existing=(), new=()):
    return {'existing_labels': list(existing), 'new_labels': list(new)}


def placement(assignments=(), groups=()):
    return {'assignments': [{'label': label, 'topic': topic} for label, topic in assignments],
            'new_topics': [{'topic': topic, 'labels': group} for topic, group in groups]}


def call_payload(request):
    return json.loads(request[-1]['content'].strip().rsplit('\n', 1)[-1])


def call_tree(request):
    return json.loads(request[1]['content'].strip().rsplit('\n', 1)[-1])


class TopicTreeTests(unittest.TestCase):
    def test_label_then_combined_placement_context_and_earlier_reviews_unchanged(self):
        reviews = [{'review_id': str(i), 'text': f'Review {i}'} for i in range(4)]
        responses = iter([
            labels(new=['data leakage', 'inadequate validation']),
            placement(groups=[('data integrity', ['data leakage', 'inadequate validation'])]),
            labels(['data leakage'], ['missing data handling', 'lack of generalizability']),
            placement([('missing data handling', 'data integrity')],
                      [('external validity', ['lack of generalizability'])]),
            labels(['data leakage']), labels(),
        ])
        calls = []
        def complete(request):
            calls.append(deepcopy(request))
            payload = call_payload(request)
            self.assertNotIn('available_labels', payload)
            self.assertNotIn('topic_label_tree', payload)
            self.assertEqual(set(payload), {'review'} if 'review' in payload else {'new_labels'})
            return json.dumps(next(responses))
        with tempfile.TemporaryDirectory() as directory:
            state = run_incremental(reviews[:1], directory, LABEL, CONFIG, complete, TOPICS)
            original = deepcopy(state['reviews'][0])
            self.assertEqual(call_tree(calls[0]), {})
            self.assertEqual(call_tree(calls[1]), {})
            state = run_incremental(reviews, directory, LABEL, CONFIG, complete, TOPICS)
            self.assertEqual(len(calls), 6)
            self.assertEqual(call_tree(calls[2]), {'data integrity': ['data leakage', 'inadequate validation']})
            self.assertEqual(calls[2][:2], calls[3][:2])
            self.assertEqual(call_payload(calls[3]), {'new_labels': ['missing data handling', 'lack of generalizability']})
            self.assertEqual(call_tree(calls[4]), state['topic_label_tree'])
            self.assertEqual(state['reviews'][0], original)
            self.assertEqual(state['reviews'][1]['topic_labels'], {
                'data integrity': ['data leakage', 'missing data handling'],
                'external validity': ['lack of generalizability']})
            self.assertEqual(state['reviews'][1]['topic_assignments'], {'missing data handling': 'data integrity'})
            self.assertEqual(state['reviews'][1]['new_topics'], {'external validity': ['lack of generalizability']})
            self.assertEqual(state['reviews'][2]['topic_labels'], {'data integrity': ['data leakage']})
            self.assertEqual(state['reviews'][3]['topic_labels'], {})
            run_incremental(reviews, directory, LABEL, CONFIG, lambda _: self.fail('Must resume'), TOPICS)
            self.assertEqual(json.loads((Path(directory) / 'topic_label_tree.json').read_text()), state['topic_label_tree'])
            self.assertEqual(json.loads((Path(directory) / 'topics_by_review.json').read_text())['0'], original['topic_labels'])

    def test_actual_message_prefix_is_shared_before_varying_review_and_stage_instructions(self):
        tree = {'data integrity': ['data leakage'], 'external validity': ['lack of generalizability']}
        first = tree_messages(TOPICS['context'], LABEL, tree, {'review': {'text': 'First review sentinel'}})
        second = tree_messages(TOPICS['context'], LABEL, tree, {'review': {'text': 'Second review sentinel'}})
        placed = tree_messages(TOPICS['context'], TOPICS['place'], tree, {'new_labels': ['missing data handling']})
        for request in [first, second, placed]:
            self.assertEqual(request[0]['role'], 'system')
            self.assertEqual(request[1]['role'], 'user')
            self.assertEqual(call_tree(request), tree)
            self.assertEqual(request[:2], first[:2])
            self.assertNotIn('sentinel', json.dumps(request[:2]))
            self.assertEqual(sum(m['content'].count(json.dumps(tree, ensure_ascii=False, separators=(',', ':')))
                                 for m in request), 1)
        self.assertNotEqual(first[-1], second[-1])
        self.assertNotEqual(first[-1], placed[-1])
        prefix = json.dumps(first[:2], ensure_ascii=False).encode()
        self.assertEqual(prefix, json.dumps(placed[:2], ensure_ascii=False).encode())

    def test_placement_does_not_repeat_long_review(self):
        review = {'text': 'Review-specific text. ' * 1000}
        tree = {'data integrity': ['data leakage']}
        generate = tree_messages(TOPICS['context'], LABEL, tree, {'review': review})
        place = tree_messages(TOPICS['context'], TOPICS['place'], tree, {'new_labels': ['missing data handling']})
        self.assertIn(review['text'], generate[-1]['content'])
        self.assertNotIn('Review-specific text.', json.dumps(place))
        self.assertEqual(call_tree(place), tree)
        self.assertLess(len(json.dumps(place)), len(json.dumps(generate)))

    def test_all_new_labels_fit_existing_topic(self):
        responses = iter([labels(new=['data leakage']), placement(groups=[('data integrity', ['data leakage'])]),
                          labels(new=['missing data handling']),
                          placement([('MISSING DATA HANDLING', 'DATA INTEGRITY')])])
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

    def test_failed_combined_placement_rolls_back_and_retries_only_unfinished_review(self):
        reviews = [{'review_id': str(i), 'text': 'review'} for i in range(2)]
        responses = iter([labels(new=['data leakage']), placement(groups=[('data integrity', ['data leakage'])]),
                          labels(new=['missing data handling', 'lack of generalizability']),
                          placement([('missing data handling', 'data integrity')])])
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                run_incremental(reviews, directory, LABEL, CONFIG, lambda _: json.dumps(next(responses)), TOPICS)
            state = json.loads((Path(directory) / 'state.json').read_text())
            self.assertEqual(state['topic_label_tree'], {'data integrity': ['data leakage']})
            self.assertEqual(state['label_space'], ['data leakage'])
            self.assertEqual(len(state['reviews']), 1)
            retry = iter([labels(new=['lack of generalizability']),
                          placement(groups=[('external validity', ['lack of generalizability'])])])
            state = run_incremental(reviews, directory, LABEL, CONFIG, lambda _: json.dumps(next(retry)), TOPICS)
            self.assertEqual(state['status'], 'completed')
            self.assertEqual(len(state['reviews']), 2)

    def test_selected_topics_hide_unselected_labels_and_resume_prefix(self):
        prompts = {**TOPICS, 'select': load_prompt(ROOT / 'prompts/select_review_topics.yaml')}
        responses = iter([
            labels(new=['data leakage', 'lack of generalizability']),
            placement(groups=[('data integrity', ['data leakage']),
                              ('external validity', ['lack of generalizability'])]),
            {'selected_topic_ids': [0]}, labels(['data leakage']),
        ])
        calls = []
        def complete(request):
            calls.append(request)
            return json.dumps(next(responses))
        reviews = [{'review_id': str(i), 'text': 'review'} for i in range(2)]
        with tempfile.TemporaryDirectory() as directory:
            first = run_incremental(reviews[:1], directory, LABEL, CONFIG, complete, prompts)
            original = deepcopy(first['reviews'][0])
            state = run_incremental(reviews, directory, LABEL, CONFIG, complete, prompts)
            self.assertEqual(call_tree(calls[2]), [{'id': 0, 'topic': 'data integrity'}, {'id': 1, 'topic': 'external validity'}])
            self.assertNotIn('data leakage', json.dumps(calls[2]))
            self.assertEqual(call_tree(calls[3]), {'data integrity': ['data leakage']})
            self.assertNotIn('external validity', call_tree(calls[3]))
            self.assertEqual(state['reviews'][0], original)
            self.assertEqual(state['reviews'][1]['selected_topics'], ['data integrity'])
            run_incremental(reviews, directory, LABEL, CONFIG, lambda _: self.fail('Must resume'), prompts)

    def test_invalid_placements_rejected(self):
        cases = [placement([('a', 'unknown')]), placement(),
                 placement(groups=[('new', ['b'])]),
                 placement([('a', 'topic')], [('new', ['a'])]),
                 placement([('a', 'topic'), ('a', 'topic')]),
                 placement(groups=[('topic', ['a'])]),
                 placement(groups=[('new', [])]),
                 {'assignments': {}, 'new_topics': []},
                 {'assignments': [], 'remaining_labels': ['a']},
                 placement(groups=[('one', ['a']), ('two', ['a'])])]
        for answer in cases:
            with self.subTest(answer=answer), self.assertRaises(ValueError):
                validate_placement(answer, ['a'], {'topic': ['existing']})
        with self.assertRaises(ValueError):
            validate_placement(placement([('a', 'topic')]), ['a'], {})

    def test_invalid_new_topics_and_duplicate_parentage_rejected(self):
        for answer in [placement(groups=[('old', ['a'])]), placement(groups=[('new', [])]), placement(),
                       placement(groups=[('new', ['unknown'])]), placement(groups=[('one', ['a']), ('two', ['a'])]),
                       placement(groups=[('new', ['a']), ('NEW', ['b'])])]:
            with self.subTest(answer=answer), self.assertRaises(ValueError):
                validate_new_topics({'new_topics': answer['new_topics']}, ['a'], {'old': ['existing']})
        for tree in [{'one': ['a'], 'two': ['a']}, {'one': []}, {'one': ['b']}]:
            with self.assertRaises(ValueError):
                validate_tree(tree, ['a'])

    def test_flat_and_legacy_tree_checkpoints_are_not_silently_converted(self):
        reviews = [{'review_id': '1', 'text': 'review'}]
        with tempfile.TemporaryDirectory() as directory:
            run_incremental(reviews, directory, LABEL, CONFIG, lambda _: json.dumps(labels()))
            with self.assertRaisesRegex(ValueError, 'prompt changed'):
                run_incremental(reviews, directory, LABEL, CONFIG, lambda _: self.fail('Must reject'), TOPICS)
        with tempfile.TemporaryDirectory() as directory:
            state = run_incremental(reviews, directory, LABEL, CONFIG, lambda _: json.dumps(labels()), TOPICS)
            state['fingerprint'] = cache_key({'prompt': LABEL, 'configuration': CONFIG, 'format': 3, 'topic_prompts': TOPICS})
            (Path(directory) / 'state.json').write_text(json.dumps(state))
            with self.assertRaisesRegex(ValueError, 'prompt changed'):
                run_incremental(reviews, directory, LABEL, CONFIG, lambda _: self.fail('Must reject'), TOPICS)

    def test_topic_stage_budget_guards_full_tree_without_committing_labels(self):
        prompt = {'system': 'labels', 'task': '{{input}}'}
        huge = {**TOPICS, 'place': {'system': 'x' * 10000, 'task': '{{input}}'}}
        calls = []
        def complete(request):
            calls.append(request)
            return json.dumps(labels(new=['data leakage']))
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, 'budget'):
                run_incremental([{'review_id': '1', 'text': 'review'}], directory, prompt,
                                {'max_input_bytes': 2000}, complete, huge)
            state = json.loads((Path(directory) / 'state.json').read_text())
            self.assertEqual(state['topic_label_tree'], {})
            self.assertEqual(state['label_space'], [])
            self.assertEqual(len(calls), 1)

    def test_generation_budget_includes_every_topic_without_pruning(self):
        large = 'x' * 10000
        tree = {'topic one': [large], 'topic two': ['data leakage']}
        new = [large, 'data leakage']
        reviews = [{'review_id': str(i), 'text': 'short review'} for i in range(2)]
        generation = tree_messages(TOPICS['context'], LABEL, tree, {'review': reviews[1]})
        placement_request = tree_messages(TOPICS['context'], TOPICS['place'], {}, {'new_labels': new})
        budget = len(json.dumps(placement_request, ensure_ascii=False).encode()) + 1
        self.assertGreater(len(json.dumps(generation, ensure_ascii=False).encode()), budget)
        self.assertEqual(call_tree(generation), tree)
        responses = iter([labels(new=new), placement(groups=list(tree.items()))])
        calls = []
        def complete(request):
            calls.append(request)
            return json.dumps(next(responses))
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, 'budget'):
                run_incremental(reviews, directory, LABEL, {'max_input_bytes': budget}, complete, TOPICS)
            state = json.loads((Path(directory) / 'state.json').read_text())
            self.assertEqual(state['topic_label_tree'], tree)
            self.assertEqual(len(state['reviews']), 1)
            self.assertEqual(len(calls), 2)

    def test_cli_tree_mode_saves_usage_and_never_exposes_credentials(self):
        from signal_extraction_pipeline import run_incremental as cli
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'input.json'
            source.write_text(json.dumps([{'review_id': 'r1', 'text': 'A review.'}]))
            argv = ['run_incremental.py', '--topic-tree', '--input', str(source),
                    '--output', str(root / 'out'), '--env', str(root / 'absent')]
            responses = [{'choices': [{'finish_reason': 'stop', 'message': {'content': json.dumps(value)}}],
                          'usage': {'total_tokens': 10, 'prompt_cache_hit_tokens': 7}} for value in
                         [labels(new=['data leakage']), placement(groups=[('data integrity', ['data leakage'])])]]
            with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'offline-only'}), \
                    patch.object(sys, 'argv', argv), patch.object(cli, 'post', side_effect=responses) as api:
                cli.main()
                self.assertEqual(api.call_count, 2)
                first, second = [call.args[1]['messages'] for call in api.call_args_list]
                self.assertEqual(call_tree(first), {})
                self.assertEqual(call_tree(second), [])
                self.assertEqual(set(call_payload(first)), {'review', 'matched_labels'})
                self.assertEqual(set(call_payload(second)), {'new_labels'})
                cli.main()
                self.assertEqual(api.call_count, 2)
            attempts = list((root / 'out/attempts').glob('*.json'))
            self.assertEqual(len(attempts), 2)
            self.assertEqual(sum(json.loads(p.read_text())['response']['usage']['total_tokens'] for p in attempts), 20)
            self.assertTrue(all(json.loads(p.read_text())['response']['usage']['prompt_cache_hit_tokens'] == 7 for p in attempts))
            self.assertTrue(all('offline-only' not in p.read_text() for p in attempts))


if __name__ == '__main__':
    unittest.main()
