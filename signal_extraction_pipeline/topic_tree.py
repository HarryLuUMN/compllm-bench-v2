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


def validate_placement(answer, labels, tree):
    if not isinstance(answer, dict) or set(answer) != {'assignments', 'new_topics'}:
        raise ValueError('Expected assignments and new_topics')
    if not isinstance(answer['assignments'], list):
        raise ValueError('Topic assignments must be an array')
    assigned = {}
    for item in answer['assignments']:
        if not isinstance(item, dict) or set(item) not in ({'label', 'topic'}, {'label', 'topic_id'}):
            raise ValueError('Each assignment requires a label and topic')
        label = canonical(item['label'], labels, 'label')
        if label in assigned:
            raise ValueError('A label can only be assigned once')
        if 'topic_id' in item:
            index = item['topic_id']
            if type(index) is not int or not 0 <= index < len(tree):
                raise ValueError('Unknown topic ID')
            assigned[label] = list(tree)[index]
        else:
            assigned[label] = canonical(item['topic'], tree, 'topic')
    remaining = [label for label in labels if label not in assigned]
    groups = validate_new_topics({'new_topics': answer['new_topics']}, remaining, tree)
    return assigned, groups


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


def tree_messages(context_prompt, stage_prompt, tree, payload):
    prefix = messages(context_prompt, 'tree', json.dumps(tree, ensure_ascii=False, separators=(',', ':')))
    stage = messages(stage_prompt, 'input', json.dumps(payload, ensure_ascii=False))
    return prefix + [{'role': 'user', 'content': '\n\n'.join(part['content'] for part in stage)}]


def complete_topic_stage(request, validate, prompt, configuration, complete):
    request = request[:]
    for attempt in range(3):
        if len(json.dumps(request, ensure_ascii=False).encode('utf-8')) > configuration['max_input_bytes']:
            raise ValueError('Topic stage exceeds input budget; no text was truncated')
        response = complete(request)
        try:
            return validate(json.loads(response))
        except ValueError as error:
            if attempt == 2:
                raise
            request += [{'role': 'assistant', 'content': response},
                        {'role': 'user', 'content': prompt['repair'].replace('{{error}}', str(error))}]


def select_topics(tree, review, prompts, configuration, complete):
    if not tree:
        return []
    request = tree_messages(prompts['context'], prompts['select'],
                            [{'id': i, 'topic': name} for i, name in enumerate(tree)], {'review': review})
    def validate(answer):
        if not isinstance(answer, dict) or set(answer) != {'selected_topic_ids'} or not isinstance(answer['selected_topic_ids'], list):
            raise ValueError('Expected selected_topic_ids array')
        ids = answer['selected_topic_ids']
        names = list(tree)
        if any(type(i) is not int or not 0 <= i < len(names) for i in ids):
            raise ValueError('Unknown topic ID')
        return [names[i] for i in dict.fromkeys(ids)]
    return complete_topic_stage(request, validate, prompts['select'], configuration, complete)


def extend_tree(tree, new_labels, prompts, configuration, complete):
    updated = {topic: labels[:] for topic, labels in tree.items()}
    if not new_labels:
        return updated, {}, {}
    request = tree_messages(prompts['context'], prompts['place'], [{'id': i, 'topic': name} for i, name in enumerate(tree)] if 'select' in prompts else tree, {'new_labels': new_labels})
    if len(json.dumps(request, ensure_ascii=False).encode('utf-8')) > configuration['max_input_bytes']:
        raise ValueError('New labels plus full topic-label tree exceeds input budget; no text was truncated')
    if 'select' in prompts:
        assigned, groups = complete_topic_stage(request, lambda answer: validate_placement(answer, new_labels, tree),
                                                prompts['place'], configuration, complete)
    else:
        assigned, groups = validate_placement(json.loads(complete(request)), new_labels, tree)
    for label, topic in assigned.items():
        updated[topic].append(label)
    updated.update(groups)
    return updated, assigned, groups
