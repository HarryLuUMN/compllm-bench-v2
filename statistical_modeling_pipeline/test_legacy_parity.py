import importlib.util
import os
import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

LEGACY_ROOT = Path(os.environ['LEGACY_CODEBASE']) if os.environ.get('LEGACY_CODEBASE') else None


@unittest.skipUnless(LEGACY_ROOT is not None, 'Set LEGACY_CODEBASE for original-to-new numerical regression')
class LegacyParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(LEGACY_ROOT))
        spec = importlib.util.spec_from_file_location('legacy_fixed_models', LEGACY_ROOT / 'scripts/analyze_mendeley_fixed_signal_models.py')
        cls.legacy = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = cls.legacy
        spec.loader.exec_module(cls.legacy)
        from modeling import (
            MODEL_SPECS,
            fit_target,
            normalize_citations,
            target_definitions,
        )
        cls.modeling = (MODEL_SPECS, fit_target, normalize_citations, target_definitions)

    def fixture(self):
        return pd.DataFrame({'doi': [f'10.test/{i}' for i in range(60)],
                             'paper_id': [f'paper_{i:04d}' for i in range(60)],
                             'year': [2020 + i % 3 for i in range(60)],
                             'primary_research_field': ['Biology' if i % 2 else 'Medicine' for i in range(60)],
                             'groundtruth_citation_count': [i * i % 113 for i in range(60)],
                             'predicted_citation_count': [i * 7 % 79 for i in range(60)],
                             'paper__signal__p': [i % 2 for i in range(60)],
                             'human__signal__h': [(i // 2) % 2 for i in range(60)],
                             'audit__signal__a': [(i // 3) % 2 for i in range(60)]})

    def test_specs_thresholds_and_normalization_match(self):
        specs, _, normalize, targets = self.modeling
        self.assertEqual([(s.key, s.blocks, s.target_family) for s in specs],
                         [(s.key, s.blocks, s.target_family) for s in self.legacy.MODEL_SPECS])
        frame = self.fixture()
        actual = normalize(frame, 5)
        expected = self.legacy.add_year_field_normalization(frame, min_cell_size=5)
        np.testing.assert_allclose(actual['normalized_log_citation_residual'], expected['normalized_log_citation_residual'])
        self.assertEqual(actual['normalization_group'].tolist(), expected['normalization_group'].tolist())
        for family, function in [('llm_prediction', self.legacy.llm_target_definitions),
                                 ('normalized_citation', self.legacy.normalized_citation_target_definitions)]:
            for current, original in zip(targets(actual, family), function(expected)):
                self.assertEqual(current['target'], original['target'])
                np.testing.assert_array_equal(current['values'], original['values'])

    def test_all_four_model_oof_metrics_and_coefficients_match(self):
        specs, fit, normalize, targets = self.modeling
        frame = normalize(self.fixture(), 5)
        blocks = {name: [f'{name}__signal__{letter}'] for name, letter in [('paper', 'p'), ('human', 'h'), ('audit', 'a')]}
        for spec in specs:
            target = targets(frame, spec.target_family)[0]
            expected, expected_coefs, expected_oof = self.legacy.fit_target(
                frame, spec=spec, target=target, feature_blocks=blocks, descriptions={}, regularization_c=0.1)
            actual, coefs, oof = fit(frame, spec=spec, target=target, feature_blocks=blocks,
                                      descriptions={}, regularization_c=0.1)
            self.assertEqual(actual['fit_status'], expected['fit_status'])
            np.testing.assert_allclose(actual['fold_aucs'], expected['fold_aucs'], atol=1e-12)
            np.testing.assert_allclose(oof['probability'], expected_oof['probability'], atol=1e-12)
            np.testing.assert_allclose(coefs.sort_values('feature')['standardized_coefficient'],
                                       expected_coefs.sort_values('feature')['standardized_coefficient'], atol=1e-12)
