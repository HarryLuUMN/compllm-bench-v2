import json
import tempfile
import unittest
from pathlib import Path

from export import export_site


class ExportTests(unittest.TestCase):
    def test_static_export_preserves_evidence_and_omits_transport(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'traces'
            root.mkdir()
            events = [
                {'type': 'model_input', 'data': {'messages': ['actual context']}},
                {'type': 'tool_execution_end', 'data': {'result': {'content': [{'text': 'actual search result'}]}}},
                {'type': 'provider_stream_event', 'data': {'duplicate': True}},
                {'type': 'run_end', 'data': {'status': 'completed', 'messages': ['duplicate context']}},
            ]
            (root / 'run.jsonl').write_text('\n'.join(json.dumps(e) for e in events))
            output = Path(directory) / 'site'
            self.assertEqual(export_site(root, output), 1)
            manifest = json.loads((output / 'data/traces.json').read_text())
            exported = json.loads((output / 'data' / manifest[0]['id']).read_text())
            self.assertEqual(exported[:2], events[:2])
            self.assertEqual(exported[-1]['data'], {'status': 'completed'})
            self.assertEqual(len(exported), 3)
            self.assertIn('window.TRACE_STATIC=true', (output / 'index.html').read_text())
            self.assertIn('./app.js', (output / 'index.html').read_text())
            self.assertTrue((output / '.nojekyll').exists())
            with self.assertRaises(ValueError):
                export_site(root, output)


if __name__ == '__main__':
    unittest.main()
