import argparse
import collections
import datetime
import json
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from signal_extraction_pipeline.core import cache_key, write_json
from signal_extraction_pipeline.incremental import load_reviews


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def prepare_input(repo, runtime, config):
    path = runtime / 'input_reviews.json'
    manifest_path = runtime / 'input_manifest.json'
    reviews = load_reviews(path if path.exists() else
                           repo / 'discritized_summary/results' / config['summary_run'])
    kinds = collections.Counter(r['source']['kind'] for r in reviews)
    papers = {r['paper_id'] for r in reviews}
    ai_papers = collections.Counter(r['paper_id'] for r in reviews if r['source']['kind'] == 'ai')
    human_papers = {r['paper_id'] for r in reviews if r['source']['kind'] == 'human'}
    if (dict(kinds) != config['expected_reviews_by_kind'] or len(papers) != config['expected_papers']
            or set(ai_papers) != papers or any(count != 1 for count in ai_papers.values())
            or human_papers != papers):
        raise ValueError('Input cohort differs from expected AI/Human coverage')
    manifest = {'reviews': len(reviews), 'papers': len(papers), 'by_kind': dict(kinds),
                'input_hash': cache_key(reviews), 'summary_run': config['summary_run']}
    if manifest_path.exists() and json.loads(manifest_path.read_text()) != manifest:
        raise ValueError('Pinned input manifest changed')
    if not path.exists():
        write_json(path, reviews)
    write_json(manifest_path, manifest)
    return manifest


def read_state(output):
    path = output / 'state.json'
    return json.loads(path.read_text()) if path.exists() else {}


def write_progress(runtime, output, total, status, **extra):
    state = read_state(output)
    reviews = state.get('reviews', [])
    row = {'checked_at': utc_now(), 'status': status, 'processed': len(reviews), 'total': total,
           'labels': len(state.get('label_space', [])), 'topics': len(state.get('topic_label_tree', {})),
           'last_review': reviews[-1]['review_id'] if reviews else None, **extra}
    write_json(runtime / 'progress.json', row)
    with (runtime / 'events.jsonl').open('a') as stream:
        stream.write(json.dumps(row) + '\n')
    print(json.dumps(row), flush=True)


def fatal_error(text):
    return any(marker in text for marker in (
        'HTTP 400', 'HTTP 401', 'HTTP 402', 'HTTP 403', 'exceeds input budget',
        'Configuration or prompt changed', 'Processed reviews changed', 'DEEPSEEK_API_KEY is missing',
        'ModuleNotFoundError', 'ImportError'))


def supervise(command, runtime, output, manifest, config):
    total = manifest['reviews']
    failed_at = None
    failures = 0
    attempt = len(list(runtime.glob('attempt_*.log')))
    while True:
        attempt += 1
        log_path = runtime / f'attempt_{attempt:04d}.log'
        write_progress(runtime, output, total, 'running', attempt=attempt)
        child = None
        try:
            with log_path.open('w') as log:
                child = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
                while True:
                    try:
                        code = child.wait(timeout=config['progress_interval_seconds'])
                        break
                    except subprocess.TimeoutExpired:
                        write_progress(runtime, output, total, 'running', attempt=attempt)
        finally:
            if child is not None and child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
        state = read_state(output)
        done = len(state.get('reviews', []))
        if code == 0:
            if state.get('status') != 'completed' or done != total:
                raise ValueError('Worker exit did not match a complete checkpoint')
            write_progress(runtime, output, total, 'completed', attempt=attempt)
            return 0
        if failed_at != done:
            failed_at, failures = done, 0
        failures += 1
        error = log_path.read_text()[-8000:]
        if fatal_error(error) or failures >= config['max_failures_per_review']:
            write_progress(runtime, output, total, 'failed', attempt=attempt, exit_code=code,
                           reason='fatal_error' if fatal_error(error) else 'repeated_failure', log=str(log_path))
            return code
        write_progress(runtime, output, total, 'retrying', attempt=attempt, failures_at_checkpoint=failures)
        time.sleep(config['retry_delay_seconds'])


def main():
    parser = argparse.ArgumentParser(description='Run one sequential, checkpointed AI/Human topic-label batch.')
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--runtime', type=Path, required=True)
    args = parser.parse_args()
    config = json.loads((ROOT / 'quest_computes/topic_label_full_run.json').read_text())
    runtime = args.runtime.resolve()
    output = args.repo.resolve() / 'signal_extraction_pipeline/results' / config['run_id']
    runtime.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    signal.signal(signal.SIGINT, lambda *_: sys.exit(130))
    try:
        manifest = prepare_input(args.repo.resolve(), runtime, config)
        print('INPUT_COHORT_VERIFIED ' + json.dumps(manifest), flush=True)
        command = [sys.executable, '-u', str(ROOT / 'signal_extraction_pipeline/run_incremental.py'),
                   '--topic-tree', '--input', str(runtime / 'input_reviews.json'), '--output', str(output),
                   '--env', str(runtime / 'deepseek.env'),
                   '--config', str(ROOT / 'quest_computes/incremental_review_labels.json')]
        return supervise(command, runtime, output, manifest, config)
    except Exception as error:
        write_json(runtime / 'progress.json', {'checked_at': utc_now(), 'status': 'failed',
                                              'error_type': type(error).__name__, 'error': str(error)})
        raise


if __name__ == '__main__':
    sys.exit(main())
