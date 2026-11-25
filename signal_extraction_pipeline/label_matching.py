import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from signal_extraction_pipeline.core import cache_key, write_json
from signal_extraction_pipeline.topic_tree import complete_topic_stage, tree_messages


class ReviewStages:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)

    def run(self, stage, inputs, action):
        path = self.directory / f'{stage}_{cache_key(inputs)}.json'
        if path.exists():
            return json.loads(path.read_text())
        result = action()
        write_json(path, result)
        return result


def request_size(request):
    return len(json.dumps(request, ensure_ascii=False).encode('utf-8'))


def match_labels(review, tree, prompts, configuration, complete, stages):
    reserve = configuration.get('correction_reserve_bytes', 16000)
    budget = configuration['max_input_bytes'] - reserve
    if budget <= 0:
        raise ValueError('Input budget must exceed correction reserve')
    candidates = [(topic, label) for topic, labels in tree.items() for label in labels]

    def request_for(group):
        context = {}
        for index, (topic, label) in enumerate(group):
            context.setdefault(topic, []).append({'id': index, 'label': label})
        return tree_messages(prompts['context'], prompts['match'], context, {'review': review})

    batches = []
    def split(group):
        if request_size(request_for(group)) <= budget:
            if group:
                batches.append(group)
            return
        if len(group) <= 1:
            raise ValueError('Review plus one candidate exceeds input budget; no text was truncated')
        middle = len(group) // 2
        split(group[:middle])
        split(group[middle:])
    split(candidates)

    def match(group):
        request = request_for(group)
        names = [label for _, label in group]
        def validate(answer):
            if not isinstance(answer, dict) or set(answer) != {'selected_label_ids'} or not isinstance(answer['selected_label_ids'], list):
                raise ValueError('Expected selected_label_ids array')
            ids = answer['selected_label_ids']
            if any(type(index) is not int or not 0 <= index < len(names) for index in ids):
                raise ValueError('Unknown candidate label ID')
            return [names[index] for index in dict.fromkeys(ids)]
        return stages.run('match', request, lambda: complete_topic_stage(
            request, validate, prompts['match'], configuration, complete))

    workers = configuration.get('label_match_workers', 4)
    if type(workers) is not int or workers < 1 or workers > 8:
        raise ValueError('label_match_workers must be between 1 and 8')
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(match, batches))
    matched = list(dict.fromkeys(label for result in results for label in result))
    return matched, len(batches)
