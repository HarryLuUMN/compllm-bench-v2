import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from quest_computes.topic_label_batch import fatal_error, prepare_input, supervise

CONFIG = {'summary_run': 'test', 'expected_papers': 1, 'expected_reviews_by_kind': {'ai': 1, 'human': 1},
          'max_failures_per_review': 3, 'retry_delay_seconds': 0, 'progress_interval_seconds': 1}


class BatchTests(unittest.TestCase):
    def test_pinned_input_keeps_both_sources_and_rejects_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            runtime = root / 'runtime'
            runtime.mkdir()
            reviews = [{'review_id': kind, 'paper_id': 'paper_0001', 'source': {'kind': kind}, 'text': kind}
                       for kind in ['ai', 'human']]
            (runtime / 'input_reviews.json').write_text(json.dumps(reviews))
            self.assertEqual(prepare_input(root, runtime, CONFIG)['reviews'], 2)
            self.assertEqual(prepare_input(root, runtime, CONFIG)['by_kind'], {'ai': 1, 'human': 1})
            reviews[0]['text'] = 'edited'
            (runtime / 'input_reviews.json').write_text(json.dumps(reviews))
            with self.assertRaisesRegex(ValueError, 'manifest changed'):
                prepare_input(root, runtime, CONFIG)
            with self.assertRaisesRegex(ValueError, 'coverage'):
                prepare_input(root, runtime, {**CONFIG, 'expected_papers': 2})

    def test_fatal_errors_are_not_retried(self):
        for message in ['API returned HTTP 402', 'API returned HTTP 401', 'exceeds input budget']:
            self.assertTrue(fatal_error(message))
        self.assertFalse(fatal_error('Every label must belong to a new topic'))
        self.check_attempts(['HTTP 402'], [0], 1, 1)

    def check_attempts(self, errors, progress, expected_calls, expected_code):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = Path(tmp)
            output = runtime / 'out'
            output.mkdir()
            calls = []
            class Child:
                def __init__(self, command, stdout, **kwargs):
                    self.index = len(calls)
                    calls.append(command)
                    stdout.write(errors[self.index])
                def wait(self, **kwargs):
                    i = self.index
                    done = progress[i]
                    state = {'status': 'completed' if not errors[i] else 'failed',
                             'reviews': [{'review_id': str(n)} for n in range(done)]}
                    (output / 'state.json').write_text(json.dumps(state))
                    return 1 if errors[i] else 0
                def poll(self):
                    return 0
            with patch('quest_computes.topic_label_batch.subprocess.Popen', Child):
                result = supervise(['worker'], runtime, output, {'reviews': 2}, CONFIG)
            self.assertEqual(len(calls), expected_calls)
            self.assertEqual(result, expected_code)

    def test_repeated_failure_stops_at_bound(self):
        self.check_attempts(['invalid labels'] * 3, [1, 1, 1], 3, 1)

    def test_progress_resets_failure_count_and_can_complete(self):
        self.check_attempts(['invalid labels', 'invalid labels', 'invalid labels', ''], [0, 0, 1, 2], 4, 0)


if __name__ == '__main__':
    unittest.main()
