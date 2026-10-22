import argparse
import json
import shutil
from pathlib import Path


def build_database(dataset: Path, output: Path) -> dict:
    source = dataset / 'processed' / 'papers.jsonl'
    if output.exists() and any(output.iterdir()):
        raise ValueError(f'Output directory must be empty: {output}')
    output.mkdir(parents=True, exist_ok=True)
    entries = []
    with source.open(encoding='utf-8') as records:
        for number, line in enumerate(records, start=1):
            record = json.loads(line)
            folder = output / f'paper_{number:04d}'
            reviews = folder / 'human_reviews'
            reviews.mkdir(parents=True)
            (reviews / 'reviews.json').write_text(
                json.dumps(record['official_review'], ensure_ascii=False, indent=2) + '\n',
                encoding='utf-8',
            )
            raw_path = record.get('full_text', {}).get('raw_path')
            pdf = None
            if raw_path:
                relative = Path(raw_path)
                if 'raw' in relative.parts:
                    relative = Path(*relative.parts[relative.parts.index('raw'):])
                    candidate = dataset / relative
                    if candidate.suffix.lower() == '.pdf' and candidate.is_file():
                        with candidate.open('rb') as asset:
                            if asset.read(5) == b'%PDF-':
                                pdf = candidate
            if pdf:
                shutil.copyfile(pdf, folder / 'paper.pdf')
            metadata = {
                'dataset_index': number,
                'doi': record.get('doi'),
                'title': record.get('title'),
                'source_urls': record.get('source_urls', {}),
                'paper_pdf_status': 'copied' if pdf else 'missing',
            }
            (folder / 'metadata.json').write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2) + '\n', encoding='utf-8'
            )
            entries.append({'folder': folder.name, **metadata})
    manifest = {
        'source': str(source.resolve()),
        'papers': len(entries),
        'pdfs_copied': sum(e['paper_pdf_status'] == 'copied' for e in entries),
        'entries': entries,
    }
    (output / 'manifest.json').write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8'
    )
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Organize dataset papers and human reviews.')
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=Path(__file__).parent / 'paper_database')
    args = parser.parse_args()
    result = build_database(args.dataset, args.output)
    print(f"Papers: {result['papers']}; paper PDFs: {result['pdfs_copied']}")
