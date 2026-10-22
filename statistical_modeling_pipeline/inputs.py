import csv
import json
from pathlib import Path

import numpy as np
import pandas as pd


def read_signal_block(source, block):
    source = Path(source)
    descriptions = {}
    if source.is_dir():
        manifest = json.loads((source / 'manifest.json').read_text(encoding='utf-8'))
        if manifest.get('status') != 'completed':
            raise ValueError(f'{block} signal extraction is not completed')
        domain = json.loads((source / 'signal_domain.json').read_text(encoding='utf-8'))
        identifiers = [item['signal_id'] for item in domain]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError(f'{block} domain contains duplicate signal IDs')
        descriptions = {item['signal_id']: item.get('high_level_label') or item['signal_id'] for item in domain}
        source = source / 'presence.json'
    else:
        identifiers = None
    if source.suffix == '.json':
        values = json.loads(source.read_text(encoding='utf-8'))
        if not isinstance(values, dict) or any(not isinstance(row, dict) for row in values.values()):
            raise ValueError('Presence JSON must map response IDs to signal mappings')
        frame = pd.DataFrame(list(values.values()), index=list(values))
        frame.index.name = 'response_id'
        if identifiers is not None:
            if set(frame.columns) != set(identifiers):
                raise ValueError(f'{block} domain and presence columns differ')
            frame = frame.reindex(columns=identifiers)
    else:
        frame = pd.read_csv(source, dtype=str, keep_default_na=False)
        id_column = next((name for name in ('response_id', 'record_id', 'paper_id') if name in frame), None)
        if id_column is None:
            raise ValueError(f'{source} needs response_id, record_id or paper_id')
        if frame[id_column].duplicated().any():
            raise ValueError(f'{source} contains duplicate response IDs')
        frame = frame.set_index(id_column)
        frame = frame.reindex(columns=[name for name in frame.columns if str(name).startswith('signal_')])
    if frame.empty and not len(frame.index):
        raise ValueError(f'{block} presence has no responses')
    if any(not isinstance(value, str) or not value.strip() for value in frame.index):
        raise ValueError(f'{block} response IDs must be nonempty strings')
    frame = pd.DataFrame(frame.apply(pd.to_numeric, errors='raise')).astype(float)
    array = frame.to_numpy()
    if not np.isfinite(array).all() or ((array < 0) | (array > 1)).any():
        raise ValueError(f'{block} presence must be finite and within [0, 1]')
    renamed = {column: f"{block}__signal__{column.removeprefix('signal__')}" for column in frame}
    if len(set(renamed.values())) != len(renamed):
        raise ValueError(f'{block} signal names collide after prefix normalization')
    descriptions = {renamed[column]: descriptions.get(column, column) for column in frame}
    return frame.rename(columns=renamed), descriptions


def read_record_map(path):
    mapping = {}
    if path is None:
        return mapping
    with Path(path).open(encoding='utf-8', newline='') as source:
        for row in csv.DictReader(source):
            block = row.get('block', '').strip()
            response_id = row.get('response_id', '').strip()
            paper_id = row.get('paper_id', '').strip()
            key = (block, response_id)
            if block not in ('paper', 'human', 'audit') or not response_id or not paper_id or key in mapping:
                raise ValueError('Record map needs unique block,response_id keys and nonempty paper_id')
            mapping[key] = paper_id
    return mapping


def build_dataset(sources, metadata_path, record_map=None):
    metadata = pd.read_csv(metadata_path, dtype=str, keep_default_na=False)
    if 'paper_id' not in metadata or metadata['paper_id'].duplicated().any() or (metadata['paper_id'].str.strip() == '').any():
        raise ValueError('Metadata must have unique nonempty paper_id values')
    if 'doi' not in metadata:
        metadata['doi'] = metadata['paper_id']
    metadata['doi'] = metadata['doi'].str.strip().str.casefold()
    if (metadata['doi'] == '').any() or metadata['doi'].duplicated().any():
        raise ValueError('Metadata must identify each physical paper exactly once (unique nonempty DOI)')
    for column in ('groundtruth_citation_count', 'predicted_citation_count', 'year'):
        if column not in metadata:
            metadata[column] = np.nan
        else:
            metadata[column] = pd.to_numeric(metadata[column].replace('', np.nan), errors='raise')
            if np.isinf(metadata[column]).any() or (metadata[column].dropna() < 0).any():
                raise ValueError(f'{column} must be finite and nonnegative when provided')
    if 'primary_research_field' not in metadata:
        metadata['primary_research_field'] = 'unknown'
    metadata['primary_research_field'] = metadata['primary_research_field'].replace('', 'unknown')
    mapping = read_record_map(record_map)
    blocks = {}
    descriptions = {}
    matrices = {}
    counts = {}
    for block, source in sources.items():
        if source is None:
            continue
        matrix, labels = read_signal_block(source, block)
        paper_ids = [mapping.get((block, response_id), response_id) for response_id in matrix.index]
        matrix.index = pd.Index(paper_ids, name='paper_id')
        unknown = set(paper_ids) - set(metadata['paper_id'])
        if unknown:
            raise ValueError(f'{block} response IDs lack metadata/mapping: {sorted(unknown)[:5]}')
        counts[block] = len(matrix)
        matrices[block] = matrix.groupby(level=0, sort=True).mean()
        blocks[block] = list(matrix.columns)
        descriptions.update(labels)
    if 'audit' not in matrices:
        raise ValueError('Audit signal output is required')
    cohort = matrices['audit'].index
    frame = metadata.set_index('paper_id').loc[cohort].copy()
    for block, matrix in matrices.items():
        missing = set(cohort) - set(matrix.index)
        if missing:
            raise ValueError(f'{block} block lacks {len(missing)} audit-cohort papers; missing signals are not zeros')
        frame = frame.join(matrix.loc[cohort], validate='one_to_one')
    return frame.reset_index().sort_values('doi').reset_index(drop=True), blocks, descriptions, {'papers': len(frame), 'response_rows': counts,
                                                      'aggregation': 'mean presence per paper within each source block'}
