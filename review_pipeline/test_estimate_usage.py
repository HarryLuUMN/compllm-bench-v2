import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location('estimate_usage', Path(__file__).with_name('estimate_usage.py'))
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class UsageTests(unittest.TestCase):
    def test_all_attempts_without_double_counting_and_model_isolation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            results, traces = root / 'results', root / 'traces'
            usage = {'input': 100, 'output': 40, 'cacheRead': 20, 'cacheWrite': 10,
                     'reasoning': 30, 'totalTokens': 170}
            message = {'role': 'assistant', 'usage': usage}
            for index, model in enumerate(['27B', '27B', '9B']):
                paper = f'paper_{index + 1:04d}'
                trace = traces / 'run' / paper / 'attempt.jsonl'
                trace.parent.mkdir(parents=True)
                rows = [
                    {'type': 'run_start', 'data': {'requested_provider': 'quest', 'requested_model': model,
                                                  'thinking_level': 'high'}},
                    {'type': 'provider_request', 'data': {}},
                    {'type': 'provider_stream_event', 'data': {'usage': usage}},
                    {'type': 'message_end', 'data': {'message': message}},
                    {'type': 'tool_execution_start', 'data': {'toolName': 'web_search'}},
                    {'type': 'tool_execution_end', 'data': {'toolName': 'web_search', 'isError': index == 1}},
                    {'type': 'run_end', 'data': {'messages': [message]}},
                ]
                trace.write_text(''.join(json.dumps(row) + '\n' for row in rows) + ('{' if index == 1 else ''))
                status = results / paper / 'status.json'
                status.parent.mkdir(parents=True)
                status.write_text(json.dumps({'status': 'failed' if index == 1 else 'completed',
                    'trajectory': '/remote/trace_databases/run/' + paper + '/attempt.jsonl'}))
            args = module.parser().parse_args(['--results', str(results), '--traces', str(traces)])
            report = module.estimate(args)
            totals = report['actual_recorded_usage']
            self.assertEqual(totals['input'], 300)
            self.assertEqual(totals['output'], 120)
            self.assertEqual(totals['reasoning_subset'], 90)
            self.assertEqual(totals['prompt'], 390)
            self.assertEqual(totals['total'], 510)
            self.assertEqual(totals['provider_requests'], 3)
            self.assertEqual(totals['web_search_starts'], 3)
            self.assertEqual(totals['web_search_successes'], 2)
            self.assertEqual(report['groups'][0]['usage']['total'], 340)
            self.assertEqual(report['groups'][1]['usage']['total'], 170)
            self.assertEqual(report['attempts'][1]['latest_status'], 'failed')
            self.assertEqual(report['attempts'][1]['malformed_lines'], 1)
            self.assertEqual(len(report['papers']), 3)

    def test_provider_usage_captures_compaction_without_duplicates(self):
        with tempfile.TemporaryDirectory() as temporary:
            trace = Path(temporary) / 'trace.jsonl'
            rows = [
                {'type': 'provider_request', 'request': 1, 'data': {}},
                {'type': 'provider_stream_event', 'request': 1, 'data': {'data': {'usage': {
                    'prompt_tokens': 120, 'completion_tokens': 40,
                    'prompt_tokens_details': {'cached_tokens': 20},
                    'completion_tokens_details': {'reasoning_tokens': 30}}}}},
                {'type': 'message_end', 'request': 1, 'data': {'message': {'role': 'assistant',
                    'usage': {'input': 100, 'cacheRead': 20, 'output': 40, 'reasoning': 30}}}},
                {'type': 'provider_request', 'request': 2, 'data': {}},
                {'type': 'provider_stream_event', 'request': 2, 'data': {'data': {'usage': {
                    'prompt_tokens': 10, 'completion_tokens': 5}}}},
            ]
            trace.write_text(''.join(json.dumps(row) + '\n' for row in rows))
            usage = module.read_trace(trace)['usage']
            self.assertEqual(usage['input'], 110)
            self.assertEqual(usage['cache_read'], 20)
            self.assertEqual(usage['output'], 45)
            self.assertEqual(usage['total'], 175)
            self.assertEqual(usage['reasoning_subset'], 30)

    def test_sidecar_skips_large_trace_and_preserves_unknown_tool_counts(self):
        with tempfile.TemporaryDirectory() as temporary:
            trace = Path(temporary) / 'trace.jsonl'
            trace.write_text('not parsed')
            sidecar = trace.with_suffix('.jsonl.tokens.json')
            sidecar.write_text(json.dumps({'provider': 'quest', 'model': '27B', 'thinking': 'high',
                'requests_started': 2, 'totals': {'input': 100, 'cacheRead': 20, 'cacheWrite': 10,
                'output': 40, 'reasoning': 30, 'totalTokens': 170}}))
            result = module.read_trace(trace)
            self.assertEqual(result['usage_source'], 'sidecar')
            self.assertEqual(result['usage']['total'], 170)
            self.assertEqual(result['usage']['reasoning_subset'], 30)
            self.assertIsNone(result['usage']['web_search_starts'])
            self.assertEqual(result['malformed_lines'], 0)

    def test_empty_report_is_zero(self):
        with tempfile.TemporaryDirectory() as temporary:
            args = module.parser().parse_args(['--results', temporary, '--traces', temporary])
            report = module.estimate(args)
            self.assertEqual(report['actual_recorded_usage']['total'], 0)
            self.assertEqual(report['groups'], [])


if __name__ == '__main__':
    unittest.main()
