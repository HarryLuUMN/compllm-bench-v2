"""Release the full batch only after all expected inputs have arrived."""
import hashlib
import json
import os
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATABASE = ROOT / 'paper_database'
RUN_ID = os.environ.get('REVIEW_RUN_ID', 'quest_qwen38_27b_full_20261023')
MARKER = ROOT / 'quest_computes/pi_runtime' / RUN_ID / 'dataset_ready.json'


def verify_dataset():
    manifest = json.loads((DATABASE / 'manifest.json').read_text())
    expected = [entry for entry in manifest['entries'] if entry.get('paper_pdf_status') == 'downloaded']
    if len(manifest['entries']) != 5024 or len(expected) != 5023:
        return None
    for entry in expected:
        folder = DATABASE / entry['folder']
        if not all((folder / name).is_file() for name in ['paper.pdf', 'metadata.json', 'human_reviews/reviews.json']):
            return None
    for entry in expected:
        folder = DATABASE / entry['folder']
        content = (folder / 'paper.pdf').read_bytes()
        if not content.startswith(b'%PDF-'):
            return None
        source = json.loads((folder / 'metadata.json').read_text()).get('paper_pdf_source', {})
        if source.get('md5') and hashlib.md5(content).hexdigest() != source['md5']:
            return None
        if source.get('sha256') and hashlib.sha256(content).hexdigest() != source['sha256']:
            return None
    return {'papers': len(manifest['entries']), 'pdfs': len(expected), 'verified_at': time.time()}


def main():
    while True:
        try:
            result = verify_dataset()
            if result:
                MARKER.parent.mkdir(parents=True, exist_ok=True)
                MARKER.write_text(json.dumps(result, indent=2) + '\n')
                print('DATASET_VERIFIED', result, flush=True)
                return
        except (OSError, ValueError):
            pass
        print('Waiting for complete dataset transfer', flush=True)
        time.sleep(60)


if __name__ == '__main__':
    main()
