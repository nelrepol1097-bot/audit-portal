"""
Shared pivot / Overall-style helpers for Rawdata (used by app.py and consolidated_mr_report).

No Streamlit page config here — safe to import from any entry script.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

TENURE_ORDER = ["1", "2-5", "6-12", "13-24", "25-36", "37 above"]

PROBI_TENURES = ["1", "2-5"]
REGULAR_TENURES = ["6-12", "13-24", "25-36", "37 above"]


def tenure_sort_key(val: str) -> int:
    try:
        return TENURE_ORDER.index(str(val))
    except ValueError:
        return 999


def sort_tenure(df: pd.DataFrame, col: str = "Tenure Bracket") -> pd.DataFrame:
    cat = pd.Categorical(df[col].astype(str), categories=TENURE_ORDER, ordered=True)
    df = df.copy()
    df["_sort"] = cat
    return df.sort_values("_sort").drop(columns=["_sort"])


def fmt_slicer_values(selected: list[str], max_show: int = 2) -> str:
    if not selected:
        return "All"
    if len(selected) <= max_show:
        return ", ".join(selected)
    return ", ".join(selected[:max_show]) + f", … (+{len(selected) - max_show})"


def productivity_summary(sub: pd.DataFrame) -> pd.DataFrame:
    if sub.empty:
        return pd.DataFrame(
            columns=[
                "Tenure Bracket",
                "Total Employee",
                "KB",
                "Field Saturation",
                "Referral",
                "FB Support",
                "Total Count",
            ]
        )
    g = (
        sub.groupby("Tenure Bracket", dropna=False)
        .agg(
            Total_Employee=("correct_name", "count"),
            KB=("KB", "sum"),
            Field_Saturation=("MR", "sum"),
            Referral=("REFERRAL", "sum"),
            FB=("FB_SUPPORT", "sum"),
            Total_Count=("TOTAL_COUNT", "sum"),
        )
        .reset_index()
    )
    g = sort_tenure(g)
    grand = pd.DataFrame(
        [
            {
                "Tenure Bracket": "Grand Total",
                "Total_Employee": int(sub["correct_name"].count()),
                "KB": sub["KB"].sum(),
                "Field_Saturation": sub["MR"].sum(),
                "Referral": sub["REFERRAL"].sum(),
                "FB": sub["FB_SUPPORT"].sum(),
                "Total_Count": sub["TOTAL_COUNT"].sum(),
            }
        ]
    )
    out = pd.concat([g, grand], ignore_index=True)
    out = out.rename(
        columns={
            "Total_Employee": "Total Employee",
            "Field_Saturation": "Field Saturation",
            "FB": "FB Support",
            "Total_Count": "Total Count",
        }
    )
    return out


def aggregate_udigroup(sub: pd.DataFrame, mod: pd.DataFrame) -> dict[str, float]:
    if sub.empty:
        return {
            "Total Employee": 0,
            "KB": 0,
            "Field Saturation": 0,
            "Referral": 0,
            "FB": 0,
            "TOTAL UDI": 0,
            "Marginal Cost": 0,
            "BEP": 0,
            "Baseline Productivity": 0,
            "Expected Productivity": 0,
        }
    m = mod.set_index("Employment Status")
    marg, bep, base, exp = [], [], [], []
    for _, r in sub.iterrows():
        st_ = str(r.get("Employment Status", ""))
        if st_ in m.index:
            marg.append(float(m.loc[st_, "Marginal Cost"]))
            bep.append(float(m.loc[st_, "BEP"]))
            base.append(float(m.loc[st_, "Baseline productivity"]))
            exp.append(float(m.loc[st_, "Expected Productivity"]))
        else:
            marg.append(0.0)
            bep.append(0.0)
            base.append(0.0)
            exp.append(0.0)
    return {
        "Total Employee": float(sub["correct_name"].count()),
        "KB": float(sub["KB_UDI"].sum()),
        "Field Saturation": float(sub["MR_UDI"].sum()),
        "Referral": float(sub["REFERRAL_UDI"].sum()),
        "FB": float(sub["FB_SUPPORT_UDI"].sum()),
        "TOTAL UDI": float(sub["TOTAL_UDI"].sum()),
        "Marginal Cost": float(np.sum(marg)),
        "BEP": float(np.sum(bep)),
        "Baseline Productivity": float(np.sum(base)),
        "Expected Productivity": float(np.sum(exp)),
    }


def udi_hierarchy_table(base: pd.DataFrame, mod: pd.DataFrame) -> pd.DataFrame:
    rows_out: list[dict] = []

    def add_row(label: str, d: dict[str, float], indent: bool = False) -> None:
        rows_out.append(
            {
                "Tenure Bracket": ("  " if indent else "") + label,
                "Total Employee": d["Total Employee"],
                "KB": d["KB"],
                "Field Saturation": d["Field Saturation"],
                "Referral": d["Referral"],
                "FB": d["FB"],
                "TOTAL UDI": d["TOTAL UDI"],
                "Marginal Cost": d["Marginal Cost"],
                "BEP": d["BEP"],
                "Baseline Productivity": d["Baseline Productivity"],
                "Expected Productivity": d["Expected Productivity"],
            }
        )

    probi = base[base["Employment Status"].astype(str) == "Probi"]
    regular = base[base["Employment Status"].astype(str) == "Regular"]

    if not probi.empty:
        add_row("Probi", aggregate_udigroup(probi, mod))
        for tb in PROBI_TENURES:
            sub = probi[probi["Tenure Bracket"].astype(str) == tb]
            if not sub.empty:
                add_row(tb, aggregate_udigroup(sub, mod), indent=True)

    if not regular.empty:
        add_row("Regular", aggregate_udigroup(regular, mod))
        for tb in REGULAR_TENURES:
            sub = regular[regular["Tenure Bracket"].astype(str) == tb]
            if not sub.empty:
                add_row(tb, aggregate_udigroup(sub, mod), indent=True)

    add_row("Grand Total", aggregate_udigroup(base, mod))
    return pd.DataFrame(rows_out)


def position_counts(sub: pd.DataFrame) -> pd.DataFrame:
    if sub.empty:
        return pd.DataFrame(columns=["position", "Total Employee"])
    s = sub.copy()
    s["position"] = s["position"].fillna("(blank)").astype(str)
    counts = s.groupby("position", dropna=False).size().reset_index(name="Total Employee")
    counts = counts.sort_values("Total Employee", ascending=False)
    grand = pd.DataFrame([{"position": "Grand Total", "Total Employee": int(len(s))}])
    return pd.concat([counts, grand], ignore_index=True)


def default_modifiers_from_raw(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for status in ["Regular", "Probi"]:
        sub = df[df["Employment Status"].astype(str) == status]
        if sub.empty:
            continue
        r0 = sub.iloc[0]
        rows.append(
            {
                "Employment Status": status,
                "Marginal Cost": float(r0["Marginal Cost"]),
                "BEP": float(r0["BEP"]),
                "Baseline productivity": float(r0["Baseline productivity"]),
                "Expected Productivity": float(r0["Expected Productivity"]),
            }
        )
    out = pd.DataFrame(rows) if rows else pd.DataFrame()
    defaults = {
        "Regular": (25000.0, 50000.0, 80000.0, 100000.0),
        "Probi": (25000.0, 25000.0, 80000.0, 100000.0),
    }
    for status, vals in defaults.items():
        if status not in out.get("Employment Status", pd.Series(dtype=str)).values:
            out = pd.concat(
                [
                    out,
                    pd.DataFrame(
                        [
                            {
                                "Employment Status": status,
                                "Marginal Cost": vals[0],
                                "BEP": vals[1],
                                "Baseline productivity": vals[2],
                                "Expected Productivity": vals[3],
                            }
                        ]
                    ),
                ],
                ignore_index=True,
            )
    return out.sort_values("Employment Status", ascending=True).reset_index(drop=True)


def coerce_modifier_table(mod: pd.DataFrame) -> pd.DataFrame:
    """Force numeric types after `st.data_editor` (edited cells may come back as strings)."""
    if mod.empty:
        return mod
    out = mod.copy()
    num_cols = ["Marginal Cost", "BEP", "Baseline productivity", "Expected Productivity"]
    for c in num_cols:
        if c in out.columns:
            out[c] = pd.to_numeric(out[c], errors="coerce").fillna(0.0)
    if "Employment Status" in out.columns:
        out["Employment Status"] = out["Employment Status"].astype(str)
    return out


def pivot_filter_banner(items: list[tuple[str, str]]) -> None:
    inner = " &nbsp;|&nbsp; ".join(f"<b>{k}</b>: {v}" for k, v in items)
    st.markdown(f'<div class="pivot-filters">{inner}</div>', unsafe_allow_html=True)


def plotly_pivot_table(
    df: pd.DataFrame,
    subtitle: str,
    highlight_column: str | None = None,
) -> None:
    if df.empty:
        st.caption("No rows.")
        return
    cols = list(df.columns)
    nrows, ncols = len(df), len(cols)
    header_colors = ["#4b5563"] * ncols
    if highlight_column and highlight_column in cols:
        header_colors[cols.index(highlight_column)] = "#ca8a04"
    cell_vals: list[list[str]] = []
    fill_by_col: list[list[str]] = []
    for j, cname in enumerate(cols):
        col_vals: list[str] = []
        col_fill: list[str] = []
        for r in range(nrows):
            v = df.iat[r, j]
            col_vals.append("" if pd.isna(v) else str(v))
            grand = "grand" in str(df.iat[r, 0]).lower()
            if highlight_column and cname == highlight_column:
                col_fill.append("#fef9c3")
            elif grand:
                col_fill.append("#e2e8f0")
            else:
                col_fill.append("#ffffff")
        cell_vals.append(col_vals)
        fill_by_col.append(col_fill)
    fig = go.Figure(
        data=[
            go.Table(
                columnwidth=[1.4] + [1] * (ncols - 1),
                header=dict(
                    values=cols,
                    fill_color=header_colors,
                    align="left",
                    font=dict(color="white", size=13),
                    height=32,
                ),
                cells=dict(
                    values=cell_vals,
                    fill_color=fill_by_col,
                    align="left",
                    font=dict(size=12),
                    height=26,
                ),
            )
        ]
    )
    layout = dict(
        height=min(110 + nrows * 28, 720),
        margin=dict(l=0, r=0, t=28 if not subtitle else 44, b=0),
        paper_bgcolor="#fff",
    )
    if subtitle:
        layout["title"] = dict(text=subtitle, font=dict(size=14))
    fig.update_layout(**layout)
    st.plotly_chart(fig, use_container_width=True)
