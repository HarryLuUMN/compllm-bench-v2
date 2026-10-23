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


    def test_shards_cover_dataset_without_overlap(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            args = self.setup_run(root)
            selected = []
            for shard in range(4):
                args.shard_count = 4
                args.shard_index = shard
                args.dry_run = True
                args.output = root / f'shard_{shard}'
                pipeline.run(args)
                selected.append({p.name for p in args.output.iterdir() if p.is_dir()})
            self.assertEqual(set.union(*selected), {'paper_0001', 'paper_0002'})
            self.assertEqual(sum(map(len, selected)), 2)
            self.assertEqual(selected[0], {'paper_0001'})
            self.assertEqual(selected[1], {'paper_0002'})
            for count, index in [(0, 0), (2, -1), (2, 2)]:
                args.shard_count, args.shard_index = count, index
                with self.assertRaisesRegex(ValueError, 'shard'):
                    pipeline.run(args)

    def strict_agent(self, command, **kwargs):
        result = self.fake_agent(command, **kwargs)
        trace = Path(command[command.index('--trajectory') + 1])
        rows = [
            {'type': 'tool_execution_end', 'data': {'toolName': 'read_pdf', 'isError': False,
             'result': {'content': [{'type': 'text', 'text': json.dumps({
                 'file': 'paper.pdf', 'total_pages': 2,
                 'pages': [{'page': 1, 'text': 'one', 'truncated': False},
                           {'page': 2, 'text': 'two', 'truncated': False}]})}]}}},
            {'type': 'message_end', 'data': {'message': {'role': 'assistant', 'stopReason': 'stop',
             'content': [{'type': 'thinking', 'thinking': 'Check the evidence.'}]}}}]
        trace.write_text(''.join(json.dumps(row) + '\n' for row in rows))
        return result

    @patch.dict(os.environ, {'YDC_API_KEY': 'test-only'})
    def test_strict_success_exports_metrics_and_thinking(self):
        from pypdf import PdfWriter
        with tempfile.TemporaryDirectory() as tmp:
            args = self.setup_run(Path(tmp))
            writer = PdfWriter()
            for _ in range(2):
                writer.add_blank_page(width=100, height=100)
            writer.write(args.database / 'paper_0001/paper.pdf')
            args.limit = 1
            args.require_full_pdf = args.require_thinking = True
            self.assertEqual(pipeline.run(args, self.strict_agent)['completed'], 1)
            result = args.output / 'paper_0001'
            status = json.loads((result / 'status.json').read_text())
            self.assertGreaterEqual(status['elapsed_seconds'], 0)
            self.assertIn('+00:00', status['completed_at'])
            self.assertEqual(status['audit']['pdf_pages_total'], 2)
            self.assertEqual(status['audit']['pdf_pages_read'], [1, 2])
            self.assertEqual((result / 'thinking.txt').read_text().strip(), 'Check the evidence.')

    def test_audit_rejects_incomplete_truncated_and_missing_thinking(self):
        from pypdf import PdfWriter
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdf = root / 'paper.pdf'
            writer = PdfWriter()
            for _ in range(2):
                writer.add_blank_page(width=100, height=100)
            writer.write(pdf)
            trace = root / 'trace.jsonl'
            tool = {'type': 'tool_execution_end', 'data': {'toolName': 'read_pdf', 'result': {
                'content': [{'type': 'text', 'text': json.dumps({'file': 'paper.pdf',
                'total_pages': 1, 'pages': [{'page': 1, 'truncated': True}, {'page': 2}]})}]}}}
            message = {'type': 'message_end', 'data': {'message': {'role': 'assistant',
                       'stopReason': 'length', 'content': [{'type': 'thinking', 'thinking': '   '}]}}}
            trace.write_text(json.dumps(tool) + '\n' + json.dumps(message) + '\n')
            metrics, thinking, errors = pipeline.audit_trajectory(trace, pdf, True, True)
            self.assertEqual(metrics['pdf_pages_read'], [2])
            self.assertEqual(metrics['pdf_pages_total'], 2)
            self.assertEqual(thinking, '')
            self.assertEqual(len(errors), 3)
            tool['data']['isError'] = True
            trace.write_text(json.dumps(tool) + '\n')
            metrics, _, errors = pipeline.audit_trajectory(trace, pdf, True, False)
            self.assertEqual(metrics['pdf_pages_read'], [])
            self.assertEqual(len(errors), 1)


if __name__ == '__main__':
    unittest.main()
