"""Restore verified missing PDFs directly from recorded public source URLs."""
import argparse
import concurrent.futures
import hashlib
import json
import time
from pathlib import Path
from urllib.request import Request, urlopen


def valid(content, source):
    return (content.startswith(b'%PDF-')
            and (not source.get('md5') or hashlib.md5(content).hexdigest() == source['md5'])
            and (not source.get('sha256') or hashlib.sha256(content).hexdigest() == source['sha256']))


def restore(database, workers):
    manifest = json.loads((database / 'manifest.json').read_text())
    entries = [e for e in manifest['entries'] if e.get('paper_pdf_status') == 'downloaded']

    def fetch(entry):
        folder = database / entry['folder']
        path = folder / 'paper.pdf'
        source = entry['paper_pdf_source']
        if path.is_file() and valid(path.read_bytes(), source):
            return 'existing'
        folder.mkdir(parents=True, exist_ok=True)
        for attempt in range(3):
            try:
                request = Request(source['url'], headers={'User-Agent': 'compllm-paper-database/1.0'})
                with urlopen(request, timeout=90) as response:
                    content = response.read()
                if not valid(content, source):
                    raise ValueError('PDF signature or checksum mismatch')
                temporary = folder / 'paper.pdf.remote.tmp'
                temporary.write_bytes(content)
                temporary.replace(path)
                print(entry['folder'], 'restored', len(content), flush=True)
                return 'restored'
            except (OSError, ValueError) as error:
                if attempt == 2:
                    print(entry['folder'], 'failed', str(error), flush=True)
                    return 'failed'
                time.sleep(2 ** attempt)

    counts = {'existing': 0, 'restored': 0, 'failed': 0}
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for status in pool.map(fetch, entries):
            counts[status] += 1
    print(json.dumps(counts), flush=True)
    return counts


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, default=Path(__file__).resolve().parents[2] / 'paper_database')
    parser.add_argument('--workers', type=int, default=8)
    args = parser.parse_args()
    if args.workers < 1:
        parser.error('--workers must be positive')
    raise SystemExit(1 if restore(args.database, args.workers)['failed'] else 0)
