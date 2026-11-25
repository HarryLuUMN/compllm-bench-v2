import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from signal_extraction_pipeline.core import load_prompt
from signal_extraction_pipeline.incremental import run_incremental
from signal_extraction_pipeline.label_matching import ReviewStages, match_labels, request_size
from signal_extraction_pipeline.topic_tree import tree_messages

ROOT = Path(__file__).resolve().parents[1]
PROMPTS = {key: load_prompt(ROOT / 'prompts' / filename) for key, filename in (
    ('context', 'topic_tree_context.yaml'), ('select', 'select_review_topics.yaml'),
    ('match', 'match_review_labels.yaml'), ('place', 'place_label_topics.yaml'))}
GENERATE = load_prompt(ROOT / 'prompts/tree_review_labels.yaml')
REVIEW = {'review_id': 'r1', 'text': 'Complete review: concern about all reported issues. Ω'}


def context(request):
    return json.loads(request[1]['content'].strip().rsplit('\n', 1)[-1])


class LabelMatchingTests(unittest.TestCase):
    def test_small_space_is_one_call_and_rejects_unknown_label(self):
        calls = []
        def complete(request):
            calls.append(request)
            return json.dumps({'selected_label_ids': [999 if len(calls) == 1 else 0]})
        with tempfile.TemporaryDirectory() as directory:
            result, count = match_labels(REVIEW, {'statistics': ['low power']}, PROMPTS,
                                         {'max_input_bytes': 20000, 'correction_reserve_bytes': 2000},
                                         complete, ReviewStages(directory))
        self.assertEqual((result, count), (['low power'], 1))
        self.assertEqual(len(calls), 2)
        self.assertIn('Unknown candidate label', calls[1][-1]['content'])

    def test_large_single_topic_preserves_all_candidates_and_complete_review(self):
        labels = [f'problem {i} ' + 'x' * 150 for i in range(40)]
        seen = []
        config = {'max_input_bytes': 5000, 'correction_reserve_bytes': 1000, 'label_match_workers': 1}
        def complete(request):
            self.assertLessEqual(request_size(request), 4000)
            self.assertIn(REVIEW['text'], request[-1]['content'])
            supplied = context(request)['statistics']
            seen.extend(item['label'] for item in supplied)
            return json.dumps({'selected_label_ids': [supplied[0]['id']]})
        with tempfile.TemporaryDirectory() as directory:
            result, count = match_labels(REVIEW, {'statistics': labels}, PROMPTS, config, complete, ReviewStages(directory))
        self.assertGreater(count, 1)
        self.assertEqual(seen, labels)
        self.assertEqual(len(result), count)

    def test_parallel_batches_cache_successes_and_retry_only_missing_batch(self):
        labels = [f'problem {i} ' + 'x' * 250 for i in range(24)]
        config = {'max_input_bytes': 5000, 'correction_reserve_bytes': 1000, 'label_match_workers': 2}
        gate = threading.Barrier(2)
        calls = []
        lock = threading.Lock()
        def complete(request):
            supplied = context(request)['statistics']
            with lock:
                calls.append(supplied[0]['label'])
                number = len(calls)
            if number <= 2:
                gate.wait(timeout=3)
            if supplied[0]['label'] == labels[0]:
                raise RuntimeError('Interrupted batch')
            return json.dumps({'selected_label_ids': [supplied[0]['id']]})
        with tempfile.TemporaryDirectory() as directory:
            stages = ReviewStages(directory)
            with self.assertRaisesRegex(RuntimeError, 'Interrupted'):
                match_labels(REVIEW, {'statistics': labels}, PROMPTS, config, complete, stages)
            successful = len(list(Path(directory).glob('match_*.json')))
            resumed = []
            def finish(request):
                supplied = context(request)['statistics']
                resumed.append(supplied[0]['label'])
                return json.dumps({'selected_label_ids': [supplied[0]['id']]})
            result, count = match_labels(REVIEW, {'statistics': labels}, PROMPTS, config, finish, stages)
            self.assertEqual(len(resumed), count - successful)
            self.assertEqual(len(result), count)
            match_labels(REVIEW, {'statistics': labels}, PROMPTS, config,
                         lambda _: self.fail('Cached batches must not run again'), stages)

    def test_pipeline_resumes_generation_without_repeating_matching_or_completed_review(self):
        config = {'max_input_bytes': 300000}
        reviews = [REVIEW, {**REVIEW, 'review_id': 'r2'}]
        first_answers = iter([
            {'existing_labels': [], 'new_labels': ['low power']},
            {'assignments': [], 'new_topics': [{'topic': 'statistics', 'labels': ['low power']}]},
        ])
        with tempfile.TemporaryDirectory() as directory:
            first = run_incremental(reviews[:1], directory, GENERATE, config,
                                    lambda _: json.dumps(next(first_answers)), PROMPTS)
            saved = json.dumps(first['reviews'][0], sort_keys=True)
            def interrupted(request):
                if 'selected_topic_ids' in request[-1]['content']:
                    return '{"selected_topic_ids":[0]}'
                if 'matched_labels' not in request[-1]['content']:
                    return '{"selected_label_ids":[0]}'
                raise RuntimeError('Interrupted generation')
            with self.assertRaisesRegex(RuntimeError, 'Interrupted generation'):
                run_incremental(reviews, directory, GENERATE, config, interrupted, PROMPTS)
            requests = []
            def resume(request):
                requests.append(request)
                self.assertIn('matched_labels', request[-1]['content'])
                return '{"existing_labels":["low power"],"new_labels":[]}'
            state = run_incremental(reviews, directory, GENERATE, config, resume, PROMPTS)
            self.assertEqual(len(requests), 1)
            self.assertEqual(json.dumps(state['reviews'][0], sort_keys=True), saved)
            self.assertEqual(state['reviews'][1]['label_match_batches'], 1)
            self.assertEqual(state['status'], 'completed')

    def test_large_candidate_space_exceeding_old_guard_is_fully_matched(self):
        tree = {f'topic {i}': [f'insufficient reporting for issue {i}-{j} in study methods'
                               for j in range(106)] for i in range(59)}
        whole = tree_messages(PROMPTS['context'], PROMPTS['match'], tree, {'review': REVIEW})
        self.assertGreater(request_size(whole), 300000)
        seen = []
        def complete(request):
            self.assertLessEqual(request_size(request), 284000)
            supplied = [label for group in context(request).values() for label in group]
            seen.extend(item['label'] for item in supplied)
            return '{"selected_label_ids":[]}'
        with tempfile.TemporaryDirectory() as directory:
            matched, count = match_labels(REVIEW, tree, PROMPTS,
                                          {'max_input_bytes': 300000, 'label_match_workers': 1},
                                          complete, ReviewStages(directory))
        self.assertGreater(count, 1)
        self.assertEqual(seen, [label for group in tree.values() for label in group])
        self.assertEqual(matched, [])

    def test_missing_label_generation_never_echoes_matched_labels(self):
        from signal_extraction_pipeline.topic_tree import validate_tree
        prompt = load_prompt(ROOT / 'prompts/generate_missing_labels.yaml')
        labels = [f'existing problem {i}' for i in range(1000)]
        first = {'review_id': 'seed', 'text': 'seed'}
        with tempfile.TemporaryDirectory() as directory:
            empty = iter([{'new_labels': []}])
            state = run_incremental([first], directory, prompt, {'max_input_bytes': 300000},
                                    lambda _: json.dumps(next(empty)), PROMPTS)
            state['label_space'] = labels
            state['topic_label_tree'] = {'statistics': labels}
            Path(directory, 'state.json').write_text(json.dumps(state))
            answers = iter([{'selected_topic_ids': [0]}, {'selected_label_ids': list(range(1000))},
                            {'new_labels': []}])
            result = run_incremental([first, REVIEW], directory, prompt, {'max_input_bytes': 300000},
                                     lambda _: json.dumps(next(answers)), PROMPTS)
            self.assertEqual(result['reviews'][-1]['labels'], labels)
            self.assertEqual(result['reviews'][-1]['new_labels'], [])
            self.assertEqual(result['reviews'][0], state['reviews'][0])
            validate_tree(result['topic_label_tree'], result['label_space'])

    def test_ids_are_batch_local_typed_and_duplicate_safe(self):
        for invalid in [True, -1, 1, '0']:
            with self.subTest(invalid=invalid), tempfile.TemporaryDirectory() as directory:
                calls = []
                def complete(request):
                    calls.append(request)
                    return json.dumps({'selected_label_ids': [invalid] if len(calls) == 1 else [0, 0]})
                result, _ = match_labels(REVIEW, {'statistics': ['exact label']}, PROMPTS,
                                         {'max_input_bytes': 20000, 'correction_reserve_bytes': 2000},
                                         complete, ReviewStages(directory))
                self.assertEqual(result, ['exact label'])
                self.assertEqual(len(calls), 2)

    def test_oversized_review_fails_before_any_call(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, 'no text was truncated'):
                match_labels({**REVIEW, 'text': 'x' * 10000}, {'topic': ['label']}, PROMPTS,
                             {'max_input_bytes': 2000, 'correction_reserve_bytes': 100},
                             lambda _: self.fail('Must not call'), ReviewStages(directory))


if __name__ == '__main__':
    unittest.main()
