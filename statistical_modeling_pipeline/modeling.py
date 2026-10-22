import math
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import StratifiedKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

RANDOM_STATE = 42
CV_SPLITS = 3
SIGNAL_PREFIX = 'signal__'
MODEL_LABELS = {
    'model_1': 'Model 1: paper + human + LLM audit',
    'model_2': 'Model 2: human + LLM audit',
    'model_3': 'Model 3: human only',
    'model_4': 'Model 4: LLM audit only',
}

@dataclass(frozen=True)
class ModelSpec:
    key: str
    blocks: tuple[str, ...]
    target_family: str

def llm_target_definitions(frame: pd.DataFrame) -> list[dict[str, Any]]:
    values = frame["predicted_citation_count"].to_numpy(dtype=float)
    median = float(np.median(values))
    p90 = float(np.percentile(values, 90))
    return [
        {
            "target": "llm_predicted_above_median",
            "label": "LLM pred ≥ median",
            "description": f"LLM predicted citation count >= median ({median:g})",
            "values": (values >= median).astype(int),
        },
        {
            "target": "llm_predicted_top10",
            "label": "LLM pred top 10%",
            "description": f"LLM predicted citation count >= 90th percentile ({p90:g})",
            "values": (values >= p90).astype(int),
        },
        {
            "target": "llm_predicted_medium_or_higher",
            "label": "LLM pred ≥ 20",
            "description": "LLM predicted citation count >= 20",
            "values": (values >= 20).astype(int),
        },
    ]

def normalized_citation_target_definitions(
    frame: pd.DataFrame,
) -> list[dict[str, Any]]:
    values = frame["normalized_log_citation_residual"].to_numpy(dtype=float)
    median = float(np.median(values))
    p90 = float(np.percentile(values, 90))
    return [
        {
            "target": "normalized_above_median",
            "label": "Above median",
            "description": f"Year-field residual >= median ({median:.4g})",
            "values": (values >= median).astype(int),
        },
        {
            "target": "normalized_top10",
            "label": "Top 10%",
            "description": f"Year-field residual >= 90th percentile ({p90:.4g})",
            "values": (values >= p90).astype(int),
        },
        {
            "target": "normalized_above_expected",
            "label": "Above expected",
            "description": "Year-field residual >= 0",
            "values": (values >= 0).astype(int),
        },
    ]

def model_pipeline(regularization_c: float = 1.0, random_state: int = RANDOM_STATE) -> Any:
    return make_pipeline(
        StandardScaler(),
        LogisticRegression(
            C=regularization_c,
            max_iter=5_000,
            random_state=random_state,
            solver="lbfgs",
        ),
    )

def fit_target(
    frame: pd.DataFrame,
    *,
    spec: ModelSpec,
    target: dict[str, Any],
    feature_blocks: dict[str, list[str]],
    descriptions: dict[str, str],
    regularization_c: float = 1.0,
    cv_splits: int = CV_SPLITS,
    random_state: int = RANDOM_STATE,
) -> tuple[dict[str, Any], pd.DataFrame, pd.DataFrame]:
    columns = [
        column for block in spec.blocks for column in feature_blocks[block]
    ]
    if not columns:
        return skipped_result(frame, spec, target, columns, "skipped_no_features")
    x = frame[columns].to_numpy(dtype=float)
    y = np.asarray(target["values"], dtype=int)
    classes, class_counts = np.unique(y, return_counts=True)
    if len(classes) != 2 or int(class_counts.min()) < cv_splits:
        counts = dict(zip(classes.tolist(), class_counts.tolist()))
        result = {
            "model": spec.key,
            "model_label": MODEL_LABELS[spec.key],
            "target": target["target"],
            "target_label": target["label"],
            "target_description": target["description"],
            "target_family": spec.target_family,
            "signal_blocks": "+".join(spec.blocks),
            "fit_status": "skipped_class_count_below_cv_splits",
            "n_rows": len(frame),
            "n_features_raw": len(columns),
            "n_features_active": 0,
            "n_positive": int(counts.get(1, 0)),
            "n_negative": int(counts.get(0, 0)),
            "positive_rate": float(np.mean(y)),
            "cv_auc_mean": math.nan,
            "cv_auc_std": math.nan,
            "oof_auc": math.nan,
            "fold_aucs": [],
        }
        return result, pd.DataFrame(), pd.DataFrame()
    cv = StratifiedKFold(
        n_splits=cv_splits,
        shuffle=True,
        random_state=random_state,
    )
    oof = np.zeros(len(frame), dtype=float)
    fold_aucs = []
    fold_rows = []
    for fold, (train_index, validation_index) in enumerate(cv.split(x, y), start=1):
        active_mask = np.std(x[train_index], axis=0) > 0
        if not active_mask.any():
            return skipped_result(frame, spec, target, columns, "skipped_no_active_features_in_fold")
        pipeline = model_pipeline(regularization_c, random_state)
        pipeline.fit(x[train_index][:, active_mask], y[train_index])
        probabilities = pipeline.predict_proba(x[validation_index][:, active_mask])[
            :, 1
        ]
        oof[validation_index] = probabilities
        fold_auc = float(roc_auc_score(y[validation_index], probabilities))
        fold_aucs.append(fold_auc)
        fold_rows.extend(
            {
                "model": spec.key,
                "target": target["target"],
                "doi": frame.iloc[index]["doi"],
                "fold": fold,
                "y_true": int(y[index]),
                "probability": float(oof[index]),
            }
            for index in validation_index
        )
    full_active_mask = np.std(x, axis=0) > 0
    active_columns = [
        column
        for column, active in zip(columns, full_active_mask)
        if active
    ]
    final_pipeline = model_pipeline(regularization_c, random_state)
    final_pipeline.fit(x[:, full_active_mask], y)
    coefficients = final_pipeline.named_steps["logisticregression"].coef_[0]
    coefficient_frame = pd.DataFrame(
        {
            "model": spec.key,
            "target": target["target"],
            "signal_block": [
                column.split("__", 1)[0] for column in active_columns
            ],
            "feature": active_columns,
            "signal_id": [
                column.rsplit(SIGNAL_PREFIX, 1)[-1] for column in active_columns
            ],
            "signal": [
                descriptions.get(
                    column,
                    descriptions.get(
                        column.rsplit(SIGNAL_PREFIX, 1)[-1],
                        column.rsplit(SIGNAL_PREFIX, 1)[-1],
                    ),
                )
                for column in active_columns
            ],
            "standardized_coefficient": coefficients,
        }
    )
    coefficient_frame["abs_standardized_coefficient"] = coefficient_frame[
        "standardized_coefficient"
    ].abs()
    coefficient_frame = coefficient_frame.sort_values(
        "abs_standardized_coefficient",
        ascending=False,
    )
    result = {
        "model": spec.key,
        "model_label": MODEL_LABELS[spec.key],
        "target": target["target"],
        "target_label": target["label"],
        "target_description": target["description"],
        "target_family": spec.target_family,
        "signal_blocks": "+".join(spec.blocks),
        "fit_status": "fit",
        "n_rows": len(frame),
        "n_features_raw": len(columns),
        "n_features_active": len(active_columns),
        "n_positive": int(np.sum(y)),
        "n_negative": int(len(y) - np.sum(y)),
        "positive_rate": float(np.mean(y)),
        "cv_auc_mean": float(np.mean(fold_aucs)),
        "cv_auc_std": float(np.std(fold_aucs, ddof=1)),
        "oof_auc": float(roc_auc_score(y, oof)),
        "fold_aucs": fold_aucs,
    }
    return result, coefficient_frame, pd.DataFrame(fold_rows)

def normalize_citations(df: pd.DataFrame, min_cell_size: int) -> pd.DataFrame:
    out = df.copy()
    log_citations = np.log1p(out["groundtruth_citation_count"].to_numpy(dtype=float))
    out["_log_citation_count"] = log_citations
    global_expected = float(np.mean(log_citations)) if len(out) else 0.0
    global_std = float(np.std(log_citations)) if len(out) else 1.0
    year_stats = (
        pd.DataFrame(out.groupby("year")
        .agg(mean=("_log_citation_count", "mean"),
             std=("_log_citation_count", lambda values: float(np.std(values))),
             count=("_log_citation_count", "count")))
        .to_dict("index")
    )
    year_field_stats = (
        pd.DataFrame(out.groupby(["year", "primary_research_field"])
        .agg(mean=("_log_citation_count", "mean"),
             std=("_log_citation_count", lambda values: float(np.std(values))),
             count=("_log_citation_count", "count")))
        .to_dict("index")
    )

    expected = []
    scales = []
    groups = []
    for year, field in out[["year", "primary_research_field"]].itertuples(index=False, name=None):
        year_field = year_field_stats.get((year, field))
        if year_field and int(year_field["count"]) >= min_cell_size:
            expected.append(float(year_field["mean"]))
            scales.append(float(year_field["std"]))
            groups.append(f"year_field:{year}:{field}")
            continue
        year_stat = year_stats.get(year)
        if year_stat and int(year_stat["count"]) >= min_cell_size:
            expected.append(float(year_stat["mean"]))
            scales.append(float(year_stat["std"]))
            groups.append(f"year:{year}")
            continue
        expected.append(global_expected)
        scales.append(global_std)
        groups.append("global")

    expected_arr = np.array(expected, dtype=float)
    scale_arr = np.array(scales, dtype=float)
    scale_arr[~np.isfinite(scale_arr) | (scale_arr <= 1e-12)] = 1.0
    out["expected_log_citation_count_year_field"] = expected_arr
    out["expected_log_citation_std_year_field"] = scale_arr
    out["normalized_log_citation_residual"] = (log_citations - expected_arr) / scale_arr
    out["normalization_group"] = groups
    out = out.drop(columns=["_log_citation_count"])
    return out

MODEL_SPECS = (
    ModelSpec('model_1', ('paper', 'human', 'audit'), 'llm_prediction'),
    ModelSpec('model_2', ('human', 'audit'), 'normalized_citation'),
    ModelSpec('model_3', ('human',), 'normalized_citation'),
    ModelSpec('model_4', ('audit',), 'normalized_citation'),
)


def target_definitions(frame, family):
    if family == 'llm_prediction':
        return llm_target_definitions(frame)
    if family == 'normalized_citation':
        return normalized_citation_target_definitions(frame)
    raise ValueError(f'Unknown target family: {family}')


def skipped_result(frame, spec, target, columns, status):
    values = np.asarray(target['values'])
    return ({'model': spec.key, 'model_label': MODEL_LABELS[spec.key],
             'target': target['target'], 'target_family': spec.target_family,
             'signal_blocks': '+'.join(spec.blocks), 'fit_status': status,
             'n_rows': len(frame), 'n_features_raw': len(columns), 'n_features_active': 0,
             'n_positive': int(np.sum(values)), 'n_negative': int(len(values) - np.sum(values)),
             'positive_rate': float(np.mean(values)) if len(values) else math.nan,
             'cv_auc_mean': math.nan, 'cv_auc_std': math.nan, 'oof_auc': math.nan, 'fold_aucs': []},
            pd.DataFrame(), pd.DataFrame())
