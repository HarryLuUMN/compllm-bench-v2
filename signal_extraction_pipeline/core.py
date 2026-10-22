import csv
import hashlib
import json
import math
from pathlib import Path

import yaml


def write_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    temporary.replace(path)


def cache_key(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def load_prompt(path):
    prompt = yaml.safe_load(Path(path).read_text(encoding='utf-8'))
    if not isinstance(prompt, dict) or any(not isinstance(prompt.get(k), str) or not prompt[k].strip()
                                           for k in ('system', 'task')):
        raise ValueError(f'Prompt requires nonempty system and task strings: {path}')
    return prompt


def messages(prompt, placeholder, value):
    marker = '{{' + placeholder + '}}'
    if marker not in prompt['task']:
        raise ValueError(f'Prompt missing {marker}')
    return [{'role': 'system', 'content': prompt['system']},
            {'role': 'user', 'content': prompt['task'].replace(marker, value)}]


def normalize(vector):
    if not vector or any(not math.isfinite(value) for value in vector):
        raise ValueError('Embedding contains invalid values')
    length = math.sqrt(sum(value * value for value in vector))
    if not math.isfinite(length) or length == 0:
        raise ValueError('Embedding must have a finite nonzero norm')
    return [value / length for value in vector]


def bounded_clusters(claims, embeddings, threshold, fits):
    if len(claims) != len(embeddings):
        raise ValueError('Claims and embedding counts differ')
    if embeddings and len({len(vector) for vector in embeddings}) != 1:
        raise ValueError('Embedding dimensions differ')
    clusters = []
    sums = []
    centroids = []
    for index, (claim, embedding) in enumerate(zip(claims, embeddings)):
        vector = normalize(embedding)
        ranked = sorted(((sum(a * b for a, b in zip(vector, center)), cluster_index)
                         for cluster_index, center in enumerate(centroids)), reverse=True)
        selected = None
        for similarity, cluster_index in ranked:
            if similarity < threshold:
                break
            if fits([claims[i] for i in clusters[cluster_index]] + [claim]):
                selected = cluster_index
                break
        if selected is None:
            if not fits([claim]):
                raise ValueError(f"Single claim {claim['claim_id']} exceeds the cluster context budget")
            clusters.append([index])
            sums.append(vector[:])
            centroids.append(vector)
        else:
            clusters[selected].append(index)
            sums[selected] = [a + b for a, b in zip(sums[selected], vector)]
            centroids[selected] = normalize(sums[selected])
    return clusters


def run_pipeline(responses, output, chat, embedder, extract_prompt, label_prompt,
                 configuration, threshold=0.82, batch_size=32, label_signals=False):
    if not 0 < threshold <= 1 or batch_size < 1:
        raise ValueError('threshold must be in (0, 1]; batch_size must be positive')
    if not responses:
        raise ValueError('No response.txt inputs found')
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    cache = output / 'cache'
    cache.mkdir(exist_ok=True)
    manifest = {'status': 'running', 'configuration': configuration, 'threshold': threshold,
                'label_signals': label_signals, 'prompt_hashes': {'extraction': cache_key(extract_prompt),
                                                               'label': cache_key(label_prompt)},
                'response_hashes': {key: cache_key(text) for key, text in responses.items()}}
    write_json(output / 'manifest.json', manifest)
    claims_by_response = {}
    for response_id, text in responses.items():
        if not text.strip():
            raise ValueError(f'Empty audit response: {response_id}')
        request = messages(extract_prompt, 'response', text)
        path = cache / (cache_key(['extract-v1', configuration, request]) + '.json')
        quotes = json.loads(path.read_text()) if path.exists() else json.loads(chat.complete(request))
        if not isinstance(quotes, list) or any(not isinstance(q, str) or not q.strip() or q not in text for q in quotes):
            raise ValueError(f'Claims must be an array of exact quotes from {response_id}')
        quotes = list(dict.fromkeys(quotes))
        write_json(path, quotes)
        claims_by_response[response_id] = quotes
        print(f'{response_id}: {len(quotes)} claims', flush=True)
    write_json(output / 'claims_by_response.json', claims_by_response)
    by_quote = {}
    for response_id, quotes in claims_by_response.items():
        for quote in quotes:
            by_quote.setdefault(quote, []).append(response_id)
    claims = [{'claim_id': f'claim_{i:06d}', 'quote': quote, 'response_ids': ids}
              for i, (quote, ids) in enumerate(by_quote.items(), 1)]
    write_json(output / 'claims.json', claims)
    embeddings = [None] * len(claims)
    missing = []
    paths = []
    for index, claim in enumerate(claims):
        path = cache / (cache_key(['embedding-v1', configuration, claim['quote']]) + '.json')
        paths.append(path)
        if path.exists():
            embeddings[index] = normalize(json.loads(path.read_text()))
        else:
            missing.append(index)
    for start in range(0, len(missing), batch_size):
        indices = missing[start:start + batch_size]
        vectors = embedder.embed([claims[index]['quote'] for index in indices])
        if len(vectors) != len(indices):
            raise ValueError('Embedding count does not match input count')
        for index, vector in zip(indices, vectors):
            embeddings[index] = normalize(vector)
            write_json(paths[index], embeddings[index])
    def cluster_messages(group):
        return messages(label_prompt, 'claims', json.dumps([claim['quote'] for claim in group], ensure_ascii=False))
    clusters = bounded_clusters(claims, embeddings, threshold, lambda group: chat.fits(cluster_messages(group)))
    domain = []
    for index, members in enumerate(clusters, 1):
        group = [claims[i] for i in members]
        request = cluster_messages(group)
        tokens, budget = chat.count(request)
        if tokens > budget:
            raise ValueError('Cluster exceeds context budget')
        label = None
        if label_signals:
            path = cache / (cache_key(['label-v1', configuration, request]) + '.json')
            label = json.loads(path.read_text()) if path.exists() else json.loads(chat.complete(request))
            if not isinstance(label, str) or not label.strip():
                raise ValueError('Signal label must be a nonempty JSON string')
            write_json(path, label)
        domain.append({'signal_id': f'signal_{index:05d}', 'high_level_label': label,
                       'claim_ids': [claim['claim_id'] for claim in group],
                       'response_ids': sorted({r for claim in group for r in claim['response_ids']}),
                       'input_tokens': tokens, 'input_token_budget': budget})
    presence = {response_id: {signal['signal_id']: int(response_id in signal['response_ids']) for signal in domain}
                for response_id in responses}
    write_json(output / 'signal_domain.json', domain)
    write_json(output / 'presence.json', presence)
    temporary = output / 'presence.csv.tmp'
    with temporary.open('w', encoding='utf-8', newline='') as target:
        writer = csv.writer(target)
        columns = [signal['signal_id'] for signal in domain]
        writer.writerow(['response_id', *columns])
        for response_id, values in presence.items():
            writer.writerow([response_id, *(values[column] for column in columns)])
    temporary.replace(output / 'presence.csv')
    manifest.update(status='completed', responses=len(responses), unique_claims=len(claims), signals=len(domain))
    write_json(output / 'manifest.json', manifest)
    return manifest
