import argparse
import json
from collections import defaultdict
from pathlib import Path

COUNTERS = ('input', 'cache_read', 'cache_write', 'output', 'prompt', 'total',
            'reasoning_subset', 'provider_requests', 'web_search_starts', 'web_search_successes')


def trace_key(path):
    return tuple(Path(path).parts[-3:])


def normalized_usage(usage, raw=False):
    if raw:
        details = usage.get('prompt_tokens_details') or {}
        cached = details.get('cached_tokens', 0) or 0
        return {'input': (usage.get('prompt_tokens', 0) or 0) - cached,
                'cache_read': cached, 'cache_write': 0, 'output': usage.get('completion_tokens', 0) or 0,
                'reasoning_subset': (usage.get('completion_tokens_details') or {}).get('reasoning_tokens', 0) or 0}
    return {key: usage.get(source, 0) or 0 for key, source in
            [('input', 'input'), ('cache_read', 'cacheRead'), ('cache_write', 'cacheWrite'),
             ('output', 'output'), ('reasoning_subset', 'reasoning')]}


def read_trace(path):
    totals = dict.fromkeys(COUNTERS, 0)
    metadata = {}
    malformed_lines = 0
    requests = {}
    sidecar = path.with_suffix(path.suffix + '.tokens.json')
    if sidecar.is_file():
        try:
            data = json.loads(sidecar.read_text(encoding='utf-8'))
            usage = normalized_usage(data['totals'])
            totals.update(usage)
            totals['provider_requests'] = data['requests_started']
            totals['web_search_starts'] = totals['web_search_successes'] = None
            metadata = {'requested_provider': data.get('provider'), 'requested_model': data.get('model'),
                        'thinking_level': data.get('thinking')}
        except (ValueError, KeyError):
            sidecar = None
    else:
        sidecar = None
    if sidecar is None:
        with path.open(encoding='utf-8') as stream:
            for line in stream:
                try:
                    event = json.loads(line)
                except ValueError:
                    malformed_lines += 1
                    continue
                data = event.get('data', {})
                kind = event.get('type')
                request = event.get('request', totals['provider_requests'])
                if kind == 'run_start':
                    metadata = data
                elif kind == 'provider_request':
                    totals['provider_requests'] += 1
                elif kind == 'tool_execution_start' and data.get('toolName') == 'web_search':
                    totals['web_search_starts'] += 1
                elif kind == 'tool_execution_end' and data.get('toolName') == 'web_search' and not data.get('isError'):
                    totals['web_search_successes'] += 1
                elif kind == 'provider_stream_event' and (data.get('data') or {}).get('usage'):
                    requests[request] = (True, normalized_usage(data['data']['usage'], raw=True))
                elif kind == 'message_end' and data.get('message', {}).get('role') == 'assistant':
                    if not requests.get(request, (False,))[0]:
                        requests[request] = (False, normalized_usage(data['message'].get('usage', {})))
        for _, usage in requests.values():
            for key, value in usage.items():
                totals[key] += value
    totals['prompt'] = totals['input'] + totals['cache_read'] + totals['cache_write']
    totals['total'] = totals['prompt'] + totals['output']
    return {'path': str(path), 'metadata': metadata, 'usage': totals, 'malformed_lines': malformed_lines,
            'usage_source': 'sidecar' if sidecar else 'trajectory'}


def sum_usage(traces):
    return {key: None if any(trace['usage'][key] is None for trace in traces) else
            sum(trace['usage'][key] for trace in traces) for key in COUNTERS}


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
        'usage_basis': 'Lower bound: recorded per-request provider usage, falling back to assistant message_end usage. Unrecorded provider usage is excluded. Tool counts are null when the token sidecar does not track them. Output already includes reasoning; reasoning is a subset and is never added again. Prompt = input + cache_read + cache_write.',
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
