# Four-model migration plan

Source of truth: `scripts/analyze_mendeley_fixed_signal_models.py` and
`scripts/analyze_fullbatch_signals.py::add_year_field_normalization` in the
original compllm-bench repository. The older
`analysis/review_signal_models/run_review_signal_models.py` also ran an OLS Model 1
and raw-citation targets; those are not the later fixed-signal experiment.

Behavior lock before migration: run the original fixed-signal and review-model
regression suites. Add numerical parity tests against the original fixed-signal
functions for model definitions, thresholds, normalization, OOF probabilities,
AUC and standardized coefficients before implementing the new modeling engine.

Bounded cleanup passes:

1. Remove legacy coupling: accept the upstream signal domain/presence outputs
   and explicit paper metadata instead of hard-coded run directories, Semantic
   Scholar cache shapes and prediction JSON formats. Keep original files intact.
2. Reuse one independent modeling implementation for all four model specifications.
   Preserve centroid-independent input namespaces, paper-level means, cohort
   normalization, fold-local feature selection/scaling and L2 logistic settings.
3. Record missing signal blocks, missing targets, constant features and class
   shortages explicitly. Never fabricate missing predictions or human signals.
4. Export a feature matrix, metrics, OOF probabilities/folds, standardized
   coefficients, paired model AUC differences, summary/report and compact figures.
   Keep compute settings under `quest_computes` and generated data ignored.
5. Run original-to-new numerical regression checks, input-boundary tests and a
   synthetic upstream-to-statistics CLI experiment, then review scoped changes.

Scope: `statistical_modeling_pipeline/`, its compute configuration,
`.gitignore`, and the project README. No production dependency on the old repo;
legacy imports are permitted only in opt-in regression tests.

Statistical boundaries: preserve the legacy cohort-defined year/field residuals
and target quantiles. Upstream signal discovery on the full cohort is transductive;
CV here evaluates fixed features and does not refit extraction/clustering in each
fold. Standardized coefficients are descriptive, not significance or causal tests.

## Verification completed

- Original fixed-signal/review suites: 15 tests passed before migration.
- New suite: 11 tests passed, including direct numerical parity for all four
  models, zero-domain handling, stale plot removal and CLI integration.
- Synthetic 60-paper smoke run: all 12 model/target combinations fitted and
  output figures visually checked. This is a software check, not real evidence.
- Ruff and Pyright on the scoped implementation: passed with no reported errors.
- Production modules do not import the original codebase. No live citation or
  signal experiment was run; real inputs must be supplied explicitly.
