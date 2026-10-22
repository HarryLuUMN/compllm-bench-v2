import json
import tempfile
import unittest
from pathlib import Path

from server import read_events, trace_path


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


if __name__ == '__main__':
    unittest.main()
