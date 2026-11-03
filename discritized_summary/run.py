import argparse
import hashlib
import json
import os
import shlex
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from signal_extraction_pipeline.clients import post
from signal_extraction_pipeline.core import cache_key, load_prompt, write_json


def load_env(path):
    for line in path.read_text().splitlines():
        values = shlex.split(line, comments=True)
        if values and values[0] == 'export':
            values = values[1:]
        if values and '=' in values[0]:
            key, value = ' '.join(values).split('=', 1)
            os.environ.setdefault(key, value)


def human_documents(data):
    parts = data.get('parts', {})
    kind = data.get('kind')
    if kind == 'bmc_peer_review_reports':
        docs = [(a.get('label', ''), a.get('text', '')) for a in parts.get('assets', [])
                if a.get('label', '').startswith('ReviewerReport_')]
    elif kind == 'elife_public_reviews':
        docs = [('public_reviews', parts.get('public_reviews', ''))]
    elif kind in ('plos_peer_review_history', 'bmj_open_peer_review_history'):
        docs = [('peer_review_history', parts.get('peer_review_history', ''))]
    else:
        raise ValueError(f'Unsupported human review kind: {kind}')
    docs = [(name, text) for name, text in docs if isinstance(text, str) and text.strip()]
    if not docs:
        raise ValueError('No human review text found')
    return docs


def validate(text):
    if not isinstance(text, str) or not text.strip():
        raise ValueError('Summary must contain bullet text')
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if any(not line.startswith('- ') or not line[2:].strip() for line in lines):
        raise ValueError('Expected plain text with one - bullet per line')
    return '\n'.join(lines)


def summarize(text, identity, prompt, config, directory, complete):
    directory.mkdir(parents=True, exist_ok=True)
    bullets = []
    usage = []
    size = config['chunk_characters']
    for index, start in enumerate(range(0, len(text), size)):
        end = min(start + size, len(text))
        source = text[start:end]
        content = (f"Source: {identity['kind']} / {identity['document']}\n"
                   f'Passage: characters {start}–{end} of {len(text)}\n\n{source}')
        messages = [{'role': 'system', 'content': prompt['system']},
                    {'role': 'user', 'content': prompt['task'].replace('{{source}}', content)}]
        fingerprint = cache_key({'messages': messages, 'configuration': config})
        path = directory / f'{index:04d}_{fingerprint}.json'
        if path.exists():
            record = json.loads(path.read_text())
        else:
            payload = {'model': config['model'], 'messages': messages,
                       'thinking': {'type': 'disabled'}, 'max_tokens': config['max_tokens']}
            response = complete(payload)
            record = {'fingerprint': fingerprint, 'request': payload, 'response': response}
            write_json(directory / f'{index:04d}_{fingerprint}.attempt.json', record)
            choice = response['choices'][0]
            if choice.get('finish_reason') != 'stop':
                raise ValueError('Incomplete summary: API did not finish with stop')
            record['summary'] = validate(choice['message']['content'])
            write_json(path, record)
        bullets.append(validate(record['summary']))
        usage.append(record['response'].get('usage', {}))
    return {'text': '\n'.join(bullets), 'source_sha256': hashlib.sha256(text.encode()).hexdigest(),
            'source_characters': len(text), 'usage_by_chunk': usage}


def main():
    parser = argparse.ArgumentParser(description='Discretize AI audits and human reviews through DeepSeek.')
    parser.add_argument('--input', type=Path, required=True, help='One specific run directory containing completed response.txt files')
    parser.add_argument('--database', type=Path, default=ROOT / 'paper_database')
    parser.add_argument('--output', type=Path, default=ROOT / 'discritized_summary/results')
    parser.add_argument('--env', type=Path, default=ROOT / '.env')
    parser.add_argument('--config', type=Path, default=ROOT / 'quest_computes/discritized_summary.json')
    parser.add_argument('--prompt', type=Path, default=ROOT / 'prompts/discritized_summary.yaml')
    parser.add_argument('--paper', action='append', help='Restrict to a paper ID; repeatable')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    if config['chunk_characters'] < 1 or config['max_tokens'] < 1:
        raise ValueError('Chunk and output budgets must be positive')
    prompt = load_prompt(args.prompt)
    if '{{source}}' not in prompt['task']:
        raise ValueError('Prompt requires {{source}}')
    if not args.input.is_dir():
        raise ValueError('Input must be an existing experiment directory')
    selected = {}
    for response in sorted(args.input.rglob('response.txt')):
        paper = response.parent.name
        if args.paper and paper not in args.paper:
            continue
        status = response.parent / 'status.json'
        if not status.exists() or json.loads(status.read_text()).get('status') != 'completed':
            continue
        if paper in selected:
            raise ValueError(f'Multiple completed responses for {paper}; select one run')
        selected[paper] = response
    if args.paper and set(args.paper) - selected.keys():
        raise ValueError('Requested papers have no completed AI response')
    if not selected:
        raise ValueError('No completed AI responses found')
    if args.dry_run:
        print(json.dumps({'papers': len(selected), 'api_calls': 0}))
        return
    if args.env.exists():
        load_env(args.env)
    key = os.environ.get('DEEPSEEK_API_KEY')
    if not key:
        raise ValueError('DEEPSEEK_API_KEY is missing')
    failures = []
    for paper, response in selected.items():
        folder = args.output / paper
        folder.mkdir(parents=True, exist_ok=True)
        try:
            documents = [('ai_final', response.read_text())]
            reviews = json.loads((args.database / paper / 'human_reviews/reviews.json').read_text())
            documents += human_documents(reviews)
            if not documents[0][1].strip():
                raise ValueError('AI final response is empty')
            result = {'paper': paper, 'ai_response': str(response.resolve()), 'configuration': config,
                      'prompt_sha256': hashlib.sha256(args.prompt.read_bytes()).hexdigest(), 'summaries': []}
            for i, (name, text) in enumerate(documents):
                identity = {'kind': 'ai' if i == 0 else 'human', 'document': name}
                value = summarize(text, identity, prompt, config, folder / 'chunks' / str(i),
                                  lambda payload: post(config['base_url'].rstrip('/') + '/chat/completions',
                                                       payload, key, config['timeout']))
                filename = f'{i:02d}_{name}.txt'
                (folder / filename).write_text(value.pop('text') + '\n', encoding='utf-8')
                result['summaries'].append({'source': identity, 'file': filename, **value})
            write_json(folder / 'metadata.json', result)
            write_json(folder / 'status.json', {'status': 'completed'})
        except (OSError, ValueError, RuntimeError, KeyError, IndexError) as error:
            write_json(folder / 'status.json', {'status': 'failed', 'error_type': type(error).__name__})
            failures.append(paper)
        print(json.dumps({'paper': paper, 'status': 'failed' if paper in failures else 'completed'}), flush=True)
    if failures:
        raise ValueError(f'{len(failures)} papers failed; validated chunks are retained for resume')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        sys.exit(str(error))
