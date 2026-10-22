import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('review_run', Path(__file__).with_name('run.py'))
pipeline = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pipeline)


class PipelineTests(unittest.TestCase):
    def setup_run(self, root):
        database = root / 'database'
        for index in (1, 2):
            folder = database / f'paper_{index:04d}'
            (folder / 'human_reviews').mkdir(parents=True)
            (folder / 'human_reviews/reviews.json').write_text(json.dumps({'text': 'Human comment'}))
            (folder / 'paper.pdf').write_bytes(b'%PDF-test')
        return pipeline.parser().parse_args(['--database', str(database), '--output', str(root / 'results'), '--trace-root', str(root / 'traces')])

    def fake_agent(self, command, **kwargs):
        def option(name):
            return Path(command[command.index(name) + 1])
        self.assertIn('Human comment', option('--question-file').read_text())
        self.assertEqual(option('--prompt').parent.name, 'prompts')
        option('--output').write_text('A possible missing methodological issue, supported by evidence.\n')
        trajectory = option('--trajectory')
        trajectory.parent.mkdir(parents=True, exist_ok=True)
        trajectory.write_text('{"type":"run_end","data":{"status":"completed"}}\n')
        option('--trace').write_text(json.dumps([{'tool': 'read_pdf', 'success': True},
                                               {'tool': 'web_search', 'success': True}]))
        return SimpleNamespace(returncode=0)

    @patch.dict(os.environ, {'YDC_API_KEY': 'test-only'})
    def test_batch_resume_and_changed_reviews(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = self.setup_run(Path(tmp))
            self.assertEqual(pipeline.run(args, self.fake_agent)['completed'], 2)
            self.assertEqual(pipeline.run(args, lambda *a, **k: self.fail('Must resume'))['resumed'], 2)
            (args.database / 'paper_0001/human_reviews/reviews.json').write_text('{"text":"Human comment updated"}')
            summary = pipeline.run(args, self.fake_agent)
            self.assertEqual(summary['completed'], 1)
            self.assertEqual(summary['resumed'], 1)
            status = json.loads((args.output / 'paper_0001/status.json').read_text())
            self.assertTrue(Path(status['trajectory']).is_file())
            self.assertEqual(len(list((args.trace_root / 'paper_0001').glob('*.jsonl'))), 2)

    @patch.dict(os.environ, {'YDC_API_KEY': 'test-only'})
    def test_missing_pdf_and_failed_agent_are_not_completed(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = self.setup_run(Path(tmp))
            (args.database / 'paper_0001/paper.pdf').unlink()
            summary = pipeline.run(args, lambda *a, **k: SimpleNamespace(returncode=1))
            self.assertEqual(summary['missing_input'], 1)
            self.assertEqual(summary['failed'], 1)
            self.assertFalse((args.output / 'paper_0002/response.txt').exists())

    @patch.dict(os.environ, {'YDC_API_KEY': 'test-only'})
    def test_requires_successful_search_and_retries(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = self.setup_run(Path(tmp))
            args.limit = 1
            def no_search(command, **kwargs):
                result = self.fake_agent(command, **kwargs)
                Path(command[command.index('--trace') + 1]).write_text('[{"tool":"read_pdf","success":true}]')
                return result
            self.assertEqual(pipeline.run(args, no_search)['failed'], 1)
            self.assertFalse((args.output / 'paper_0001/response.txt').exists())
            self.assertEqual(pipeline.run(args, self.fake_agent)['completed'], 1)

    @patch.dict(os.environ, {'YOU_COM_API_KEY': 'test-only'}, clear=True)
    def test_you_com_alias_is_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = self.setup_run(Path(tmp))
            args.limit = 1
            self.assertEqual(pipeline.run(args, self.fake_agent)['completed'], 1)

    def test_dry_run_needs_no_search_credentials(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {}, clear=True):
            args = self.setup_run(Path(tmp))
            args.dry_run = True
            args.limit = 1
            self.assertEqual(pipeline.run(args)['ready'], 1)
            args.dry_run = False
            with self.assertRaisesRegex(ValueError, 'YDC_API_KEY'):
                pipeline.run(args)


if __name__ == '__main__':
    unittest.main()
