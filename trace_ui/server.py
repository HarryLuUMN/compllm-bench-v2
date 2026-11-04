"""Local, dependency-free viewer for append-only agent trajectories."""
import argparse
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ASSETS = Path(__file__).resolve().parent
DEFAULT_ROOT = ASSETS.parent / 'trace_databases'
DEFAULT_SUMMARIES = ASSETS.parent / 'discritized_summary/results/deepseek_discretization_20261103'


def trace_path(root, name):
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()) or path.suffix != '.jsonl':
        raise ValueError('Invalid trace path')
    return path


def read_events(path, cursor):
    if cursor < 0:
        raise ValueError('Invalid cursor')
    events = []
    with path.open('rb') as stream:
        size = stream.seek(0, 2)
        reset = cursor > size
        if reset:
            cursor = 0
        stream.seek(cursor)
        for _ in range(500):
            line = stream.readline()
            if not line.endswith(b'\n'):
                break
            cursor = stream.tell()
            try:
                events.append(json.loads(line))
            except (ValueError, UnicodeDecodeError):
                events.append({'type': 'parse_error', 'data': {'error': 'Invalid JSONL record'}})
    return {'events': events, 'cursor': cursor, 'reset': reset, 'more': cursor < size and bool(events)}


def summary_detail(root, paper):
    if not re.fullmatch(r'paper_[0-9]{4}', paper):
        raise ValueError('Invalid paper ID')
    folder = (root / paper).resolve()
    if not folder.is_relative_to(root.resolve()):
        raise ValueError('Invalid summary path')
    if json.loads((folder / 'status.json').read_text())['status'] != 'completed':
        raise ValueError('Summary is not complete')
    metadata = json.loads((folder / 'metadata.json').read_text())
    sections = []
    for item in metadata['summaries']:
        path = (folder / item['file']).resolve()
        if not path.is_relative_to(folder) or path.suffix != '.txt':
            raise ValueError('Invalid summary file')
        sections.append({'kind': item['source']['kind'], 'document': item['source']['document'],
                         'text': path.read_text(encoding='utf-8')})
    return {'paper': paper, 'sections': sections}


def handler_for(root, summaries_root=DEFAULT_SUMMARIES):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            url = urlsplit(self.path)
            try:
                if url.path == '/api/summaries':
                    papers = sorted(p.name for p in summaries_root.iterdir()
                                    if re.fullmatch(r'paper_[0-9]{4}', p.name)) if summaries_root.exists() else []
                    self.send_json({'papers': papers})
                elif url.path == '/api/summary':
                    query = parse_qs(url.query)
                    self.send_json(summary_detail(summaries_root, query.get('paper', [''])[0]))
                elif url.path == '/api/traces':
                    rows = []
                    for path in root.rglob('*.jsonl'):
                        if not path.resolve().is_relative_to(root.resolve()):
                            continue
                        stat = path.stat()
                        rows.append({'id': path.relative_to(root).as_posix(), 'modified': stat.st_mtime, 'bytes': stat.st_size})
                    self.send_json(sorted(rows, key=lambda row: row['modified'], reverse=True))
                elif url.path == '/api/events':
                    query = parse_qs(url.query)
                    self.send_json(read_events(trace_path(root, query.get('trace', [''])[0]), int(query.get('cursor', ['0'])[0])))
                else:
                    names = {'/': 'index.html', '/app.js': 'app.js', '/style.css': 'style.css',
                             '/summaries': 'summaries.html', '/summaries.js': 'summaries.js',
                             '/summaries.css': 'summaries.css'}
                    if url.path not in names:
                        self.send_error(404)
                        return
                    path = ASSETS / names[url.path]
                    content = path.read_bytes()
                    self.send_response(200)
                    self.send_header('Content-Type', {'html': 'text/html', 'js': 'text/javascript', 'css': 'text/css'}[path.suffix[1:]] + '; charset=utf-8')
                    self.send_header('Content-Length', str(len(content)))
                    self.end_headers()
                    self.wfile.write(content)
            except (ValueError, OSError) as error:
                self.send_json({'error': str(error)}, 400)

        def send_json(self, value, status=200):
            content = json.dumps(value).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(content)))
            self.end_headers()
            self.wfile.write(content)

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=DEFAULT_ROOT)
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--summaries-root', type=Path, default=DEFAULT_SUMMARIES)
    args = parser.parse_args()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), handler_for(args.root.resolve(), args.summaries_root.resolve()))
    print(f'Trace viewer: http://127.0.0.1:{args.port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
