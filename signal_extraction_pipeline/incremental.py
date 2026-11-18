import json
from pathlib import Path

from signal_extraction_pipeline.core import cache_key, messages, write_json
from signal_extraction_pipeline.topic_tree import extend_tree, select_topics, tree_messages, validate_tree


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


def run_incremental(reviews, output, prompt, configuration, complete, topic_prompts=None):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    checkpoint = output / 'state.json'
    settings = {'prompt': prompt, 'configuration': configuration, 'format': 2}
    if topic_prompts is not None:
        settings.update(format=5 if 'select' in topic_prompts else 4, topic_prompts=topic_prompts)
    fingerprint = cache_key(settings)
    state = json.loads(checkpoint.read_text()) if checkpoint.exists() else {
        'fingerprint': fingerprint, 'configuration': configuration, 'label_space': [], 'reviews': []}
    if state['fingerprint'] != fingerprint:
        raise ValueError('Configuration or prompt changed; use a new output directory')
    if topic_prompts is not None:
        if not checkpoint.exists():
            state['topic_label_tree'] = {}
        validate_tree(state['topic_label_tree'], state['label_space'])
    done = state['reviews']
    if len(reviews) < len(done) or any(record['input_sha256'] != cache_key(review)
                                     for record, review in zip(done, reviews)):
        raise ValueError('Processed reviews changed or were reordered; use a new output directory')
    state['status'] = 'running'
    write_json(checkpoint, state)
    try:
        for review in reviews[len(done):]:
            if topic_prompts is not None:
                selected = select_topics(state['topic_label_tree'], review, topic_prompts, configuration, complete) if 'select' in topic_prompts else list(state['topic_label_tree'])
                visible_tree = {name: state['topic_label_tree'][name] for name in selected}
                request = tree_messages(topic_prompts['context'], prompt, visible_tree, {'review': review})
            else:
                data = {'available_labels': state['label_space'], 'review': review}
                request = messages(prompt, 'input', json.dumps(data, ensure_ascii=False))
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
            if topic_prompts is not None:
                tree, assigned, created = extend_tree(state['topic_label_tree'], new, topic_prompts,
                                                      configuration, complete)
                validate_tree(tree, state['label_space'] + new)
                record.update(selected_topics=selected, topic_assignments=assigned, new_topics=created,
                              topic_labels={topic: [label for label in record['labels'] if label in group]
                                            for topic, group in tree.items()
                                            if any(label in group for label in record['labels'])})
                state['topic_label_tree'] = tree
            state['reviews'].append(record)
            state['label_space'].extend(new)
            write_json(checkpoint, state)
        write_json(output / 'label_space.json', state['label_space'])
        write_json(output / 'labels_by_review.json', {r['review_id']: r['labels'] for r in state['reviews']})
        if topic_prompts is not None:
            write_json(output / 'topic_label_tree.json', state['topic_label_tree'])
            write_json(output / 'topics_by_review.json', {r['review_id']: r['topic_labels'] for r in state['reviews']})
        state['status'] = 'completed'
        write_json(checkpoint, state)
    except Exception:
        state['status'] = 'failed'
        write_json(checkpoint, state)
        raise
    return state
