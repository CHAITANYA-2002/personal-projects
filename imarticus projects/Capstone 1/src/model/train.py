"""Model training, calibration and blocked validation.

The modelling choices here matter less than the validation design, so that is
where the care goes.

Validation is blocked in time and in space, never random. A random split lets a
2015 row sit in training while its 2016 neighbour is tested, and since weather
is autocorrelated over days and terrain is constant per cell, the model can
score beautifully by recognising cells it has already seen. The failure is
silent: every metric looks excellent and the system generalises to nothing.

So:
  temporal   train 2007-2013, validate 2014, test 2015-2016
  spatial    leave-one-region-out, reported separately

Probabilities are calibrated on the validation split. An uncalibrated gradient
booster ranks well but its raw scores are not probabilities, and a district
officer acting on "80%" needs that to mean eight times in ten.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from src.model import metrics

log = logging.getLogger(__name__)

RANDOM_STATE = 42


@dataclass
class TrainedModel:
    name: str
    estimator: object
    features: list[str]
    scores: dict[str, dict[str, float]] = field(default_factory=dict)

    def predict_proba(self, frame: pd.DataFrame) -> np.ndarray:
        return self.estimator.predict_proba(frame[self.features])[:, 1]


def build_estimators(
    scale_pos_weight: float,
    n_positives: int = 1000,
) -> dict[str, object]:
    """Three models: an interpretable floor, a check, and the production one.

    Tree capacity is scaled to the number of positives rather than fixed. With a
    few hundred positives and forty features, a deep forest memorises the
    training split outright — a training PR-AUC of 1.000 against a test score of
    0.26 is not a good model, it is a lookup table. Depth and leaf size are
    tightened until the gap closes.
    """
    logistic = Pipeline([
        ("impute", SimpleImputer(strategy="median")),
        ("scale", StandardScaler()),
        ("clf", LogisticRegression(
            max_iter=2000, class_weight="balanced",
            C=0.5, random_state=RANDOM_STATE,
        )),
    ])

    small = n_positives < 500
    forest_depth = 5 if small else 12
    leaf_size = max(10, n_positives // 25) if small else 5

    forest = Pipeline([
        ("impute", SimpleImputer(strategy="median")),
        ("clf", RandomForestClassifier(
            n_estimators=500,
            max_depth=forest_depth,
            min_samples_leaf=leaf_size,
            max_features="sqrt",
            class_weight="balanced_subsample",
            n_jobs=-1,
            random_state=RANDOM_STATE,
        )),
    ])

    estimators = {"logistic": logistic, "random_forest": forest}

    try:
        from xgboost import XGBClassifier
    except ImportError:  # pragma: no cover - xgboost is in requirements
        log.warning("xgboost unavailable — continuing without it")
        return estimators

    # No imputer: XGBoost handles missing values natively, and letting it learn
    # the direction to send a NaN is better than filling with a median that
    # invents a value the sensor never reported.
    estimators["xgboost"] = XGBClassifier(
        n_estimators=250 if small else 600,
        max_depth=3 if small else 5,
        learning_rate=0.05,
        subsample=0.7,
        colsample_bytree=0.6,
        min_child_weight=10 if small else 5,
        reg_lambda=10.0 if small else 2.0,
        reg_alpha=1.0 if small else 0.0,
        gamma=1.0 if small else 0.0,
        scale_pos_weight=scale_pos_weight,
        eval_metric="aucpr",
        tree_method="hist",
        random_state=RANDOM_STATE,
        n_jobs=-1,
    )
    return estimators


def train_all(
    frame: pd.DataFrame,
    features: list[str],
    calibrate: bool = True,
) -> dict[str, TrainedModel]:
    """Fit every estimator on the training split and score it everywhere."""
    train = frame[frame["split"] == "train"]
    val = frame[frame["split"] == "val"]
    test = frame[frame["split"] == "test"]

    _log_splits(train, val, test)

    positives = int(train["label"].sum())
    negatives = len(train) - positives
    if positives == 0:
        raise ValueError("training split contains no positives")
    scale_pos_weight = negatives / positives

    trained: dict[str, TrainedModel] = {}

    for name, estimator in build_estimators(scale_pos_weight, positives).items():
        log.info("training %s on %d rows (%d positives)",
                 name, len(train), positives)
        estimator.fit(train[features], train["label"])

        if calibrate and not val.empty and val["label"].sum() > 10:
            estimator = _calibrate(estimator, val, features)

        model = TrainedModel(name=name, estimator=estimator, features=features)
        for split_name, split in (("train", train), ("val", val), ("test", test)):
            if split.empty:
                continue
            probabilities = model.predict_proba(split)
            model.scores[split_name] = metrics.evaluate(
                split["label"].to_numpy(), probabilities
            )
        trained[name] = model
        _log_scores(model)

    return trained


# Isotonic is non-parametric and needs enough positives to fit a stable step
# function. Below this it overfits the validation split badly, and Platt scaling
# — one sigmoid, two parameters — is the more honest choice.
ISOTONIC_MIN_POSITIVES = 50


def _calibrate(estimator, val: pd.DataFrame, features: list[str]):
    """Calibrate on the validation split only.

    Freezing the fitted model is what keeps this honest: it is not refit, so the
    calibrator only ever sees data the model was not trained on. scikit-learn
    1.6 deprecated cv="prefit" for this and 1.9 removed it, so FrozenEstimator
    is used where available with the old spelling as a fallback.
    """
    positives = int(val["label"].sum())
    method = "isotonic" if positives >= ISOTONIC_MIN_POSITIVES else "sigmoid"
    if method == "sigmoid":
        log.info(
            "calibrating with Platt scaling — only %d positives in validation, "
            "below the %d isotonic needs to stay stable",
            positives, ISOTONIC_MIN_POSITIVES,
        )

    try:
        from sklearn.frozen import FrozenEstimator

        calibrated = CalibratedClassifierCV(
            FrozenEstimator(estimator), method=method
        )
    except ImportError:  # scikit-learn < 1.6
        calibrated = CalibratedClassifierCV(estimator, method=method, cv="prefit")

    calibrated.fit(val[features], val["label"])
    return calibrated


def spatial_cv(
    frame: pd.DataFrame,
    features: list[str],
    model_name: str = "xgboost",
) -> pd.DataFrame:
    """Leave-one-region-out. The real test of whether conditions were learned.

    A model that scores well in-region but collapses when a whole region is
    held out has memorised where landslides happen, not why.
    """
    if "region_block" not in frame.columns:
        raise KeyError("frame has no region_block column")

    rows: list[dict] = []
    blocks = sorted(frame["region_block"].dropna().unique())

    for block in blocks:
        holdout = frame[frame["region_block"] == block]
        rest = frame[frame["region_block"] != block]

        if holdout["label"].sum() < 5 or rest["label"].sum() < 20:
            log.warning("skipping block %s — too few positives", block)
            continue

        positives = int(rest["label"].sum())
        estimators = build_estimators(
            (len(rest) - positives) / positives, positives
        )
        estimator = estimators.get(model_name)
        if estimator is None:
            raise KeyError(f"unknown model {model_name!r}")

        estimator.fit(rest[features], rest["label"])
        probabilities = estimator.predict_proba(holdout[features])[:, 1]

        score = metrics.evaluate(holdout["label"].to_numpy(), probabilities)
        score["block"] = block
        score["held_out_rows"] = len(holdout)
        rows.append(score)
        log.info(
            "block %-18s pr_auc %.3f  recall@5%% %.3f  (%d rows, %d positives)",
            block, score["pr_auc"], score["recall_at_5pct"],
            len(holdout), score["positives"],
        )

    return pd.DataFrame(rows)


def explain(model: TrainedModel, frame: pd.DataFrame, sample_size: int = 2000):
    """SHAP values, so a prediction can be argued with rather than just believed."""
    try:
        import shap
    except ImportError:
        log.warning("shap unavailable — skipping explanations")
        return None

    inner = _unwrap(model.estimator)
    subset = frame.sample(min(sample_size, len(frame)), random_state=RANDOM_STATE)

    values = shap_values(inner, model, subset)
    if values is None:
        return None

    importance = (
        pd.DataFrame({
            "feature": model.features,
            "mean_abs_shap": np.abs(values).mean(axis=0),
        })
        .sort_values("mean_abs_shap", ascending=False)
        .reset_index(drop=True)
    )
    return importance


def _log_splits(train: pd.DataFrame, val: pd.DataFrame, test: pd.DataFrame) -> None:
    log.info("--- splits ---")
    for name, split in (("train", train), ("val", val), ("test", test)):
        if split.empty:
            log.warning("%-6s EMPTY", name)
            continue
        positives = int(split["label"].sum())
        log.info("%-6s %7d rows, %5d positives (%.2f%%)",
                 name, len(split), positives, 100 * positives / len(split))


def _log_scores(model: TrainedModel) -> None:
    for split, score in model.scores.items():
        if "pr_auc" not in score:
            continue
        interval = ""
        if score.get("pr_auc_lo") == score.get("pr_auc_lo"):   # not NaN
            interval = f" [{score['pr_auc_lo']:.3f}-{score['pr_auc_hi']:.3f}]"
        log.info(
            "%-14s %-6s pr_auc %.3f%s  roc_auc %.3f  recall@5%% %.3f  brier %.4f",
            model.name, split, score["pr_auc"], interval, score["roc_auc"],
            score["recall_at_5pct"], score["brier"],
        )


def _unwrap(estimator):
    """Strip calibration, freezing and pipeline layers to reach the raw model.

    SHAP's TreeExplainer needs the fitted booster or forest itself. Calibration
    wraps it, FrozenEstimator wraps that, and a Pipeline may wrap the lot, so
    each layer is peeled in turn rather than assuming a fixed depth.
    """
    seen = 0
    while seen < 5:
        seen += 1
        if hasattr(estimator, "calibrated_classifiers_"):
            estimator = estimator.calibrated_classifiers_[0].estimator
            continue
        if estimator.__class__.__name__ == "FrozenEstimator":
            estimator = estimator.estimator
            continue
        if isinstance(estimator, Pipeline):
            estimator = estimator.named_steps.get("clf", estimator)
            continue
        break
    return estimator


def shap_values(inner, model: "TrainedModel", frame: pd.DataFrame):
    """SHAP values for whichever kind of model was selected.

    TreeExplainer is exact and fast but only handles trees. A logistic
    regression needs LinearExplainer, and a pipeline needs its inputs imputed
    and scaled first — feeding raw NaNs to a linear explainer produces silent
    nonsense rather than an error.
    """
    try:
        import shap
    except ImportError:
        log.warning("shap unavailable — no attributions")
        return None

    data = frame[model.features]

    if hasattr(inner, "coef_"):
        prepared = _apply_preprocessing(model.estimator, data)
        try:
            explainer = shap.LinearExplainer(inner, prepared)
            return explainer.shap_values(prepared)
        except Exception as exc:
            log.warning("LinearExplainer failed for %s: %s", model.name, exc)
            return None

    try:
        explainer = shap.TreeExplainer(inner)
        return _positive_class(explainer.shap_values(data))
    except Exception as exc:
        log.warning("TreeExplainer failed for %s: %s", model.name, exc)
        return None


def _positive_class(values):
    """Reduce SHAP output to one attribution per row and feature.

    A binary forest returns one set of values per class — either as a list of
    two arrays or, in newer SHAP, a single (rows, features, classes) array.
    Gradient boosting returns just the one. Only the positive class matters
    here; without this the downstream frame gets a 2-D column and fails with
    "per-column arrays must each be 1-dimensional", which does not hint at the
    cause at all.
    """
    if isinstance(values, list):
        return values[1] if len(values) > 1 else values[0]
    if getattr(values, "ndim", 2) == 3:
        return values[:, :, 1] if values.shape[2] > 1 else values[:, :, 0]
    return values


def _apply_preprocessing(estimator, data: pd.DataFrame) -> pd.DataFrame:
    """Run a pipeline's transform steps without its final classifier."""
    pipeline = estimator
    for _ in range(5):
        if hasattr(pipeline, "calibrated_classifiers_"):
            pipeline = pipeline.calibrated_classifiers_[0].estimator
            continue
        if pipeline.__class__.__name__ == "FrozenEstimator":
            pipeline = pipeline.estimator
            continue
        break

    if not isinstance(pipeline, Pipeline):
        return data

    transformed = data
    for name, step in pipeline.steps[:-1]:
        transformed = step.transform(transformed)
    return pd.DataFrame(transformed, columns=data.columns, index=data.index)
