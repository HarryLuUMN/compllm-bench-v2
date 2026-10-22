"""Export the viewer and selected trajectories as a static Pages site."""
import argparse
import hashlib
import json
import shutil
from pathlib import Path

ASSETS = Path(__file__).resolve().parent
VISIBLE = {'run_start', 'model_input', 'message_update', 'message_end',
           'tool_execution_start', 'tool_execution_end', 'run_error',
           'parse_error', 'run_end', 'final_response'}


def export_site(root, destination):
    if destination.exists():
        raise ValueError('Use a new output directory')
    destination.mkdir(parents=True)
    data = destination / 'data'
    data.mkdir()
    for name in ['index.html', 'style.css', 'app.js']:
        shutil.copyfile(ASSETS / name, destination / name)
    index = destination / 'index.html'
    index.write_text(index.read_text().replace('<script type="module"',
                    '<script>window.TRACE_STATIC=true;</script><script type="module"'))
    (destination / '.nojekyll').touch()
    traces = []
    for path in sorted(root.rglob('*.jsonl'), key=lambda p: p.stat().st_mtime, reverse=True):
        if not path.resolve().is_relative_to(root.resolve()):
            continue
        events = []
        for line in path.read_text().splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get('type') not in VISIBLE:
                continue
            if event['type'] == 'message_update':
                if event['data'].get('assistantMessageEvent', {}).get('type') not in {'thinking_delta', 'text_delta'}:
                    continue
            if event['type'] == 'run_end':
                event['data'] = {'status': event['data'].get('status')}
            events.append(event)
        name = path.relative_to(root).as_posix()
        filename = hashlib.sha256(name.encode()).hexdigest()[:20] + '.json'
        (data / filename).write_text(json.dumps(events, ensure_ascii=False))
        traces.append({'id': filename, 'label': name})
    (data / 'traces.json').write_text(json.dumps(traces))
    return len(traces)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(f'Exported {export_site(args.root, args.output)} traces to {args.output}')


if __name__ == '__main__':
    main()
