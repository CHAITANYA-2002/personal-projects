"""
Missing-Value Analyzer — Streamlit UI.  Upload ANY csv.
Run:  streamlit run app.py
All heavy logic lives in mv_core.py (dataset-agnostic, testable).
"""
import io
import os

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
import streamlit as st

import mv_core as mc

st.set_page_config(page_title="Missing-Value Analyzer", layout="wide")
S = st.session_state


def reset_downstream(*keys):
    for k in keys:
        S.pop(k, None)


st.title("🩹 Missing-Value Analyzer")
st.caption("Detect · diagnose (MCAR / MAR / MNAR / **Structural**) · pick & validate the best "
           "handling strategy per column — for any dataset.")

# ── 1. UPLOAD ───────────────────────────────────────────────────────────────
st.header("1 · Upload data")
up = st.file_uploader("CSV file", type=["csv"])
bundled = os.path.join(os.path.dirname(__file__), "data", "train.csv")
if up is None and os.path.exists(bundled):
    if st.checkbox(f"Use bundled sample ({os.path.basename(bundled)})", value=False):
        up = bundled
if up is None:
    st.info("Upload a CSV to begin.")
    st.stop()

raw = pd.read_csv(up)
st.write(f"**{raw.shape[0]:,} rows × {raw.shape[1]} columns**")
with st.expander("Preview"):
    st.dataframe(raw.head(20), use_container_width=True)

# ── 2. SENTINELS (disguised missing) ────────────────────────────────────────
st.header("2 · Disguised missing values")
sent = mc.detect_sentinels(raw)
if len(sent):
    st.write("These tokens often *mean* missing. Tick the ones to convert to NaN "
             "(`-1` etc. can be legitimate — you decide):")
    st.dataframe(sent, use_container_width=True, height=min(300, 40 + 28 * len(sent)))
    labels = [f"{r.column}  ←  {r.token}  ({r.count})" for r in sent.itertuples()]
    chosen = st.multiselect("Convert to NaN", labels)
    picks = [(sent.iloc[labels.index(c)].column, sent.iloc[labels.index(c)].token) for c in chosen]
    df = mc.apply_sentinels(raw, picks) if picks else raw.copy()
else:
    st.success("No common disguised-missing tokens found.")
    df = raw.copy()

# ── 3. TARGET + TASK ────────────────────────────────────────────────────────
st.header("3 · Target & split")
c1, c2 = st.columns(2)
target = c1.selectbox("Output (target) column", df.columns,
                      index=len(df.columns) - 1, on_change=reset_downstream, args=("split",))
task_guess = mc.infer_task(df[target])
task = c2.selectbox("Task type", ["regression", "classification"],
                    index=0 if task_guess == "regression" else 1)
if task != task_guess:
    st.caption(f"(auto-detected **{task_guess}** — overridden)")

id_cols = [c for c in mc.id_like_columns(df) if c != target]
drop_ids = st.multiselect("Exclude ID-like columns from modelling (recommended)",
                          id_cols, default=id_cols)

n_before = len(df)
df = df.dropna(subset=[target]).drop(columns=drop_ids)
if len(df) < n_before:
    st.caption(f"Dropped {n_before - len(df)} rows with a missing **target** (never impute y).")

if st.button("Train/test split (80 / 20)", type="primary"):
    from sklearn.model_selection import train_test_split
    y = df[target]
    strat = y if task == "classification" and y.nunique() > 1 else None
    tr, te = train_test_split(df, test_size=0.2, random_state=42, stratify=strat)
    S.split = dict(train=tr.reset_index(drop=True), test=te.reset_index(drop=True),
                   target=target, task=task)
    reset_downstream("diag_all")

if "split" not in S:
    st.stop()

sp = S.split
train, test, target, task = sp["train"], sp["test"], sp["target"], sp["task"]
y_train, y_test = train[target], test[target]
num_cols, cat_cols = mc.split_columns(train, target)
st.success(f"Split done · train **{len(train)}** / test **{len(test)}** (test sealed until step 8) · "
           f"task **{task}**")

# ── 4. MISSING TABLE ────────────────────────────────────────────────────────
st.header("4 · Columns with missing values (train)")
Xtr = train.drop(columns=[target])
mt = mc.missing_table(Xtr)
if not len(mt):
    st.success("🎉 No missing values in the feature columns — dataset is clean.")
    st.stop()
st.dataframe(mt, use_container_width=True)
miss_cols = mt["column"].tolist()

# ── 5. HEATMAPS ─────────────────────────────────────────────────────────────
st.header("5 · Missingness maps")
c1, c2 = st.columns(2)
with c1:
    st.caption("Nullity map (rows × missing columns)")
    fig, ax = plt.subplots(figsize=(6, 4))
    sns.heatmap(train[miss_cols].isnull(), cbar=False, cmap="rocket_r", ax=ax)
    ax.set_xlabel(""); ax.set_ylabel("rows")
    st.pyplot(fig)
with c2:
    st.caption("Co-missingness correlation — blocks share a cause")
    if len(miss_cols) > 1:
        fig2, ax2 = plt.subplots(figsize=(6, 4))
        sns.heatmap(mc.comissing_matrix(train, miss_cols), cmap="mako", vmin=0, vmax=1,
                    square=False, ax=ax2)
        st.pyplot(fig2)
    else:
        st.info("Only one column missing — no co-missing structure.")

# ── 6. MECHANISM DIAGNOSIS ──────────────────────────────────────────────────
st.header("6 · Mechanism per column")
st.caption("Order: **Structural** (NaN = 'none') → **MNAR-suspected** → **MAR** → **MCAR**. "
           "MNAR is only ever *suspected* — it can't be proven from observed data.")
if "diag_all" not in S:
    with st.spinner("Diagnosing every missing column…"):
        S.diag_all = {c: mc.diagnose_column(train, c, y_train, num_cols, cat_cols)
                      for c in miss_cols}
diag_all = S.diag_all

diag_rows = []
for c in miss_cols:
    d = diag_all[c]
    dep = ", ".join(f"{k}({e:.2f})" for k, e, _ in d["depends_on"]) or "—"
    te_eff, te_p = d["target_assoc"]
    diag_rows.append(dict(column=c, dtype=d["dtype"], pct=d["pct_missing"], band=d["band"],
                          mechanism=d["mechanism"], structural_ref=d["structural_ref"] or "—",
                          depends_on=dep,
                          target_assoc=f"{te_eff:.2f} (p={te_p:.3f})" if not np.isnan(te_p) else "—",
                          add_indicator=d["indicator_default"]))
diag_df = pd.DataFrame(diag_rows)


def color_mech(v):
    return {"STRUCTURAL": "background-color:#e8f4ff",
            "MNAR-suspected": "background-color:#fff0ed",
            "MAR": "background-color:#fffaeb",
            "MCAR": "background-color:#edfaf3"}.get(v, "")
st.dataframe(diag_df.style.map(color_mech, subset=["mechanism"]), use_container_width=True)

# ── 7. STRATEGY SEARCH + VALIDATION ─────────────────────────────────────────
st.header("7 · Strategy search & validation")
st.caption("Each strategy is CV-scored on train (others baseline-filled) against the 6 flowchart "
           "checks. **cv_delta** = model gain vs a median/mode baseline = *effect on loss*.")

with st.sidebar:
    st.subheader("Composite-score weights")
    W = dict(
        cv=st.slider("model gain (cv_delta)", 0, 100, 60),
        var=st.slider("variance-shift penalty", 0, 40, 12),
        corr=st.slider("correlation-shift penalty", 0, 40, 10),
        sign=st.slider("sign-flip penalty", 0, 40, 15),
        mean=st.slider("mean-shift penalty", 0, 40, 8),
        median=st.slider("median-shift penalty", 0, 40, 6),
        outlier=st.slider("new-outlier penalty", 0, 40, 5),
        cvstd=st.slider("cv-instability penalty", 0, 40, 4),
    )

fmt_cols = ["strategy", "indicator", "cv_mean", "cv_std", "cv_delta",
            "mean_shift", "median_shift", "var_shift", "new_outliers",
            "corr_shift", "sign_flip", "corr_target_shift", "score"]


@st.cache_data(show_spinner=False)
def analyze(sig, col):
    d = diag_all[col]
    res, base, gcol = mc.recommend_column(train, col, y_train, task, num_cols, cat_cols, d, W)
    return res, base, gcol


sig = (target, task, tuple(sorted(W.items())), len(train))
tab_one, tab_all = st.tabs(["Inspect one column", "Analyze ALL → final plan"])

with tab_one:
    col = st.selectbox("Column", miss_cols)
    if st.button("Run strategy search", key="one"):
        with st.spinner(f"Scoring strategies for '{col}'…"):
            res, base, gcol = analyze(sig, col)
        d = diag_all[col]
        st.write(f"**{col}** · {d['mechanism']} · baseline CV = `{base:.4f}`"
                 + (f" · grouped-by `{gcol}`" if gcol else ""))
        best = res.iloc[0]
        st.success(f"Recommended: **{best['strategy']}**"
                   f"{' + missing-indicator' if best['indicator'] else ''}  "
                   f"(score {best['score']}, cv_delta {best['cv_delta']:+.4f})")
        show = [c for c in fmt_cols if c in res.columns]
        st.dataframe(res[show].style.highlight_max(subset=["score"], color="#c8f5d0"),
                     use_container_width=True)

with tab_all:
    st.warning("Runs the full grid for every missing column — can take a few minutes on wide data.")
    if st.button("Analyze all columns", type="primary", key="all"):
        prog = st.progress(0.0, "starting…")
        plan, final_rows = {}, []
        for i, c in enumerate(miss_cols, 1):
            prog.progress(i / len(miss_cols), f"{c}  ({i}/{len(miss_cols)})")
            res, base, gcol = analyze(sig, c)
            d = diag_all[c]
            best = res.iloc[0].to_dict()
            plan[c] = dict(strategy=best["strategy"], indicator=bool(best["indicator"]),
                           group_col=gcol)
            final_rows.append(dict(
                column=c, dtype=d["dtype"], pct=d["pct_missing"], band=d["band"],
                mechanism=d["mechanism"],
                depends_on=", ".join(k for k, *_ in d["depends_on"]) or "—",
                recommended=best["strategy"], indicator=bool(best["indicator"]),
                cv_delta=best["cv_delta"], var_shift=best.get("var_shift"),
                corr_shift=best.get("corr_shift"), score=best["score"]))
        prog.empty()
        S.plan = plan
        S.final = pd.DataFrame(final_rows)

    if "final" in S:
        st.subheader("Final recommendation table")
        st.dataframe(S.final.style.map(color_mech, subset=["mechanism"]),
                     use_container_width=True)

        # ── 8. APPLY on sealed test + export ──
        st.header("8 · Apply to sealed test set & export")
        if st.button("Fit on train → score sealed test", key="holdout"):
            with st.spinner("Applying plan and scoring on held-out test…"):
                hs = mc.holdout_score(train, test, y_train, y_test, S.plan,
                                      num_cols, cat_cols, task, target)
            c1, c2, c3 = st.columns(3)
            c1.metric(f"Plan ({hs['metric']})", hs["plan_score"])
            c2.metric("Median/mode baseline", hs["baseline_score"])
            c3.metric("Improvement", f"{hs['delta']:+.4f}",
                      delta=f"{hs['delta']:+.4f}")
            st.caption("This is the honest, joint effect of all chosen strategies on unseen data.")

        tr_out, te_out, _, dropped = mc.apply_plan(train, test, S.plan, num_cols, cat_cols)
        st.download_button("⬇ processed train.csv", tr_out.to_csv(index=False),
                           "train_imputed.csv", "text/csv")
        st.download_button("⬇ processed test.csv", te_out.to_csv(index=False),
                           "test_imputed.csv", "text/csv")
        st.download_button("⬇ imputation code (.py)",
                           mc.export_plan_code(S.plan, num_cols), "impute_plan.py", "text/plain")
        st.download_button("⬇ plan table (.csv)", S.final.to_csv(index=False),
                           "missing_value_plan.csv", "text/csv")
        if dropped:
            st.caption("Columns the plan drops: " + ", ".join(dropped))
