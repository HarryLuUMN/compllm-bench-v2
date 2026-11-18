import json
from pathlib import Path

from signal_extraction_pipeline.core import cache_key, messages, write_json


def load_reviews(path):
    path = Path(path)
    if path.is_file():
        reviews = json.loads(path.read_text(encoding='utf-8'))
    else:
        reviews = []
        for metadata in sorted(path.glob('paper_*/metadata.json')):
            folder = metadata.parent
            if json.loads((folder / 'status.json').read_text())['status'] != 'completed':
                continue
            for item in json.loads(metadata.read_text())['summaries']:
                source = (folder / item['file']).resolve()
                if not source.is_relative_to(folder.resolve()) or source.suffix != '.txt':
                    raise ValueError('Summary file must be a text file inside its paper folder')
                reviews.append({'review_id': f'{folder.name}::{source.stem}', 'paper_id': folder.name,
                                'source': item['source'], 'text': source.read_text(encoding='utf-8')})
    if not isinstance(reviews, list) or not reviews:
        raise ValueError('Input must contain at least one review')
    seen = set()
    for review in reviews:
        if not isinstance(review, dict) or any(not isinstance(review.get(k), str) or not review[k].strip()
                                               for k in ('review_id', 'text')):
            raise ValueError('Each review requires nonempty review_id and text strings')
        if review['review_id'] in seen:
            raise ValueError('Duplicate review_id')
        seen.add(review['review_id'])
    return reviews


def validate_labels(answer, label_space):
    if not isinstance(answer, dict) or set(answer) != {'existing_labels', 'new_labels'}:
        raise ValueError('Expected existing_labels and new_labels arrays')
    used = set()
    normalized_space = {' '.join(label.casefold().split()): label for label in label_space}
    existing, new = [], []
    for key in ('existing_labels', 'new_labels'):
        if not isinstance(answer[key], list):
            raise ValueError(f'{key} must be an array')
        for label in answer[key]:
            if not isinstance(label, str) or not label.strip() or label != label.strip():
                raise ValueError('Labels must be nonempty trimmed strings')
            normalized = ' '.join(label.casefold().split())
            if normalized in used:
                raise ValueError('Duplicate labels in model output')
            if normalized in normalized_space:
                existing.append(normalized_space[normalized])
            else:
                new.append(label)
            used.add(normalized)
    return existing, new


def run_incremental(reviews, output, prompt, configuration, complete):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    checkpoint = output / 'state.json'
    fingerprint = cache_key({'prompt': prompt, 'configuration': configuration, 'format': 2})
    state = json.loads(checkpoint.read_text()) if checkpoint.exists() else {
        'fingerprint': fingerprint, 'configuration': configuration, 'label_space': [], 'reviews': []}
    if state['fingerprint'] != fingerprint:
        raise ValueError('Configuration or prompt changed; use a new output directory')
    done = state['reviews']
    if len(reviews) < len(done) or any(record['input_sha256'] != cache_key(review)
                                     for record, review in zip(done, reviews)):
        raise ValueError('Processed reviews changed or were reordered; use a new output directory')
    state['status'] = 'running'
    write_json(checkpoint, state)
    try:
        for review in reviews[len(done):]:
            payload = json.dumps({'available_labels': state['label_space'], 'review': review}, ensure_ascii=False)
            request = messages(prompt, 'input', payload)
            if len(json.dumps(request, ensure_ascii=False).encode('utf-8')) > configuration['max_input_bytes']:
                raise ValueError('Review plus full label space exceeds input budget; no text was truncated')
            response = complete(request)
            proposed = json.loads(response)
            existing, new = validate_labels(proposed, state['label_space'])
            record = {k: v for k, v in review.items() if k != 'text'}
            record.update(input_sha256=cache_key(review), existing_labels=existing, new_labels=new,
                          labels=existing + new,
                          classification_corrected=(existing != proposed['existing_labels']
                                                    or new != proposed['new_labels']))
            state['reviews'].append(record)
            state['label_space'].extend(new)
            write_json(checkpoint, state)
        write_json(output / 'label_space.json', state['label_space'])
        write_json(output / 'labels_by_review.json', {r['review_id']: r['labels'] for r in state['reviews']})
        state['status'] = 'completed'
        write_json(checkpoint, state)
    except Exception:
        state['status'] = 'failed'
        write_json(checkpoint, state)
        raise
    return state
