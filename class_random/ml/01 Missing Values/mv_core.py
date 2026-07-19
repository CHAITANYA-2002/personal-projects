"""
mv_core.py — dataset-agnostic missing-value analysis engine.

Pure pandas / sklearn. No Streamlit here so the logic stays testable.
Works on ANY csv: classification or regression, numeric / categorical / mixed.

Pipeline of concepts (mirrors the classroom flowchart, with 3 corrections):
  0. sentinel detection      -999/-1/9999/""/"N/A"/... are missing in disguise
  1. task inference          object or nunique<=THR -> classification, else regression
  2. train/test split        0.8/0.2, test set SEALED until final apply
  3. missing table           col, dtype, n_missing, %miss, band
  4. mechanism diagnosis      STRUCTURAL / MNAR-suspect / MAR / MCAR  (per column, train only)
  5. strategy grid            only the methods that a column's (dtype, mechanism, band) permit
  6. validation scoring       6 flowchart checks + CV model gain -> composite score
  7. recommendation           best strategy per column

Corrections vs the flowchart:
  * STRUCTURAL is a 4th mechanism (NaN means "none", e.g. PoolQC when there is no pool).
  * target-dependency != MNAR; we condition on observed features first, and only ever
    report MNAR as *suspected* (untestable from observed data alone).
  * dependency scan uses Benjamini-Hochberg correction (else false positives with many cols).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.base import BaseEstimator, TransformerMixin
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.impute import KNNImputer
from sklearn.model_selection import StratifiedKFold, KFold, cross_val_score
from sklearn.preprocessing import OrdinalEncoder

# IterativeImputer is still experimental in sklearn -> must enable explicitly.
from sklearn.experimental import enable_iterative_imputer  # noqa: F401
from sklearn.impute import IterativeImputer

RANDOM_STATE = 42
CLASS_MAX_UNIQUE = 20          # numeric target with <= this many uniques -> classification
COMISS_STRUCTURAL = 0.95       # nullity-corr threshold to propagate structural verdict
BH_ALPHA = 0.05                # dependency significance after BH correction

# common non-standard "missing" tokens; scanned case-insensitively for strings
SENTINELS_STR = {"", " ", "na", "n/a", "nan", "null", "none", "unknown", "?", "-", "--"}
SENTINELS_NUM = {-999, -9999, 9999, -1}   # -1 offered, NOT auto-applied (may be legit)


# ────────────────────────────────────────────────────────────────────────────
# 0. sentinels
# ────────────────────────────────────────────────────────────────────────────
def detect_sentinels(df: pd.DataFrame) -> pd.DataFrame:
    """Per-column disguised-missing tokens and their counts. User decides what to apply."""
    rows = []
    for c in df.columns:
        s = df[c].dropna()          # real NaN is already missing, not a disguised token
        if df[c].dtype == object:
            low = s.astype(str).str.strip().str.lower()
            for tok in SENTINELS_STR:
                n = int((low == tok).sum())
                if n:
                    rows.append((c, repr(tok) if tok.strip() == "" else tok, n))
        else:
            for tok in SENTINELS_NUM:
                n = int((s == tok).sum())
                if n:
                    rows.append((c, tok, n))
    return pd.DataFrame(rows, columns=["column", "token", "count"])


def apply_sentinels(df: pd.DataFrame, pairs: list[tuple]) -> pd.DataFrame:
    """Replace chosen (column, token) pairs with NaN. `token` may be str or number."""
    out = df.copy()
    for col, tok in pairs:
        if out[col].dtype == object:
            mask = out[col].astype(str).str.strip().str.lower() == str(tok).strip().lower()
        else:
            mask = out[col] == tok
        out.loc[mask, col] = np.nan
    return out


# ────────────────────────────────────────────────────────────────────────────
# 1. task inference / column typing
# ────────────────────────────────────────────────────────────────────────────
def infer_task(y: pd.Series) -> str:
    if y.dtype == object or y.dtype.name in ("category", "bool"):
        return "classification"
    return "classification" if y.nunique(dropna=True) <= CLASS_MAX_UNIQUE else "regression"


def split_columns(df: pd.DataFrame, target: str) -> tuple[list, list]:
    feats = [c for c in df.columns if c != target]
    num = [c for c in feats if pd.api.types.is_numeric_dtype(df[c])]
    cat = [c for c in feats if c not in num]
    return num, cat


def id_like_columns(df: pd.DataFrame) -> list[str]:
    n = len(df)
    return [c for c in df.columns if df[c].nunique(dropna=True) >= 0.99 * n and n > 20]


# ────────────────────────────────────────────────────────────────────────────
# 3. missing table
# ────────────────────────────────────────────────────────────────────────────
def band(pct: float) -> str:
    if pct <= 5:   return "≤5% (low)"
    if pct <= 15:  return "5-15% (moderate)"
    if pct <= 30:  return "15-30% (high)"
    return ">30% (very high)"


def missing_table(df: pd.DataFrame) -> pd.DataFrame:
    n = len(df)
    rows = []
    for c in df.columns:
        miss = int(df[c].isnull().sum())
        if miss:
            pct = round(100 * miss / n, 2)
            rows.append((c, str(df[c].dtype), miss, pct, band(pct)))
    out = pd.DataFrame(rows, columns=["column", "dtype", "n_missing", "pct_missing", "band"])
    return out.sort_values("pct_missing", ascending=False).reset_index(drop=True)


# ────────────────────────────────────────────────────────────────────────────
# 4. mechanism diagnosis
# ────────────────────────────────────────────────────────────────────────────
def _bh_reject(pvals: list[float], alpha: float = BH_ALPHA) -> list[bool]:
    """Benjamini-Hochberg: which p-values survive FDR control. NaNs never reject."""
    p = np.asarray(pvals, float)
    ok = ~np.isnan(p)
    reject = np.zeros(len(p), bool)
    idx = np.where(ok)[0]
    if len(idx) == 0:
        return reject.tolist()
    order = idx[np.argsort(p[idx])]
    m = len(order)
    thresh = 0
    for rank, j in enumerate(order, 1):
        if p[j] <= rank / m * alpha:
            thresh = rank
    for rank, j in enumerate(order, 1):
        if rank <= thresh:
            reject[j] = True
    return reject.tolist()


def _dep_test(k_vals: pd.Series, miss: pd.Series) -> tuple[float, float]:
    """Association between a predictor column and a missingness mask.
    Returns (effect_size, p_value). numeric->Mann-Whitney+rank-biserial, cat->chi2+Cramer's V."""
    g1, g0 = k_vals[miss], k_vals[~miss]
    try:
        if pd.api.types.is_numeric_dtype(k_vals):
            a, b = g1.dropna(), g0.dropna()
            if len(a) < 5 or len(b) < 5:
                return np.nan, np.nan
            u, p = stats.mannwhitneyu(a, b, alternative="two-sided")
            eff = abs(1 - 2 * u / (len(a) * len(b)))          # |rank-biserial|
            return float(eff), float(p)
        ct = pd.crosstab(k_vals.fillna("__na__"), miss)
        if ct.shape[0] < 2 or ct.shape[1] < 2:
            return np.nan, np.nan
        chi2, p, _, _ = stats.chi2_contingency(ct)
        n = ct.values.sum()
        v = np.sqrt(chi2 / (n * (min(ct.shape) - 1)))          # Cramer's V
        return float(v), float(p)
    except Exception:
        return np.nan, np.nan


def _structural_ref(train: pd.DataFrame, col: str, num_cols: list[str]) -> str | None:
    """Missingness that IS 'attribute absent': col missing <=> some numeric k == 0.
    Generalises the PoolQC<->PoolArea==0 pattern to any dataset (fires only when it holds)."""
    m = train[col].isnull()
    if m.sum() < 5 or (~m).sum() < 5:
        return None
    for k in num_cols:
        if k == col:
            continue
        kv = train[k]
        if kv.isnull().any():
            continue
        p_miss0 = (kv[m] == 0).mean()
        p_pres0 = (kv[~m] == 0).mean()
        if p_miss0 > 0.90 and p_pres0 < 0.10:
            return k
    return None


def diagnose_column(train: pd.DataFrame, col: str, y: pd.Series,
                    num_cols: list[str], cat_cols: list[str]) -> dict:
    """Full per-column mechanism verdict. Uses train split only (no leakage)."""
    m = train[col].isnull()
    n = len(train)
    pct = round(100 * m.sum() / n, 2)
    others = [c for c in num_cols + cat_cols if c != col]

    # dependency scan on observed features
    effects, pvals = {}, []
    for k in others:
        e, p = _dep_test(train[k], m)
        effects[k] = (e, p)
        pvals.append(p)
    rej = _bh_reject(pvals)
    depends_on = sorted(
        [(k, effects[k][0], effects[k][1]) for k, r in zip(others, rej) if r],
        key=lambda t: -(t[1] if not np.isnan(t[1]) else 0),
    )[:3]

    # target association (encode y numerically for a uniform test)
    y_num = pd.Series(pd.factorize(y)[0], index=y.index) if y.dtype == object else y
    t_eff, t_p = _dep_test(y_num, m)
    target_assoc = (t_eff, t_p)

    # structural?
    sref = _structural_ref(train, col, num_cols)

    # verdict order: STRUCTURAL > MNAR-suspect > MAR > MCAR
    if sref is not None:
        mech = "STRUCTURAL"
    elif (not np.isnan(t_p) and t_p < BH_ALPHA and (t_eff or 0) > 0.1 and not depends_on):
        # target-linked AND not explained by observed features -> MNAR suspicion
        mech = "MNAR-suspected"
    elif depends_on:
        mech = "MAR"
    else:
        mech = "MCAR"

    indicator_default = mech in ("MNAR-suspected",) or (mech == "MAR" and pct >= 10) \
        or (not np.isnan(t_p) and t_p < BH_ALPHA)

    return dict(column=col, dtype=str(train[col].dtype), pct_missing=pct, band=band(pct),
                mechanism=mech, structural_ref=sref, depends_on=depends_on,
                target_assoc=target_assoc, indicator_default=bool(indicator_default))


def comissing_matrix(train: pd.DataFrame, miss_cols: list[str]) -> pd.DataFrame:
    """Nullity correlation between missing columns — exposes co-missing blocks."""
    return train[miss_cols].isnull().astype(int).corr()


# ────────────────────────────────────────────────────────────────────────────
# 5. imputation primitive (shared by stat-checks and the CV preprocessor)
# ────────────────────────────────────────────────────────────────────────────
def _numeric_matrix(df: pd.DataFrame, cols: list[str]) -> np.ndarray:
    return df[cols].apply(pd.to_numeric, errors="coerce").to_numpy(float)


def impute_column_values(fit_df: pd.DataFrame, apply_df: pd.DataFrame, col: str,
                         strategy: str, num_cols: list[str], group_col: str | None):
    """Return imputed values of `col` for apply_df, learning stats from fit_df only.
    Leakage-safe: used identically for full-train stat checks and per-fold CV."""
    s_apply = apply_df[col]
    miss = s_apply.isnull()
    if not miss.any():
        return s_apply.to_numpy()
    out = s_apply.copy()
    src = fit_df[col].dropna()

    if strategy == "mean":
        out[miss] = pd.to_numeric(src).mean()
    elif strategy == "median":
        out[miss] = pd.to_numeric(src).median()
    elif strategy == "mode":
        out[miss] = src.mode().iloc[0] if len(src.mode()) else np.nan
    elif strategy == "arbitrary":
        out[miss] = -999 if pd.api.types.is_numeric_dtype(s_apply) else "Missing"
    elif strategy == "structural":
        out[miss] = 0 if pd.api.types.is_numeric_dtype(s_apply) else "None"
    elif strategy == "grouped":
        num = pd.api.types.is_numeric_dtype(s_apply)
        agg = (lambda x: pd.to_numeric(x).median()) if num else \
              (lambda x: x.mode().iloc[0] if len(x.mode()) else np.nan)
        gmap = fit_df.dropna(subset=[col]).groupby(group_col)[col].agg(agg)
        glob = pd.to_numeric(src).median() if num else (src.mode().iloc[0] if len(src.mode()) else np.nan)
        filled = apply_df.loc[miss, group_col].map(gmap)
        out[miss] = filled.fillna(glob).to_numpy()
    elif strategy in ("knn", "iterative"):
        cols = num_cols if col in num_cols else [col] + num_cols
        # nan-aware standardisation learned on fit_df so distances are scale-fair
        fm = _numeric_matrix(fit_df, cols)
        mu = np.nanmean(fm, axis=0)
        sd = np.nanstd(fm, axis=0)
        sd[sd == 0 | np.isnan(sd)] = 1.0
        imp = (KNNImputer(n_neighbors=5) if strategy == "knn"
               else IterativeImputer(max_iter=5, random_state=RANDOM_STATE))
        imp.fit((fm - mu) / sd)
        am = ( _numeric_matrix(apply_df, cols) - mu) / sd
        res = imp.transform(am) * sd + mu
        j = cols.index(col)
        out[miss] = res[miss.to_numpy(), j]
    else:
        raise ValueError(f"unknown strategy {strategy}")
    return out.to_numpy()


# ────────────────────────────────────────────────────────────────────────────
# 6a. preprocessor: target col gets candidate strategy, everything else baseline
# ────────────────────────────────────────────────────────────────────────────
class Preprocessor(BaseEstimator, TransformerMixin):
    """One column gets the candidate strategy; all others get a fixed baseline
    (numeric->median, categorical->mode). Categoricals ordinal-encoded for the RF.
    Optional missing-indicator for the target column, built BEFORE imputation."""

    def __init__(self, col, strategy, num_cols, cat_cols,
                 add_indicator=False, group_col=None, drop_col=False):
        self.col = col; self.strategy = strategy
        self.num_cols = num_cols; self.cat_cols = cat_cols
        self.add_indicator = add_indicator; self.group_col = group_col
        self.drop_col = drop_col

    def fit(self, X, y=None):
        self.fit_df_ = X.reset_index(drop=True)
        self.num_median_ = {c: pd.to_numeric(X[c], errors="coerce").median() for c in self.num_cols}
        self.cat_mode_ = {c: (X[c].mode().iloc[0] if len(X[c].mode()) else "Missing")
                          for c in self.cat_cols}
        self.enc_cols_ = [c for c in self.cat_cols if not (self.drop_col and c == self.col)]
        self.encoder_ = OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=-1)
        if self.enc_cols_:
            base = X[self.enc_cols_].copy()
            for c in self.enc_cols_:
                base[c] = base[c].fillna(self.cat_mode_[c]).astype(str)
            self.encoder_.fit(base)
        return self

    def transform(self, X):
        X = X.reset_index(drop=True)
        parts = {}
        ind = None
        if self.add_indicator and not self.drop_col:
            ind = X[self.col].isnull().astype(int).to_numpy()

        for c in self.num_cols:
            if self.drop_col and c == self.col:
                continue
            if c == self.col:
                parts[c] = impute_column_values(self.fit_df_, X, c, self.strategy,
                                                self.num_cols, self.group_col)
            else:
                parts[c] = pd.to_numeric(X[c], errors="coerce").fillna(self.num_median_[c]).to_numpy()

        cat_out = X[self.enc_cols_].copy()
        for c in self.enc_cols_:
            if c == self.col:
                cat_out[c] = impute_column_values(self.fit_df_, X, c, self.strategy,
                                                  self.num_cols, self.group_col)
            else:
                cat_out[c] = X[c].fillna(self.cat_mode_[c])
            cat_out[c] = cat_out[c].astype(str)
        enc = self.encoder_.transform(cat_out) if self.enc_cols_ else np.empty((len(X), 0))

        mat = np.column_stack([np.asarray(list(parts.values())).T if parts else np.empty((len(X), 0)),
                               enc])
        if ind is not None:
            mat = np.column_stack([mat, ind])
        return np.nan_to_num(mat.astype(float), nan=0.0)


# ────────────────────────────────────────────────────────────────────────────
# 6b. strategy grid + scoring
# ────────────────────────────────────────────────────────────────────────────
def strategy_grid(dtype_numeric: bool, mechanism: str, pct: float,
                  has_group: bool) -> list[str]:
    """Only the methods a column's (dtype, mechanism, band) actually permit."""
    g = ["drop_col"]                                    # honest floor for every column
    if mechanism == "MCAR" and pct <= 5:
        g.append("drop_rows")
    if dtype_numeric:
        g += ["mean", "median"]
    g += ["mode", "arbitrary"]
    if mechanism == "STRUCTURAL":
        g.append("structural")
    if has_group and mechanism in ("MAR", "MNAR-suspected"):
        g.append("grouped")
    if dtype_numeric and mechanism in ("MAR", "MNAR-suspected"):
        g += ["knn", "iterative"]
    seen = set()
    return [s for s in g if not (s in seen or seen.add(s))]


def _cv_score(pre, X, y, task) -> tuple[float, float]:
    if task == "classification":
        model = RandomForestClassifier(n_estimators=50, max_depth=12,
                                       n_jobs=-1, random_state=RANDOM_STATE)
        yy = pd.factorize(y)[0]
        scoring = "roc_auc" if len(np.unique(yy)) == 2 else "f1_macro"
        cv = StratifiedKFold(3, shuffle=True, random_state=RANDOM_STATE)
    else:
        model = RandomForestRegressor(n_estimators=50, max_depth=12,
                                      n_jobs=-1, random_state=RANDOM_STATE)
        yy = np.asarray(y, float)
        scoring = "r2"
        cv = KFold(3, shuffle=True, random_state=RANDOM_STATE)
    from sklearn.pipeline import Pipeline
    sc = cross_val_score(Pipeline([("pre", pre), ("m", model)]), X, yy,
                         cv=cv, scoring=scoring, n_jobs=-1)
    return float(sc.mean()), float(sc.std())


def _stat_checks(train, col, strategy, num_cols, group_col, y) -> dict:
    """The 6 flowchart validation checks, computed on full train (observed vs imputed)."""
    s = train[col]
    numeric = pd.api.types.is_numeric_dtype(s)
    if strategy in ("drop_col", "drop_rows"):
        return dict(mean_shift=0.0, median_shift=0.0, var_shift=0.0, new_outliers=0,
                    corr_shift=np.nan, sign_flip=False, corr_target_shift=np.nan)
    filled = pd.Series(impute_column_values(train, train, col, strategy, num_cols, group_col),
                       index=train.index)
    if not numeric:
        obs = s.dropna(); share_b = obs.value_counts(normalize=True).max()
        share_a = filled.value_counts(normalize=True).max()
        return dict(mean_shift=np.nan, median_shift=np.nan,
                    var_shift=round(100 * abs(share_a - share_b) / share_b, 2),
                    new_outliers=0, corr_shift=np.nan, sign_flip=False, corr_target_shift=np.nan)
    obs = pd.to_numeric(s, errors="coerce").dropna()
    fa = pd.to_numeric(filled, errors="coerce")
    ms = 100 * abs(fa.mean() - obs.mean()) / (abs(obs.mean()) + 1e-9)
    mds = 100 * abs(fa.median() - obs.median()) / (abs(obs.median()) + 1e-9)
    vs = 100 * abs(fa.var() - obs.var()) / (obs.var() + 1e-9)
    q1, q3 = obs.quantile([.25, .75]); iqr = q3 - q1
    lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    imp_only = fa[s.isnull()]
    new_out = int(((imp_only < lo) | (imp_only > hi)).sum())
    # correlation shift vs other numeric cols and vs target
    corr_shift, sign_flip = np.nan, False
    others = [c for c in num_cols if c != col]
    if others:
        diffs, flips = [], False
        for k in others:
            kv = pd.to_numeric(train[k], errors="coerce")
            cb = obs.corr(kv[s.notnull()]); ca = fa.corr(kv)
            if not (np.isnan(cb) or np.isnan(ca)):
                diffs.append(abs(ca - cb))
                if np.sign(ca) != np.sign(cb) and min(abs(ca), abs(cb)) > 0.1:
                    flips = True
        corr_shift = round(float(max(diffs)), 3) if diffs else np.nan
        sign_flip = flips
    yn = pd.Series(pd.factorize(y)[0], index=y.index) if y.dtype == object else pd.to_numeric(y, errors="coerce")
    ctb = obs.corr(yn[s.notnull()]); cta = fa.corr(yn)
    cts = abs(cta - ctb) if not (np.isnan(ctb) or np.isnan(cta)) else np.nan
    return dict(mean_shift=round(ms, 2), median_shift=round(mds, 2), var_shift=round(vs, 2),
                new_outliers=new_out, corr_shift=corr_shift, sign_flip=bool(sign_flip),
                corr_target_shift=round(cts, 3) if not np.isnan(cts) else np.nan)


DEFAULT_WEIGHTS = dict(cv=60, mean=8, median=6, var=12, corr=10, sign=15, outlier=5, cvstd=4)


def composite_score(row: dict, z_delta: float, w: dict) -> float:
    def clip(x, hi):
        x = 0.0 if x is None or (isinstance(x, float) and np.isnan(x)) else x
        return float(np.clip(x / hi, 0, 3))
    return (w["cv"] * z_delta
            - w["mean"] * clip(row.get("mean_shift"), 5)
            - w["median"] * clip(row.get("median_shift"), 3)
            - w["var"] * clip(row.get("var_shift"), 10)
            - w["corr"] * clip(row.get("corr_shift"), 0.05)
            - w["sign"] * (1 if row.get("sign_flip") else 0)
            - w["outlier"] * (1 if (row.get("new_outliers") or 0) > 0 else 0)
            - w["cvstd"] * clip(row.get("cv_std"), 0.05))


def recommend_column(train, col, y, task, num_cols, cat_cols, diag, weights=None,
                     add_indicator=None):
    """Evaluate the whole permitted grid for one column and pick the best strategy."""
    weights = weights or DEFAULT_WEIGHTS
    numeric = col in num_cols
    group_col = diag["depends_on"][0][0] if diag["depends_on"] else None
    if group_col is not None and pd.api.types.is_numeric_dtype(train[group_col]):
        group_col = next((k for k, *_ in diag["depends_on"]
                          if not pd.api.types.is_numeric_dtype(train[k])), None)
    grid = strategy_grid(numeric, diag["mechanism"], diag["pct_missing"], group_col is not None)
    ind = diag["indicator_default"] if add_indicator is None else add_indicator

    # baseline: every column median/mode filled (delta measured against this)
    base_pre = Preprocessor(col, "median" if numeric else "mode", num_cols, cat_cols)
    base_mean, _ = _cv_score(base_pre, train[num_cols + cat_cols], y, task)

    rows = []
    for strat in grid:
        use_ind = ind and strat not in ("drop_col", "drop_rows")
        if strat == "drop_rows":
            keep = train[col].notnull()
            pre = Preprocessor(col, "median" if numeric else "mode", num_cols, cat_cols)
            cvm, cvs = _cv_score(pre, train.loc[keep, num_cols + cat_cols], y.loc[keep], task)
        else:
            pre = Preprocessor(col, strat if strat != "drop_col" else "median",
                               num_cols, cat_cols, add_indicator=use_ind,
                               group_col=group_col, drop_col=(strat == "drop_col"))
            cvm, cvs = _cv_score(pre, train[num_cols + cat_cols], y, task)
        checks = _stat_checks(train, col, strat, num_cols, group_col, y)
        rows.append(dict(strategy=strat, indicator=use_ind, cv_mean=round(cvm, 4),
                         cv_std=round(cvs, 4), cv_delta=round(cvm - base_mean, 4), **checks))

    dz = np.array([r["cv_delta"] for r in rows], float)
    z = (dz - dz.mean()) / (dz.std() + 1e-9)
    for r, zz in zip(rows, z):
        r["score"] = round(composite_score(r, zz, weights), 2)
    res = pd.DataFrame(rows).sort_values("score", ascending=False).reset_index(drop=True)
    return res, base_mean, group_col


# ────────────────────────────────────────────────────────────────────────────
# 7. apply a full per-column plan and score on the SEALED test set
# ────────────────────────────────────────────────────────────────────────────
def apply_plan(train: pd.DataFrame, test: pd.DataFrame, plan: dict,
               num_cols: list[str], cat_cols: list[str]):
    """plan: {col: {'strategy','indicator','group_col'}}. Learns on train, applies to both.
    Returns human-readable (imputed, not encoded) train_X, test_X + list of dropped cols."""
    def build(df, drop_train_rows):
        out = df.copy()
        dropped = []
        for col, spec in plan.items():
            strat = spec["strategy"]
            if strat == "drop_col":
                dropped.append(col); continue
            if spec.get("indicator"):
                out[f"{col}__was_missing"] = df[col].isnull().astype(int)
            if strat == "drop_rows":
                # only meaningful on train; test must still be filled -> median/mode
                fb = "median" if col in num_cols else "mode"
                out[col] = impute_column_values(train, df, col, fb, num_cols, None)
            else:
                out[col] = impute_column_values(train, df, col, strat, num_cols,
                                                spec.get("group_col"))
        out = out.drop(columns=[c for c in dropped if c in out.columns])
        return out
    tr = build(train, True)
    te = build(test, False)
    # honour drop_rows on the TRAIN frame (rows where that col was originally missing)
    drop_mask = pd.Series(False, index=train.index)
    for col, spec in plan.items():
        if spec["strategy"] == "drop_rows":
            drop_mask |= train[col].isnull()
    return tr, te, drop_mask, [c for c, s in plan.items() if s["strategy"] == "drop_col"]


def _encode_for_model(tr: pd.DataFrame, te: pd.DataFrame):
    cats = [c for c in tr.columns if not pd.api.types.is_numeric_dtype(tr[c])]
    enc = OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=-1)
    trX, teX = tr.copy(), te.copy()
    if cats:
        enc.fit(tr[cats].astype(str))
        trX[cats] = enc.transform(tr[cats].astype(str))
        teX[cats] = enc.transform(te[cats].astype(str))
    for c in trX.columns:
        trX[c] = pd.to_numeric(trX[c], errors="coerce").fillna(trX[c].median() if pd.api.types.is_numeric_dtype(trX[c]) else 0)
        teX[c] = pd.to_numeric(teX[c], errors="coerce")
    return trX.fillna(0).to_numpy(float), teX.fillna(0).to_numpy(float)


def holdout_score(train, test, y_train, y_test, plan, num_cols, cat_cols, task, target):
    """Fit RF on plan-processed train, score on SEALED test. Compare to median/mode baseline."""
    from sklearn.metrics import r2_score, roc_auc_score, f1_score

    def score(trX, teX, ytr, yte):
        if task == "classification":
            model = RandomForestClassifier(n_estimators=100, max_depth=12,
                                           n_jobs=-1, random_state=RANDOM_STATE)
            ytr_c, uniq = pd.factorize(ytr)
            yte_c = pd.Categorical(yte, categories=list(pd.Series(uniq))).codes
            model.fit(trX, ytr_c)
            if len(np.unique(ytr_c)) == 2:
                return "roc_auc", roc_auc_score(yte_c, model.predict_proba(teX)[:, 1])
            return "f1_macro", f1_score(yte_c, model.predict(teX), average="macro")
        model = RandomForestRegressor(n_estimators=100, max_depth=12,
                                      n_jobs=-1, random_state=RANDOM_STATE)
        model.fit(trX, np.asarray(ytr, float))
        return "r2", r2_score(np.asarray(yte, float), model.predict(teX))

    drop_target = lambda d: d.drop(columns=[target]) if target in d.columns else d
    # plan
    tr, te, drop_mask, _ = apply_plan(train, test, plan, num_cols, cat_cols)
    trX, teX = _encode_for_model(drop_target(tr), drop_target(te))
    keep = ~drop_mask.to_numpy()
    metric, plan_s = score(trX[keep], teX, y_train[keep], y_test)
    # baseline: median/mode everywhere, no indicators, no drops
    base_plan = {c: {"strategy": "median" if c in num_cols else "mode", "indicator": False}
                 for c in list(plan)}
    btr, bte, _, _ = apply_plan(train, test, base_plan, num_cols, cat_cols)
    btrX, bteX = _encode_for_model(drop_target(btr), drop_target(bte))
    _, base_s = score(btrX, bteX, y_train, y_test)
    return dict(metric=metric, plan_score=round(plan_s, 4),
                baseline_score=round(base_s, 4), delta=round(plan_s - base_s, 4))


def export_plan_code(plan: dict, num_cols: list[str]) -> str:
    """Emit a copy-pasteable sklearn-style function reproducing the chosen plan."""
    lines = ["import numpy as np, pandas as pd",
             "from sklearn.impute import KNNImputer",
             "from sklearn.experimental import enable_iterative_imputer  # noqa",
             "from sklearn.impute import IterativeImputer", "",
             "def impute(train, apply_df):",
             "    out = apply_df.copy()"]
    for col, spec in plan.items():
        s = spec["strategy"]
        if spec.get("indicator") and s not in ("drop_col", "drop_rows"):
            lines.append(f"    out['{col}__was_missing'] = apply_df['{col}'].isnull().astype(int)")
        if s == "drop_col":
            lines.append(f"    out = out.drop(columns=['{col}'])")
        elif s in ("mean", "median"):
            lines.append(f"    out['{col}'] = out['{col}'].fillna(train['{col}'].{s}())")
        elif s == "mode":
            lines.append(f"    out['{col}'] = out['{col}'].fillna(train['{col}'].mode().iloc[0])")
        elif s == "arbitrary":
            lines.append(f"    out['{col}'] = out['{col}'].fillna(-999)  # or 'Missing' if categorical")
        elif s == "structural":
            lines.append(f"    out['{col}'] = out['{col}'].fillna(0)  # or 'None' if categorical")
        elif s == "grouped":
            g = spec.get("group_col")
            lines.append(f"    gmap = train.groupby('{g}')['{col}'].median()")
            lines.append(f"    out['{col}'] = out['{col}'].fillna(out['{g}'].map(gmap)).fillna(train['{col}'].median())")
        elif s in ("knn", "iterative"):
            imp = "KNNImputer(n_neighbors=5)" if s == "knn" else "IterativeImputer(max_iter=5, random_state=42)"
            lines.append(f"    num = {num_cols}")
            lines.append(f"    imp = {imp}.fit(train[num]); out[num] = imp.transform(out[num])")
        elif s == "drop_rows":
            lines.append(f"    # train-time only: train = train[train['{col}'].notnull()]")
    lines.append("    return out")
    return "\n".join(lines)


# ────────────────────────────────────────────────────────────────────────────
# self-check
# ────────────────────────────────────────────────────────────────────────────
def _demo():
    import os
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "data", "train.csv")
    if os.path.exists(path):
        df = pd.read_csv(path)
        target = "SalePrice" if "SalePrice" in df.columns else df.columns[-1]
    else:  # synthetic fallback so the check always runs
        rng = np.random.default_rng(0)
        df = pd.DataFrame({
            "area": rng.normal(100, 20, 400),
            "rooms": rng.integers(1, 6, 400),
            "kind": rng.choice(["a", "b", "c"], 400),
            "price": rng.normal(300, 50, 400),
        })
        df.loc[rng.random(400) < 0.15, "area"] = np.nan
        df.loc[df["kind"] == "a", "rooms"] = df["rooms"].where(rng.random(400) < .5)  # MAR-ish
        target = "price"

    df = df.dropna(subset=[target])
    task = infer_task(df[target])
    y = df[target]; X = df.drop(columns=[target])
    num, cat = split_columns(df, target)

    mt = missing_table(X)
    assert set(mt.columns) == {"column", "dtype", "n_missing", "pct_missing", "band"}
    assert mt["pct_missing"].is_monotonic_decreasing, "table must be sorted desc"

    if len(mt):
        col = mt.iloc[0]["column"]
        d = diagnose_column(X, col, y, num, cat)
        assert d["mechanism"] in {"STRUCTURAL", "MNAR-suspected", "MAR", "MCAR"}
        res, base, gcol = recommend_column(X, col, y, task, num, cat, d)
        assert len(res) and "score" in res.columns
        assert res["score"].is_monotonic_decreasing
        print(f"task={task}  target={target}  cols_missing={len(mt)}")
        print(f"top-miss col='{col}'  mechanism={d['mechanism']}  ref={d['structural_ref']}")
        print(f"depends_on={[(k, round(e,2)) for k,e,_ in d['depends_on']]}")
        print(f"baseline_cv={base:.4f}  best={res.iloc[0]['strategy']} "
              f"(score={res.iloc[0]['score']}, cv_delta={res.iloc[0]['cv_delta']})")
        print(res[["strategy", "indicator", "cv_delta", "var_shift", "score"]].to_string(index=False))
    else:
        print("no missing values in feature columns")
    print("\nself-check OK")


if __name__ == "__main__":
    _demo()
