import json
import math
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from clients import ChatClient, EmbeddingClient
from core import bounded_clusters, load_prompt, normalize, run_pipeline

ROOT = Path(__file__).resolve().parents[1]
EXTRACT = load_prompt(ROOT / 'prompts/extract_claims.yaml')
LABEL = load_prompt(ROOT / 'prompts/label_signal.yaml')


class FakeChat:
    def __init__(self):
        self.calls = 0

    def complete(self, messages):
        self.calls += 1
        if 'high-level' in messages[0]['content']:
            return '"Uncontrolled confounding"'
        response = messages[1]['content'].split('<response>\n')[1].split('\n</response>')[0]
        return json.dumps([response] if response != 'No extra issues.' else [])

    def count(self, messages):
        return len(json.dumps(messages)), 10000

    def fits(self, messages):
        count, budget = self.count(messages)
        return count <= budget


class FakeEmbedder:
    def __init__(self):
        self.inputs = []

    def embed(self, texts):
        self.inputs.extend(texts)
        return [[1, 0] if 'confounding' in text else [0, 1] for text in texts]


class PipelineTests(unittest.TestCase):
    def test_quotes_domain_presence_optional_labels_and_resume(self):
        responses = {'paper_1': 'Missing confounding control.', 'paper_2': 'Missing confounding control.',
                     'paper_3': 'No extra issues.', 'paper_4': 'Small sample size.'}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            chat, embedder = FakeChat(), FakeEmbedder()
            result = run_pipeline(responses, root, chat, embedder, EXTRACT, LABEL, {}, batch_size=1)
            self.assertEqual(result['unique_claims'], 2)
            self.assertEqual(result['signals'], 2)
            self.assertEqual(len(embedder.inputs), 2)
            claims = json.loads((root / 'claims_by_response.json').read_text())
            self.assertEqual(claims['paper_1'], ['Missing confounding control.'])
            presence = json.loads((root / 'presence.json').read_text())
            self.assertEqual(presence['paper_1'], presence['paper_2'])
            self.assertEqual(sum(presence['paper_3'].values()), 0)
            self.assertIsNone(json.loads((root / 'signal_domain.json').read_text())[0]['high_level_label'])
            chat.complete = lambda _: self.fail('Extraction should be cached')
            embedder.embed = lambda _: self.fail('Embeddings should be cached')
            run_pipeline(responses, root, chat, embedder, EXTRACT, LABEL, {})
            labeled = FakeChat()
            run_pipeline(responses, root, labeled, embedder, EXTRACT, LABEL, {}, label_signals=True)
            self.assertEqual(labeled.calls, 2)
            self.assertEqual(json.loads((root / 'signal_domain.json').read_text())[0]['high_level_label'], 'Uncontrolled confounding')

    def test_context_splits_similar_claims_and_rejects_oversized_singleton(self):
        claims = [{'claim_id': str(i), 'quote': str(i)} for i in range(5)]
        groups = bounded_clusters(claims, [[1, 0]] * 5, 0.8, lambda group: len(group) <= 2)
        self.assertEqual(sorted(map(len, groups)), [1, 2, 2])
        self.assertEqual(sorted(i for group in groups for i in group), list(range(5)))
        with self.assertRaisesRegex(ValueError, 'Single claim'):
            bounded_clusters(claims[:1], [[1, 0]], 0.8, lambda _: False)

    def test_cosine_uses_normalized_vectors(self):
        claims = [{'claim_id': str(i)} for i in range(3)]
        groups = bounded_clusters(claims, [[2, 0], [8, 0], [0, 1]], 0.9, lambda _: True)
        self.assertEqual(groups, [[0, 1], [2]])
        for vector in ([0, 0], [math.nan, 0], []):
            with self.assertRaises(ValueError):
                normalize(vector)
        with self.assertRaisesRegex(ValueError, 'dimensions'):
            bounded_clusters(claims[:2], [[1], [1, 0]], 0.9, lambda _: True)

    def test_rejects_paraphrases_instead_of_saving_unsupported_claims(self):
        chat = FakeChat()
        chat.complete = lambda _: '["Not actually quoted"]'
        with tempfile.TemporaryDirectory() as tmp, self.assertRaisesRegex(ValueError, 'exact quotes'):
            run_pipeline({'paper': 'Original response.'}, tmp, chat, FakeEmbedder(), EXTRACT, LABEL, {})

    def test_all_empty_claim_arrays_produce_zero_signal_presence(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run_pipeline({'paper': 'No extra issues.'}, tmp, FakeChat(), FakeEmbedder(), EXTRACT, LABEL, {})
            self.assertEqual(result['signals'], 0)
            self.assertEqual(json.loads((Path(tmp) / 'presence.json').read_text()), {'paper': {}})

    def test_tokenizer_counts_chat_template_and_reserves_output(self):
        chat = ChatClient('http://example/v1', 'test-model', 100, 20)
        with patch('clients.post', return_value={'count': 71, 'max_model_len': 90}) as post:
            self.assertFalse(chat.fits([{'role': 'user', 'content': 'text'}]))
            self.assertTrue(post.call_args.args[1]['add_generation_prompt'])
            self.assertIn('messages', post.call_args.args[1])
            with self.assertRaisesRegex(ValueError, 'context window'):
                chat.complete([{'role': 'user', 'content': 'text'}])
            self.assertEqual(post.call_count, 2)

    def test_embeddings_restore_order_and_reject_duplicates(self):
        client = EmbeddingClient('http://example/v1', 'embedding')
        with patch('clients.post', return_value={'data': [{'index': 1, 'embedding': [0, 1]},
                                                        {'index': 0, 'embedding': [1, 0]}]}):
            self.assertEqual(client.embed(['a', 'b']), [[1, 0], [0, 1]])
        with patch('clients.post', return_value={'data': [{'index': 0, 'embedding': [1]},
                                                        {'index': 0, 'embedding': [1]}]}):
            with self.assertRaisesRegex(ValueError, 'indices'):
                client.embed(['a', 'b'])

    def test_cli_against_local_http_services(self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                if self.path == '/tokenize':
                    value = {'count': len(json.dumps(data['messages'])), 'max_model_len': 32768}
                elif self.path == '/v1/embeddings':
                    value = {'data': [{'index': i, 'embedding': [1, 0]} for i, _ in enumerate(data['input'])]}
                elif self.path == '/v1/chat/completions':
                    content = '"Missing confounder control"' if 'high-level' in data['messages'][0]['content'] else '["No confounder adjustment."]'
                    value = {'choices': [{'finish_reason': 'stop', 'message': {'content': content}}]}
                else:
                    self.send_error(404)
                    return
                body = json.dumps(value).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                folder = root / 'input/paper_0001'
                folder.mkdir(parents=True)
                (folder / 'response.txt').write_text('No confounder adjustment.')
                stale = root / 'input/paper_0002'
                stale.mkdir()
                (stale / 'response.txt').write_text('Stale output from a failed rerun.')
                (stale / 'status.json').write_text('{"status":"failed"}')
                base = f'http://127.0.0.1:{server.server_port}/v1'
                result = subprocess.run([sys.executable, str(ROOT / 'signal_extraction_pipeline/run.py'),
                                         '--input', str(root / 'input'), '--output', str(root / 'output'),
                                         '--llm-base-url', base, '--llm-model', 'test',
                                         '--embedding-base-url', base, '--label-signals'],
                                        capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stderr)
                manifest = json.loads((root / 'output/manifest.json').read_text())
                self.assertEqual(manifest['status'], 'completed')
                self.assertEqual(manifest['responses'], 1)
                self.assertEqual(json.loads((root / 'output/presence.json').read_text())['paper_0001']['signal_00001'], 1)
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == '__main__':
    unittest.main()
