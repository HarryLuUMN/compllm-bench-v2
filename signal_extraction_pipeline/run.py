import argparse
import json
import os
import sys
from pathlib import Path

from clients import ChatClient, EmbeddingClient
from core import load_prompt, run_pipeline, write_json

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description='Extract audit quotes, cluster by cosine similarity, and map signal presence.')
    parser.add_argument('--input', type=Path, default=ROOT / 'review_pipeline/results')
    parser.add_argument('--output', type=Path, default=ROOT / 'signal_extraction_pipeline/results')
    parser.add_argument('--llm-base-url', required=True, help='OpenAI-compatible base URL ending in /v1')
    parser.add_argument('--llm-model', required=True)
    parser.add_argument('--tokenize-url', help='Matching vLLM chat /tokenize endpoint; inferred from LLM base URL by default')
    parser.add_argument('--embedding-base-url', required=True)
    parser.add_argument('--embedding-model', default='Qwen/Qwen3-Embedding-8B')
    parser.add_argument('--context-window', type=int, default=32768)
    parser.add_argument('--max-output-tokens', type=int, default=4096)
    parser.add_argument('--threshold', type=float, default=0.82)
    parser.add_argument('--batch-size', type=int, default=32)
    parser.add_argument('--label-signals', action='store_true')
    parser.add_argument('--extract-prompt', type=Path, default=ROOT / 'prompts/extract_claims.yaml')
    parser.add_argument('--label-prompt', type=Path, default=ROOT / 'prompts/label_signal.yaml')
    parser.add_argument('--timeout', type=int, default=120)
    args = parser.parse_args()
    if not 0 < args.max_output_tokens < args.context_window or args.timeout < 1:
        parser.error('Require 0 < max-output-tokens < context-window and a positive timeout')
    if not args.input.is_dir():
        parser.error('Input must be a directory of audit response.txt files')
    responses = {}
    for path in sorted(args.input.rglob('response.txt')):
        status_path = path.parent / 'status.json'
        if status_path.exists() and json.loads(status_path.read_text()).get('status') != 'completed':
            continue
        responses[path.parent.relative_to(args.input).as_posix()] = path.read_text(encoding='utf-8')
    configuration = {'llm_base_url': args.llm_base_url, 'llm_model': args.llm_model,
                     'embedding_base_url': args.embedding_base_url, 'embedding_model': args.embedding_model,
                     'tokenize_url': args.tokenize_url, 'context_window': args.context_window,
                     'max_output_tokens': args.max_output_tokens}
    chat = ChatClient(args.llm_base_url, args.llm_model, args.context_window, args.max_output_tokens,
                      os.environ.get('LLM_API_KEY'), args.tokenize_url, args.timeout)
    embedder = EmbeddingClient(args.embedding_base_url, args.embedding_model,
                               os.environ.get('EMBEDDING_API_KEY'), args.timeout)
    try:
        summary = run_pipeline(responses, args.output, chat, embedder,
                               load_prompt(args.extract_prompt), load_prompt(args.label_prompt), configuration,
                               args.threshold, args.batch_size, args.label_signals)
    except Exception as error:
        args.output.mkdir(parents=True, exist_ok=True)
        write_json(args.output / 'manifest.json', {'status': 'failed', 'error': str(error), 'configuration': configuration})
        raise
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        sys.exit(str(error))
