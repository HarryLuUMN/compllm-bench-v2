"""Local, dependency-free viewer for append-only agent trajectories."""
import argparse
import hashlib
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ASSETS = Path(__file__).resolve().parent
DEFAULT_ROOT = ASSETS.parent / 'trace_databases'
DEFAULT_SUMMARIES = ASSETS.parent / 'discritized_summary/results/deepseek_discretization_20261103'
DEFAULT_DATABASE = ASSETS.parent / 'paper_database'
DEFAULT_AI_RESULTS = ASSETS.parent / 'review_pipeline/results'
DEFAULT_LABELS = ASSETS.parent / 'signal_extraction_pipeline/results/topic_labels_ai_human_full_20261118/state.json'


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
        sections.append({'review_id': f'{paper}::{path.stem}',
                         'kind': item['source']['kind'], 'document': item['source']['document'],
                         'text': path.read_text(encoding='utf-8')})
    return {'paper': paper, 'sections': sections}


def original_detail(root, paper, review_id, database=DEFAULT_DATABASE, ai_results=DEFAULT_AI_RESULTS):
    sections = summary_detail(root, paper)['sections']
    section = next((row for row in sections if row['review_id'] == review_id), None)
    if section is None:
        raise ValueError('Unknown review')
    metadata = json.loads((root / paper / 'metadata.json').read_text())
    item = next(row for row in metadata['summaries']
                if f"{paper}::{Path(row['file']).stem}" == review_id)
    note = ''
    if section['kind'] == 'ai':
        path = Path(metadata['ai_response']).resolve()
        if not path.is_relative_to(ai_results.resolve()) or path.name != 'response.txt' or path.parent.name != paper:
            raise ValueError('Invalid original AI response path')
        text = path.read_text(encoding='utf-8')
    elif section['kind'] == 'human':
        path = (database / paper / 'human_reviews/reviews.json').resolve()
        if not path.is_relative_to(database.resolve()):
            raise ValueError('Invalid original review path')
        data = json.loads(path.read_text())
        parts = data.get('parts', {})
        document = section['document']
        if data.get('kind') == 'bmc_peer_review_reports' and document.startswith('ReviewerReport_'):
            matches = [a.get('text') for a in parts.get('assets', []) if a.get('label') == document]
            if len(matches) != 1:
                raise ValueError('Original reviewer document is missing or ambiguous')
            text = matches[0]
        elif data.get('kind') == 'elife_public_reviews' and document in (
                'public_reviews', 'reviewer_comments_quoted_in_author_response'):
            text = parts.get('public_reviews' if document == 'public_reviews' else 'author_response')
            if document == 'reviewer_comments_quoted_in_author_response':
                note = 'Source history includes author responses; the summary extracts reviewer comments.'
        elif data.get('kind') in ('plos_peer_review_history', 'bmj_open_peer_review_history') and document == 'peer_review_history':
            text = parts.get('peer_review_history')
            note = 'Original peer-review history may include author replies and editorial material.'
        else:
            raise ValueError('Unsupported original reviewer document')
    else:
        raise ValueError('Unsupported review kind')
    if not isinstance(text, str) or not text.strip():
        raise ValueError('Original review text is empty')
    digest = hashlib.sha256(text.encode()).hexdigest()
    if item.get('source_sha256') and item['source_sha256'] != digest:
        raise ValueError('Original source differs from the text used for this summary')
    return {'review_id': review_id, 'kind': section['kind'], 'document': section['document'],
            'text': text, 'note': note, 'source_sha256': digest}


class LabelResults:
    def __init__(self, path):
        self.path = Path(path)
        self.signature = None
        self.papers = {}
        self.progress = {}
        self.lock = threading.Lock()

    def detail(self, paper):
        if not re.fullmatch(r'paper_[0-9]{4}', paper):
            raise ValueError('Invalid paper ID')
        with self.lock:
            if not self.path.exists():
                return {'paper': paper, 'available': False, 'reviews': []}
            stat = self.path.stat()
            signature = (stat.st_ino, stat.st_mtime_ns, stat.st_size)
            if signature != self.signature:
                state = json.loads(self.path.read_text(encoding='utf-8'))
                papers = {}
                for record in state['reviews']:
                    paper_id = record['review_id'].split('::', 1)[0]
                    row = {'review_id': record['review_id'],
                           **{key: record.get(key, []) for key in ('labels', 'existing_labels', 'new_labels')},
                           'topic_labels': record.get('topic_labels', {})}
                    papers.setdefault(paper_id, []).append(row)
                self.papers = papers
                self.progress = {'status': state['status'], 'processed_reviews': len(state['reviews']),
                                 'label_count': len(state['label_space']),
                                 'topic_count': len(state['topic_label_tree']), 'updated_at': stat.st_mtime}
                self.signature = signature
            return {'paper': paper, 'available': True, **self.progress,
                    'reviews': self.papers.get(paper, [])}


def handler_for(root, summaries_root=DEFAULT_SUMMARIES, labels_state=DEFAULT_LABELS):
    labels = LabelResults(labels_state)
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            url = urlsplit(self.path)
            try:
                if url.path == '/api/original':
                    query = parse_qs(url.query)
                    self.send_json(original_detail(summaries_root, query.get('paper', [''])[0],
                                                   query.get('review', [''])[0]))
                elif url.path == '/api/labels':
                    query = parse_qs(url.query)
                    self.send_json(labels.detail(query.get('paper', [''])[0]))
                elif url.path == '/api/summaries':
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
    parser.add_argument('--labels-state', type=Path, default=DEFAULT_LABELS)
    args = parser.parse_args()
    server = ThreadingHTTPServer(('127.0.0.1', args.port), handler_for(args.root.resolve(), args.summaries_root.resolve(), args.labels_state.resolve()))
    print(f'Trace viewer: http://127.0.0.1:{args.port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
