import argparse
import hashlib
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def write_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def fingerprint(paths, configuration):
    digest = hashlib.sha256(json.dumps(configuration, sort_keys=True).encode())
    for path in paths:
        with path.open('rb') as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b''):
                digest.update(chunk)
    return digest.hexdigest()


def run(args, runner=subprocess.run):
    if args.limit is not None and args.limit < 1:
        raise ValueError('--limit must be positive')
    if args.timeout <= 0:
        raise ValueError('--timeout must be positive')
    if bool(args.provider) != bool(args.model):
        raise ValueError('Specify --provider and --model together')
    prompt_path = args.prompt.resolve(strict=True)
    prompt = yaml.safe_load(prompt_path.read_text(encoding='utf-8'))
    if not isinstance(prompt, dict) or not isinstance(prompt.get('system'), str) or not isinstance(prompt.get('task'), str):
        raise ValueError('Experiment prompt needs system and task strings')
    if '{{human_reviews}}' not in prompt['task']:
        raise ValueError('Experiment task must contain {{human_reviews}}')
    folders = sorted((p for p in args.database.resolve(strict=True).iterdir()
                      if p.is_dir() and not p.is_symlink() and p.name.startswith('paper_')
                      and p.name[6:].isdigit()), key=lambda p: int(p.name[6:]))
    if not args.dry_run and not (os.environ.get('YDC_API_KEY') or os.environ.get('YOU_COM_API_KEY') or os.environ.get('BRAVE_SEARCH_API_KEY')):
        raise ValueError('YDC_API_KEY or BRAVE_SEARCH_API_KEY is required: this experiment must perform web search')
    args.output.mkdir(parents=True, exist_ok=True)
    counts = {'completed': 0, 'resumed': 0, 'missing_input': 0, 'failed': 0, 'ready': 0}
    attempted = 0
    configuration = {'provider': args.provider, 'model': args.model, 'thinking': args.thinking, 'agent_dir': str(args.agent_dir),
                     'sdk_path': os.environ.get('PI_SDK_PATH')}
    for folder in folders:
        destination = args.output / folder.name
        destination.mkdir(exist_ok=True)
        status_path = destination / 'status.json'
        pdf = folder / 'paper.pdf'
        reviews_path = folder / 'human_reviews' / 'reviews.json'
        if not pdf.is_file() or not reviews_path.is_file():
            counts['missing_input'] += 1
            write_json(status_path, {'status': 'missing_input', 'paper': str(folder)})
            continue
        key = fingerprint([pdf, reviews_path, prompt_path, ROOT / 'agent/run.mjs',
                           ROOT / 'agent/tools.mjs', ROOT / 'agent/pdf_reader.py', ROOT / 'agent/trajectory.mjs', Path(__file__)], configuration)
        response_path = destination / 'response.txt'
        previous = json.loads(status_path.read_text()) if status_path.exists() else {}
        if (previous.get('status') == 'completed' and previous.get('fingerprint') == key
                and response_path.is_file() and response_path.read_text(encoding='utf-8').strip()):
            counts['resumed'] += 1
            continue
        if args.limit is not None and attempted >= args.limit:
            continue
        attempted += 1
        if args.dry_run:
            counts['ready'] += 1
            print(f'{folder.name}: ready', flush=True)
            continue
        status = {'status': 'running', 'paper': str(folder), 'fingerprint': key,
                  'provider': args.provider, 'model': args.model, 'thinking': args.thinking}
        write_json(status_path, status)
        try:
            with pdf.open('rb') as source:
                if source.read(5) != b'%PDF-':
                    raise ValueError('paper.pdf is not a PDF')
            reviews = json.loads(reviews_path.read_text(encoding='utf-8'))
            if not reviews:
                raise ValueError('Human review record is empty')
            task = prompt['task'].replace('{{human_reviews}}', json.dumps(reviews, ensure_ascii=False))
            question = destination / 'input.txt'
            question.write_text(task, encoding='utf-8')
            pending = destination / 'response.pending.txt'
            trace_path = destination / 'tools.json'
            trajectory = (args.trace_root / folder.name / (uuid.uuid4().hex + '.jsonl')).resolve()
            status['trajectory'] = str(trajectory)
            write_json(status_path, status)
            pending.unlink(missing_ok=True)
            trace_path.unlink(missing_ok=True)
            command = ['node', str(ROOT / 'agent/run.mjs'), '--folder', str(folder),
                       '--prompt', str(prompt_path), '--question-file', str(question.resolve()),
                       '--output', str(pending.resolve()), '--trace', str(trace_path.resolve()),
                       '--trajectory', str(trajectory),
                       '--agent-dir', str(args.agent_dir.resolve()), '--thinking', args.thinking]
            if args.model:
                command.extend(['--provider', args.provider, '--model', args.model])
            with (destination / 'run.log').open('w', encoding='utf-8') as log:
                result = runner(command, stdout=log, stderr=subprocess.STDOUT, timeout=args.timeout, check=False)
            if not trajectory.is_file() or not trajectory.stat().st_size:
                raise ValueError('Agent did not save a trajectory')
            if result.returncode:
                raise ValueError(f'Agent exited with code {result.returncode}; see run.log')
            successful_tools = {event['tool'] for event in json.loads(trace_path.read_text()) if event['success']}
            if not {'read_pdf', 'web_search'} <= successful_tools:
                raise ValueError('Agent did not successfully read the PDF and perform web search')
            if not pending.read_text(encoding='utf-8').strip():
                raise ValueError('Agent returned an empty response')
            pending.replace(response_path)
            status['status'] = 'completed'
            counts['completed'] += 1
        except (OSError, ValueError, KeyError, subprocess.TimeoutExpired) as error:
            status.update(status='failed', error=str(error))
            counts['failed'] += 1
        write_json(status_path, status)
        print(f"{folder.name}: {status['status']}", flush=True)
        write_json(args.output / 'summary.json', counts)
    write_json(args.output / 'summary.json', counts)
    return counts


def parser():
    result = argparse.ArgumentParser(description='Run complementary peer-review experiments with the pi agent.')
    result.add_argument('--database', type=Path, default=ROOT / 'paper_database')
    result.add_argument('--output', type=Path, default=ROOT / 'review_pipeline/results')
    result.add_argument('--prompt', type=Path, default=ROOT / 'prompts/review_experiment.yaml')
    result.add_argument('--agent-dir', type=Path, default=ROOT.parent / 'pi-agent/agent')
    result.add_argument('--trace-root', type=Path, default=ROOT / 'trace_databases/paper_review')
    result.add_argument('--thinking', choices=['off', 'low', 'medium', 'high'], default='off')
    result.add_argument('--provider')
    result.add_argument('--model')
    result.add_argument('--limit', type=int)
    result.add_argument('--timeout', type=int, default=900, help='Maximum seconds per paper')
    result.add_argument('--dry-run', action='store_true')
    return result


if __name__ == '__main__':
    try:
        summary = run(parser().parse_args())
    except (OSError, ValueError) as error:
        sys.exit(str(error))
    print(json.dumps(summary))
    sys.exit(1 if summary['failed'] else 0)
