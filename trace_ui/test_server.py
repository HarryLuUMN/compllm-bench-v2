import json
import tempfile
import unittest
from pathlib import Path

from server import read_events, trace_path, summary_detail


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


if __name__ == '__main__':
    unittest.main()
