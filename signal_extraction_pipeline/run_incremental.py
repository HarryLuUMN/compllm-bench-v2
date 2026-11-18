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
    parser.add_argument('--output', type=Path, default=None)
    parser.add_argument('--config', type=Path, default=ROOT / 'quest_computes/incremental_review_labels.json')
    parser.add_argument('--prompt', type=Path, help='Override the label-generation prompt')
    parser.add_argument('--env', type=Path, default=ROOT / '.env')
    parser.add_argument('--topic-tree', action='store_true', help='Group new labels into existing or newly created topics')
    parser.add_argument('--tree-context-prompt', type=Path, default=ROOT / 'prompts/topic_tree_context.yaml')
    parser.add_argument('--place-topics-prompt', type=Path, default=ROOT / 'prompts/place_label_topics.yaml')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    if args.output is None:
        args.output = ROOT / 'signal_extraction_pipeline/results' / (
            'incremental_topic_tree' if args.topic_tree else 'incremental')
    reviews = load_reviews(args.input)
    config = json.loads(args.config.read_text())
    if any(type(config.get(k)) is not int or config[k] < 1 for k in ('max_tokens', 'timeout', 'max_input_bytes')):
        raise ValueError('Token, timeout and input budgets must be positive integers')
    prompt_path = args.prompt or ROOT / 'prompts' / (
        'tree_review_labels.yaml' if args.topic_tree else 'incremental_review_labels.yaml')
    prompt = load_prompt(prompt_path)
    topic_prompts = {'context': load_prompt(args.tree_context_prompt),
                     'place': load_prompt(args.place_topics_prompt)} if args.topic_tree else None
    for template in [prompt, *([topic_prompts['place']] if topic_prompts else [])]:
        if '{{input}}' not in template['task']:
            raise ValueError('Stage prompt must contain {{input}}')
    if topic_prompts and '{{tree}}' not in topic_prompts['context']['task']:
        raise ValueError('Context prompt must contain {{tree}}')
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

        state = run_incremental(reviews, args.output, prompt, config, complete, topic_prompts=topic_prompts)
    summary = {'status': state['status'], 'reviews': len(state['reviews']), 'labels': len(state['label_space'])}
    if args.topic_tree:
        summary['topics'] = len(state['topic_label_tree'])
    print(json.dumps(summary))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, KeyError, IndexError) as error:
        sys.exit(str(error))
