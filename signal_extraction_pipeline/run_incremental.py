import argparse
import fcntl
import json
import os
import sys
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from discritized_summary.run import load_env
from signal_extraction_pipeline.clients import post
from signal_extraction_pipeline.core import load_prompt, write_json
from signal_extraction_pipeline.incremental import load_reviews, run_incremental


def main():
    parser = argparse.ArgumentParser(description='Assign review labels while growing a shared label space from zero.')
    parser.add_argument('--input', type=Path, required=True, help='Completed summary run directory or a JSON array of reviews')
    parser.add_argument('--output', type=Path, default=ROOT / 'signal_extraction_pipeline/results/incremental')
    parser.add_argument('--config', type=Path, default=ROOT / 'quest_computes/incremental_review_labels.json')
    parser.add_argument('--prompt', type=Path, default=ROOT / 'prompts/incremental_review_labels.yaml')
    parser.add_argument('--env', type=Path, default=ROOT / '.env')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    reviews = load_reviews(args.input)
    config = json.loads(args.config.read_text())
    if any(type(config.get(k)) is not int or config[k] < 1 for k in ('max_tokens', 'timeout', 'max_input_bytes')):
        raise ValueError('Token, timeout and input budgets must be positive integers')
    prompt = load_prompt(args.prompt)
    if '{{input}}' not in prompt['task']:
        raise ValueError('Prompt must contain {{input}}')
    if args.dry_run:
        print(json.dumps({'reviews': len(reviews), 'api_calls': 0}))
        return
    if args.env.exists():
        load_env(args.env)
    key = os.environ.get('DEEPSEEK_API_KEY')
    if not key:
        raise ValueError('DEEPSEEK_API_KEY is missing')
    args.output.mkdir(parents=True, exist_ok=True)
    with (args.output / 'run.lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        attempts = args.output / 'attempts'
        attempts.mkdir(exist_ok=True)

        def complete(request):
            payload = {'model': config['model'], 'messages': request, 'thinking': {'type': 'disabled'},
                       'response_format': {'type': 'json_object'}, 'max_tokens': config['max_tokens']}
            response = post(config['base_url'].rstrip('/') + '/chat/completions', payload, key, config['timeout'])
            write_json(attempts / f'{uuid4().hex}.json', {'request': payload, 'response': response})
            choice = response['choices'][0]
            if choice.get('finish_reason') != 'stop':
                raise ValueError('LLM did not finish normally')
            text = choice['message']['content']
            if not isinstance(text, str) or not text.strip():
                raise ValueError('LLM returned no labels')
            return text

        state = run_incremental(reviews, args.output, prompt, config, complete)
    print(json.dumps({'status': state['status'], 'reviews': len(state['reviews']),
                      'labels': len(state['label_space'])}))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, KeyError, IndexError) as error:
        sys.exit(str(error))
