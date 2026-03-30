"""
Interactive Excel analytics dashboard.

Run from this folder (not `python app.py`):
  streamlit run app.py
"""

from __future__ import annotations

import io
import re
import sys
from pathlib import Path
from typing import Any

TENURE_ORDER_SL = ["1", "2-5", "6-12", "13-24", "25-36", "37 above"]

_LOCAL_XLSM = Path(__file__).resolve().parent / "CONSOLIDATED_MONTHLY_SUMMARY.xlsm"

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from scipy import stats

from report_pivots import (
    PROBI_TENURES,
    REGULAR_TENURES,
    coerce_modifier_table,
    default_modifiers_from_raw,
    fmt_slicer_values,
    pivot_filter_banner,
    plotly_pivot_table,
    position_counts,
    productivity_summary,
    TENURE_ORDER,
    udi_hierarchy_table,
)


def load_excel(source: bytes | str, sheet_name: str | int | None = 0) -> pd.DataFrame:
    return pd.read_excel(source, sheet_name=sheet_name, engine="openpyxl")


def _tenure_sort_sl(val: str) -> int:
    try:
        return TENURE_ORDER_SL.index(str(val))
    except ValueError:
        return 999


def _sidebar_slicer_values(
    label: str,
    options: list[str],
    *,
    widget_key: str,
    use_multi: bool,
    default_multi: list[str] | None = None,
) -> list[str]:
    if default_multi is None:
        default_multi = []
    if not options:
        st.sidebar.caption(f"_(No values: {label})_")
        return []
    if use_multi:
        return st.sidebar.multiselect(label, options, default=default_multi, key=widget_key)
    all_lbl = "— All —"
    pick = st.sidebar.selectbox(label, [all_lbl] + list(options), key=widget_key + "_single")
    return [] if pick == all_lbl else [pick]


def apply_app_slicers(
    d: pd.DataFrame,
    report_periods: list[str] | None,
    positions: list[str] | None,
    names: list[str] | None,
    active_statuses: list[str] | None,
    tenure_brackets: list[str] | None,
    employment_statuses: list[str] | None,
    branches: list[str] | None = None,
    branch_column: str | None = None,
) -> pd.DataFrame:
    out = d
    if report_periods and "Report Period" in out.columns:
        rp = out["Report Period"].astype(str).str.strip()
        out = out.loc[rp.isin([str(p).strip() for p in report_periods])]
    if positions and "position" in out.columns:
        out = out.loc[out["position"].fillna("(blank)").astype(str).isin(positions)]
    if names and "correct_name" in out.columns:
        out = out.loc[out["correct_name"].astype(str).isin(names)]
    if active_statuses and "active_status" in out.columns:
        ast = out["active_status"].astype(str).str.strip()
        out = out.loc[ast.isin([str(a).strip() for a in active_statuses])]
    if tenure_brackets and "Tenure Bracket" in out.columns:
        out = out.loc[out["Tenure Bracket"].astype(str).isin(tenure_brackets)]
    if employment_statuses and "Employment Status" in out.columns:
        out = out.loc[out["Employment Status"].astype(str).isin(employment_statuses)]
    if branches and branch_column and str(branch_column) in out.columns:
        bc = out[str(branch_column)].map(lambda x: str(x).strip() if pd.notna(x) else "")
        out = out.loc[bc.isin([str(b).strip() for b in branches])]
    return out.copy()


def slice_period_and_production(
    d: pd.DataFrame,
    period: str,
    with_production: str | None,
) -> pd.DataFrame:
    """One Report Period row set; optional With Production = Yes/No (All = no extra filter)."""
    if "Report Period" not in d.columns:
        return d.iloc[0:0].copy()
    rp = d["Report Period"].astype(str).str.strip()
    out = d.loc[rp == str(period).strip()].copy()
    if with_production is not None and str(with_production) not in ("", "All"):
        if "With Production" in out.columns:
            out = out.loc[out["With Production"].astype(str) == str(with_production)]
    return out


def compute_overview_kpis(sub: pd.DataFrame) -> dict[str, int | float | None]:
    """Headcounts and activity sums for one period slice (already filtered by sidebar)."""
    out: dict[str, int | float | None] = {}
    n = len(sub)
    if n == 0:
        return {
            "active": 0,
            "inactive": 0,
            "wp_yes": 0,
            "wp_no": 0,
            "kb": None,
            "referral": None,
            "field_sat": None,
            "fb": None,
        }
    if "active_status" in sub.columns:
        ast = sub["active_status"].astype(str).str.strip().str.casefold()
        act = int((ast == "active").sum())
        out["active"] = act
        out["inactive"] = int(n - act)
    else:
        out["active"] = None
        out["inactive"] = None
    if "With Production" in sub.columns:
        wp = sub["With Production"].astype(str).str.strip().str.casefold()
        out["wp_yes"] = int((wp == "yes").sum())
        out["wp_no"] = int((wp == "no").sum())
    else:
        out["wp_yes"] = None
        out["wp_no"] = None

    def _sum_cols(candidates: tuple[str, ...]) -> float | None:
        for c in candidates:
            if c in sub.columns:
                return float(pd.to_numeric(sub[c], errors="coerce").fillna(0).sum())
        return None

    out["kb"] = _sum_cols(("KB_UDI", "KB"))
    out["referral"] = _sum_cols(("REFERRAL_UDI", "REFERRAL"))
    out["field_sat"] = _sum_cols(("MR_UDI", "MR"))
    out["fb"] = _sum_cols(("FB_SUPPORT_UDI", "FB_SUPPORT"))
    return out


def build_report_period_trend_table(df: pd.DataFrame) -> pd.DataFrame | None:
    """Sum key metrics by Report Period, sorted in calendar order (for trend line charts)."""
    if "Report Period" not in df.columns or df.empty:
        return None
    sub = df.copy()
    sub["_rp"] = sub["Report Period"].astype(str).str.strip()
    want = [
        c
        for c in (
            "TOTAL_UDI",
            "KB_UDI",
            "KB",
            "REFERRAL_UDI",
            "REFERRAL",
            "MR_UDI",
            "MR",
            "FB_SUPPORT_UDI",
            "FB_SUPPORT",
        )
        if c in sub.columns
    ]
    if not want:
        return None
    for c in want:
        sub[c] = pd.to_numeric(sub[c], errors="coerce")
    g = sub.groupby("_rp", dropna=False)[want].sum().reset_index()
    labels = g["_rp"].astype(str).tolist()
    dts = pd.to_datetime(pd.Series(labels), errors="coerce")
    if dts.isna().any():
        for i, lab in enumerate(labels):
            if pd.isna(dts.iloc[i]):
                for fmt in ("%B %Y", "%b %Y", "%Y-%m"):
                    t = pd.to_datetime(lab, format=fmt, errors="coerce")
                    if pd.notna(t):
                        dts.iloc[i] = t
                        break
    order = np.argsort(dts.fillna(pd.Timestamp.max).to_numpy())
    g = g.iloc[order].reset_index(drop=True)
    return g.rename(columns={"_rp": "Report Period"})


def _norm_position_for_trend_match(s: str) -> str:
    t = re.sub(r"\s+", " ", str(s).strip()).casefold()
    t = t.replace("ofiice", "office").replace("–", "-").replace("—", "-")
    return t


# Overview: TOTAL UDI trend lines for these roles (matches Rawdata `position` text; longest label wins on substring match).
_OVERVIEW_UDI_POSITION_TREND_LABELS: list[str] = [
    "Area Head",
    "Assistant Area Head",
    "Marketing Representative",
    "Office in Charge for Control",
    "Officer in Charge for Operations",
    "TeleMarketer",
    "Training Leader - Trainee",
]


def resolve_overview_position_trend_label(raw: str) -> str | None:
    """Map a Rawdata `position` value to a canonical overview trend label."""
    n = _norm_position_for_trend_match(raw)
    if not n or n == "(blank)":
        return None
    for label in sorted(_OVERVIEW_UDI_POSITION_TREND_LABELS, key=len, reverse=True):
        ln = _norm_position_for_trend_match(label)
        if n == ln:
            return label
    for label in sorted(_OVERVIEW_UDI_POSITION_TREND_LABELS, key=len, reverse=True):
        ln = _norm_position_for_trend_match(label)
        if len(ln) < 8:
            continue
        if ln in n or (len(n) >= 8 and n in ln):
            return label
    return None


def build_udi_trend_by_position(df: pd.DataFrame) -> pd.DataFrame | None:
    """Long format: Report Period, Position (canonical), TOTAL_UDI (summed). Chronologically sorted periods."""
    if "Report Period" not in df.columns or "position" not in df.columns or "TOTAL_UDI" not in df.columns:
        return None
    if df.empty:
        return None
    sub = df.copy()
    sub["_pos"] = sub["position"].map(lambda x: resolve_overview_position_trend_label(str(x)))
    sub = sub.loc[sub["_pos"].notna()]
    if sub.empty:
        return None
    sub["TOTAL_UDI"] = pd.to_numeric(sub["TOTAL_UDI"], errors="coerce").fillna(0.0)
    sub["_rp"] = sub["Report Period"].astype(str).str.strip()
    g = sub.groupby(["_rp", "_pos"], dropna=False)["TOTAL_UDI"].sum().reset_index()
    g = g.rename(columns={"_rp": "Report Period", "_pos": "Position"})
    labels = g["Report Period"].astype(str).unique().tolist()
    dts = pd.to_datetime(pd.Series(labels), errors="coerce")
    if dts.isna().any():
        for i, lab in enumerate(labels):
            if pd.isna(dts.iloc[i]):
                for fmt in ("%B %Y", "%b %Y", "%Y-%m"):
                    t = pd.to_datetime(lab, format=fmt, errors="coerce")
                    if pd.notna(t):
                        dts.iloc[i] = t
                        break
    order = np.argsort(dts.fillna(pd.Timestamp.max).to_numpy())
    ordered_rp = [labels[i] for i in order]
    g["Report Period"] = pd.Categorical(g["Report Period"].astype(str), categories=ordered_rp, ordered=True)
    pos_rank = {p: i for i, p in enumerate(_OVERVIEW_UDI_POSITION_TREND_LABELS)}
    g["_pr"] = g["Position"].map(lambda x: pos_rank.get(str(x), 999))
    g = g.sort_values(["Report Period", "_pr"]).drop(columns=["_pr"])
    return g.reset_index(drop=True)


_VIZ_FONT = dict(family="Segoe UI, system-ui, sans-serif", size=12, color="#334155")
_VIZ_GRID = "rgba(203, 213, 225, 0.65)"


def _format_axis_si() -> str:
    """Compact axis ticks (e.g. 20M) similar to Excel / executive charts."""
    return ".3s"


# (candidate column names…), legend label, color, marker symbol, line width
_TREND_LINE_SLOTS: list[tuple[tuple[str, ...], str, str, str, float]] = [
    (("TOTAL_UDI",), "TOTAL UDI", "#2563eb", "circle", 3.0),
    (("KB_UDI", "KB"), "KB", "#ea580c", "square", 2.5),
    (("MR_UDI", "MR"), "Field saturation (MR)", "#16a34a", "circle", 2.5),
    (("REFERRAL_UDI", "REFERRAL"), "Referral", "#9333ea", "diamond", 2.5),
    (("FB_SUPPORT_UDI", "FB_SUPPORT"), "FB support", "#dc2626", "triangle-up", 2.5),
]

# Presentation copy: Rawdata layout — four count columns → TOTAL_COUNT; four UDI columns → TOTAL_UDI.
_HELP_TEXT_TOTAL_UDI = (
    "Rawdata: TOTAL_UDI = KB_UDI + MR_UDI (field saturation) + REFERRAL_UDI + FB_SUPPORT_UDI (row sum)."
)
_OVERVIEW_METRIC_DEFINITIONS_MD = (
    "**Count side (Rawdata):** **KB**, **MR** (field saturation), **REFERRAL**, and **FB_SUPPORT** are the four "
    "activity **counts**. **`TOTAL_COUNT`** is the **sum of those four counts** — it is the summary column for that block, "
    "not a separate fifth category.\n\n"
    "**UDI side (Rawdata):** **KB_UDI**, **MR_UDI**, **REFERRAL_UDI**, and **FB_SUPPORT_UDI** are the **UDI amounts** for "
    "the same four pillars. **`TOTAL_UDI`** is the **sum of those four UDI columns** — the summary for the UDI block."
)
_CORR_HEATMAP_CAPTION_MD = (
    "**Same structure as Rawdata:** **`TOTAL_COUNT`** = sum of **KB + MR + REFERRAL + FB_SUPPORT**. "
    "**`TOTAL_UDI`** = sum of **KB_UDI + MR_UDI + REFERRAL_UDI + FB_SUPPORT_UDI** (field saturation = **MR** / **MR_UDI**)."
)

# Scatter plot: collapse the four UDI component columns into one “bubble size” choice.
_SCATTER_UDI_SUM_LABEL = "UDI sum (KB+MR+Referral+FB)"
_UDI_COMPONENT_COLS_SCATTER = ("KB_UDI", "MR_UDI", "REFERRAL_UDI", "FB_SUPPORT_UDI")


def _scatter_udi_components_sum(d: pd.DataFrame) -> pd.Series:
    """Row-wise sum of KB/MR/Referral/FB UDI columns present in the frame."""
    parts: list[pd.Series] = []
    for c in _UDI_COMPONENT_COLS_SCATTER:
        if c in d.columns:
            parts.append(pd.to_numeric(d[c], errors="coerce").fillna(0.0))
    if not parts:
        return pd.Series(0.0, index=d.index)
    out = parts[0].copy()
    for s in parts[1:]:
        out = out + s
    return out


def _scatter_point_size_options(num_cols: list[str], x_col: str | None, y_col: str | None) -> list[str | None]:
    """Numeric columns for bubble size; individual UDI pillars replaced by one combined option."""
    exclude = {c for c in (x_col, y_col) if c is not None}
    raw = [c for c in num_cols if c not in exclude]
    have_any_udi = any(c in raw for c in _UDI_COMPONENT_COLS_SCATTER)
    rest = [c for c in raw if c not in _UDI_COMPONENT_COLS_SCATTER]
    out: list[str | None] = [None]
    if have_any_udi:
        out.append(_SCATTER_UDI_SUM_LABEL)
    out.extend(rest)
    return out


def reorder_grouped_series_chronologically(s: pd.Series) -> pd.Series:
    """Sort a grouped Series by calendar order when index labels parse as dates (e.g. Report Period)."""
    if s.empty:
        return s
    labels = [str(x) for x in s.index]
    parsed = pd.to_datetime(pd.Series(labels), errors="coerce")
    if parsed.isna().all():
        return s
    mask = parsed.isna()
    if bool(mask.any()):
        for i in np.where(mask.values)[0]:
            for fmt in ("%B %Y", "%b %Y", "%m/%d/%Y", "%d/%m/%Y", "%Y-%m"):
                t = pd.to_datetime(labels[i], format=fmt, errors="coerce")
                if pd.notna(t):
                    parsed.iloc[i] = t
                    break
    order = np.argsort(parsed.fillna(pd.Timestamp.max).to_numpy())
    new_labels = [labels[i] for i in order]
    return s.reindex(new_labels)


def dedupe_column_names(df: pd.DataFrame) -> pd.DataFrame:
    """Excel layout sheets often repeat labels; duplicate names break pandas indexing."""
    seen: dict[str, int] = {}
    new: list[str] = []
    for c in df.columns:
        label = str(c).strip() if c is not None else "Unnamed"
        if label in seen:
            seen[label] += 1
            new.append(f"{label}__{seen[label]}")
        else:
            seen[label] = 0
            new.append(label)
    out = df.copy()
    out.columns = new
    return out


# MR Productivity insights (Advanced tab): defaults align with Rawdata / Overall modifiers when cells are blank.
_DEFAULT_REGULAR_BASELINE = 80_000.0
_DEFAULT_REGULAR_EXPECTED = 100_000.0
_DEFAULT_REGULAR_BEP = 57_000.0
_DEFAULT_PROBI_BEP = 25_000.0


def _emp_is_probi(emp: str) -> bool:
    return str(emp).strip().casefold() == "probi"


def classify_mr_productivity_row(r: pd.Series) -> tuple[str, str, str]:
    """Classify one Rawdata row: (insight tier, suggested action, rule note). Uses TOTAL_UDI vs BEP / baseline / expected."""
    emp = str(r.get("Employment Status", "")).strip()
    is_probi = _emp_is_probi(emp)
    tenure = str(r.get("Tenure Bracket", "")).strip()
    udi = pd.to_numeric(r.get("TOTAL_UDI"), errors="coerce")
    base = pd.to_numeric(r.get("Baseline productivity"), errors="coerce")
    expc = pd.to_numeric(r.get("Expected Productivity"), errors="coerce")
    bep = pd.to_numeric(r.get("BEP"), errors="coerce")

    if pd.isna(udi):
        return ("No UDI data", "—", "TOTAL_UDI missing or not numeric")

    if is_probi and tenure == "1":
        return (
            "Non-bearing (Probi month 1)",
            "No production-bearing UDI target",
            "First month as Probi: production treated as non-bearing.",
        )

    if is_probi:
        if pd.isna(bep):
            bep = _DEFAULT_PROBI_BEP
        if udi >= bep:
            return (
                "Meets / exceeds BEP (Probi)",
                "Retain (monitor)",
                f"Probi UDI ≥ BEP ({bep:,.0f}).",
            )
        if udi >= 0.5 * bep:
            return (
                "Below BEP — mid gap (Probi)",
                "Train / coach",
                f"UDI between 50% and 100% of BEP ({bep:,.0f}).",
            )
        return (
            "Below 50% BEP (Probi)",
            "Retain vs fire — decision",
            f"UDI < 50% of BEP ({bep:,.0f}).",
        )

    # Regular
    if pd.isna(base):
        base = _DEFAULT_REGULAR_BASELINE
    if pd.isna(expc):
        expc = _DEFAULT_REGULAR_EXPECTED
    if pd.isna(bep):
        bep = _DEFAULT_REGULAR_BEP

    if udi >= expc:
        return ("Meets expected (Regular)", "Retain", f"TOTAL_UDI ≥ Expected Productivity ({expc:,.0f}).")
    if udi >= base:
        return (
            "Between baseline & expected (Regular)",
            "Train / develop",
            f"Baseline ({base:,.0f}) ≤ UDI < Expected ({expc:,.0f}).",
        )
    if udi >= bep:
        return (
            "Below baseline, above BEP (Regular)",
            "PIP / performance review",
            f"UDI below baseline but ≥ BEP ({bep:,.0f}).",
        )
    return (
        "Below BEP (Regular)",
        "Review; fire-risk band",
        f"UDI < BEP ({bep:,.0f}).",
    )


def build_mr_insight_table(df: pd.DataFrame) -> pd.DataFrame | None:
    """Append Insight tier / Suggested action / Rule applied. Requires Employment Status + Tenure + TOTAL_UDI."""
    req = ("TOTAL_UDI", "Employment Status", "Tenure Bracket")
    if not all(c in df.columns for c in req):
        return None
    out = df.copy()
    tups = [classify_mr_productivity_row(out.iloc[i]) for i in range(len(out))]
    out["Insight tier"] = [t[0] for t in tups]
    out["Suggested action"] = [t[1] for t in tups]
    out["Rule applied"] = [t[2] for t in tups]
    return out


def _parse_report_period_series(s: pd.Series) -> pd.Series:
    """Datetime for sorting / comparing Report Period labels (matches trend-table parsing)."""
    if pd.api.types.is_datetime64_any_dtype(s):
        return pd.to_datetime(s, errors="coerce")
    lab = s.astype(str).str.strip()
    dts = pd.to_datetime(lab, errors="coerce")
    if dts.isna().any():
        for i in np.where(dts.isna().values)[0]:
            for fmt in ("%B %Y", "%b %Y", "%Y-%m"):
                t = pd.to_datetime(lab.iloc[i], format=fmt, errors="coerce")
                if pd.notna(t):
                    dts.iloc[i] = t
                    break
    return dts


def resolve_report_timeline_column(df: pd.DataFrame) -> str | None:
    """Panel time key: **Report Period** (preferred) or **Report date** / **Report Date** in Rawdata."""
    by_cf = {str(c).strip().casefold(): c for c in df.columns}
    if "report period" in by_cf:
        return str(by_cf["report period"])
    if "report date" in by_cf:
        return str(by_cf["report date"])
    for c in df.columns:
        cl = str(c).strip().casefold().replace(" ", "_")
        if cl in ("report_period", "report_date", "reportdate"):
            return str(c)
    return None


def mr_insight_latest_row_per_employee(df: pd.DataFrame) -> pd.DataFrame:
    """One row per correct_name: keep the latest report month in the current filter (stops month-on-month duplicates)."""
    tl = resolve_report_timeline_column(df)
    if df.empty or "correct_name" not in df.columns or tl is None:
        return df
    sub = df.copy()
    sub["_rp_sort"] = _parse_report_period_series(sub[tl])
    sub = sub.sort_values(["correct_name", "_rp_sort"], na_position="last")
    return sub.drop_duplicates(subset=["correct_name"], keep="last").drop(columns=["_rp_sort"])


# User-facing column labels (Rawdata still uses correct_name internally).
_DISPLAY_COLUMN_ALIASES: dict[str, str] = {
    "correct_name": "Name",
}


def resolve_promotion_date_column(df: pd.DataFrame) -> str | None:
    """First column that looks like a promotion / date-promoted field (Excel naming varies)."""
    for c in df.columns:
        cl = str(c).strip().casefold().replace(" ", "_")
        if cl in ("date_promoted", "promotion_date", "promoted_date"):
            return str(c)
        if str(c).strip().casefold() in ("date promoted", "promotion date"):
            return str(c)
    return None


def rename_columns_for_display(df: pd.DataFrame) -> pd.DataFrame:
    """Rename columns for tables only; keeps code using correct_name elsewhere."""
    out = df.copy()
    for old, new in _DISPLAY_COLUMN_ALIASES.items():
        if old in out.columns:
            out = out.rename(columns={old: new})
    br_code = resolve_branch_column(out)
    if br_code and br_code in out.columns and str(br_code).strip() != "Branch code":
        out = out.rename(columns={br_code: "Branch code"})
    br_name = resolve_branch_name_column(out)
    if br_name and br_name in out.columns and str(br_name).strip() != "Branch Name":
        out = out.rename(columns={br_name: "Branch Name"})
    hired = resolve_date_hired_column(out)
    if hired and hired in out.columns and str(hired).strip() != "Date Hired":
        out = out.rename(columns={hired: "Date Hired"})
    pdc = resolve_promotion_date_column(out)
    if pdc and pdc in out.columns and pdc != "Date promoted":
        out = out.rename(columns={pdc: "Date promoted"})
    return out


def resolve_branch_column(df: pd.DataFrame) -> str | None:
    """Rawdata-style branch key (column name may vary slightly in exports)."""
    by_cf = {str(c).strip().casefold().replace(" ", "_"): str(c) for c in df.columns}
    for key in ("branchcode", "branch_code", "branch"):
        if key in by_cf:
            return by_cf[key]
    for c in df.columns:
        cl = str(c).strip().casefold()
        if cl in ("branch code", "branchcode"):
            return str(c)
    return None


def resolve_branch_name_column(df: pd.DataFrame) -> str | None:
    """Full branch label column if present (distinct from branchcode)."""
    by_cf = {str(c).strip().casefold().replace(" ", "_"): str(c) for c in df.columns}
    for key in ("branch_name", "branchname", "branch_desc", "branchdescription", "branch_legend"):
        if key in by_cf:
            return by_cf[key]
    for c in df.columns:
        cl = str(c).strip().casefold()
        if cl in ("branch name", "branchname", "branch description"):
            return str(c)
    return None


def resolve_date_hired_column(df: pd.DataFrame) -> str | None:
    """Hire / employment start date column if present."""
    by_cf = {str(c).strip().casefold().replace(" ", "_"): str(c) for c in df.columns}
    for key in (
        "date_hired",
        "hire_date",
        "hired_date",
        "date_of_hire",
        "dateemployed",
        "employment_date",
        "date_started",
    ):
        if key in by_cf:
            return by_cf[key]
    for c in df.columns:
        cl = str(c).strip().casefold()
        if cl in ("date hired", "hire date", "date of hire", "employed date", "employment date"):
            return str(c)
    return None


def mr_insights_table_column_order(ins: pd.DataFrame) -> list[str]:
    """Column order for the Advanced insights grid (matches on-screen table)."""
    _tl_ins = resolve_report_timeline_column(ins)
    _promo_raw = resolve_promotion_date_column(ins)
    _br_ins = resolve_branch_column(ins)
    _brname_ins = resolve_branch_name_column(ins)
    _dh_ins = resolve_date_hired_column(ins)
    _show_cols: list[str] = []
    if "correct_name" in ins.columns:
        _show_cols.append("correct_name")
    for _c in (_br_ins, _brname_ins, _dh_ins):
        if _c and _c in ins.columns and _c not in _show_cols:
            _show_cols.append(_c)
    for c in (
        "position",
        "Report Period",
        "Report date",
        "Employment Status",
        "Tenure Bracket",
        "TOTAL_UDI",
        "Marginal Cost",
        "BEP",
        "Baseline productivity",
        "Expected Productivity",
        "Insight tier",
        "Suggested action",
        "Rule applied",
    ):
        if c in ins.columns and c not in _show_cols:
            _show_cols.append(c)
    for _extra in (_tl_ins, _promo_raw):
        if _extra and _extra in ins.columns and _extra not in _show_cols:
            if "position" in _show_cols:
                _show_cols.insert(_show_cols.index("position") + 1, _extra)
            else:
                _show_cols.append(_extra)
    return _show_cols


def mr_insights_display_export(ins: pd.DataFrame) -> pd.DataFrame:
    """Exact dataframe shown in Advanced (friendly headers): use for CSV / Snowflake."""
    return rename_columns_for_display(ins[mr_insights_table_column_order(ins)].copy())


def top_branches_by_total_udi(df: pd.DataFrame, branch_col: str, n: int = 5) -> pd.DataFrame:
    """Branches ranked by sum(TOTAL_UDI) in the current frame."""
    if branch_col not in df.columns or "TOTAL_UDI" not in df.columns:
        return pd.DataFrame(columns=["branch", "TOTAL_UDI"])
    sub = df[[branch_col, "TOTAL_UDI"]].copy()
    sub["_b"] = sub[branch_col].map(lambda x: str(x).strip() if pd.notna(x) and str(x).strip().casefold() != "nan" else "")
    sub = sub.loc[sub["_b"].ne("")]
    sub["TOTAL_UDI"] = pd.to_numeric(sub["TOTAL_UDI"], errors="coerce").fillna(0.0)
    g = sub.groupby("_b", dropna=False)["TOTAL_UDI"].sum().reset_index()
    g = g.rename(columns={"_b": "branch"}).sort_values("TOTAL_UDI", ascending=False).head(int(n))
    return g.reset_index(drop=True)


_PIE_EXEC_PALETTE: tuple[str, ...] = (
    "#f97316",
    "#a3e635",
    "#cbd5e1",
    "#38bdf8",
    "#1d4ed8",
    "#a855f7",
    "#f43f5e",
    "#2dd4bf",
    "#eab308",
    "#64748b",
)


def pie_exploded_share_figure(
    labels: list[str],
    values: list[float],
    *,
    title: str,
    value_caption: str = "Rows",
) -> go.Figure:
    """
    Exploded pie with bold category labels outside each slice (branch code / name shows like ‘tags’ on wedges).
    True isometric 3D is not available in Plotly; pull + thick borders approximates separated chunks.
    """
    n = len(labels)
    if n == 0:
        return go.Figure()
    pull = [0.055 + 0.012 * (i % 5) for i in range(n)]
    colors = [_PIE_EXEC_PALETTE[i % len(_PIE_EXEC_PALETTE)] for i in range(n)]

    fig = go.Figure(
        data=[
            go.Pie(
                labels=labels,
                values=values,
                pull=pull,
                marker=dict(colors=colors, line=dict(color="rgba(255,255,255,0.98)", width=3)),
                texttemplate="<b>%{label}</b><br>%{percent}",
                textposition="outside",
                insidetextorientation="auto",
                hovertemplate=f"<b>%{{label}}</b><br>{value_caption}: %{{value:,}}<br>Share: %{{percent}}<extra></extra>",
                textfont=dict(size=12, color="#0f172a", family="Segoe UI, system-ui, sans-serif"),
                rotation=22,
                hole=0.0,
                sort=True,
                direction="clockwise",
            )
        ]
    )
    fig.update_layout(
        title=dict(text=title, x=0.5, xanchor="center", font=dict(size=16, color="#0f172a")),
        paper_bgcolor="#e0f0fa",
        plot_bgcolor="#e0f0fa",
        font=dict(family="Segoe UI, system-ui, sans-serif", size=12, color="#334155"),
        height=max(540, 120 + n * 28),
        showlegend=True,
        legend=dict(
            title=dict(text="Label"),
            orientation="v",
            yanchor="middle",
            y=0.5,
            x=1.02,
            xanchor="left",
            bgcolor="rgba(255,255,255,0.82)",
            bordercolor="#94a3b8",
            borderwidth=1,
        ),
        margin=dict(t=80, b=56, l=40, r=150, pad=6),
        uniformtext=dict(minsize=9, mode="hide"),
    )
    return fig


def _timeline_display_label(timeline_col: str) -> str:
    """Column header for the month the change is observed (matches Rawdata name when possible)."""
    cl = str(timeline_col).strip().casefold()
    if cl == "report date":
        return "Report date"
    if cl == "report period":
        return "Report period"
    return str(timeline_col).strip()


def detect_position_changes_from_panel(df: pd.DataFrame) -> pd.DataFrame | None:
    """When the same person has consecutive report months (Report Period or Report date) with different position."""
    tl = resolve_report_timeline_column(df)
    if tl is None or "correct_name" not in df.columns or "position" not in df.columns or len(df) < 2:
        return None
    sub = df.copy()
    sub["_rp"] = _parse_report_period_series(sub[tl])
    sub = sub.sort_values(["correct_name", "_rp"], na_position="last")
    tlab = _timeline_display_label(tl)
    rows: list[dict[str, str]] = []
    for name, g in sub.groupby("correct_name", sort=False):
        g = g.reset_index(drop=True)
        prev_pos: str | None = None
        for i in range(len(g)):
            cur = str(g.loc[i, "position"]).strip()
            rp = g.loc[i, tl]
            if prev_pos is not None and cur != prev_pos:
                rows.append(
                    {
                        "Name": str(name).strip(),
                        tlab: str(rp).strip(),
                        "Previous position": prev_pos,
                        "Current position": cur,
                    }
                )
            prev_pos = cur
    if not rows:
        return pd.DataFrame(columns=["Name", tlab, "Previous position", "Current position"])
    return pd.DataFrame(rows)


_SLICER_STRIP_COLS = (
    "Report Period",
    "active_status",
    "position",
    "correct_name",
    "Tenure Bracket",
    "Employment Status",
    "With Production",
)


def _strip_slicer_cell(x: Any) -> Any:
    if pd.isna(x):
        return x
    if isinstance(x, str):
        return x.strip()
    return str(x).strip()


def normalize_slicer_text_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Strip leading/trailing spaces on slicer columns so filters match Excel (avoids off-by-one counts)."""
    out = df.copy()
    _strip_cols = list(_SLICER_STRIP_COLS)
    _brc = resolve_branch_column(out)
    if _brc and _brc not in _strip_cols:
        _strip_cols.append(_brc)
    for _xn in (resolve_branch_name_column(out), resolve_date_hired_column(out)):
        if _xn and _xn not in _strip_cols:
            _strip_cols.append(_xn)
    for c in _strip_cols:
        if c not in out.columns:
            continue
        out[c] = out[c].map(_strip_slicer_cell)
    return out


def sheet_names_from_bytes(data: bytes) -> list[str]:
    import openpyxl

    bio = io.BytesIO(data)
    wb = openpyxl.load_workbook(bio, read_only=True, data_only=True)
    try:
        return list(wb.sheetnames)
    finally:
        wb.close()


def numeric_df(df: pd.DataFrame) -> pd.DataFrame:
    return df.select_dtypes(include=[np.number]).copy()


def categorical_columns(df: pd.DataFrame, max_unique: int = 40) -> list[str]:
    out: list[str] = []
    for c in df.columns:
        s = df[c]
        if s.dtype == object or str(s.dtype) == "category":
            n = s.nunique(dropna=True)
            if 1 < n <= max_unique:
                out.append(c)
    return out


def safe_corr(df: pd.DataFrame) -> pd.DataFrame | None:
    num = numeric_df(df)
    if num.shape[1] < 2:
        return None
    c = num.corr(numeric_only=True)
    return c


def _near_constant_series(s: pd.Series, min_distinct: int = 4) -> bool:
    """True when a column is almost fixed (explains useless straight-line scatter-matrix panels)."""
    v = pd.to_numeric(s, errors="coerce").dropna()
    if len(v) < min_distinct:
        return True
    if int(v.nunique(dropna=True)) < min_distinct:
        return True
    std = float(v.std(ddof=0))
    if not np.isfinite(std) or std == 0.0:
        return True
    return False


def split_numeric_by_variance(num: pd.DataFrame) -> tuple[list[str], list[str]]:
    varied: list[str] = []
    flat: list[str] = []
    for c in num.columns:
        if _near_constant_series(num[c]):
            flat.append(str(c))
        else:
            varied.append(str(c))
    return varied, flat


def pick_relationship_outcome_column(varied: list[str]) -> str | None:
    for pref in ("TOTAL_UDI", "TOTAL_COUNT", "KB_UDI", "MR_UDI", "REFERRAL_UDI", "KB", "MR"):
        if pref in varied:
            return pref
    for pref in ("Tenure_by_months",):
        if pref in varied:
            return pref
    return varied[0] if varied else None


def pearson_vs_outcome(num: pd.DataFrame, outcome: str) -> pd.Series:
    """Pearson r between outcome and every other numeric column (pairwise complete rows)."""
    o = pd.to_numeric(num[outcome], errors="coerce")
    rows: dict[str, float] = {}
    for c in num.columns:
        if str(c) == str(outcome):
            continue
        x = pd.to_numeric(num[c], errors="coerce")
        pair = pd.DataFrame({"o": o, "x": x}).dropna()
        if len(pair) < 8:
            continue
        r = pair["o"].corr(pair["x"])
        if r is not None and np.isfinite(float(r)):
            rows[str(c)] = float(r)
    s = pd.Series(rows, dtype=float)
    if s.empty:
        return s
    order = np.argsort(-np.abs(s.to_numpy(dtype=float)))
    return s.iloc[order]


def build_numeric_relationship_narrative(
    n_rows: int,
    outcome: str | None,
    varied: list[str],
    flat: list[str],
    corrs: pd.Series,
) -> str:
    lines: list[str] = []
    lines.append(f"- **Rows in this view:** {n_rows:,}.")
    if outcome:
        lines.append(
            f"- **Focus metric:** **{outcome}** — the bar chart ranks other numeric fields by how strongly they move "
            "in a straight-line sense with this column (Pearson correlation)."
        )
    lines.append(
        f"- **Columns with enough spread to plot meaningfully:** {len(varied)}. "
        f"**Near-flat columns** (mostly one value — typical for Excel defaults like BEP / baseline blocks): **{len(flat)}**."
    )
    if flat:
        shown = flat[:14]
        suf = " …" if len(flat) > 14 else ""
        lines.append(f"  - Flat / low-spread: {', '.join(f'`{c}`' for c in shown)}{suf}")
    if outcome and not corrs.empty:
        lines.append("- **Strongest links** (*r* closer to ±1 = stronger linear pattern):")
        for idx, val in corrs.head(4).items():
            direction = "tend to increase together" if val > 0 else "tend to move in opposite directions"
            lines.append(f"  - **`{idx}`** vs `{outcome}`: **r = {val:+.2f}** → they **{direction}** (linearly).")
        lines.append(
            "- **Caveat:** correlation is **not** causation. Flat columns were excluded so you are not misled by artificial lines."
        )
    elif outcome:
        lines.append("- Not enough overlapping data to estimate correlations — widen filters or check missing values.")
    return "\n".join(lines)


def normality_hint(series: pd.Series) -> dict[str, Any]:
    s = pd.to_numeric(series, errors="coerce").dropna()
    if len(s) < 8:
        return {"ok": False, "reason": "Need at least 8 numeric values."}
    stat, p = stats.shapiro(s.iloc[: min(5000, len(s))])
    return {"ok": True, "shapiro_p": float(p), "is_normalish": p > 0.05}


FREQ_TO_PANDAS = {
    "Year": "YS",
    "Month": "MS",
    "Week": "W-MON",
}


def aggregate_by_period(
    df: pd.DataFrame,
    date_col: str,
    value_col: str,
    freq_label: str,
    how: str,
) -> pd.DataFrame:
    """Return columns: period (Timestamp), period_label, actual."""
    empty = pd.DataFrame(columns=["period", "period_label", "actual"])
    if date_col == value_col:
        return empty
    # Build with explicit names — avoids duplicate column labels when Excel uses messy headers
    d = pd.DataFrame(
        {
            "_dt": pd.to_datetime(df[date_col], errors="coerce"),
            "_y": pd.to_numeric(df[value_col], errors="coerce"),
        }
    )
    d = d.dropna(subset=["_dt", "_y"])
    if d.empty:
        return empty
    freq = FREQ_TO_PANDAS[freq_label]
    g = d.groupby(pd.Grouper(key="_dt", freq=freq))["_y"]
    if how == "sum":
        actual = g.sum()
    elif how == "mean":
        actual = g.mean()
    else:
        actual = g.count()
    actual = actual[actual.notna()]
    out = actual.reset_index()
    out.columns = ["period", "actual"]
    out["period_label"] = out["period"].dt.strftime(
        "%Y" if freq_label == "Year" else "%Y-%m" if freq_label == "Month" else "%Y-%m-%d (week)"
    )
    return out.sort_values("period").reset_index(drop=True)


def aggregate_by_period_quarter(
    df: pd.DataFrame,
    date_col: str,
    value_col: str,
    how: str,
) -> pd.DataFrame:
    empty = pd.DataFrame(columns=["period", "period_label", "actual"])
    if date_col == value_col:
        return empty
    d = pd.DataFrame(
        {
            "_dt": pd.to_datetime(df[date_col], errors="coerce"),
            "_y": pd.to_numeric(df[value_col], errors="coerce"),
        }
    )
    d = d.dropna(subset=["_dt", "_y"])
    if d.empty:
        return empty
    g = d.groupby(pd.Grouper(key="_dt", freq="QS"))["_y"]
    if how == "sum":
        actual = g.sum()
    elif how == "mean":
        actual = g.mean()
    else:
        actual = g.count()
    actual = actual[actual.notna()]
    out = actual.reset_index()
    out.columns = ["period", "actual"]
    out["period_label"] = out["period"].dt.to_period("Q").astype(str)
    return out.sort_values("period").reset_index(drop=True)


def _goals_state_key(sheet: str, value_col: str, freq: str) -> str:
    return f"goals_targets::{sheet}::{value_col}::{freq}"


st.set_page_config(
    page_title="MR PRODUCTIVITY REPORT",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    html, body, .stApp { font-family: "Segoe UI", system-ui, -apple-system, BlinkMacSystemFont, sans-serif; }
    div[data-testid="stMetricValue"] { font-size: 1.65rem; }
    .exec-sub { color: #5c6370; font-size: 0.95rem; margin-top: -0.35rem; }
    .pivot-filters {
      background: linear-gradient(180deg, #f8fafc 0%, #f1f5f9 100%);
      border: 1px solid #e2e8f0;
      border-radius: 8px;
      padding: 0.45rem 0.85rem;
      margin: 0.2rem 0 0.65rem 0;
      font-size: 0.86rem;
      color: #334155;
    }
    .pivot-h2 {
      font-size: 1.12rem;
      font-weight: 700;
      color: #0f172a;
      margin: 0.5rem 0 0.1rem 0;
    }
    </style>
    """,
    unsafe_allow_html=True,
)
st.title("MR PRODUCTIVITY REPORT")
st.markdown('<p class="exec-sub">Interactive view from Excel — performance vs. your targets by week, month, or year.</p>', unsafe_allow_html=True)

with st.sidebar:
    st.header("Data source")
    mode = st.radio("Source", ["Upload .xlsx", "File path"], horizontal=True)
    uploaded = None
    path_str = ""
    if mode == "Upload .xlsx":
        uploaded = st.file_uploader("Excel file", type=["xlsx", "xlsm"])
    else:
        path_str = st.text_input(
            "Full path to .xlsx / .xlsm",
            value=str(_LOCAL_XLSM) if _LOCAL_XLSM.is_file() else "",
            placeholder=r"C:\path\to\workbook.xlsx",
        )

data_bytes: bytes | None = None
file_label = ""

if mode == "Upload .xlsx" and uploaded is not None:
    data_bytes = uploaded.getvalue()
    file_label = uploaded.name
elif mode == "File path" and path_str.strip():
    try:
        with open(path_str.strip(), "rb") as f:
            data_bytes = f.read()
        file_label = path_str.strip()
    except OSError as e:
        st.error(f"Could not read file: {e}")

if not data_bytes:
    st.info("Upload an Excel file or enter a valid path to begin.")
    st.stop()
    sys.exit(0)

try:
    sheets = sheet_names_from_bytes(data_bytes)
except Exception as e:
    st.error(f"Could not open workbook: {e}")
    st.stop()
    sys.exit(1)

if not sheets:
    st.error("No sheets found in this workbook.")
    st.stop()
    sys.exit(1)

def _sheet_default_index(names: list[str]) -> int:
    if "Rawdata" in names:
        return names.index("Rawdata")
    return 0


col_a, col_b = st.columns([1, 2])
with col_a:
    sheet = st.selectbox(
        "Sheet",
        sheets,
        index=_sheet_default_index(sheets),
        help="Overall / monthly sheets are dashboard layouts. For analytics, choose Rawdata (or it auto-loads below).",
    )
with col_b:
    st.write(f"**File:** `{file_label}`")

# Overall & monthly tabs are pivot/dashboard layouts — not a flat table. Use Rawdata for pandas.
analysis_sheet = sheet
if "Rawdata" in sheets and sheet != "Rawdata":
    analysis_sheet = "Rawdata"

try:
    df = load_excel(io.BytesIO(data_bytes), sheet_name=analysis_sheet)
except Exception as e:
    st.error(f"Could not load sheet: {e}")
    st.stop()
    sys.exit(1)

df = df.dropna(how="all").dropna(axis=1, how="all")
df = dedupe_column_names(df)
df = normalize_slicer_text_columns(df)

if "Rawdata" in sheets and sheet != "Rawdata":
    st.warning(
        f"**{sheet}** is not a flat table (merged cells / titles). "
        f"All tabs below **automatically use the `Rawdata` sheet** — that is what feeds the Overall pivots in Excel. "
        f"For the full president-style report: `python -m streamlit run consolidated_mr_report.py`"
    )
elif "Rawdata" not in sheets:
    st.warning(
        "This workbook has no **Rawdata** sheet. "
        "**Overall** / monthly tabs are report layouts only — they may lack numeric columns and have bad Unnamed headers."
    )

# --- Sidebar: slicers / filters (applied to all tabs) ---
_fl_tag = re.sub(r"[^\w\-]+", "_", str(file_label))[:40] if file_label else "file"
_sk = f"sl_{analysis_sheet}_{_fl_tag}_{len(df.columns)}"

with st.sidebar:
    st.divider()
    st.header("Slicers / filters")
    st.caption("Nothing selected = **All** for that field. Multi = many; Single = one.")

    use_multi = st.radio(
        "Slicer style",
        ["Multi-select", "Single choice"],
        index=0,
        horizontal=True,
        key=f"sl_mode_{_sk}",
    )
    multi_mode = use_multi.startswith("Multi")

    sel_report_periods: list[str] = []
    if "Report Period" in df.columns:
        rp_opts = sorted(df["Report Period"].dropna().astype(str).unique())
        sel_report_periods = _sidebar_slicer_values(
            "Report Period",
            rp_opts,
            widget_key=f"sl_rp_{_sk}",
            use_multi=multi_mode,
        )

    sel_pos_sl: list[str] = []
    if "position" in df.columns:
        po = sorted(df["position"].fillna("(blank)").astype(str).unique())
        mr_def = [p for p in ["Marketing Representative"] if p in po]
        sel_pos_sl = _sidebar_slicer_values(
            "Position",
            po,
            widget_key=f"sl_pos_{_sk}",
            use_multi=multi_mode,
            default_multi=mr_def if multi_mode else None,
        )

    sel_names_sl: list[str] = []
    if "correct_name" in df.columns:
        nm = sorted(df["correct_name"].dropna().astype(str).unique())
        sel_names_sl = _sidebar_slicer_values(
            "Name",
            nm,
            widget_key=f"sl_nm_{_sk}",
            use_multi=multi_mode,
        )
        st.sidebar.caption("Rawdata column: `correct_name`")

    sel_act_sl: list[str] = []
    if "active_status" in df.columns:
        ac = sorted(df["active_status"].dropna().astype(str).unique())
        sel_act_sl = _sidebar_slicer_values(
            "Current status (active_status)",
            ac,
            widget_key=f"sl_ac_{_sk}",
            use_multi=multi_mode,
        )

    sel_ten_sl: list[str] = []
    if "Tenure Bracket" in df.columns:
        tn = sorted(df["Tenure Bracket"].dropna().astype(str).unique(), key=_tenure_sort_sl)
        sel_ten_sl = _sidebar_slicer_values(
            "Tenure Bracket",
            tn,
            widget_key=f"sl_tn_{_sk}",
            use_multi=multi_mode,
        )

    sel_emp_sl: list[str] = []
    if "Employment Status" in df.columns:
        em = sorted(df["Employment Status"].dropna().astype(str).unique())
        sel_emp_sl = _sidebar_slicer_values(
            "Employment status",
            em,
            widget_key=f"sl_em_{_sk}",
            use_multi=multi_mode,
        )

    _br_col_sl = resolve_branch_column(df)
    sel_br_sl: list[str] = []
    if _br_col_sl:
        _br_opts = sorted(df[_br_col_sl].dropna().astype(str).str.strip().unique())
        _br_key = f"sl_br_{_sk}"
        sel_br_sl = _sidebar_slicer_values(
            "Branch",
            _br_opts,
            widget_key=_br_key,
            use_multi=multi_mode,
        )
        if multi_mode and _br_opts and st.sidebar.button(
            "Select all branches",
            key=f"sl_br_all_{_sk}",
            help="Fills the Branch multi-select with every branch in the file (same as clearing the filter).",
        ):
            st.session_state[_br_key] = list(_br_opts)
            st.rerun()
        st.sidebar.caption(f"Rawdata column: `{_br_col_sl}` · empty = **all** branches")

    _df_pre_slicers = df.copy()
    df_before = len(df)
    df = apply_app_slicers(
        df,
        report_periods=sel_report_periods or None,
        positions=sel_pos_sl or None,
        names=sel_names_sl or None,
        active_statuses=sel_act_sl or None,
        tenure_brackets=sel_ten_sl or None,
        employment_statuses=sel_emp_sl or None,
        branches=sel_br_sl or None,
        branch_column=_br_col_sl,
    )
    # Key-role UDI trend chart: always show all tracked positions; do not apply the Position slicer here.
    df_key_roles_udi_trend = apply_app_slicers(
        _df_pre_slicers,
        report_periods=sel_report_periods or None,
        positions=None,
        names=sel_names_sl or None,
        active_statuses=sel_act_sl or None,
        tenure_brackets=sel_ten_sl or None,
        employment_statuses=sel_emp_sl or None,
        branches=sel_br_sl or None,
        branch_column=_br_col_sl,
    )
    st.metric(
        "Rows (filtered)",
        f"{len(df):,}",
        delta=(len(df) - df_before) if len(df) != df_before else None,
    )

tab_exec, tab_overview, tab_stats, tab_viz, tab_advanced = st.tabs(
    ["Goals & performance", "Overview", "Statistics", "Visualizations", "Advanced"]
)

with tab_exec:
    st.subheader("Map your data")
    st.caption(
        "With **Rawdata**: pick **Report Period** (date) and **TOTAL_UDI** or another numeric column (metric). "
        "If dropdowns are **blank** after changing sheet, **refresh** the browser once (Ctrl+R)."
    )
    all_cols = list(df.columns)
    # Keys must change when sheet / columns change — old session state can leave selectboxes blank
    _wk = f"{sheet}__{analysis_sheet}__{len(all_cols)}"

    if not all_cols:
        st.error("The loaded sheet has no columns. Try another sheet or re-upload the file.")
    else:
        date_ix = all_cols.index("Report Period") if "Report Period" in all_cols else 0
        date_ix = max(0, min(date_ix, len(all_cols) - 1))

        c1, c2, c3 = st.columns(3)
        with c1:
            date_col = st.selectbox(
                "Date column",
                all_cols,
                index=date_ix,
                key=f"exec_date_{_wk}",
                help="Date or Report Period (must differ from the metric).",
            )
        with c2:
            metric_choices = [c for c in all_cols if c != date_col]
            if not metric_choices:
                metric_choices = all_cols
            mi = 0
            if "TOTAL_UDI" in metric_choices:
                mi = metric_choices.index("TOTAL_UDI")
            else:
                nums = [c for c in metric_choices if pd.api.types.is_numeric_dtype(df[c])]
                if nums:
                    mi = metric_choices.index(nums[0])
            mi = max(0, min(mi, len(metric_choices) - 1))
            value_col = st.selectbox(
                "Metric to track (actuals)",
                metric_choices,
                index=mi,
                key=f"exec_metric_{_wk}",
                help="Must be numeric; cannot be the same as the Date column.",
            )
        with c3:
            freq_label = st.selectbox(
                "Target period",
                ["Month", "Quarter", "Year", "Week"],
                index=0,
                key=f"exec_freq_{_wk}",
            )

    if not all_cols:
        st.stop()

    if date_col == value_col:
        st.error(
            "**Date** and **Metric** cannot be the same. Choose a different metric "
            "or change the Date column (e.g. **Report Period** when using Rawdata)."
        )

    how = st.radio(
        "Roll up actuals as",
        ["sum", "mean", "count"],
        horizontal=True,
        key=f"exec_how_{_wk}",
    )

    if freq_label == "Quarter":
        period_df = aggregate_by_period_quarter(df, date_col, value_col, how)
    else:
        period_df = aggregate_by_period(df, date_col, value_col, freq_label, how)

    if period_df.empty:
        st.warning("No rows after parsing dates and numbers. Check the date and metric columns.")
    else:
        dr = st.date_input(
            "Limit to date range (optional)",
            value=(
                period_df["period"].min().to_pydatetime().date(),
                period_df["period"].max().to_pydatetime().date(),
            ),
            key=f"exec_range_{_wk}",
        )
        if isinstance(dr, tuple) and len(dr) == 2:
            start_d, end_d = dr
            mask = (period_df["period"].dt.date >= start_d) & (period_df["period"].dt.date <= end_d)
            period_df = period_df.loc[mask].reset_index(drop=True)

    if not period_df.empty:
        st.divider()
        st.subheader("Targets")
        st.caption(
            "Enter a goal for each period below, or set one number and spread it evenly across all periods in the table."
        )

        state_key = _goals_state_key(f"{sheet}_{analysis_sheet}_{_wk}", value_col, freq_label)
        if state_key not in st.session_state:
            st.session_state[state_key] = pd.DataFrame(
                {
                    "Period": period_df["period_label"].astype(str),
                    "Actual": period_df["actual"].values,
                    "Target": np.nan,
                }
            )

        # Resync rows if period_df changed (same key): replace table if length/labels differ
        existing = st.session_state[state_key]
        if len(existing) != len(period_df) or (
            list(existing["Period"].astype(str)) != list(period_df["period_label"].astype(str))
        ):
            old = existing.set_index("Period")["Target"] if "Target" in existing.columns else pd.Series(dtype=float)
            new_targets = []
            for pl in period_df["period_label"].astype(str):
                new_targets.append(old.get(pl, np.nan))
            st.session_state[state_key] = pd.DataFrame(
                {
                    "Period": period_df["period_label"].astype(str),
                    "Actual": period_df["actual"].values,
                    "Target": new_targets,
                }
            )

        flat_target = st.number_input(
            "Fill: same target every period",
            value=0.0,
            format="%.4f",
            help="Applies the same goal to every row in the table (including zero).",
            key=f"exec_flat_target_{_wk}",
        )
        if st.button("Apply to all periods", key=f"exec_apply_flat_{_wk}"):
            t = st.session_state[state_key].copy()
            t["Target"] = float(flat_target)
            st.session_state[state_key] = t
            st.rerun()

        edited = st.data_editor(
            st.session_state[state_key],
            column_config={
                "Period": st.column_config.TextColumn("Period", disabled=True),
                "Actual": st.column_config.NumberColumn("Actual", disabled=True, format="%.4f"),
                "Target": st.column_config.NumberColumn("Your target", format="%.4f"),
            },
            use_container_width=True,
            num_rows="fixed",
            key=f"exec_editor_{_wk}",
        )
        st.session_state[state_key] = edited

        tgt = pd.to_numeric(edited["Target"], errors="coerce")
        act = pd.to_numeric(edited["Actual"], errors="coerce")
        merged = edited.copy()
        merged["_tgt"] = tgt
        merged["_act"] = act
        merged["Variance"] = merged["_act"] - merged["_tgt"]
        merged["Attainment %"] = np.where(
            merged["_tgt"].notna() & (merged["_tgt"] != 0),
            100.0 * merged["_act"] / merged["_tgt"],
            np.nan,
        )

        valid = merged["_tgt"].notna()
        total_act = merged.loc[valid, "_act"].sum()
        total_tgt = merged.loc[valid, "_tgt"].sum()
        pct_overall = (100.0 * total_act / total_tgt) if total_tgt and total_tgt != 0 else np.nan

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Actual (in view)", f"{total_act:,.2f}")
        m2.metric("Target (periods with a goal)", f"{total_tgt:,.2f}")
        m3.metric("Variance", f"{total_act - total_tgt:,.2f}" if valid.any() else "—")
        m4.metric("Attainment", f"{pct_overall:.1f}%" if not np.isnan(pct_overall) else "—")

        plot_df = merged[valid].copy()
        if not plot_df.empty:
            fig_b = go.Figure()
            fig_b.add_bar(
                x=plot_df["Period"].astype(str),
                y=plot_df["_act"],
                name="Actual",
                marker_color="#2563eb",
            )
            fig_b.add_bar(
                x=plot_df["Period"].astype(str),
                y=plot_df["_tgt"],
                name="Target",
                marker_color="#94a3b8",
            )
            fig_b.update_layout(
                barmode="group",
                title=f"{value_col} — actual vs. target ({freq_label})",
                xaxis_title="",
                yaxis_title=value_col,
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                height=460,
                margin=dict(t=60),
            )
            st.plotly_chart(fig_b, use_container_width=True)

            line_fig = go.Figure()
            line_fig.add_trace(
                go.Scatter(
                    x=plot_df["Period"].astype(str),
                    y=plot_df["Attainment %"],
                    mode="lines+markers",
                    name="Attainment %",
                    line=dict(color="#059669", width=3),
                )
            )
            line_fig.add_hline(y=100, line_dash="dash", line_color="#64748b", annotation_text="100%")
            line_fig.update_layout(
                title="Attainment vs. target (100% = on goal)",
                yaxis_title="Percent of target",
                xaxis_title="",
                height=380,
            )
            st.plotly_chart(line_fig, use_container_width=True)

        show_tbl = merged[["Period", "Actual", "Target", "Variance", "Attainment %"]].copy()
        st.dataframe(show_tbl, use_container_width=True, height=min(420, 38 * (len(show_tbl) + 2)))

with tab_overview:
    def _overview_pane(period: str, extras: list[tuple[str, str]] | None = None) -> None:
        parts: list[tuple[str, str]] = [
            ("Report Period", period),
            ("active_status", fmt_slicer_values(sel_act_sl)),
            ("position", fmt_slicer_values(sel_pos_sl)),
            ("Name (Rawdata: correct_name)", fmt_slicer_values(sel_names_sl)),
            ("Branch", fmt_slicer_values(sel_br_sl)),
            ("Tenure Bracket", fmt_slicer_values(sel_ten_sl)),
            ("Employment status", fmt_slicer_values(sel_emp_sl)),
        ]
        if extras:
            parts.extend(extras)
        pivot_filter_banner(parts)

    prod_title_pos = fmt_slicer_values(sel_pos_sl) if sel_pos_sl else "All positions"

    if "Report Period" not in df.columns or not len(df):
        st.warning("No **Report Period** column or no rows — cannot build Overall pivots.")
    else:
        rp_series = df["Report Period"].dropna().astype(str)
        periods_ov = list(dict.fromkeys(rp_series.tolist()))
        if not periods_ov:
            st.warning("No valid **Report Period** values after filtering.")
        else:
            h1, h2 = st.columns([1, 1])
            with h1:
                st.subheader("Overview")
            with h2:
                ov_period = st.selectbox(
                    "Report period",
                    periods_ov,
                    index=len(periods_ov) - 1,
                    key=f"ov_period_{_sk}",
                    help="KPIs and tables below use this month plus sidebar slicers.",
                )

            df_ov = slice_period_and_production(df, ov_period, None)
            kpi = compute_overview_kpis(df_ov)
            st.caption(
                f"KPIs for **{ov_period}** · {len(df_ov):,} rows in view (sidebar filters applied). "
                "Change slicers or period to refresh."
            )
            with st.expander("Definitions: Total Count vs TOTAL UDI", expanded=False):
                st.markdown(_OVERVIEW_METRIC_DEFINITIONS_MD)
            m1, m2, m3, m4 = st.columns(4)
            with m1:
                st.metric(
                    "Active employees",
                    f"{kpi['active']:,}" if kpi["active"] is not None else "—",
                )
            with m2:
                st.metric(
                    "Inactive / other status",
                    f"{kpi['inactive']:,}" if kpi["inactive"] is not None else "—",
                )
            with m3:
                st.metric(
                    "With production",
                    f"{kpi['wp_yes']:,}" if kpi["wp_yes"] is not None else "—",
                )
            with m4:
                st.metric(
                    "No production",
                    f"{kpi['wp_no']:,}" if kpi["wp_no"] is not None else "—",
                )
            u1, u2, u3, u4 = st.columns(4)
            with u1:
                v = kpi["kb"]
                st.metric("UDI / KB (sum)", f"{v:,.0f}" if v is not None else "—")
            with u2:
                v = kpi["referral"]
                st.metric("Referral (sum)", f"{v:,.0f}" if v is not None else "—")
            with u3:
                v = kpi["field_sat"]
                st.metric("Field saturation (sum)", f"{v:,.0f}" if v is not None else "—")
            with u4:
                v = kpi["fb"]
                st.metric("FB support (sum)", f"{v:,.0f}" if v is not None else "—")

            st.divider()
            st.markdown(
                '<p class="pivot-h2">TOTAL UDI trend by position (key roles)</p>',
                unsafe_allow_html=True,
            )
            st.caption(
                "Sum of **TOTAL_UDI** by **Report Period** and role: Area Head, Assistant Area Head, Marketing Representative, "
                "Office in Charge for Control, Officer in Charge for Operations, TeleMarketer, Training Leader — Trainee. "
                "**Position** sidebar filter does **not** apply to this chart (so every key role appears); "
                "other slicers still apply. Labels are matched to these roles (typos such as *Ofiice* are normalized)."
            )
            _udi_pos_trend = build_udi_trend_by_position(df_key_roles_udi_trend)
            if _udi_pos_trend is not None and not _udi_pos_trend.empty:
                _pos_in_data = list(dict.fromkeys(_udi_pos_trend["Position"].astype(str)))
                _pos_cat = [p for p in _OVERVIEW_UDI_POSITION_TREND_LABELS if p in _pos_in_data]
                _rp_card_order = list(dict.fromkeys(_udi_pos_trend["Report Period"].astype(str).tolist()))

                with st.container(border=True):
                    st.caption("Interactive **value cards** — choose the month; Δ compares to the prior month in the chart.")
                    _card_rp = st.selectbox(
                        "Report period (value cards)",
                        _rp_card_order,
                        index=len(_rp_card_order) - 1,
                        key=f"udi_trend_card_rp_{_sk}",
                    )
                    _sub_card = _udi_pos_trend.loc[_udi_pos_trend["Report Period"].astype(str) == str(_card_rp)]
                    _prev_ix = _rp_card_order.index(_card_rp) - 1 if _card_rp in _rp_card_order else -1
                    _prev_rp = _rp_card_order[_prev_ix] if _prev_ix >= 0 else None
                    _prev_map: dict[str, float] = {}
                    if _prev_rp is not None:
                        _p = _udi_pos_trend.loc[_udi_pos_trend["Report Period"].astype(str) == str(_prev_rp)]
                        _prev_map = dict(zip(_p["Position"].astype(str), pd.to_numeric(_p["TOTAL_UDI"], errors="coerce")))

                    _n_pos = len(_pos_cat)
                    _cols_per = 4
                    for _r0 in range(0, max(_n_pos, 1), _cols_per):
                        _rowc = st.columns(_cols_per)
                        for _ci, _col in enumerate(_rowc):
                            _pi = _r0 + _ci
                            if _pi >= _n_pos:
                                break
                            _pn = _pos_cat[_pi]
                            _row = _sub_card.loc[_sub_card["Position"].astype(str) == _pn]
                            _v = float(pd.to_numeric(_row["TOTAL_UDI"], errors="coerce").iloc[0]) if len(_row) else 0.0
                            _pv = _prev_map.get(_pn)
                            _delta_s = None
                            if _prev_rp is not None and _pv is not None and pd.notna(_pv):
                                _delta_s = f"{_v - float(_pv):+,.0f} vs prior month"
                            with _col:
                                st.metric(
                                    _pn,
                                    f"{_v:,.0f}",
                                    delta=_delta_s,
                                )

                fig_ud_pos = px.line(
                    _udi_pos_trend,
                    x="Report Period",
                    y="TOTAL_UDI",
                    color="Position",
                    markers=True,
                    category_orders={"Position": _pos_cat} if _pos_cat else None,
                    title="TOTAL UDI over report periods — by position",
                    height=520,
                    color_discrete_sequence=px.colors.qualitative.Bold,
                )
                fig_ud_pos.update_traces(line=dict(width=2.5))
                fig_ud_pos.update_layout(
                    template="plotly_white",
                    paper_bgcolor="#ffffff",
                    plot_bgcolor="#fafafa",
                    font=_VIZ_FONT,
                    hovermode="x unified",
                    title=dict(x=0.5, xanchor="center"),
                    legend=dict(
                        title=dict(text="Position"),
                        orientation="v",
                        yanchor="top",
                        y=1,
                        x=1.01,
                        xanchor="left",
                    ),
                    margin=dict(t=56, b=96, l=72, r=200),
                    xaxis=dict(
                        title=dict(text="Report Period", standoff=28),
                        tickangle=-45,
                        showgrid=True,
                        gridcolor=_VIZ_GRID,
                        automargin=True,
                    ),
                    yaxis=dict(
                        title=dict(text="TOTAL UDI (sum)", standoff=12),
                        tickformat=_format_axis_si(),
                        showgrid=True,
                        gridcolor=_VIZ_GRID,
                        zeroline=False,
                        automargin=True,
                    ),
                )
                st.plotly_chart(
                    fig_ud_pos,
                    use_container_width=True,
                    config={
                        "scrollZoom": True,
                        "displayModeBar": True,
                        "displaylogo": False,
                    },
                )
            else:
                st.info(
                    "Need **TOTAL_UDI**, **position**, and **Report Period** with rows that match the key roles listed above. "
                    "Widen sidebar filters if a role is missing."
                )

            st.divider()

            filtered_period = df_ov
            pos_tbl = position_counts(filtered_period)
            pos_display = pos_tbl.rename(columns={"position": "Months"})

            with st.container(border=True):
                st.markdown(
                    f'<p class="pivot-h2">Active Employee Count as of {ov_period}</p>',
                    unsafe_allow_html=True,
                )
                _overview_pane(ov_period, [("With Production", "All")])
                st.caption(
                    "**Pivot:** `groupby(position)` → row counts. Excel **Months** = `position` (may show *(blank)*)."
                )
                plotly_pivot_table(pos_display, "", None)

            st.divider()

            prod_cols = {"KB", "MR", "REFERRAL", "FB_SUPPORT", "TOTAL_COUNT", "Tenure Bracket", "correct_name"}
            missing_prod = sorted(c for c in prod_cols if c not in df.columns)
            if missing_prod:
                st.warning(
                    "Missing columns for **MR productivity** pivots: " + ", ".join(missing_prod)
                )
            else:
                sub_yes = slice_period_and_production(df, ov_period, "Yes")
                sub_no = slice_period_and_production(df, ov_period, "No")
                prod_yes = productivity_summary(sub_yes)
                prod_no = productivity_summary(sub_no)
                st.markdown(
                    f'<p class="pivot-h2">MR productivity count by tenure — {ov_period} · {prod_title_pos}</p>',
                    unsafe_allow_html=True,
                )
                st.caption(
                    "**Pivot:** `groupby(Tenure Bracket)` → sum **KB**, **MR** (Field Saturation), **REFERRAL**, **FB_SUPPORT**, **TOTAL_COUNT**. "
                    "On Rawdata, **`TOTAL_COUNT`** = **KB + MR + REFERRAL + FB_SUPPORT** (counts). "
                    "To match Excel, choose **Marketing Representative** under Position in the sidebar."
                )
                py, pn = st.columns(2)
                with py:
                    st.markdown(f"**MR Productivity Count — {ov_period}**")
                    _overview_pane(ov_period, [("With Production", "Yes")])
                    plotly_pivot_table(prod_yes, "", "Total Count")
                with pn:
                    st.markdown('**MR with "No Production"**')
                    _overview_pane(ov_period, [("With Production", "No")])
                    plotly_pivot_table(prod_no, "", None)

                gty = prod_yes[prod_yes["Tenure Bracket"] == "Grand Total"]
                gtn = prod_no[prod_no["Tenure Bracket"] == "Grand Total"]
                if not gty.empty and not gtn.empty:
                    fig_p = go.Figure()
                    fig_p.add_trace(
                        go.Bar(
                            name="With production (Yes)",
                            x=["Total Count", "Employees"],
                            y=[float(gty["Total Count"].iloc[0]), float(gty["Total Employee"].iloc[0])],
                            marker_color="#1d4ed8",
                        )
                    )
                    fig_p.add_trace(
                        go.Bar(
                            name="Without production (No)",
                            x=["Total Count", "Employees"],
                            y=[float(gtn["Total Count"].iloc[0]), float(gtn["Total Employee"].iloc[0])],
                            marker_color="#94a3b8",
                        )
                    )
                    fig_p.update_layout(
                        title="Grand total: productivity vs headcount (Yes vs No)",
                        barmode="group",
                        height=380,
                        legend=dict(orientation="h", yanchor="bottom", y=1.05, x=1),
                        template="plotly_white",
                    )
                    st.plotly_chart(fig_p, use_container_width=True)

            udi_cols = {
                "Employment Status",
                "Tenure Bracket",
                "KB_UDI",
                "MR_UDI",
                "REFERRAL_UDI",
                "FB_SUPPORT_UDI",
                "TOTAL_UDI",
                "Marginal Cost",
                "BEP",
                "Baseline productivity",
                "Expected Productivity",
            }
            missing_udi = sorted(c for c in udi_cols if c not in df.columns)
            if missing_udi:
                st.warning(
                    "Missing columns for **Regular / Probi MR Total UDI**: " + ", ".join(missing_udi)
                )
            else:
                mod_key_ov = f"overview_mod_{_sk}"
                mod_ver_key = f"{mod_key_ov}_widget_ver"
                if mod_key_ov not in st.session_state:
                    st.session_state[mod_key_ov] = default_modifiers_from_raw(df)
                if mod_ver_key not in st.session_state:
                    st.session_state[mod_ver_key] = 0
                base_ud = slice_period_and_production(df, ov_period, None)

                st.divider()
                st.subheader("Amount modifiers (targets per employee)")
                cap_col, reset_col = st.columns([5, 1])
                with cap_col:
                    st.caption(
                        "**Adjust the values below** (rates are per employee). **Roll-up** = summed per row by "
                        "Employment Status — like Overall. Metrics and the pivot below update automatically when "
                        "numbers change."
                    )
                with reset_col:
                    if st.button("Reset to defaults", key=f"reset_mod_{_sk}", help="Restore values from Rawdata"):
                        st.session_state[mod_key_ov] = default_modifiers_from_raw(df)
                        st.session_state[mod_ver_key] = int(st.session_state[mod_ver_key]) + 1
                        st.rerun()

                _wver = int(st.session_state[mod_ver_key])
                _mod_base = st.session_state[mod_key_ov].copy()
                _rows_mod: list[dict[str, Any]] = []
                with st.container(border=True):
                    for _, _r in _mod_base.iterrows():
                        _st = str(_r["Employment Status"])
                        st.markdown(f"**{_st}**")
                        z1, z2, z3, z4 = st.columns(4)
                        with z1:
                            _mc = st.number_input(
                                "Marginal Cost",
                                min_value=0.0,
                                value=float(_r["Marginal Cost"]),
                                step=500.0,
                                format="%d",
                                key=f"ov_nm_mc_{_sk}_{_st}_{_wver}",
                            )
                        with z2:
                            _bep = st.number_input(
                                "BEP",
                                min_value=0.0,
                                value=float(_r["BEP"]),
                                step=500.0,
                                format="%d",
                                key=f"ov_nm_bep_{_sk}_{_st}_{_wver}",
                            )
                        with z3:
                            _bl = st.number_input(
                                "Baseline productivity",
                                min_value=0.0,
                                value=float(_r["Baseline productivity"]),
                                step=1000.0,
                                format="%d",
                                key=f"ov_nm_bl_{_sk}_{_st}_{_wver}",
                            )
                        with z4:
                            _ex = st.number_input(
                                "Expected Productivity",
                                min_value=0.0,
                                value=float(_r["Expected Productivity"]),
                                step=1000.0,
                                format="%d",
                                key=f"ov_nm_ex_{_sk}_{_st}_{_wver}",
                            )
                        _rows_mod.append(
                            {
                                "Employment Status": _st,
                                "Marginal Cost": float(_mc or 0),
                                "BEP": float(_bep or 0),
                                "Baseline productivity": float(_bl or 0),
                                "Expected Productivity": float(_ex or 0),
                            }
                        )
                mod_eff = (
                    coerce_modifier_table(pd.DataFrame(_rows_mod))
                    if _rows_mod
                    else coerce_modifier_table(_mod_base)
                )
                st.session_state[mod_key_ov] = mod_eff.copy()

                udi_tbl = udi_hierarchy_table(base_ud, mod_eff)

                if not udi_tbl.empty and (udi_tbl["Tenure Bracket"].str.strip() == "Grand Total").any():
                    gt0 = udi_tbl[udi_tbl["Tenure Bracket"].str.strip() == "Grand Total"].iloc[0]
                    att_b = (
                        (gt0["TOTAL UDI"] / gt0["Baseline Productivity"] * 100)
                        if gt0["Baseline Productivity"]
                        else None
                    )
                    att_e = (
                        (gt0["TOTAL UDI"] / gt0["Expected Productivity"] * 100)
                        if gt0["Expected Productivity"]
                        else None
                    )
                    k1, k2, k3, k4, k5 = st.columns(5)
                    k1.metric("TOTAL UDI", f"{gt0['TOTAL UDI']:,.0f}", help=_HELP_TEXT_TOTAL_UDI)
                    k2.metric("vs baseline target", f"{att_b:.1f}%" if att_b is not None else "—")
                    k3.metric("vs expected target", f"{att_e:.1f}%" if att_e is not None else "—")
                    k4.metric("Headcount", f"{int(gt0['Total Employee']):,}")
                    k5.metric("Baseline $ (target)", f"{gt0['Baseline Productivity']:,.0f}")

                with st.container(border=True):
                    st.markdown(
                        f'<p class="pivot-h2">Regular / Probi MR Total UDI — {ov_period}</p>',
                        unsafe_allow_html=True,
                    )
                    _overview_pane(ov_period, [("With Production", "All")])
                    st.caption(
                        "**Pivot:** Probi / Regular × **Tenure Bracket** → sum **KB_UDI … TOTAL_UDI**; "
                        "modifiers × headcount for cost and targets. "
                        "**TOTAL UDI** = roll-up of **KB_UDI**, **MR_UDI**, **REFERRAL_UDI**, and **FB_SUPPORT_UDI**."
                    )
                    disp = udi_tbl.copy()
                    disp["Total Employee"] = disp["Total Employee"].map(
                        lambda x: f"{int(round(x)):,}" if pd.notna(x) else ""
                    )
                    for c in disp.columns:
                        if c not in ("Tenure Bracket", "Total Employee") and disp[c].dtype == float:
                            disp[c] = disp[c].map(lambda x: f"{x:,.2f}" if pd.notna(x) else "")
                    plotly_pivot_table(disp, "", "TOTAL UDI")
                    with st.expander("Show as table (copy/paste)"):
                        st.dataframe(disp, use_container_width=True, hide_index=True)

                leaf = udi_tbl[udi_tbl["Tenure Bracket"].str.strip().isin(TENURE_ORDER)].copy()
                if not leaf.empty:
                    leaf["Tenure Bracket"] = leaf["Tenure Bracket"].str.strip()
                    fig_ud = go.Figure()
                    fig_ud.add_trace(
                        go.Bar(
                            x=leaf["Tenure Bracket"],
                            y=leaf["TOTAL UDI"],
                            name="TOTAL UDI",
                            marker_color="#1d4ed8",
                        )
                    )
                    fig_ud.update_layout(
                        title="TOTAL UDI by tenure (detail rows)",
                        height=400,
                        template="plotly_white",
                    )
                    st.plotly_chart(fig_ud, use_container_width=True)

    st.divider()
    st.subheader("Preview")
    st.dataframe(df.head(200), use_container_width=True, height=360)
    dupes = df.duplicated().sum()
    st.write(f"**Duplicate rows:** {dupes:,}")

with tab_stats:
    st.subheader("Summary (numeric)")
    num = numeric_df(df)
    if num.empty:
        st.warning("No numeric columns detected for describe().")
    else:
        st.dataframe(num.describe().T, use_container_width=True)

    st.subheader("Missing values by column")
    miss_s = df.isna().sum().sort_values(ascending=False)
    miss_s = miss_s[miss_s > 0]
    if miss_s.empty:
        st.success("No missing values in loaded columns.")
    else:
        fig_m = px.bar(
            x=miss_s.values,
            y=miss_s.index.astype(str),
            orientation="h",
            labels={"x": "Missing count", "y": "Column"},
            title="Missing value counts",
        )
        fig_m.update_layout(yaxis={"categoryorder": "total ascending"}, height=max(320, len(miss_s) * 22))
        st.plotly_chart(fig_m, use_container_width=True)

with tab_viz:
    btn_a, btn_b = st.columns(2)
    with btn_a:
        if st.button("Spotlight · refresh view", type="primary", use_container_width=True, key="viz_spotlight"):
            st.balloons()
    with btn_b:
        if st.button("Pulse · highlight chart", use_container_width=True, key="viz_pulse"):
            st.snow()

    _plotly_config = {
        "scrollZoom": True,
        "displayModeBar": True,
        "displaylogo": False,
        "toImageButtonOptions": {"format": "png", "filename": "chart"},
    }

    trend_tbl = build_report_period_trend_table(df)
    st.subheader("Trends over report periods")
    if trend_tbl is not None and len(trend_tbl) > 0:
        xcat = trend_tbl["Report Period"].astype(str)
        fig_t = go.Figure()
        n_traces = 0
        for candidates, leg, color, sym, lw in _TREND_LINE_SLOTS:
            col = next((c for c in candidates if c in trend_tbl.columns), None)
            if col is None:
                continue
            fig_t.add_trace(
                go.Scatter(
                    x=xcat,
                    y=trend_tbl[col],
                    name=leg,
                    mode="lines+markers",
                    line=dict(color=color, width=lw),
                    marker=dict(size=9 if lw >= 3 else 8, symbol=sym, line=dict(width=1.2, color="#ffffff")),
                    hovertemplate="<b>%{x}</b><br>"
                    + leg
                    + ": %{y:"
                    + _format_axis_si()
                    + "}<extra></extra>",
                )
            )
            n_traces += 1
        if n_traces == 0:
            first_m = [c for c in trend_tbl.columns if c != "Report Period"][0]
            fig_t.add_trace(
                go.Scatter(
                    x=xcat,
                    y=trend_tbl[first_m],
                    name=first_m,
                    mode="lines+markers",
                    line=dict(color="#2563eb", width=3),
                    marker=dict(size=9, symbol="circle", line=dict(width=1.5, color="#ffffff")),
                )
            )
            n_traces = 1
        fig_t.update_layout(
            title=dict(
                text=(
                    "Key metrics over time"
                    "<br><sup style='font-size:11px;font-weight:normal;color:#64748b'>"
                    "TOTAL UDI, KB, field saturation, referral, FB support · sums by report period"
                    "</sup>"
                ),
                font=dict(size=15, color="#0f172a", family="Segoe UI, system-ui, sans-serif"),
                x=0.5,
                xanchor="center",
                y=0.985,
                yanchor="top",
                yref="paper",
            ),
            hovermode="x unified",
            height=580,
            template="plotly_white",
            paper_bgcolor="#ffffff",
            plot_bgcolor="#fafafa",
            font=_VIZ_FONT,
            # Legend below plot so it cannot overlap the title (was y≈1.22 above axes).
            legend=dict(
                orientation="h",
                yanchor="top",
                y=-0.2,
                xanchor="center",
                x=0.5,
                bgcolor="rgba(255,255,255,0.95)",
                bordercolor="#e2e8f0",
                borderwidth=1,
            ),
            margin=dict(t=96, b=150, l=72, r=72),
        )
        fig_t.update_xaxes(
            title_text="Report Period",
            tickangle=-45,
            showgrid=True,
            gridcolor=_VIZ_GRID,
            showline=True,
            linewidth=1,
            linecolor="#94a3b8",
        )
        fig_t.update_yaxes(
            title_text="Amount (sum)",
            showgrid=True,
            gridcolor=_VIZ_GRID,
            zeroline=False,
            tickformat=_format_axis_si(),
        )
        st.plotly_chart(fig_t, use_container_width=True, config=_plotly_config)
        if n_traces and n_traces < 3:
            st.caption(
                "Showing available series only. Add **TOTAL_UDI**, **KB_UDI** / **KB**, **MR_UDI** / **MR**, "
                "**REFERRAL_UDI** / **REFERRAL**, **FB_SUPPORT_UDI** / **FB_SUPPORT** in Rawdata for the full set. "
                "**TOTAL UDI** sums the four UDI pillars; **Total Count** (where used) sums the four count pillars."
            )
    else:
        st.info("Need **Report Period** plus numeric columns such as **TOTAL_UDI** and **KB** / **KB_UDI** for trend lines.")

    num = numeric_df(df)
    cats = categorical_columns(df)

    if num.shape[1] >= 2:
        st.subheader("Correlation heatmap")
        st.caption(_CORR_HEATMAP_CAPTION_MD)
        corr = safe_corr(df)
        if corr is not None:
            fig_h = px.imshow(
                corr,
                text_auto=".2f",
                aspect="auto",
                color_continuous_scale="RdBu_r",
                zmin=-1,
                zmax=1,
                title="Pearson correlation (numeric columns)",
            )
            fig_h.update_layout(height=520)
            st.plotly_chart(fig_h, use_container_width=True, config=_plotly_config)
    else:
        st.info("Need at least two numeric columns for a correlation heatmap.")

    st.subheader("Distributions & relationships")
    if num.empty:
        st.warning("No numeric columns for charts.")
    else:
        r1c1, r1c2 = st.columns(2)
        with r1c1:
            y_col = st.selectbox("Y axis (numeric)", list(num.columns), index=0, key="y_scatter")
        with r1c2:
            x_options = [c for c in num.columns if c != y_col]
            x_col = (
                st.selectbox("X axis (numeric)", x_options, index=0 if x_options else None, key="x_scatter")
                if x_options
                else None
            )

        color_opt = [None] + cats
        size_opts = _scatter_point_size_options(list(num.columns), x_col, y_col)
        if "size_scatter" in st.session_state and st.session_state["size_scatter"] not in size_opts:
            st.session_state["size_scatter"] = size_opts[0]
        r2c1, r2c2, r2c3 = st.columns(3)
        with r2c1:
            color_col = st.selectbox("Color by (category)", color_opt, index=0, key="color_scatter")
        with r2c2:
            size_col = st.selectbox(
                "Point size (numeric, optional)",
                size_opts,
                index=0,
                key="size_scatter",
                help=(
                    "Larger points = higher value in that field. "
                    "The four UDI pillars are grouped as **"
                    + _SCATTER_UDI_SUM_LABEL
                    + "** (row sum of whichever of KB/MR/Referral/FB UDI columns exist)."
                ),
            )
        with r2c3:
            show_trend = st.checkbox("Show OLS trendline", value=len(df) >= 10, key="scatter_trend", disabled=len(df) < 10)

        if x_col:
            hover_extra = [c for c in ("Report Period", "correct_name", "position", "active_status") if c in df.columns]
            plot_df = df
            size_for_px = size_col
            size_labels: dict[str, str] = {}
            if size_col == _SCATTER_UDI_SUM_LABEL:
                plot_df = df.copy()
                plot_df["_scatter_udi_sum"] = _scatter_udi_components_sum(plot_df)
                size_for_px = "_scatter_udi_sum"
                size_labels["_scatter_udi_sum"] = _SCATTER_UDI_SUM_LABEL
            fig_s = px.scatter(
                plot_df,
                x=x_col,
                y=y_col,
                color=color_col if color_col else None,
                size=size_for_px if size_for_px else None,
                size_max=28,
                hover_name="correct_name" if "correct_name" in plot_df.columns else None,
                hover_data=hover_extra[:6] if hover_extra else None,
                trendline="ols" if show_trend and len(df) >= 10 else None,
                title=f"{y_col} vs {x_col}",
                opacity=1.0,
                height=580,
                color_discrete_sequence=px.colors.qualitative.Bold,
                labels=size_labels if size_labels else None,
            )
            fig_s.update_traces(
                marker=dict(symbol="circle-open", line=dict(width=1.35)),
                selector=dict(mode="markers"),
            )
            fig_s.update_layout(
                template="plotly_white",
                legend_title_text=color_col or "",
                hovermode="closest",
                dragmode="zoom",
                paper_bgcolor="#ffffff",
                plot_bgcolor="#fafafa",
                font=_VIZ_FONT,
                title=dict(font=dict(size=14, color="#0f172a", family="Segoe UI, system-ui, sans-serif")),
                margin=dict(l=64, r=48, t=56, b=48),
                xaxis=dict(
                    showgrid=True,
                    gridcolor=_VIZ_GRID,
                    zeroline=False,
                    showline=True,
                    linecolor="#94a3b8",
                ),
                yaxis=dict(
                    showgrid=True,
                    gridcolor=_VIZ_GRID,
                    zeroline=False,
                    showline=True,
                    linecolor="#94a3b8",
                ),
            )
            st.plotly_chart(fig_s, use_container_width=True, config=_plotly_config)

        hist_col = st.selectbox("Histogram column", list(num.columns), key="hist_col")
        bins = st.slider("Bins", 10, 80, 30)
        fig_hist = px.histogram(df, x=hist_col, nbins=bins, marginal="box", title=f"Distribution of {hist_col}")
        st.plotly_chart(fig_hist, use_container_width=True, config=_plotly_config)

    if cats:
        st.subheader("Category breakdown")
        st.caption("Hover bars for exact values · drag to zoom · double-click to reset. Bar color reflects value height.")
        cat_pick = st.selectbox("Category column", cats, key="cat_pick")
        agg_col_candidates = [c for c in num.columns]
        if agg_col_candidates:
            val_col = st.selectbox("Value to aggregate", agg_col_candidates, key="cat_val")
            agg = st.selectbox("Aggregation", ["sum", "mean", "count", "median"], index=1)
            g = df.groupby(cat_pick, dropna=False)[val_col]
            if agg == "sum":
                s = g.sum()
            elif agg == "mean":
                s = g.mean()
            elif agg == "median":
                s = g.median()
            else:
                s = g.count()
            s = reorder_grouped_series_chronologically(s)
            x_labels = s.index.astype(str)
            y_vals = s.values.astype(float)
            fig_b = px.bar(
                x=x_labels,
                y=y_vals,
                color=y_vals,
                color_continuous_scale="Blues",
                labels={
                    "x": cat_pick,
                    "y": f"{agg} of {val_col}",
                    "color": f"{agg} value",
                },
                title=f"{agg.title()} of {val_col} by {cat_pick}",
            )
            fig_b.update_traces(
                hovertemplate="<b>%{x}</b><br>"
                + f"{agg} of {val_col}"
                + ": %{y:,.4f}<extra></extra>",
            )
            fig_b.update_layout(
                xaxis={
                    "categoryorder": "array",
                    "categoryarray": list(x_labels),
                    "tickangle": -42,
                },
                yaxis=dict(rangemode="tozero"),
                height=460,
                template="plotly_white",
                hovermode="x unified",
                coloraxis_colorbar=dict(title=dict(text=f"{agg}")),
                margin=dict(b=120),
            )
            st.plotly_chart(fig_b, use_container_width=True, config=_plotly_config)

with tab_advanced:
    st.subheader("MR Productivity — automatic insights")
    st.caption(
        "Uses the **same filtered rows** as the rest of the app (sidebar + sheet). "
        "Compares **TOTAL_UDI** to **BEP**, **Baseline productivity**, and **Expected Productivity** from Rawdata; "
        "if those cells are blank, Regular defaults to 80k / 100k baseline–expected and 57k BEP; Probi BEP defaults to 25k."
    )
    with st.expander("Objective & rules (how actions are assigned)", expanded=False):
        st.markdown(
            f"""
**Purpose:** suggest a **coaching / HR action** from UDI vs cost targets (not a legal or payroll decision).

**Employee type**
- **Probi** ({", ".join(PROBI_TENURES)} tenure buckets): first month **Tenure = 1** is **non-bearing** (no UDI target). After that, rules are **BEP-centric** (typical Probi BEP **25k** if the row is blank).
- **Regular** ({", ".join(REGULAR_TENURES)}): **expected** productivity **{_DEFAULT_REGULAR_EXPECTED:,.0f}**, **baseline** **{_DEFAULT_REGULAR_BASELINE:,.0f}**, **BEP** **{_DEFAULT_REGULAR_BEP:,.0f}** when missing on the row.

**Cost context (illustrative)**  
Marginal cost = per-person salaries & benefits (your **Marginal Cost** column). **BEP** is the break-even UDI hurdle. Example: 10 MRs × 25k marginal ≈ 250k total marginal cost in that group.

**Actions (simplified)**  
- **Probi:** retain vs fire path informed by UDI vs BEP (and month 1 non-bearing).  
- **Regular:** retain → train/develop → PIP / review → fire-risk band as UDI falls through baseline and BEP.

Tenure buckets follow Rawdata: **{", ".join(TENURE_ORDER)}**.
            """
        )

    _mr_req = ["TOTAL_UDI", "Employment Status", "Tenure Bracket"]
    _mr_missing = [c for c in _mr_req if c not in df.columns]
    mr_only = True
    if "position" in df.columns:
        mr_only = st.checkbox(
            "Restrict insights to Marketing Representative rows",
            value=True,
            key="adv_mr_only",
            help="Filters rows whose position text contains “Marketing”.",
        )

    df_adv_mr = df.copy()
    if mr_only and "position" in df_adv_mr.columns:
        pos = df_adv_mr["position"].astype(str)
        df_adv_mr = df_adv_mr.loc[pos.str.contains("Marketing", case=False, na=False)].copy()
    df_panel_mr = df_adv_mr.copy()

    _grain_opts = (
        "One row per employee (latest report month in view)",
        "All periods (panel — one row per person per month)",
    )
    grain = st.radio(
        "Row grain",
        _grain_opts,
        index=0,
        key="adv_mr_grain",
        help=(
            "Rawdata is usually **one row per person per month**, so names repeat. "
            "Default keeps only each person’s **latest** month in the current filter for insights."
        ),
    )
    _n_before_grain = len(df_adv_mr)
    if grain == _grain_opts[0]:
        _tl_adv = resolve_report_timeline_column(df_adv_mr)
        if "correct_name" in df_adv_mr.columns and _tl_adv is not None:
            df_adv_mr = mr_insight_latest_row_per_employee(df_adv_mr)
            st.caption(
                f"Using **{len(df_adv_mr):,}** employees (latest report month each, by **{_tl_adv}**; "
                f"**{_n_before_grain:,}** rows before deduplicating by name)."
            )
        else:
            st.warning(
                "Add **correct_name** and **Report period** or **Report date** to deduplicate. Showing all rows."
            )

    if _mr_missing:
        st.warning("MR insights need columns: " + ", ".join(_mr_missing))
    elif df_adv_mr.empty:
        st.info("No rows left after MR filter. Turn off “Restrict to Marketing Representative” or widen sidebar filters.")
    else:
        ins = build_mr_insight_table(df_adv_mr)
        if "Marginal Cost" in ins.columns:
            mc = pd.to_numeric(ins["Marginal Cost"], errors="coerce")
            st.metric("Total marginal cost (sum in view)", f"{mc.sum():,.0f}")
            if grain == _grain_opts[0]:
                st.caption("Sum of **Marginal Cost** for one row per employee (latest period).")
            else:
                st.caption(
                    "Sum of **Marginal Cost** across **all** rows — the same person can appear each month, so this can **over-count** cost vs headcount."
                )

        tier = ins["Insight tier"].astype(str)
        n_nonbearing = int(tier.str.contains("Non-bearing", na=False).sum())
        n_met_reg = int((tier == "Meets expected (Regular)").sum())
        n_met_prob = int((tier == "Meets / exceeds BEP (Probi)").sum())
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Rows in view", f"{len(ins):,}")
        c2.metric("Regular — met expected", f"{n_met_reg:,}")
        c3.metric("Probi — met / exceeded BEP", f"{n_met_prob:,}")
        c4.metric("Non-bearing (Probi mo. 1)", f"{n_nonbearing:,}")

        _br_ins = resolve_branch_column(ins)
        _brname_ins = resolve_branch_name_column(ins)
        _dh_ins = resolve_date_hired_column(ins)
        _promo_raw = resolve_promotion_date_column(ins)
        _show_cols = mr_insights_table_column_order(ins)

        _hr_hint: list[str] = []
        if _br_ins and not _brname_ins:
            _hr_hint.append("add a **Branch Name** column (`Branch Name` / `branch_name`) beside `branchcode`")
        if not _dh_ins:
            _hr_hint.append("add **Date Hired** (`Date Hired` / `hire_date`) to Rawdata")
        if _hr_hint:
            st.caption("To show full branch title & hire date: " + "; ".join(_hr_hint) + ".")
        _insights_export_df = mr_insights_display_export(ins)
        st.dataframe(
            _insights_export_df,
            use_container_width=True,
            hide_index=True,
        )

        st.download_button(
            "Download this insights table (CSV)",
            _insights_export_df.to_csv(index=False).encode("utf-8"),
            file_name="mr_productivity_insights_table.csv",
            mime="text/csv",
            key="dl_mr_insights_csv",
            help="Exactly the table above — current sidebar filters & MR / grain options.",
        )

        try:
            import snowflake.connector  # noqa: F401
            from snowflake.connector.pandas_tools import write_pandas as _sf_write_pandas
        except ImportError:
            _sf_write_pandas = None

        with st.expander("Load this table into Snowflake", expanded=False):
            st.caption(
                "Uploads **only** the insights grid (same as CSV). "
                "Install: `pip install \"snowflake-connector-python[pandas]\"`. "
                "Use a service account; avoid sharing passwords in screenshots."
            )
            if _sf_write_pandas is None:
                st.info('Install the Snowflake driver to enable upload: **snowflake-connector-python[pandas]**')
            else:
                s1, s2 = st.columns(2)
                with s1:
                    sf_account = st.text_input("Account locator", key="sf_acct", placeholder="XY12345.region")
                    sf_user = st.text_input("User", key="sf_user")
                    sf_pass = st.text_input("Password", type="password", key="sf_pass")
                    sf_role = st.text_input("Role (optional)", key="sf_role", placeholder="ANALYST")
                with s2:
                    sf_wh = st.text_input("Warehouse", key="sf_wh")
                    sf_db = st.text_input("Database", key="sf_db")
                    sf_sc = st.text_input("Schema", key="sf_sc", value="PUBLIC")
                    sf_tbl = st.text_input("Table name", key="sf_tbl", value="MR_PRODUCTIVITY_INSIGHTS")

                replace_tbl = st.checkbox(
                    "Replace table if it exists",
                    value=True,
                    key="sf_replace",
                    help="Uses CREATE OR REPLACE TABLE when supported; otherwise create only.",
                )
                if st.button("Upload dataframe to Snowflake", type="primary", key="sf_upload_btn"):
                    if not all([sf_account, sf_user, sf_pass, sf_wh, sf_db, sf_sc, sf_tbl]):
                        st.error("Fill in account, user, password, warehouse, database, schema, and table name.")
                    else:
                        try:
                            _conn = snowflake.connector.connect(
                                account=sf_account.strip(),
                                user=sf_user.strip(),
                                password=sf_pass,
                                warehouse=sf_wh.strip(),
                                database=sf_db.strip(),
                                schema=sf_sc.strip(),
                                role=(sf_role.strip() or None),
                            )
                            _tbl = sf_tbl.strip().upper()
                            _up = _insights_export_df.copy()
                            ok, _chunks, rows, out_msg = _sf_write_pandas(
                                _conn,
                                _up,
                                _tbl,
                                database=sf_db.strip(),
                                schema=sf_sc.strip(),
                                auto_create_table=True,
                                quote_identifiers=True,
                                overwrite=replace_tbl,
                            )
                            _conn.close()
                            if ok:
                                st.success(
                                    f"Loaded **{int(rows):,}** rows into "
                                    f"`{sf_db.strip()}.{sf_sc.strip()}.{_tbl}`."
                                )
                            else:
                                st.error(f"Snowflake did not confirm success: {out_msg}")
                        except Exception as e:
                            st.error(f"Snowflake error: {e}")

        if _br_ins and ins[_br_ins].notna().any():
            st.markdown("**By branch — rows per insight tier**")
            ct = pd.crosstab(ins[_br_ins].astype(str), ins["Insight tier"])
            st.dataframe(ct, use_container_width=True)

        st.subheader("Name & promotion monitoring")
        st.caption(
            "Promotion moves are detected when **position** changes from one month to the next for the same person, "
            "ordered by **Report period** or **Report date** (whichever your Rawdata uses). "
            "Optional: add **Date promoted** for an explicit HR date."
        )
        if _promo_raw and _promo_raw in ins.columns:
            _pcols: list[str] = []
            for c in (
                "correct_name",
                _br_ins,
                _brname_ins,
                _dh_ins,
                "position",
                _promo_raw,
                "Report Period",
                "Report date",
            ):
                if c and c in ins.columns and c not in _pcols:
                    _pcols.append(c)
            st.markdown("**Recorded promotion date (from sheet)**")
            st.dataframe(
                rename_columns_for_display(ins[_pcols].copy()),
                use_container_width=True,
                hide_index=True,
            )
        _chg = detect_position_changes_from_panel(df_panel_mr)
        if _chg is not None and not _chg.empty:
            st.markdown("**Inferred position changes (from report timeline)**")
            st.dataframe(_chg, use_container_width=True, hide_index=True)
        elif _chg is not None:
            st.caption(
                "No position change between consecutive report months in this view — widen **Report period** / **Report date** "
                "in the sidebar or check that **position** text is comparable row-to-row."
            )

    st.divider()
    st.caption("Pick a column: the **title below** matches the visualization (distribution, branch UDI rank, or category mix).")
    col_pick = st.selectbox("Column to explore", list(df.columns), key="adv_col")
    _branch_res = resolve_branch_column(df)
    s = df[col_pick]
    _pick_is_branch = (
        _branch_res is not None and str(col_pick).strip().casefold() == str(_branch_res).strip().casefold()
    )

    if pd.api.types.is_numeric_dtype(s):
        _deep_title = f"Numeric distribution & Q–Q plot — `{col_pick}`"
    elif _pick_is_branch and "TOTAL_UDI" in df.columns:
        _deep_title = f"Top 5 branches by TOTAL UDI — `{col_pick}`"
    else:
        _deep_title = f"Category mix (share of rows) — `{col_pick}`"

    st.subheader(_deep_title)
    st.write(f"**dtype:** `{s.dtype}`  ·  **non-null:** {s.notna().sum():,}  ·  **unique:** {s.nunique():,}")

    if pd.api.types.is_numeric_dtype(s):
        arr = pd.to_numeric(s, errors="coerce").dropna()
        if len(arr) >= 3:
            q1, q3 = arr.quantile([0.25, 0.75])
            iqr = q3 - q1
            low, high = q1 - 1.5 * iqr, q3 + 1.5 * iqr
            outliers = ((arr < low) | (arr > high)).sum()
            st.write(f"**Tukey outliers (1.5×IQR):** {outliers:,}  ·  bounds [{low:g}, {high:g}]")

        hint = normality_hint(s)
        if hint.get("ok"):
            st.write(
                f"**Shapiro–Wilk** (sample up to 5k): p = {hint['shapiro_p']:.4f} → "
                f"{'roughly normal' if hint['is_normalish'] else 'likely non-normal'} for Gaussian assumptions."
            )
        else:
            st.caption(hint.get("reason", ""))

        fig_qq = go.Figure()
        sample = arr.iloc[: min(5000, len(arr))]
        osm, osr = stats.probplot(sample, dist="norm", fit=False)
        fig_qq.add_trace(go.Scatter(x=osm, y=osr, mode="markers", name="Sample"))
        fig_qq.add_trace(
            go.Scatter(x=osm, y=np.poly1d(np.polyfit(osm, osr, 1))(osm), mode="lines", name="Fit")
        )
        fig_qq.update_layout(title="Q–Q plot vs normal", xaxis_title="Theoretical quantiles", yaxis_title="Ordered values")
        st.plotly_chart(fig_qq, use_container_width=True)

    elif _pick_is_branch and "TOTAL_UDI" in df.columns and _branch_res is not None:
        top5_br = top_branches_by_total_udi(df, str(_branch_res), 5)
        if top5_br.empty:
            st.info("No branch + **TOTAL_UDI** values to sum in the current filter.")
        else:
            _nc = len(top5_br)
            _metric_cols = st.columns(_nc)
            for _mi in range(_nc):
                _r = top5_br.iloc[_mi]
                with _metric_cols[_mi]:
                    st.metric(str(_r["branch"]), f"{float(_r['TOTAL_UDI']):,.0f}")
            fig_br = go.Figure(
                go.Bar(
                    x=top5_br["branch"].astype(str),
                    y=top5_br["TOTAL_UDI"],
                    marker_color="#1d4ed8",
                    text=top5_br["TOTAL_UDI"].map(lambda v: f"{float(v):,.0f}"),
                    textposition="outside",
                    hovertemplate="<b>%{x}</b><br>TOTAL UDI sum: %{y:" + _format_axis_si() + "}<extra></extra>",
                )
            )
            fig_br.update_layout(
                title="Sum of TOTAL_UDI · top 5 branches (current filters)",
                xaxis_title="Branch",
                yaxis=dict(
                    title="TOTAL UDI (sum)",
                    tickformat=_format_axis_si(),
                    gridcolor=_VIZ_GRID,
                ),
                template="plotly_white",
                height=440,
                paper_bgcolor="#ffffff",
                plot_bgcolor="#fafafa",
                font=_VIZ_FONT,
                margin=dict(t=48, b=64),
                xaxis=dict(showgrid=False),
                showlegend=False,
            )
            st.plotly_chart(fig_br, use_container_width=True)
            if len(top5_br) >= 2:
                _udi_vals = [float(x) for x in top5_br["TOTAL_UDI"].tolist()]
                _br_labs = [str(x) for x in top5_br["branch"].tolist()]
                fig_br_pie = pie_exploded_share_figure(
                    _br_labs,
                    _udi_vals,
                    title="TOTAL UDI **share** — top 5 branches (same totals as the bar chart)",
                    value_caption="TOTAL_UDI (sum)",
                )
                fig_br_pie.update_layout(legend=dict(title=dict(text="Branch code")))
                st.plotly_chart(
                    fig_br_pie,
                    use_container_width=True,
                    config={"displayModeBar": True, "displaylogo": False},
                )
            st.caption(
                "**TOTAL_UDI** is summed per branch for every row in the current view (same as sidebar filters). "
                "The **pie** uses the same top 5 branches; each **label** is the **branch code** (no icons). "
                "Pick any other column in the dropdown to see a different tool."
            )
    elif s.dtype == object or str(s.dtype) == "category":
        vc_all = s.astype(str).value_counts()
        _pie_cap = 15
        vc = vc_all.head(_pie_cap)
        _labs = list(vc.index.astype(str))
        _vals = [float(x) for x in vc.values]
        if len(vc_all) > _pie_cap:
            _other = float(vc_all.iloc[_pie_cap:].sum())
            if _other > 0:
                _labs.append(f"Other ({len(vc_all) - _pie_cap} categories)")
                _vals.append(_other)
        fig_p = pie_exploded_share_figure(
            _labs,
            _vals,
            title=f"Share of rows — `{col_pick}` (exploded · labels on slices)",
            value_caption="Rows in view",
        )
        st.plotly_chart(
            fig_p,
            use_container_width=True,
            config={
                "displayModeBar": True,
                "displaylogo": False,
                "scrollZoom": False,
            },
        )
        st.caption(
            "Each **label** is the category value (e.g. **branch code** when you pick `branchcode`). "
            "Exploded wedges mimic a chunky / 3D-style chart; Plotly cannot render true isometric 3D pie. "
            f"Top **{_pie_cap}** categories shown" + (" · remainder grouped as **Other**." if len(vc_all) > _pie_cap else ".")
        )

    st.subheader("How numbers relate (guided view)")
    st.caption(
        "Easier than a scatter-matrix: we **drop near-constant columns** (they only draw straight lines), "
        "then show **one clear bar chart** and **one scatter** you control — plus an **auto summary**."
    )
    num = numeric_df(df)
    if num.shape[1] < 2:
        st.info("Need at least two numeric columns for relationship charts.")
    else:
        varied, flat = split_numeric_by_variance(num)
        outcome = pick_relationship_outcome_column(varied) if varied else None
        corrs = pearson_vs_outcome(num, outcome) if outcome else pd.Series(dtype=float)
        with st.expander("Automatic narrative report", expanded=True):
            st.markdown(
                build_numeric_relationship_narrative(len(df), outcome, varied, flat, corrs),
            )

        m1, m2, m3 = st.columns(3)
        m1.metric("Numeric columns (all)", num.shape[1])
        m2.metric("Varied enough to plot", len(varied))
        m3.metric("Hidden (flat)", len(flat))

        if outcome is None or len(varied) < 2:
            st.warning(
                "After removing flat columns, fewer than two **varying** numeric fields remain. "
                "That is why the old matrix looked empty — widen slicers or expect duplicates like shared BEP rates across rows."
            )
        else:
            top_n = corrs.head(min(16, max(1, len(corrs))))
            if top_n.empty:
                top_n = pd.Series(dtype=float)
            if not top_n.empty:
                top_n_plot = top_n.iloc[::-1]
                fig_rel = go.Figure(
                    go.Bar(
                        x=top_n_plot.values,
                        y=top_n_plot.index.astype(str),
                        orientation="h",
                        marker_color=["#16a34a" if v >= 0 else "#dc2626" for v in top_n_plot.values],
                        hovertemplate="%{y}<br>r = %{x:.2f}<extra></extra>",
                    )
                )
                fig_rel.update_layout(
                    title=f"Strength of linear link vs **{outcome}** (Pearson r, −1 to +1)",
                    xaxis_title="Correlation with focus metric",
                    yaxis_title="",
                    xaxis=dict(range=[-1, 1], zeroline=True, zerolinewidth=2, gridcolor=_VIZ_GRID),
                    template="plotly_white",
                    height=max(340, 52 + 32 * len(top_n_plot)),
                    paper_bgcolor="#ffffff",
                    plot_bgcolor="#fafafa",
                    font=_VIZ_FONT,
                    margin=dict(l=8, r=24, t=48, b=48),
                )
                st.plotly_chart(fig_rel, use_container_width=True)
            else:
                st.info(f"No correlations could be computed for **{outcome}** (need ≥8 rows with both values).")

            x_opts = [c for c in varied if c != outcome]
            y_opts = varied.copy()
            default_x_ix = 0
            if len(corrs) and corrs.index[0] in x_opts:
                default_x_ix = x_opts.index(corrs.index[0])
            default_y_ix = y_opts.index(outcome) if outcome in y_opts else 0
            cxa, cyb = st.columns(2)
            with cxa:
                x_sc = st.selectbox("Scatter — horizontal (X)", x_opts, index=default_x_ix, key="adv_rel_x")
            with cyb:
                y_sc = st.selectbox("Scatter — vertical (Y)", y_opts, index=default_y_ix, key="adv_rel_y")

            if x_sc == y_sc:
                st.warning("Choose **two different columns** for the scatter plot (X and Y).")
            else:
                scatter_df = df[[x_sc, y_sc] + [c for c in ("position", "correct_name") if c in df.columns]].copy()
                scatter_df[x_sc] = pd.to_numeric(scatter_df[x_sc], errors="coerce")
                scatter_df[y_sc] = pd.to_numeric(scatter_df[y_sc], errors="coerce")
                scatter_df = scatter_df.dropna(subset=[x_sc, y_sc])
                color_sc = "position" if "position" in scatter_df.columns else None
                fig_xy = px.scatter(
                    scatter_df,
                    x=x_sc,
                    y=y_sc,
                    color=color_sc,
                    hover_name="correct_name" if "correct_name" in scatter_df.columns else None,
                    trendline="ols" if len(scatter_df) >= 15 else None,
                    title=f"{y_sc} vs {x_sc}",
                    height=520,
                    opacity=0.85,
                    color_discrete_sequence=px.colors.qualitative.Bold,
                )
                fig_xy.update_layout(
                    template="plotly_white",
                    paper_bgcolor="#ffffff",
                    plot_bgcolor="#fafafa",
                    font=_VIZ_FONT,
                    legend=dict(orientation="h", yanchor="bottom", y=-0.28, x=0.5, xanchor="center"),
                    margin=dict(t=56, b=100),
                    xaxis=dict(showgrid=True, gridcolor=_VIZ_GRID),
                    yaxis=dict(showgrid=True, gridcolor=_VIZ_GRID),
                )
                fig_xy.update_traces(marker=dict(size=10, line=dict(width=0.8, color="#ffffff")))
                st.plotly_chart(fig_xy, use_container_width=True)
                if len(scatter_df) < 15:
                    st.caption("Trendline appears when **≥15** points have both X and Y.")

    csv = df.to_csv(index=False).encode("utf-8")
    st.download_button("Download current sheet as CSV", csv, file_name=f"{sheet}_export.csv", mime="text/csv")
