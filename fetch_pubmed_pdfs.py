import argparse
import hashlib
import json
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen

BUCKET = 'https://pmc-oa-opendata.s3.amazonaws.com/'
CONVERTER = 'https://pmc.ncbi.nlm.nih.gov/tools/idconv/api/v1/articles/'


class Client:
    def __init__(self):
        self.last_request = 0.0

    def get(self, url):
        for attempt in range(3):
            time.sleep(max(0, 0.4 - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
            try:
                with urlopen(Request(url, headers={'User-Agent': 'compllm-paper-database/1.0'}), timeout=60) as response:
                    return response.read()
            except HTTPError as error:
                if error.code not in (429, 500, 502, 503, 504) or attempt == 2:
                    raise
                time.sleep(max(float(error.headers.get('Retry-After', 0)), 2 ** attempt))
            except (URLError, TimeoutError):
                if attempt == 2:
                    raise
                time.sleep(2 ** attempt)


def resolve_pdf(doi, client, email=None):
    params = {'ids': doi, 'idtype': 'doi', 'format': 'json', 'tool': 'compllm-paper-database'}
    if email:
        params['email'] = email
    records = json.loads(client.get(CONVERTER + '?' + urlencode(params))).get('records', [])
    record = next((r for r in records if str(r.get('doi', '')).lower() == doi.lower()), None)
    if not record or not record.get('pmcid') or record.get('live') in (False, 'false'):
        return None
    pmcid = record['pmcid']
    prefixes = []
    token = None
    while True:
        query = {'list-type': '2', 'prefix': pmcid + '.', 'delimiter': '/'}
        if token:
            query['continuation-token'] = token
        listing = ET.fromstring(client.get(BUCKET + '?' + urlencode(query)))
        prefixes.extend(e.text for e in listing.findall('{*}CommonPrefixes/{*}Prefix'))
        token = listing.findtext('{*}NextContinuationToken')
        if not token:
            break
    candidates = []
    for prefix in sorted(prefixes):
        version = prefix.rstrip('/')
        metadata = json.loads(client.get(BUCKET + prefix + version + '.json'))
        if str(metadata.get('doi', '')).lower() != doi.lower():
            continue
        if metadata.get('pdf_url'):
            candidates.append(metadata)
    if not candidates:
        return None
    candidates.sort(key=lambda m: (m.get('is_manuscript') in ('yes', True), int(m['version'])))
    selected = candidates[0]
    parsed = urlsplit(selected['pdf_url'])
    if parsed.scheme == 's3' and parsed.netloc == 'pmc-oa-opendata':
        url = BUCKET + parsed.path.lstrip('/')
    elif parsed.scheme == 'https' and parsed.netloc == 'pmc-oa-opendata.s3.amazonaws.com':
        url = BUCKET + parsed.path.lstrip('/')
    else:
        raise ValueError('Unexpected PMC PDF storage URL')
    return {'pmcid': pmcid, 'pmid': record.get('pmid'), 'version': selected['version'],
            'license_code': selected.get('license_code'), 'is_manuscript': selected.get('is_manuscript'),
            'url': url, 'md5': parse_qs(parsed.query).get('md5', [None])[0]}


def write_json(path, value):
    temporary = path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def fetch_database(database, limit=None, email=None, client=None):
    if limit is not None and limit < 1:
        raise ValueError('limit must be positive')
    client = client or Client()
    manifest_path = database / 'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    counts = {'downloaded': 0, 'existing': 0, 'unavailable': 0, 'failed': 0}
    processed = 0
    try:
        for entry in manifest['entries']:
            folder = database / entry['folder']
            pdf_path = folder / 'paper.pdf'
            metadata_path = folder / 'metadata.json'
            metadata = json.loads(metadata_path.read_text())
            if pdf_path.exists():
                with pdf_path.open('rb') as existing:
                    valid = existing.read(5) == b'%PDF-'
                if valid:
                    counts['existing'] += 1
                    entry.update(metadata)
                    continue
            if limit is not None and processed >= limit:
                break
            processed += 1
            try:
                if not metadata.get('doi'):
                    raise ValueError('Missing DOI')
                source = resolve_pdf(metadata['doi'], client, email)
                if source is None:
                    metadata['paper_pdf_status'] = 'unavailable'
                    counts['unavailable'] += 1
                else:
                    content = client.get(source['url'])
                    if not content.startswith(b'%PDF-'):
                        raise ValueError('Download is not a PDF')
                    if source['md5'] and hashlib.md5(content).hexdigest() != source['md5']:
                        raise ValueError('PDF checksum mismatch')
                    temporary = pdf_path.with_suffix('.pdf.tmp')
                    temporary.write_bytes(content)
                    temporary.replace(pdf_path)
                    metadata.update(paper_pdf_status='downloaded', paper_pdf_source=source)
                    counts['downloaded'] += 1
                metadata.pop('paper_pdf_error', None)
            except (HTTPError, URLError, TimeoutError, ValueError, OSError, ET.ParseError, KeyError) as error:
                metadata.update(paper_pdf_status='failed', paper_pdf_error=str(error))
                counts['failed'] += 1
            write_json(metadata_path, metadata)
            entry.update(metadata)
            print(f"{entry['folder']}: {metadata['paper_pdf_status']}", flush=True)
    finally:
        manifest['pdfs_downloaded'] = sum(e.get('paper_pdf_status') == 'downloaded' for e in manifest['entries'])
        manifest['last_pdf_run'] = counts
        write_json(manifest_path, manifest)
    return counts


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Fetch existing dataset paper PDFs from the PMC Cloud Service.')
    parser.add_argument('--database', type=Path, default=Path(__file__).parent / 'paper_database')
    parser.add_argument('--limit', type=int, help='Maximum missing PDFs to attempt this run')
    parser.add_argument('--email', help='Maintainer email for the NCBI API')
    args = parser.parse_args()
    print(json.dumps(fetch_database(args.database, args.limit, args.email)))
