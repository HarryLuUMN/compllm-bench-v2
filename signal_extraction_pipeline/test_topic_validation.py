import json
import unittest
from pathlib import Path

from signal_extraction_pipeline.core import load_prompt
from signal_extraction_pipeline.topic_tree import extend_tree, select_topics

ROOT = Path(__file__).resolve().parents[1]
PROMPTS = {key: load_prompt(ROOT / 'prompts' / name) for key, name in (
    ('context', 'topic_tree_context.yaml'), ('select', 'select_review_topics.yaml'),
    ('place', 'place_label_topics.yaml'))}
CONFIG = {'max_input_bytes': 300000}


class TopicValidationTests(unittest.TestCase):
    def test_duplicate_selected_ids_are_one_selection(self):
        selected = select_topics({'statistics': ['low power']}, {'text': 'review'}, PROMPTS, CONFIG,
                                 lambda _: json.dumps({'selected_topic_ids': [0, 0]}))
        self.assertEqual(selected, ['statistics'])

    def test_placement_ids_resolve_to_existing_topic_names(self):
        tree = {'statistics': ['low power']}
        calls = []
        def complete(request):
            calls.append(request)
            return json.dumps({'assignments': [{'label': 'confounding', 'topic_id': 0}], 'new_topics': []})
        updated, assigned, created = extend_tree(tree, ['confounding'], PROMPTS, CONFIG, complete)
        self.assertEqual(assigned, {'confounding': 'statistics'})
        self.assertEqual(updated, {'statistics': ['low power', 'confounding']})
        self.assertEqual(tree, {'statistics': ['low power']})
        self.assertEqual(created, {})
        self.assertIn('"id":0', calls[0][1]['content'])

    def test_invalid_placement_is_corrected_without_regenerating_labels(self):
        answers = iter([
            {'assignments': [{'label': 'confounding', 'topic_id': 99}], 'new_topics': []},
            {'assignments': [{'label': 'confounding', 'topic_id': 0}], 'new_topics': []},
        ])
        calls = []
        def complete(request):
            calls.append(request[:])
            return json.dumps(next(answers))
        result = extend_tree({'statistics': ['low power']}, ['confounding'], PROMPTS, CONFIG, complete)
        self.assertEqual(result[1], {'confounding': 'statistics'})
        self.assertEqual(len(calls), 2)
        self.assertIn('Unknown topic ID', calls[1][-1]['content'])

    def test_repeated_invalid_selection_stops_after_three_calls(self):
        calls = []
        def complete(request):
            calls.append(request)
            return json.dumps({'selected_topic_ids': [True]})
        with self.assertRaisesRegex(ValueError, 'Unknown topic ID'):
            select_topics({'statistics': ['low power']}, {'text': 'review'}, PROMPTS, CONFIG, complete)
        self.assertEqual(len(calls), 3)


if __name__ == '__main__':
    unittest.main()
