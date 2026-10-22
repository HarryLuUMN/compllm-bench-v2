import argparse
import hashlib
import json
import math
from importlib.metadata import version
from pathlib import Path

import pandas as pd
import yaml
from inputs import build_dataset
from modeling import MODEL_SPECS, fit_target, normalize_citations, target_definitions

ROOT = Path(__file__).resolve().parents[1]


def read_config(path):
    config = yaml.safe_load(Path(path).read_text(encoding='utf-8'))
    required = {'cv_splits', 'random_state', 'regularization_c', 'normalization_min_cell_size', 'plot'}
    if not isinstance(config, dict) or set(config) != required:
        raise ValueError(f'Compute config must contain exactly {sorted(required)}')
    if (type(config['cv_splits']) is not int or config['cv_splits'] < 2
            or type(config['normalization_min_cell_size']) is not int or config['normalization_min_cell_size'] < 1
            or type(config['random_state']) is not int or not 0 <= config['random_state'] < 2 ** 32
            or not isinstance(config['regularization_c'], (int, float)) or isinstance(config['regularization_c'], bool)
            or not math.isfinite(config['regularization_c']) or config['regularization_c'] <= 0
            or type(config['plot']) is not bool):
        raise ValueError('Invalid modeling compute configuration')
    return config


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def plot_results(results, output):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    fitted = results[results['fit_status'] == 'fit']
    if fitted.empty:
        return
    fig, ax = plt.subplots(figsize=(10, max(4, len(fitted) * 0.35)))
    labels = [f"{row.model} / {row.target}" for row in fitted.itertuples()]
    ax.barh(labels, fitted['cv_auc_mean'], xerr=fitted['cv_auc_std'], color='#4c78a8', capsize=3)
    ax.axvline(0.5, color='grey', linestyle='--', linewidth=1)
    ax.set_xlim(0, 1.05)
    ax.set_xlabel('Mean cross-validation AUC (error bars: fold SD)')
    ax.invert_yaxis()
    fig.tight_layout()
    directory = output / 'figures'
    directory.mkdir(exist_ok=True)
    fig.savefig(directory / 'model_auc.png', dpi=160)
    plt.close(fig)


def analyze(args):
    config = read_config(args.config)
    args.output.mkdir(parents=True, exist_ok=True)
    summary_path = args.output / 'summary.json'
    write_json(summary_path, {'status': 'running', 'configuration': config})
    (args.output / 'figures/model_auc.png').unlink(missing_ok=True)
    try:
        frame, blocks, descriptions, provenance = build_dataset(
            {'audit': args.audit_signals, 'human': args.human_signals, 'paper': args.paper_signals},
            args.metadata, args.record_map)
        results, coefficients, predictions = [], [], []
        family_frames = {}
        for family, required in [('llm_prediction', ['predicted_citation_count']),
                                 ('normalized_citation', ['groundtruth_citation_count', 'year'])]:
            eligible = frame.dropna(subset=required).copy().reset_index(drop=True)
            if family == 'normalized_citation' and not eligible.empty:
                eligible = normalize_citations(eligible, config['normalization_min_cell_size'])
            family_frames[family] = eligible
        for spec in MODEL_SPECS:
            dataset = family_frames[spec.target_family]
            missing_blocks = [block for block in spec.blocks if block not in blocks]
            if missing_blocks or dataset.empty:
                results.append({'model': spec.key, 'target_family': spec.target_family,
                                'target': None, 'signal_blocks': '+'.join(spec.blocks),
                                'fit_status': 'skipped_missing_blocks' if missing_blocks else 'skipped_missing_targets',
                                'missing_blocks': '+'.join(missing_blocks), 'n_rows': len(dataset)})
                continue
            for target in target_definitions(dataset, spec.target_family):
                result, coefs, oof = fit_target(dataset, spec=spec, target=target, feature_blocks=blocks,
                                               descriptions=descriptions, regularization_c=config['regularization_c'],
                                               cv_splits=config['cv_splits'], random_state=config['random_state'])
                results.append(result)
                if not coefs.empty:
                    coefficients.append(coefs)
                if not oof.empty:
                    oof = oof.merge(dataset[['doi', 'paper_id']], on='doi', validate='many_to_one')
                    predictions.append(oof)
        result_frame = pd.DataFrame(results)
        coefficient_frame = pd.concat(coefficients, ignore_index=True) if coefficients else pd.DataFrame(
            columns=['model', 'target', 'signal_block', 'feature', 'signal_id', 'signal', 'standardized_coefficient'])
        prediction_frame = pd.concat(predictions, ignore_index=True) if predictions else pd.DataFrame(
            columns=['model', 'target', 'doi', 'paper_id', 'fold', 'y_true', 'probability'])
        normalized = family_frames['normalized_citation']
        normalized_columns = [c for c in normalized if c.startswith(('expected_log_', 'normaliz'))]
        exported = frame.merge(normalized[['paper_id', *normalized_columns]], on='paper_id', how='left', validate='one_to_one')
        exported.to_csv(args.output / 'paper_feature_matrix.csv', index=False)
        result_frame.to_csv(args.output / 'model_results.csv', index=False)
        coefficient_frame.to_csv(args.output / 'feature_coefficients.csv', index=False)
        prediction_frame.to_csv(args.output / 'out_of_fold_predictions.csv', index=False)
        differences = []
        for target in result_frame['target'].dropna().unique():
            values = {row['model']: row.get('cv_auc_mean') for row in results
                      if row.get('target') == target and row.get('fit_status') == 'fit'}
            for left, right in [('model_2', 'model_3'), ('model_2', 'model_4'), ('model_4', 'model_3')]:
                if left in values and right in values:
                    differences.append({'target': target, 'comparison': left + '_minus_' + right,
                                        'cv_auc_mean_difference': values[left] - values[right]})
        pd.DataFrame(differences, columns=['target', 'comparison', 'cv_auc_mean_difference']).to_csv(
            args.output / 'auc_differences.csv', index=False)
        if config['plot']:
            plot_results(result_frame, args.output)
        safe_results = json.loads(result_frame.to_json(orient='records') or '[]')
        input_hashes = {}
        for source in (args.audit_signals, args.human_signals, args.paper_signals, args.metadata, args.record_map):
            if source is None:
                continue
            paths = [source / name for name in ('manifest.json', 'signal_domain.json', 'presence.json')] if source.is_dir() else [source]
            for path in paths:
                input_hashes[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
        summary = {'status': 'completed', 'configuration': config, 'provenance': provenance,
                   'input_hashes': input_hashes,
                   'software_versions': {package: version(package) for package in ('numpy', 'pandas', 'scikit-learn')},
                   'feature_blocks': blocks, 'family_cohort_sizes': {key: len(value) for key, value in family_frames.items()},
                   'excluded_missing_targets': {key: len(frame) - len(value) for key, value in family_frames.items()},
                   'model_results': safe_results,
                   'inputs': {key: str(getattr(args, key)) for key in ('audit_signals', 'human_signals', 'paper_signals', 'metadata', 'record_map')}}
        lines = ['# Four-model signal analysis', '',
                 'Model 1: paper + human + audit signals predict LLM citation-count labels.',
                 'Models 2/3/4: human + audit / human / audit predict normalized observed-citation labels.', '',
                 '| Model | Target | Status | Papers | Mean CV AUC |', '| --- | --- | --- | --- | --- |']
        for result in safe_results:
            auc = result.get('cv_auc_mean')
            lines.append(f"| {result['model']} | {result.get('target') or '—'} | {result['fit_status']} | {result['n_rows']} | {auc if auc is not None else '—'} |")
        lines.extend(['', 'Skipped models are not fitted models. Missing targets are excluded, never set to zero.',
                      'All citation-model comparisons use the same paper cohort and target-specific folds.',
                      'Signal discovery and citation normalization are cohort-defined; CV evaluates fixed features.',
                      'Fold SD is not a confidence interval. Coefficients and AUC differences are descriptive.',
                      'No significance, causal claim or nested signal-discovery validation is implied.'])
        (args.output / 'report.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
        write_json(summary_path, summary)
        return summary
    except Exception as error:
        write_json(summary_path, {'status': 'failed', 'error': str(error), 'configuration': config})
        raise


def parser():
    result = argparse.ArgumentParser(description='Run the legacy four fixed-signal logistic models on extracted signals.')
    result.add_argument('--audit-signals', type=Path, default=ROOT / 'signal_extraction_pipeline/results')
    result.add_argument('--human-signals', type=Path)
    result.add_argument('--paper-signals', type=Path)
    result.add_argument('--metadata', type=Path, required=True)
    result.add_argument('--record-map', type=Path)
    result.add_argument('--config', type=Path, default=ROOT / 'quest_computes/statistical_modeling.yaml')
    result.add_argument('--output', type=Path, default=ROOT / 'statistical_modeling_pipeline/results')
    return result


if __name__ == '__main__':
    summary = analyze(parser().parse_args())
    fitted = sum(row['fit_status'] == 'fit' for row in summary['model_results'])
    print(json.dumps({'status': summary['status'], 'fitted_model_targets': fitted, 'papers': summary['provenance']['papers']}))
