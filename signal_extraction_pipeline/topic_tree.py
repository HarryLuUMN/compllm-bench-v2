import json

from signal_extraction_pipeline.core import messages


def normalized(name):
    return ' '.join(name.casefold().split())


def canonical(value, names, kind):
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f'{kind} must be a nonempty trimmed string')
    lookup = {normalized(name): name for name in names}
    if normalized(value) not in lookup:
        raise ValueError(f'Unknown {kind}: {value}')
    return lookup[normalized(value)]


def validate_assignment(answer, labels, tree):
    if not isinstance(answer, dict) or set(answer) != {'assignments', 'remaining_labels'}:
        raise ValueError('Expected assignments and remaining_labels')
    if not isinstance(answer['assignments'], list) or not isinstance(answer['remaining_labels'], list):
        raise ValueError('Topic assignments and remaining labels must be arrays')
    assigned = {}
    remaining = []
    for item in answer['assignments']:
        if not isinstance(item, dict) or set(item) != {'label', 'topic'}:
            raise ValueError('Each assignment requires a label and topic')
        label = canonical(item['label'], labels, 'label')
        if label in assigned:
            raise ValueError('A label can only be assigned once')
        assigned[label] = canonical(item['topic'], tree, 'topic')
    for value in answer['remaining_labels']:
        label = canonical(value, labels, 'label')
        if label in assigned or label in remaining:
            raise ValueError('Duplicate assigned or remaining label')
        remaining.append(label)
    if set(assigned) | set(remaining) != set(labels):
        raise ValueError('Every new label must be assigned or marked remaining')
    return assigned, remaining


def validate_new_topics(answer, remaining, tree):
    if not isinstance(answer, dict) or set(answer) != {'new_topics'} or not isinstance(answer['new_topics'], list):
        raise ValueError('Expected a new_topics array')
    groups = {}
    names = {normalized(name) for name in tree}
    used = set()
    for item in answer['new_topics']:
        if not isinstance(item, dict) or set(item) != {'topic', 'labels'}:
            raise ValueError('Each new topic requires topic and labels')
        topic = item['topic']
        if not isinstance(topic, str) or not topic.strip() or topic != topic.strip():
            raise ValueError('Topic must be a nonempty trimmed string')
        if normalized(topic) in names:
            raise ValueError('New topic already exists')
        if not isinstance(item['labels'], list) or not item['labels']:
            raise ValueError('A new topic must contain at least one remaining label')
        group = []
        for value in item['labels']:
            label = canonical(value, remaining, 'remaining label')
            if label in used:
                raise ValueError('A label can belong to only one topic')
            used.add(label)
            group.append(label)
        groups[topic] = group
        names.add(normalized(topic))
    if used != set(remaining):
        raise ValueError('Every remaining label must belong to a new topic')
    return groups


def validate_tree(tree, labels):
    if not isinstance(tree, dict):
        raise ValueError('Topic-label tree must be an object')
    names = set()
    members = []
    for topic, group in tree.items():
        if not isinstance(topic, str) or not topic.strip() or topic != topic.strip() or normalized(topic) in names:
            raise ValueError('Invalid or duplicate topic name')
        if not isinstance(group, list) or not group:
            raise ValueError('Each topic must contain labels')
        names.add(normalized(topic))
        members.extend(group)
    if any(not isinstance(label, str) or not label.strip() for label in members):
        raise ValueError('Invalid label in topic-label tree')
    if len({normalized(label) for label in members}) != len(members) or set(members) != set(labels):
        raise ValueError('Every label must belong to exactly one topic')


def extend_tree(tree, new_labels, review, prompts, configuration, complete):
    updated = {topic: labels[:] for topic, labels in tree.items()}

    def ask(stage, payload):
        request = messages(prompts[stage], 'input', json.dumps(payload, ensure_ascii=False))
        if len(json.dumps(request, ensure_ascii=False).encode('utf-8')) > configuration['max_input_bytes']:
            raise ValueError('Review plus full topic-label tree exceeds input budget; no text was truncated')
        return json.loads(complete(request))

    assigned, remaining = {}, list(new_labels)
    if new_labels and updated:
        answer = ask('assign', {'review': review, 'topic_label_tree': updated, 'new_labels': new_labels})
        assigned, remaining = validate_assignment(answer, new_labels, updated)
    for label, topic in assigned.items():
        updated[topic].append(label)
    groups = {}
    if remaining:
        answer = ask('create', {'review': review, 'topic_label_tree': updated, 'remaining_labels': remaining})
        groups = validate_new_topics(answer, remaining, updated)
        updated.update(groups)
    return updated, assigned, groups
