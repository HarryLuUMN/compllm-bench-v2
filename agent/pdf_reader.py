import argparse
import json
from pathlib import Path


def read_pdf(root, filename, start_page=1, end_page=None):
    from pypdf import PdfReader

    root = Path(root).resolve(strict=True)
    path = (root / filename).resolve(strict=True)
    if not path.is_relative_to(root) or path.suffix.lower() != '.pdf':
        raise ValueError('PDF must be inside the selected folder')
    reader = PdfReader(path)
    end_page = min(start_page + 4, len(reader.pages)) if end_page is None else end_page
    if not 1 <= start_page <= end_page <= len(reader.pages) or end_page - start_page >= 10:
        raise ValueError(f'PDF has {len(reader.pages)} pages. Choose an inclusive range of at most 10 pages, numbered from 1.')
    pages = []
    remaining = 60000
    for number in range(start_page, end_page + 1):
        text = reader.pages[number - 1].extract_text() or ''
        excerpt = text[:remaining]
        pages.append({'page': number, 'text': excerpt, 'truncated': len(excerpt) < len(text)})
        remaining -= len(excerpt)
    return {'file': str(path.relative_to(root)), 'total_pages': len(reader.pages), 'pages': pages,
            'note': 'Text extraction only; scanned pages may require OCR. Figures are not interpreted.'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['prompt', 'pdf'])
    parser.add_argument('path')
    parser.add_argument('--root')
    parser.add_argument('--start', type=int, default=1)
    parser.add_argument('--end', type=int)
    args = parser.parse_args()
    if args.mode == 'prompt':
        import yaml

        prompt = yaml.safe_load(Path(args.path).read_text(encoding='utf-8'))
        if not isinstance(prompt, dict) or not isinstance(prompt.get('system'), str) or not prompt['system'].strip():
            raise ValueError('Prompt YAML must contain a nonempty system string')
        print(json.dumps(prompt, ensure_ascii=False))
    else:
        if not args.root:
            parser.error('--root is required for PDF reading')
        print(json.dumps(read_pdf(args.root, args.path, args.start, args.end), ensure_ascii=False))
