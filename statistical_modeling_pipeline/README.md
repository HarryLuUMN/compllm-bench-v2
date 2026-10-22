# Four-model statistical analysis

This is the downstream stage of `signal_extraction_pipeline`. It consumes its
completed signal domain and presence outputs and fits the original codebase's
later fixed-signal logistic models. It does not call LLMs or fetch citations.

| Model | Predictors | Outcome family |
| --- | --- | --- |
| Model 1 | paper + human + audit signals | LLM-predicted citation counts, discretized |
| Model 2 | human + audit signals | year/field-normalized observed citations |
| Model 3 | human signals | year/field-normalized observed citations |
| Model 4 | audit signals | year/field-normalized observed citations |

Model 1 has three binary targets: count >= median, >= 90th percentile, and >= 20.
Models 2–4 each have three binary targets: normalized residual >= median,
>= 90th percentile, and >= 0. Ties use `>=`, including Model 1. This follows
`scripts/analyze_mendeley_fixed_signal_models.py`, rather than the earlier
`analysis/review_signal_models/` experiment's extra OLS/raw-citation models.

## Inputs

The audit source defaults to `signal_extraction_pipeline/results`. Source
directories must contain `manifest.json` with `status: completed`,
`signal_domain.json`, and `presence.json`. Labels are optional. Separate paper and
human signal runs can be passed through `--paper-signals` and `--human-signals`.
Feature names are namespaced by block so independent domains may reuse IDs.
This stage does not extract paper/human signals automatically.

A source may also be a presence JSON file, or a CSV with `response_id`, `record_id`
or `paper_id` and `signal_*` columns, including legacy `signal__*` columns.
Standalone files have no completion manifest to check; the caller is responsible
for their validity. Presence values must be finite and in [0, 1].

Supply `--metadata` as CSV with one row per physical paper:

```csv
paper_id,doi,year,primary_research_field,groundtruth_citation_count,predicted_citation_count
paper_0001,10.example/paper1,2021,Biology,42,30
```

The example DOI is illustrative. Paper IDs should match the upstream response
IDs (normally folder names). If DOI is omitted entirely, paper_id is used as the
canonical identity; if present, DOI must be nonempty and unique after casefolding.
Preserve the citation source and snapshot date in additional metadata columns
when possible. Additional columns are preserved, but never used as predictors.

For response/reviewer IDs that differ from paper IDs, pass `--record-map`:

```csv
block,response_id,paper_id
human,paper_0001::review::1,paper_0001
human,paper_0001::review::2,paper_0001
```

The block must be `paper`, `human`, or `audit`. Unmapped IDs are assumed to be
paper IDs. Multiple responses belonging to one paper are averaged within each
block, exactly as in the old paper-level analysis. The audit block defines the
cohort. Every provided additional block must cover this cohort; missing papers
are errors rather than all-zero rows. Metadata duplicates and unknown IDs fail.

The current upstream produces audit signals only. With just audit signals and
observed citation/year metadata, Model 4 can run; Models 1–3 explicitly record
`skipped_missing_blocks`. All four require the other two signal sources and LLM
citation predictions in metadata. Missing target values are excluded separately
by outcome family, never replaced by zero. Model 1 does not require observed
citation coverage; Models 2–4 do not require LLM citation predictions.

## Running

Use an environment with the numerical libraries already used in the original
analysis: NumPy, pandas, scikit-learn and matplotlib, plus PyYAML for configuration.
Their environment specification is in
`quest_computes/statistical_modeling_requirements.txt`. No old-repository module
is imported at runtime. All compute settings are in
`quest_computes/statistical_modeling.yaml`; override the file with `--config`.

From the v2 repository root:

```sh
python3 statistical_modeling_pipeline/run.py \
  --audit-signals signal_extraction_pipeline/results \
  --human-signals /path/to/human_signal_results \
  --paper-signals /path/to/paper_signal_results \
  --metadata /path/to/paper_metadata.csv
```

For an audit-only experiment, omit `--human-signals` and `--paper-signals`.
`--output` defaults to `statistical_modeling_pipeline/results` (ignored by Git).
Run one process per output directory. Summary status `completed` means the
analysis finished; inspect each model's fit status to see which could be fitted.
Only consume outputs from a completed summary, since older files may remain
after a failed rerun. Plotting can be disabled in the compute config.

## Statistical method and parity

- Aggregate features to one row per paper, preventing reviewer-level train/test
  overlap. The three observed-citation models share an identical eligible cohort
  and target-specific folds, making their AUC differences paired comparisons.
- Normalize `log1p(observed citations)` by the cohort year/field cell mean and
  population SD. Default minimum cell size is 5, then fall back to the year cell
  and finally the global cohort. Zero/invalid SD becomes 1, matching the original.
  Missing research fields use `unknown`; inspect normalization groups before
  interpreting this as field-adjusted evidence.
- Three-fold shuffled `StratifiedKFold`, seed 42. Per training fold, retain only
  nonconstant features, fit `StandardScaler`, then `LogisticRegression` with
  L2 regularization (`C=1`, `lbfgs`, max_iter=5000). These legacy defaults are
  preserved; C, folds and seed can be changed through the compute config.
- Skip targets lacking enough members of either class. Record no-feature and
  fold-constant-feature cases explicitly, without invented OOF predictions.
- Report mean/SD of fold AUCs and pooled out-of-fold AUC. Coefficients come from a
  separate standardized full-cohort fit and are descriptive, not CV estimates.

Cohort-normalized residuals and quantile thresholds follow the legacy analysis;
this is not train-fold-only target construction. Upstream signal discovery is
also performed on the full cohort. These CV metrics evaluate a fixed, transductive
signal representation, not the generalization of an entirely refitted discovery
pipeline. Fold SD is not a confidence interval, and coefficients/AUC differences
are not significance or causal estimates. No parameter sweep is selected on these
OOF results automatically.

## Outputs

- `paper_feature_matrix.csv`: paper-level features, metadata and normalization.
- `model_results.csv`: targets, fit/skip statuses, class counts and AUC metrics.
- `out_of_fold_predictions.csv`: paper/DOI, model, target, fold, truth, probability.
- `feature_coefficients.csv`: block-aware signal descriptions and standardized coefficients.
- `auc_differences.csv`: Model 2−3, 2−4 and 4−3 mean-CV-AUC differences per target.
- `figures/model_auc.png`: fitted model/target AUCs with fold SD.
- `summary.json` and `report.md`: configuration, cohorts, exclusions and results.

## Verification

```sh
python3 -m unittest discover -s statistical_modeling_pipeline -p 'test_*.py'
```

Optional original-to-new numerical checks require a local checkout of the old
repository, plus its original numerical dependencies (including matplotlib):

```sh
LEGACY_CODEBASE=/path/to/compllm-bench \
  python3 -m unittest discover -s statistical_modeling_pipeline -p 'test_legacy_parity.py'
```

These compare all four models' OOF probabilities, fold AUCs, standardized
coefficients, target definitions and normalization directly against the legacy
functions. Normal runs and non-parity tests do not depend on the old checkout.
