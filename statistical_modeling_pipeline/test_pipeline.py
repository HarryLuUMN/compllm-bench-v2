import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from inputs import build_dataset, read_signal_block
from modeling import MODEL_SPECS, fit_target, normalize_citations, target_definitions
from run import analyze, parser

ROOT = Path(__file__).resolve().parents[1]


def write_block(path, values, label='A signal'):
    path.mkdir(parents=True)
    (path / 'manifest.json').write_text('{"status":"completed"}')
    (path / 'signal_domain.json').write_text(json.dumps([{'signal_id': 'signal_00001', 'high_level_label': label}]))
    (path / 'presence.json').write_text(json.dumps({key: {'signal_00001': value} for key, value in values.items()}))


def fixture(root, count=60):
    metadata = []
    for index in range(count):
        metadata.append({'paper_id': f'paper_{index:04d}', 'doi': f'10.example/{index}',
                         'year': 2020 + index % 3, 'primary_research_field': 'Biology',
                         'groundtruth_citation_count': index * index % 113,
                         'predicted_citation_count': index * 7 % 79})
    pd.DataFrame(metadata).to_csv(root / 'metadata.csv', index=False)
    for block, divisor in [('paper', 1), ('human', 2), ('audit', 3)]:
        write_block(root / block, {row['paper_id']: (index // divisor) % 2 for index, row in enumerate(metadata)}, block)
    config = yaml.safe_load((ROOT / 'quest_computes/statistical_modeling.yaml').read_text())
    config['plot'] = False
    (root / 'config.yaml').write_text(yaml.safe_dump(config))
    return parser().parse_args(['--audit-signals', str(root / 'audit'), '--human-signals', str(root / 'human'),
                               '--paper-signals', str(root / 'paper'), '--metadata', str(root / 'metadata.csv'),
                               '--config', str(root / 'config.yaml'), '--output', str(root / 'output')])


class PipelineTests(unittest.TestCase):
    def test_full_downstream_cli_exports_all_twelve_model_targets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            args = fixture(root)
            command = [sys.executable, str(ROOT / 'statistical_modeling_pipeline/run.py'),
                       '--audit-signals', str(args.audit_signals), '--human-signals', str(args.human_signals),
                       '--paper-signals', str(args.paper_signals), '--metadata', str(args.metadata),
                       '--config', str(args.config), '--output', str(args.output)]
            completed = subprocess.run(command, capture_output=True, text=True, timeout=60, check=False)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            summary = json.loads((args.output / 'summary.json').read_text())
            self.assertEqual(summary['status'], 'completed')
            self.assertEqual(sum(r['fit_status'] == 'fit' for r in summary['model_results']), 12)
            oof = pd.read_csv(args.output / 'out_of_fold_predictions.csv')
            self.assertEqual(len(oof), 60 * 12)
            self.assertEqual(set(oof['fold']), {1, 2, 3})
            self.assertFalse(oof.duplicated(['model', 'target', 'doi']).any())
            for _, group in oof[oof['model'] != 'model_1'].groupby('target'):
                self.assertTrue((group.groupby('doi')['fold'].nunique() == 1).all())
            self.assertEqual(len(pd.read_csv(args.output / 'auc_differences.csv')), 9)

    def test_zero_signal_domain_preserves_papers_and_skips_fits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            args = fixture(root)
            (args.audit_signals / 'signal_domain.json').write_text('[]')
            (args.audit_signals / 'presence.json').write_text(json.dumps({f'paper_{i:04d}': {} for i in range(60)}))
            args.human_signals = None
            args.paper_signals = None
            summary = analyze(args)
            self.assertEqual(summary['provenance']['papers'], 60)
            statuses = [r['fit_status'] for r in summary['model_results'] if r['model'] == 'model_4']
            self.assertEqual(statuses, ['skipped_no_features'] * 3)

    def test_rerun_removes_stale_plot_when_plotting_is_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = fixture(Path(tmp))
            figure = args.output / 'figures/model_auc.png'
            figure.parent.mkdir(parents=True)
            figure.write_bytes(b'old plot')
            analyze(args)
            self.assertFalse(figure.exists())

    def test_missing_blocks_skip_models_instead_of_zero_filling(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = fixture(Path(tmp))
            args.human_signals = None
            args.paper_signals = None
            summary = analyze(args)
            fitted = {r['model'] for r in summary['model_results'] if r['fit_status'] == 'fit'}
            self.assertEqual(fitted, {'model_4'})
            self.assertTrue(all(r['fit_status'] == 'skipped_missing_blocks' for r in summary['model_results'] if r['model'] != 'model_4'))

    def test_missing_targets_are_excluded_by_family(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = fixture(Path(tmp))
            metadata = pd.read_csv(args.metadata)
            metadata.loc[0, 'predicted_citation_count'] = np.nan
            metadata.loc[1, 'groundtruth_citation_count'] = np.nan
            metadata.to_csv(args.metadata, index=False)
            summary = analyze(args)
            self.assertEqual(summary['family_cohort_sizes'], {'llm_prediction': 59, 'normalized_citation': 59})
            self.assertEqual(summary['excluded_missing_targets'], {'llm_prediction': 1, 'normalized_citation': 1})

    def test_record_mapping_averages_review_presence_and_namespaces_signals(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            args = fixture(root)
            human = root / 'human/presence.json'
            original = json.loads(human.read_text())
            original.pop('paper_0000')
            original['review1'] = {'signal_00001': 1}
            original['review2'] = {'signal_00001': 0}
            human.write_text(json.dumps(original))
            mapping = root / 'map.csv'
            mapping.write_text('block,response_id,paper_id\nhuman,review1,paper_0000\nhuman,review2,paper_0000\n')
            frame, blocks, descriptions, _ = build_dataset({'audit': args.audit_signals, 'human': args.human_signals}, args.metadata, mapping)
            self.assertEqual(frame.set_index('paper_id').loc['paper_0000', blocks['human'][0]], 0.5)
            self.assertNotEqual(blocks['human'], blocks['audit'])
            self.assertEqual(descriptions[blocks['human'][0]], 'human')
            self.assertEqual(descriptions[blocks['audit'][0]], 'audit')

    def test_rejects_uncompleted_sources_missing_coverage_and_duplicate_dois(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            args = fixture(root)
            (args.audit_signals / 'manifest.json').write_text('{"status":"failed"}')
            with self.assertRaisesRegex(ValueError, 'not completed'):
                read_signal_block(args.audit_signals, 'audit')
            (args.audit_signals / 'manifest.json').write_text('{"status":"completed"}')
            presence = args.human_signals / 'presence.json'
            values = json.loads(presence.read_text())
            values.pop('paper_0000')
            presence.write_text(json.dumps(values))
            with self.assertRaisesRegex(ValueError, 'missing signals are not zeros'):
                build_dataset({'audit': args.audit_signals, 'human': args.human_signals}, args.metadata)
            metadata = pd.read_csv(args.metadata)
            metadata.loc[1, 'doi'] = metadata.loc[0, 'doi'].upper()
            metadata.to_csv(args.metadata, index=False)
            with self.assertRaisesRegex(ValueError, 'unique nonempty DOI'):
                build_dataset({'audit': args.audit_signals}, args.metadata)

    def test_constant_features_and_class_shortage_are_explicit_skips(self):
        frame = pd.DataFrame({'doi': [str(i) for i in range(12)], 'human__signal__h': [0] * 12})
        spec = MODEL_SPECS[2]
        target = {'target': 'test', 'label': 'test', 'description': 'test', 'values': [0, 1] * 6}
        result, _coefs, oof = fit_target(frame, spec=spec, target=target, feature_blocks={'human': ['human__signal__h']}, descriptions={})
        self.assertEqual(result['fit_status'], 'skipped_no_active_features_in_fold')
        self.assertTrue(oof.empty)
        target['values'] = [0] + [1] * 11
        result, _, _ = fit_target(frame, spec=spec, target=target, feature_blocks={'human': ['human__signal__h']}, descriptions={})
        self.assertEqual(result['fit_status'], 'skipped_class_count_below_cv_splits')

    def test_normalization_fallback_and_tied_median_use_legacy_semantics(self):
        frame = pd.DataFrame({'year': [2020, 2020, 2020, 2021],
                              'primary_research_field': ['A', 'A', 'B', 'C'],
                              'groundtruth_citation_count': [0, 10, 20, 30],
                              'predicted_citation_count': [0, 0, 10, 20]})
        normalized = normalize_citations(frame, 2)
        self.assertEqual(normalized['normalization_group'].tolist(), ['year_field:2020:A', 'year_field:2020:A', 'year:2020', 'global'])
        self.assertEqual(target_definitions(frame, 'llm_prediction')[0]['values'].tolist(), [0, 0, 1, 1])
        frame['predicted_citation_count'] = [0, 0, 0, 20]
        self.assertEqual(target_definitions(frame, 'llm_prediction')[0]['values'].tolist(), [1, 1, 1, 1])


if __name__ == '__main__':
    unittest.main()
