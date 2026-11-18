import json
import hashlib
import tempfile
import unittest
from pathlib import Path

from server import read_events, trace_path, summary_detail, original_detail, LabelResults


class TraceTests(unittest.TestCase):
    def test_incremental_and_incomplete_line(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'run.jsonl'
            first = b'{"type":"run_start"}\n'
            path.write_bytes(first + b'{"type":')
            data = read_events(path, 0)
            self.assertEqual(data['cursor'], len(first))
            self.assertEqual(len(data['events']), 1)
            self.assertEqual(read_events(path, data['cursor'])['events'], [])
            with path.open('ab') as stream:
                stream.write(b'"run_end"}\n')
            self.assertEqual(read_events(path, data['cursor'])['events'][0]['type'], 'run_end')
            path.write_text('')
            self.assertTrue(read_events(path, data['cursor'])['reset'])

    def test_pagination(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'run.jsonl'
            path.write_text((json.dumps({'type': 'test'}) + '\n') * 501)
            first = read_events(path, 0)
            self.assertEqual(len(first['events']), 500)
            self.assertTrue(first['more'])
            self.assertEqual(len(read_events(path, first['cursor'])['events']), 1)

    def test_path_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ['../secret.jsonl', '/tmp/secret.jsonl', 'config.env']:
                with self.assertRaises(ValueError):
                    trace_path(root, name)
            (root / 'escape.jsonl').symlink_to('/tmp/secret.jsonl')
            with self.assertRaises(ValueError):
                trace_path(root, 'escape.jsonl')
            self.assertEqual(trace_path(root, 'nested/run.jsonl'), (root / 'nested/run.jsonl').resolve())

    def test_summary_reads_completed_text_and_blocks_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paper = root / 'paper_0001'
            paper.mkdir()
            (paper / 'status.json').write_text('{"status":"completed"}')
            (paper / 'ai.txt').write_text('- A finding.\n')
            metadata = {'summaries': [{'source': {'kind': 'ai', 'document': 'ai_final'}, 'file': 'ai.txt'}]}
            (paper / 'metadata.json').write_text(json.dumps(metadata))
            self.assertEqual(summary_detail(root, 'paper_0001')['sections'][0]['text'], '- A finding.\n')
            self.assertEqual(summary_detail(root, 'paper_0001')['sections'][0]['review_id'], 'paper_0001::ai')
            for value in ['../secret', 'paper_0001/../../secret', '/tmp/file']:
                with self.assertRaises(ValueError):
                    summary_detail(root, value)
            metadata['summaries'][0]['file'] = '../secret.txt'
            (paper / 'metadata.json').write_text(json.dumps(metadata))
            with self.assertRaises(ValueError):
                summary_detail(root, 'paper_0001')
            (paper / 'status.json').write_text('{"status":"failed"}')
            with self.assertRaises(ValueError):
                summary_detail(root, 'paper_0001')

    def test_original_sources_match_summary_hash_and_stay_in_allowed_roots(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summaries = root / 'summaries'
            paper = summaries / 'paper_0001'
            paper.mkdir(parents=True)
            (paper / 'status.json').write_text('{"status":"completed"}')
            ai_root = root / 'ai'
            ai = ai_root / 'run/paper_0001/response.txt'
            ai.parent.mkdir(parents=True)
            ai.write_text('Original AI audit.\n')
            database = root / 'database'
            human = database / 'paper_0001/human_reviews/reviews.json'
            human.parent.mkdir(parents=True)
            human.write_text(json.dumps({'kind': 'bmc_peer_review_reports', 'parts': {'assets': [
                {'label': 'ReviewerReport_V0_R1', 'text': 'Full reviewer text.\n'},
                {'label': 'AuthorResponse', 'text': 'Not the selected review'}]}}))
            items = []
            for name, kind, doc, text in [('00_ai_final', 'ai', 'ai_final', ai.read_text()),
                                          ('01_ReviewerReport_V0_R1', 'human', 'ReviewerReport_V0_R1', 'Full reviewer text.\n')]:
                (paper / (name + '.txt')).write_text('- Summary.\n')
                items.append({'file': name + '.txt', 'source': {'kind': kind, 'document': doc},
                              'source_sha256': hashlib.sha256(text.encode()).hexdigest()})
            metadata = {'ai_response': str(ai), 'summaries': items}
            (paper / 'metadata.json').write_text(json.dumps(metadata))
            self.assertEqual(original_detail(summaries, 'paper_0001', 'paper_0001::00_ai_final', database, ai_root)['text'], ai.read_text())
            self.assertEqual(original_detail(summaries, 'paper_0001', 'paper_0001::01_ReviewerReport_V0_R1', database, ai_root)['text'], 'Full reviewer text.\n')
            with self.assertRaisesRegex(ValueError, 'Unknown review'):
                original_detail(summaries, 'paper_0001', '../secret', database, ai_root)
            ai.write_text('Changed audit')
            with self.assertRaisesRegex(ValueError, 'differs'):
                original_detail(summaries, 'paper_0001', 'paper_0001::00_ai_final', database, ai_root)
            metadata['ai_response'] = str(root / 'outside/response.txt')
            (paper / 'metadata.json').write_text(json.dumps(metadata))
            with self.assertRaisesRegex(ValueError, 'Invalid original AI'):
                original_detail(summaries, 'paper_0001', 'paper_0001::00_ai_final', database, ai_root)

    def test_labels_match_review_ids_and_refresh_atomic_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'
            results = LabelResults(path)
            self.assertFalse(results.detail('paper_0001')['available'])
            record = {'review_id': 'paper_0001::00_ai_final', 'labels': ['data leakage'],
                      'existing_labels': [], 'new_labels': ['data leakage'],
                      'topic_labels': {'data integrity': ['data leakage']}}
            other = {**record, 'review_id': 'paper_0002::00_ai_final'}
            state = {'status': 'running', 'reviews': [record, other],
                     'label_space': ['data leakage'], 'topic_label_tree': {'data integrity': ['data leakage']}}
            path.write_text(json.dumps(state))
            first = results.detail('paper_0001')
            self.assertEqual(first['reviews'], [record])
            self.assertEqual(first['processed_reviews'], 2)
            self.assertEqual(results.detail('paper_0003')['reviews'], [])
            signature = results.signature
            self.assertEqual(results.detail('paper_0001'), first)
            self.assertEqual(results.signature, signature)
            human = {**record, 'review_id': 'paper_0001::01_ReviewerReport_V0_R1',
                     'labels': [], 'topic_labels': {}, 'existing_labels': [], 'new_labels': []}
            state['reviews'].append(human)
            updated = path.with_suffix('.tmp')
            updated.write_text(json.dumps(state))
            updated.replace(path)
            second = results.detail('paper_0001')
            self.assertEqual(second['reviews'], [record, human])
            self.assertEqual(second['processed_reviews'], 3)
            self.assertNotEqual(results.signature, signature)
            for paper in ['../state.json', 'paper_0001/../paper_0002', '/tmp/secret']:
                with self.assertRaises(ValueError):
                    results.detail(paper)


if __name__ == '__main__':
    unittest.main()
