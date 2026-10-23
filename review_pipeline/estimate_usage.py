import argparse
import json
from collections import defaultdict
from pathlib import Path

COUNTERS = ('input', 'cache_read', 'cache_write', 'output', 'prompt', 'total',
            'reasoning_subset', 'provider_requests', 'web_search_starts', 'web_search_successes')


def trace_key(path):
    return tuple(Path(path).parts[-3:])


def read_trace(path):
    totals = dict.fromkeys(COUNTERS, 0)
    metadata = {}
    malformed_lines = 0
    with path.open(encoding='utf-8') as stream:
        for line in stream:
            try:
                event = json.loads(line)
            except ValueError:
                malformed_lines += 1
                continue
            data = event.get('data', {})
            kind = event.get('type')
            if kind == 'run_start':
                metadata = data
            elif kind == 'provider_request':
                totals['provider_requests'] += 1
            elif kind == 'tool_execution_start' and data.get('toolName') == 'web_search':
                totals['web_search_starts'] += 1
            elif kind == 'tool_execution_end' and data.get('toolName') == 'web_search' and not data.get('isError'):
                totals['web_search_successes'] += 1
            elif kind == 'message_end' and data.get('message', {}).get('role') == 'assistant':
                usage = data['message'].get('usage', {})
                for field, source in [('input', 'input'), ('cache_read', 'cacheRead'),
                                      ('cache_write', 'cacheWrite'), ('output', 'output'),
                                      ('reasoning_subset', 'reasoning')]:
                    totals[field] += usage.get(source, 0) or 0
    totals['prompt'] = totals['input'] + totals['cache_read'] + totals['cache_write']
    totals['total'] = totals['prompt'] + totals['output']
    return {'path': str(path), 'metadata': metadata, 'usage': totals, 'malformed_lines': malformed_lines}


def sum_usage(traces):
    return {key: sum(trace['usage'][key] for trace in traces) for key in COUNTERS}


def estimate(args):
    statuses = {}
    for path in sorted(args.results.rglob('status.json')):
        status = json.loads(path.read_text(encoding='utf-8'))
        if status.get('trajectory'):
            statuses[trace_key(status['trajectory'])] = status
    groups = defaultdict(list)
    papers = defaultdict(list)
    attempts = []
    for path in sorted(args.traces.rglob('*.jsonl')):
        trace = read_trace(path)
        status = statuses.get(trace_key(path), {})
        metadata = trace.pop('metadata')
        group = (metadata.get('requested_provider') or status.get('provider'),
                 metadata.get('requested_model') or status.get('model'),
                 metadata.get('thinking_level') or status.get('thinking'))
        trace['provider'], trace['model'], trace['thinking'] = group
        trace['paper'] = path.parent.name
        trace['latest_status'] = status.get('status')
        groups[group].append(trace)
        papers[(group, path.parent.name)].append(trace)
        attempts.append(trace)
    return {
        'usage_basis': 'Lower bound: recorded assistant message_end usage only. Interrupted or unrecorded provider usage is excluded. Output already includes reasoning; reasoning is a subset and is never added again. Prompt = input + cache_read + cache_write.',
        'actual_recorded_usage': sum_usage(attempts),
        'groups': [{'provider': key[0], 'model': key[1], 'thinking': key[2],
                    'attempts': len(traces), 'usage': sum_usage(traces)} for key, traces in groups.items()],
        'papers': [{'provider': key[0][0], 'model': key[0][1], 'thinking': key[0][2],
                    'paper': key[1], 'attempts': len(traces), 'usage': sum_usage(traces)}
                   for key, traces in papers.items()],
        'attempts': attempts,
    }


def parser():
    result = argparse.ArgumentParser(description='Record actual token usage across all review attempts.')
    result.add_argument('--results', type=Path, required=True)
    result.add_argument('--traces', type=Path, required=True)
    result.add_argument('--output', type=Path)
    return result


if __name__ == '__main__':
    arguments = parser().parse_args()
    report = json.dumps(estimate(arguments), ensure_ascii=False, indent=2) + '\n'
    if arguments.output:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(report, encoding='utf-8')
    print(report, end='')
