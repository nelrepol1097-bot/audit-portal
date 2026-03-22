import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LinearRegression
import time
import re

st.set_page_config(layout="wide")

# =========================================================
# 🌌 FUTURISTIC UI
# =========================================================
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Orbitron:wght@500;600;700&family=Rajdhani:wght@500;600;700&display=swap');

.stApp {
    background: linear-gradient(135deg, #0f2027, #203a43, #2c5364);
    font-family: 'Rajdhani', system-ui, sans-serif;
}
.stApp h1, .stApp h2, .stApp h3 {
    font-family: 'Orbitron', 'Rajdhani', sans-serif !important;
    font-weight: 600 !important;
    letter-spacing: 0.06em;
    color: #00eaff !important;
    text-shadow: 0 0 24px rgba(0, 234, 255, 0.35);
}

.glass {
    background: rgba(255,255,255,0.05);
    backdrop-filter: blur(12px);
    border-radius: 15px;
    padding: 20px;
    box-shadow: 0 0 20px rgba(0,255,255,0.2);
}

/* ——— Sci-fi sidebar: glass + neon ——— */
[data-testid="stSidebar"] {
    position: relative;
    background: linear-gradient(
        175deg,
        rgba(8, 22, 32, 0.94) 0%,
        rgba(18, 42, 58, 0.88) 45%,
        rgba(6, 16, 28, 0.96) 100%
    ) !important;
    border-right: 1px solid rgba(0, 234, 255, 0.35) !important;
    box-shadow:
        4px 0 28px rgba(0, 0, 0, 0.45),
        inset -1px 0 0 rgba(0, 234, 255, 0.12),
        0 0 40px rgba(0, 200, 255, 0.08) !important;
    backdrop-filter: blur(18px) saturate(1.15);
    -webkit-backdrop-filter: blur(18px) saturate(1.15);
}

[data-testid="stSidebar"] > div:first-child {
    background: transparent !important;
}

/* Sidebar chrome: subtle scan-line + top glow */
[data-testid="stSidebar"]::before {
    content: "";
    position: absolute;
    top: 0; left: 0; right: 0;
    height: 3px;
    background: linear-gradient(90deg, transparent, #00eaff, #a855f7, transparent);
    opacity: 0.85;
    pointer-events: none;
    z-index: 1;
}

/* Navigation title */
[data-testid="stSidebar"] [data-testid="stWidgetLabel"] p,
[data-testid="stSidebar"] label p {
    font-family: 'Orbitron', sans-serif !important;
    font-size: 0.95rem !important;
    font-weight: 700 !important;
    letter-spacing: 0.12em !important;
    text-transform: uppercase;
    color: #7eeeff !important;
    text-shadow: 0 0 16px rgba(0, 234, 255, 0.45);
}

/* Radio nav rows */
[data-testid="stSidebar"] .stRadio > div {
    gap: 0.35rem !important;
}

[data-testid="stSidebar"] .stRadio label {
    font-family: 'Rajdhani', sans-serif !important;
    font-size: 1.05rem !important;
    font-weight: 600 !important;
    letter-spacing: 0.04em !important;
    color: #c5eef9 !important;
    border-radius: 10px !important;
    padding: 0.55rem 0.75rem !important;
    margin: 2px 0 !important;
    border: 1px solid transparent;
    transition: color 0.2s ease, background 0.2s ease, box-shadow 0.25s ease, transform 0.2s ease, border-color 0.2s ease;
}

[data-testid="stSidebar"] .stRadio label:hover {
    color: #ffffff !important;
    background: linear-gradient(90deg, rgba(0, 234, 255, 0.12), rgba(168, 85, 247, 0.08)) !important;
    border-color: rgba(0, 234, 255, 0.35) !important;
    box-shadow: 0 0 18px rgba(0, 234, 255, 0.25), inset 0 0 20px rgba(0, 234, 255, 0.06);
    transform: translateX(4px);
    text-shadow: 0 0 12px rgba(0, 234, 255, 0.55);
}

/* Selected page */
[data-testid="stSidebar"] .stRadio label:has(input:checked) {
    color: #0a1620 !important;
    background: linear-gradient(90deg, rgba(0, 234, 255, 0.95), rgba(120, 220, 255, 0.88)) !important;
    border-color: rgba(0, 255, 255, 0.8) !important;
    box-shadow:
        0 0 22px rgba(0, 234, 255, 0.55),
        inset 0 1px 0 rgba(255, 255, 255, 0.35);
    text-shadow: none !important;
    transform: translateX(6px);
    animation: navPulse 2.4s ease-in-out infinite;
}

@keyframes navPulse {
    0%, 100% { box-shadow: 0 0 18px rgba(0, 234, 255, 0.45); }
    50%      { box-shadow: 0 0 28px rgba(168, 85, 247, 0.35); }
}

/* Hide default radio circle for cleaner “console” look (keep clickable) */
[data-testid="stSidebar"] .stRadio label input {
    accent-color: #00eaff;
}

/* Sidebar section spacing */
[data-testid="stSidebar"] .block-container {
    padding-top: 1.5rem !important;
    padding-bottom: 2rem !important;
}
</style>
""", unsafe_allow_html=True)

# =========================================================
# 🤖 STARTUP
# =========================================================
if "booted" not in st.session_state:
    st.session_state.booted = False

if not st.session_state.booted:
    boot = st.empty()
    for s in ["Initializing AI Core...","Loading Data Engine...","Launching Dashboard..."]:
        boot.markdown(f"<h2 style='text-align:center'>{s}</h2>", unsafe_allow_html=True)
        time.sleep(0.5)
    st.session_state.booted = True
    st.rerun()

# =========================================================
# 🔐 LOGIN
# =========================================================
users = {
    "admin": {"password": "1234", "role": "HR"},
    "boss": {"password": "boss123", "role": "EXECUTIVE"}
}

if "login" not in st.session_state:
    st.session_state.login = False

if not st.session_state.login:
    st.title("🔐 Login")
    u = st.text_input("Username")
    p = st.text_input("Password", type="password")

    if st.button("Login"):
        if u in users and users[u]["password"] == p:
            st.session_state.login = True
            st.session_state.role = users[u]["role"]
            st.rerun()
        else:
            st.error("Invalid login")
    st.stop()

# =========================================================
# 📌 NAVIGATION (POWER BI STYLE)
# =========================================================
page = st.sidebar.radio("📊 Navigation", [
    "🏠 Main Dashboard",
    "📉 Attrition Intelligence",
    "🎯 Hiring vs Target",
    "😊 eNPS Survey",
    "🧠 Executive Story"
])

# =========================================================
# 📂 FILE
# =========================================================
file = st.file_uploader("Upload HR Master Excel", type=["xlsx"])

if file:

    # LOAD CORE
    df = pd.read_excel(file, sheet_name="HR DATABASE")
    df.columns = df.columns.str.replace("\n"," ").str.strip().str.upper()
    df = df.loc[:, ~df.columns.duplicated()]

    # LOAD INFO
    try:
        df_info = pd.read_excel(file, sheet_name="INFO")
        df_info.columns = df_info.columns.str.upper().str.strip()
    except Exception:
        df_info = None

    # LOAD HIRING
    def safe_sheet(name):
        try:
            return pd.read_excel(file, sheet_name=name)
        except Exception:
            return None

    fc = safe_sheet("FC Hiring Actual vs Target")
    pcni = safe_sheet("PCNI Hiring Actual vs Target")
    suki = safe_sheet("SUKI Hiring Actual vs. Target")

    # =========================================================
    # AUTO DETECT
    # =========================================================
    def find_col(keys, exclude_cols=None):
        exclude = set(exclude_cols or [])
        for col in df.columns:
            if col in exclude:
                continue
            for k in keys:
                if k in col:
                    return col
        return None

    col_name = find_col(["NAME"])
    col_company = find_col(["COMPANY"])
    # Prefer CURRENT STATUS (authoritative in HR DATABASE); generic "STATUS" can be another column
    if "CURRENT STATUS" in df.columns:
        col_status = "CURRENT STATUS"
    else:
        col_status = find_col(["CURRENT STATUS", "EMPLOYMENT STATUS", "STATUS"])
    col_age = find_col(["AGE"])
    col_tenure = find_col(["TENURE"])
    col_edu = find_col(["EDUCATION"])
    col_dept = find_col(["DEPARTMENT", "DEPT"])
    col_branch = find_col(["BRANCH"])
    col_pos_group = find_col(["POSITION GROUPING", "POSITION GROUP", "POS GROUP"])
    _ex_pos = {c for c in [col_pos_group] if c}
    col_position = find_col(["JOB TITLE", "POSITION TITLE", "DESIGNATION"], exclude_cols=_ex_pos)
    if not col_position:
        col_position = find_col(["POSITION"], exclude_cols=_ex_pos)
    col_gender = find_col(["GENDER", "SEX"])
    col_civil = find_col(["CIVIL STATUS", "MARITAL STATUS", "CIVIL", "MARITAL"])
    col_role_level = find_col(["ROLE LEVEL", "JOB LEVEL", "GRADE", "BAND"])

    # CLEAN
    if col_status:
        df[col_status] = df[col_status].astype(str).str.upper().str.strip()
        df[col_status] = df[col_status].str.replace(r"\s+", " ", regex=True)

    def compute_active_mask(series):
        """Match common 'active' labels in HR exports (fixes strict == 'ACTIVE')."""
        if series is None:
            return pd.Series(False, index=df.index)
        s = series.astype(str).str.upper().str.strip().str.replace(r"\s+", " ", regex=True)
        s = s.replace({"NAN": "", "NONE": ""})
        resigned = s.str.contains(
            r"RESIGN|TERMINAT|SEPARAT|INACTIVE|QUIT|END\s*OF|LEFT",
            na=False,
            regex=True,
        )
        active_like = (
            s.eq("ACTIVE")
            | s.str.contains(r"^\s*ACTIVE\s*$", na=False, regex=True)
            | s.str.contains(r"\b(EMPLOYED|CURRENT|REGULAR|PROBATIONARY)\b", na=False, regex=True)
            | s.isin(["A", "Y", "YES", "1"])
        )
        return active_like & ~resigned

    if col_age:
        df[col_age] = pd.to_numeric(df[col_age], errors="coerce")

    def clean_tenure(val):
        try:
            n = int(re.findall(r"\d+", str(val))[0])
            return n
        except (IndexError, ValueError):
            return 0

    if col_tenure:
        df["TENURE_NUM"] = df[col_tenure].apply(clean_tenure)

    # =========================================================
    # ML
    # =========================================================
    if col_status:
        _active = compute_active_mask(df[col_status])
        df["IS_ATTRITION"] = (~_active).astype(int)
    else:
        df["IS_ATTRITION"] = 0

    features = [c for c in [col_age,"TENURE_NUM"] if c in df.columns]
    df_ml = df.dropna(subset=features)

    if not df_ml.empty and df_ml["IS_ATTRITION"].nunique()>1:
        model = RandomForestClassifier()
        model.fit(df_ml[features], df_ml["IS_ATTRITION"])
        df["RISK_SCORE"] = model.predict_proba(df[features].fillna(0))[:,1]
    else:
        df["RISK_SCORE"] = 0

    # =========================================================
    # 🏠 MAIN DASHBOARD
    # =========================================================
    if page == "🏠 Main Dashboard":

        def chart_layout(fig, title=None):
            fig.update_layout(
                template="plotly_dark",
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                font=dict(color="#e0f7ff"),
                margin=dict(l=40, r=20, t=56, b=40),
                legend=dict(bgcolor="rgba(0,0,0,0)"),
            )
            if title:
                fig.update_layout(title=dict(text=title, font=dict(size=16, color="#00eaff")))
            return fig

        def top_n_counts(series, n=15):
            vc = series.dropna().astype(str).str.strip()
            vc = vc[vc != ""]
            return vc.value_counts().head(n)

        def gender_bucket(g):
            if g is None or (isinstance(g, float) and pd.isna(g)):
                return "Unknown"
            s = str(g).upper().strip()
            if "FEMALE" in s or s in ("F", "F."):
                return "Female"
            if "MALE" in s and "FEMALE" not in s:
                return "Male"
            if s in ("M", "M."):
                return "Male"
            return "Other"

        st.title("🤖 HR EXECUTIVE DASHBOARD")

        if col_status:
            workforce_filter = st.radio(
                "Workforce",
                ["All", "Active", "Inactive"],
                horizontal=True,
                index=0,
                key="main_dashboard_workforce",
                help="Filter every KPI and chart below to active employees, inactive only, or everyone.",
            )
        else:
            workforce_filter = "All"
            st.caption("Add a **CURRENT STATUS** column in HR DATABASE to enable Active / Inactive filtering.")

        search = st.text_input("Search Employee")
        filtered_df = df.copy()

        if col_name and search:
            filtered_df = filtered_df[
                filtered_df[col_name].astype(str).str.contains(search, case=False, na=False)
            ]

        if col_company:
            opts = ["All"] + sorted(df[col_company].dropna().unique().astype(str).tolist())
            comp = st.selectbox("Company", opts)
            if comp != "All":
                filtered_df = filtered_df[filtered_df[col_company].astype(str) == comp]

        if col_status:
            _mask_all = compute_active_mask(filtered_df[col_status])
            if workforce_filter == "Active":
                filtered_df = filtered_df.loc[_mask_all].copy()
            elif workforce_filter == "Inactive":
                filtered_df = filtered_df.loc[~_mask_all].copy()

        active_mask = (
            compute_active_mask(filtered_df[col_status])
            if col_status
            else pd.Series(False, index=filtered_df.index)
        )
        total = len(filtered_df)
        active = int(active_mask.sum())
        attrition_n = max(total - active, 0)

        if col_gender:
            gb = filtered_df[col_gender].map(gender_bucket)
            male_n = int((gb == "Male").sum())
            female_n = int((gb == "Female").sum())
        else:
            male_n = female_n = 0

        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("Total", f"{total:,}")
        m2.metric("Active", f"{active:,}")
        m3.metric("Attrition (headcount)", f"{attrition_n:,}")
        m4.metric("Male", f"{male_n:,}" if col_gender else "—")
        m5.metric("Female", f"{female_n:,}" if col_gender else "—")

        if col_status and workforce_filter != "All":
            st.caption(
                f"Filtered to **{workforce_filter}** employees — **{len(filtered_df):,}** record(s). "
                "Charts below match this cohort."
            )

        st.markdown('<div class="glass">', unsafe_allow_html=True)

        row1a, row1b = st.columns(2)
        if col_gender and col_status:
            fd = filtered_df.assign(_G=filtered_df[col_gender].map(gender_bucket))
            if workforce_filter == "All":
                fd = fd.assign(_A=active_mask.map({True: "Active", False: "Attrited"}))
                ctab = fd.groupby(["_G", "_A"], dropna=False).size().reset_index(name="n")
                fig_ga = px.bar(
                    ctab,
                    x="_G",
                    y="n",
                    color="_A",
                    barmode="group",
                    color_discrete_map={"Active": "#00eaff", "Attrited": "#ff6b6b"},
                    labels={"_G": "Gender", "n": "Headcount", "_A": "Status"},
                )
                row1a.plotly_chart(
                    chart_layout(fig_ga, "Gender × Active vs attrited"),
                    use_container_width=True,
                )
            else:
                ctab = fd.groupby("_G", dropna=False).size().reset_index(name="n")
                fig_ga = px.bar(
                    ctab,
                    x="_G",
                    y="n",
                    labels={"_G": "Gender", "n": "Headcount"},
                )
                fig_ga.update_traces(marker_color="#00eaff", marker_line_color="#00eaff")
                row1a.plotly_chart(
                    chart_layout(
                        fig_ga,
                        f"Gender — {workforce_filter} only",
                    ),
                    use_container_width=True,
                )
            mix = fd["_G"].value_counts().reset_index()
            mix.columns = ["Gender", "count"]
            fig_mix = px.pie(
                mix,
                values="count",
                names="Gender",
                hole=0.45,
                color_discrete_sequence=px.colors.sequential.Teal_r,
            )
            pie_title = "Gender mix" if workforce_filter == "All" else f"Gender mix ({workforce_filter} only)"
            row1b.plotly_chart(chart_layout(fig_mix, pie_title), use_container_width=True)
        elif col_gender:
            mix = filtered_df[col_gender].map(gender_bucket).value_counts().reset_index()
            mix.columns = ["Gender", "count"]
            fig_mix = px.pie(
                mix,
                values="count",
                names="Gender",
                hole=0.45,
                color_discrete_sequence=px.colors.sequential.Teal_r,
            )
            row1a.plotly_chart(chart_layout(fig_mix, "Gender mix"), use_container_width=True)
            row1b.info("Add a STATUS column to see gender × attrition breakdown.")

        row2a, row2b = st.columns(2)
        if col_branch:
            vc = top_n_counts(filtered_df[col_branch], 15)
            fig_br = px.bar(
                x=vc.values,
                y=vc.index.astype(str),
                orientation="h",
                labels={"x": "Headcount", "y": col_branch},
            )
            row2a.plotly_chart(chart_layout(fig_br, "Branch (top 15)"), use_container_width=True)
        if col_dept:
            vc = top_n_counts(filtered_df[col_dept], 15)
            fig_dp = px.bar(
                x=vc.values,
                y=vc.index.astype(str),
                orientation="h",
                labels={"x": "Headcount", "y": col_dept},
            )
            row2b.plotly_chart(chart_layout(fig_dp, "Department (top 15)"), use_container_width=True)

        row3a, row3b = st.columns(2)
        if col_position:
            vc = top_n_counts(filtered_df[col_position], 15)
            fig_pos = px.bar(
                x=vc.values,
                y=vc.index.astype(str),
                orientation="h",
                labels={"x": "Headcount", "y": col_position},
            )
            row3a.plotly_chart(chart_layout(fig_pos, "Position (top 15)"), use_container_width=True)
        if col_pos_group:
            vc = top_n_counts(filtered_df[col_pos_group], 15)
            fig_pg = px.bar(
                x=vc.values,
                y=vc.index.astype(str),
                orientation="h",
                labels={"x": "Headcount", "y": col_pos_group},
            )
            row3b.plotly_chart(
                chart_layout(fig_pg, "Position grouping (top 15)"),
                use_container_width=True,
            )

        row4a, row4b = st.columns(2)
        if col_civil:
            vc = top_n_counts(filtered_df[col_civil], 12).reset_index()
            vc.columns = ["label", "count"]
            fig_cv = px.treemap(vc, path=["label"], values="count")
            row4a.plotly_chart(chart_layout(fig_cv, "Civil status"), use_container_width=True)
        if col_role_level:
            vc = top_n_counts(filtered_df[col_role_level], 15)
            fig_rl = px.bar(
                x=vc.values,
                y=vc.index.astype(str),
                orientation="h",
                labels={"x": "Headcount", "y": col_role_level},
            )
            row4b.plotly_chart(chart_layout(fig_rl, "Role level (top 15)"), use_container_width=True)

        if col_edu:
            edu_a, edu_b = st.columns(2)
            vc = top_n_counts(filtered_df[col_edu], 12)
            fig_ed = px.pie(
                values=vc.values,
                names=vc.index.astype(str),
                hole=0.35,
            )
            edu_a.plotly_chart(chart_layout(fig_ed, "Education mix"), use_container_width=True)
            if col_company:
                co = filtered_df[col_company].astype(str).value_counts().reset_index()
                co.columns = [col_company, "n"]
                fig_co = px.bar(
                    co,
                    x=col_company,
                    y="n",
                    labels={"n": "Headcount"},
                )
                edu_b.plotly_chart(chart_layout(fig_co, "Headcount by company"), use_container_width=True)

        st.markdown("</div>", unsafe_allow_html=True)

        with st.expander("Filtered employee table"):
            st.dataframe(filtered_df, use_container_width=True)

    # =========================================================
    # 📉 ATTRITION INTELLIGENCE
    # =========================================================
    if page == "📉 Attrition Intelligence":

        st.title("📉 Attrition Intelligence")

        if df_info is None:
            st.warning("Upload an HR Excel file that includes an **INFO** sheet with a **DATE** column and attrition series for **FC**, **PCNI**, and **SUKI** (e.g. *FC ATTRITION FTM*).")
        else:
            dfi = df_info.copy()
            dfi.columns = dfi.columns.str.upper().str.strip().str.replace(r"\s+", " ", regex=True)

            date_col = "DATE" if "DATE" in dfi.columns else next((c for c in dfi.columns if "DATE" in c), None)
            if not date_col:
                st.error("INFO sheet must include a **DATE** column.")
            else:
                dfi[date_col] = pd.to_datetime(dfi[date_col], errors="coerce")
                dfi = dfi.dropna(subset=[date_col]).sort_values(date_col)

                for _c in list(dfi.columns):
                    if _c == date_col:
                        continue
                    dfi[_c] = pd.to_numeric(
                        dfi[_c].astype(str).str.replace("%", "", regex=False).str.replace(",", "", regex=False),
                        errors="coerce",
                    )

                # Excel "Percentage" cells are often 0.0113 for 1.13% — scale to percent points for display
                for _c in list(dfi.columns):
                    if _c == date_col or "ATTRITION" not in _c.upper():
                        continue
                    mx = dfi[_c].max(skipna=True)
                    if pd.notna(mx) and float(mx) <= 1.0 + 1e-9:
                        dfi[_c] = dfi[_c] * 100.0

                def detect_company_attrition_columns(columns, date_c):
                    """Map FC / PCNI / SUKI to the best ATTRITION column (prefer FTM)."""
                    out = {"FC": None, "PCNI": None, "SUKI": None}
                    prio = {"FC": -1, "PCNI": -1, "SUKI": -1}

                    def score_name(name):
                        u = name.upper()
                        s = 0
                        if "FTM" in u or "FOR THE MONTH" in u:
                            s += 3
                        if "ATTRITION" in u:
                            s += 2
                        return s

                    for c in columns:
                        if c == date_c:
                            continue
                        u = c.upper()
                        if "ATTRITION" not in u:
                            continue
                        key = None
                        if "PCNI" in u:
                            key = "PCNI"
                        elif "SUKI" in u:
                            key = "SUKI"
                        elif u.startswith("FC ") or u.startswith("FC_") or re.search(r"\bFC\b", u):
                            if "PCNI" not in u and "SUKI" not in u:
                                key = "FC"
                        if not key:
                            continue
                        sc = score_name(c)
                        if sc > prio[key]:
                            prio[key] = sc
                            out[key] = c
                    return out

                cmap = detect_company_attrition_columns(dfi.columns, date_col)
                missing = [k for k, v in cmap.items() if v is None]
                if missing:
                    st.caption(f"No ATTRITION column auto-detected for: {', '.join(missing)}. Check INFO headers include FC / PCNI / SUKI.")

                years = sorted(dfi[date_col].dt.year.dropna().unique().astype(int).tolist())
                if not years:
                    st.error("No valid years in DATE column.")
                else:
                    c1, c2, c3 = st.columns(3)
                    with c1:
                        sel_year = st.selectbox("Filter year", years, index=len(years) - 1, key="attr_yr")
                    with c2:
                        month_opts = list(range(1, 13))
                        sub = dfi[dfi[date_col].dt.year == sel_year]
                        if not sub.empty:
                            mxs = sorted(sub[date_col].dt.month.dropna().unique().astype(int).tolist())
                            default_m = mxs[-1] if mxs else 12
                        else:
                            mxs = month_opts
                            default_m = 12
                        sel_month = st.selectbox(
                            "As-of month",
                            month_opts,
                            index=month_opts.index(default_m) if default_m in month_opts else len(month_opts) - 1,
                            key="attr_mo",
                        )
                    with c3:
                        fc_horizon = st.slider("Forecast months ahead", 1, 6, 3, key="attr_fc")

                    def series_ytd_mean(ser_date, values, year, month_through):
                        m = (ser_date.dt.year == year) & (ser_date.dt.month <= month_through)
                        v = values[m]
                        return float(v.mean()) if v.notna().any() else np.nan

                    def value_at_month(ser_date, values, year, month):
                        m = (ser_date.dt.year == year) & (ser_date.dt.month == month)
                        if not m.any():
                            return np.nan
                        return float(values[m].iloc[-1])

                    st.markdown("##### 📌 Selected month — attrition % (FTM-style)")
                    k1, k2, k3 = st.columns(3)
                    labs = [("PCNI", cmap["PCNI"], "#a855f7"), ("FC", cmap["FC"], "#00eaff"), ("SUKI", cmap["SUKI"], "#f472b6")]
                    ftm_vals = {}
                    for i, (label, col, color) in enumerate(labs):
                        box = [k1, k2, k3][i]
                        with box:
                            if col and col in dfi.columns:
                                v = value_at_month(dfi[date_col], dfi[col], sel_year, sel_month)
                                ftm_vals[label] = v
                                st.metric(
                                    f"{label} Attrition FTM",
                                    f"{v:.2f}%" if pd.notna(v) else "—",
                                )
                            else:
                                ftm_vals[label] = np.nan
                                st.metric(f"{label} Attrition FTM", "—")

                    st.markdown("##### 📊 YTD vs prior-year YTD (avg. monthly attrition %, Jan → selected month)")
                    y1, y2, y3 = st.columns(3)
                    ytd_rows = []
                    for i, (label, col, color) in enumerate(labs):
                        with [y1, y2, y3][i]:
                            if col and col in dfi.columns:
                                ytd_cy = series_ytd_mean(dfi[date_col], dfi[col], sel_year, sel_month)
                                ytd_py = series_ytd_mean(dfi[date_col], dfi[col], sel_year - 1, sel_month)
                                delta = None
                                if pd.notna(ytd_cy) and pd.notna(ytd_py):
                                    delta = ytd_cy - ytd_py
                                st.metric(
                                    f"{label} YTD",
                                    f"{ytd_cy:.2f}%" if pd.notna(ytd_cy) else "—",
                                    delta=f"{delta:+.2f} pp vs Y-1 YTD" if delta is not None and pd.notna(delta) else None,
                                    delta_color="inverse",
                                )
                                ytd_rows.append((label, ytd_cy, ytd_py, delta))
                            else:
                                st.metric(f"{label} YTD", "—")

                    # --- Dynamic insight ---
                    insight_parts = []
                    valid_ftm = {k: v for k, v in ftm_vals.items() if pd.notna(v)}
                    if valid_ftm:
                        worst = max(valid_ftm, key=valid_ftm.get)
                        best = min(valid_ftm, key=valid_ftm.get)
                        insight_parts.append(
                            f"**{worst}** shows the highest **monthly** attrition ({valid_ftm[worst]:.2f}%) in **{sel_year}-{sel_month:02d}**; **{best}** is lowest ({valid_ftm[best]:.2f}%)."
                        )
                    for label, ytd_cy, ytd_py, delta in ytd_rows:
                        if delta is not None and pd.notna(delta):
                            insight_parts.append(
                                f"**{label} YTD** vs same period last year: **{'up' if delta > 0 else 'down'} {abs(delta):.2f} pp**."
                            )
                    if insight_parts:
                        st.markdown(
                            '<div class="glass"><h4 style="margin-top:0">🧠 Dynamic insight</h4><p style="font-size:1.05rem">'
                            + " ".join(insight_parts)
                            + "</p></div>",
                            unsafe_allow_html=True,
                        )

                    # --- Monthly lines + forecast ---
                    st.markdown("##### 📈 Monthly attrition trend & forecast")

                    def build_forecast(dates, y, horizon):
                        """Linear trend on last up to 24 points; extend dates monthly."""
                        dfv = pd.DataFrame({"ds": dates, "y": y}).dropna()
                        if len(dfv) < 3:
                            return None, None
                        tail = dfv.tail(24).reset_index(drop=True)
                        X = np.arange(len(tail)).reshape(-1, 1)
                        yv = tail["y"].values
                        model = LinearRegression().fit(X, yv)
                        last_date = tail["ds"].max()
                        future_dates = []
                        for h in range(1, horizon + 1):
                            future_dates.append(last_date + pd.DateOffset(months=h))
                        Xf = np.arange(len(tail), len(tail) + horizon).reshape(-1, 1)
                        yhat = model.predict(Xf)
                        return tail, (future_dates, yhat)

                    fig = go.Figure()
                    colors = {"PCNI": "#a855f7", "FC": "#00eaff", "SUKI": "#f472b6"}
                    for label, col, _ in labs:
                        if not col or col not in dfi.columns:
                            continue
                        sub = dfi[[date_col, col]].dropna().sort_values(date_col)
                        if sub.empty:
                            continue
                        fig.add_trace(
                            go.Scatter(
                                x=sub[date_col],
                                y=sub[col],
                                mode="lines+markers",
                                name=f"{label} (actual)",
                                line=dict(width=2.5, color=colors.get(label, "#ccc")),
                                marker=dict(size=6),
                                hovertemplate="%{x|%Y-%m}<br>%{y:.2f}%<extra>" + label + "</extra>",
                            )
                        )
                        tail, fc = build_forecast(sub[date_col].values, sub[col].values, fc_horizon)
                        if fc is not None:
                            fd, yhat = fc
                            fig.add_trace(
                                go.Scatter(
                                    x=fd,
                                    y=yhat,
                                    mode="lines+markers",
                                    name=f"{label} (forecast)",
                                    line=dict(width=2, dash="dash", color=colors.get(label, "#ccc")),
                                    opacity=0.85,
                                    hovertemplate="%{x|%Y-%m}<br>forecast %{y:.2f}%<extra>" + label + "</extra>",
                                )
                            )

                    fig.update_layout(
                        template="plotly_dark",
                        paper_bgcolor="rgba(0,0,0,0)",
                        plot_bgcolor="rgba(0,0,0,0)",
                        font=dict(color="#e0f7ff"),
                        hovermode="x unified",
                        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                        xaxis=dict(showgrid=True, gridcolor="rgba(0,234,255,0.12)"),
                        yaxis=dict(title="Attrition %", showgrid=True, gridcolor="rgba(0,234,255,0.12)"),
                        title=dict(text="Attrition % over time — actuals + linear forecast", font=dict(size=16, color="#00eaff")),
                        margin=dict(l=50, r=30, t=70, b=50),
                    )
                    st.plotly_chart(fig, use_container_width=True)

                    # --- By calendar year (avg) comparison ---
                    st.markdown("##### 🗓️ Average attrition by year (full-year & partial)")
                    yearly_rows = []
                    for label, col, _ in labs:
                        if not col or col not in dfi.columns:
                            continue
                        for yr, grp in dfi.groupby(dfi[date_col].dt.year):
                            yearly_rows.append(
                                {"Company": label, "Year": int(yr), "Avg attrition %": grp[col].mean()}
                            )
                    if yearly_rows:
                        ydf = pd.DataFrame(yearly_rows).dropna(subset=["Avg attrition %"])
                        if not ydf.empty:
                            fig_y = px.bar(
                                ydf,
                                x="Year",
                                y="Avg attrition %",
                                color="Company",
                                barmode="group",
                                color_discrete_map=colors,
                            )
                            fig_y.update_layout(
                                template="plotly_dark",
                                paper_bgcolor="rgba(0,0,0,0)",
                                plot_bgcolor="rgba(0,0,0,0)",
                                font=dict(color="#e0f7ff"),
                                xaxis=dict(type="category"),
                                title=dict(text="Year-over-year average (from INFO monthly data)", font=dict(color="#00eaff")),
                            )
                            st.plotly_chart(fig_y, use_container_width=True)

                    with st.expander("Raw INFO data"):
                        st.dataframe(dfi, use_container_width=True)

    # =========================================================
    # 🎯 HIRING
    # =========================================================
    if page == "🎯 Hiring vs Target":

        def hiring_chart_layout(fig, title=None):
            fig.update_layout(
                template="plotly_dark",
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                font=dict(color="#e0f7ff"),
                margin=dict(l=50, r=30, t=60, b=50),
                legend=dict(bgcolor="rgba(0,0,0,0)"),
            )
            if title:
                fig.update_layout(title=dict(text=title, font=dict(size=16, color="#00eaff")))
            return fig

        def normalize_hiring_sheet(raw):
            if raw is None or raw.empty:
                return None
            t = raw.copy()
            t.columns = t.columns.str.replace("\n", " ").str.strip().str.upper()
            t.columns = t.columns.str.replace(r"\s+", " ", regex=True)
            return t

        def find_hiring_date_col(cols):
            for hint in ["DATE", "AS OF", "PERIOD", "MONTH END", "MONTH-YEAR", "YEAR"]:
                for c in cols:
                    if hint in c:
                        return c
            return None

        def find_category_pairs(cols):
            """Match Active/Target pairs for FC-style (Branch MRs, OMR, Branch OIC) or legacy names."""
            pairs = []
            specs = [
                (
                    "Branch MRs",
                    lambda u: (
                        ("BRANCH" in u and ("MR" in u or "MRS" in u) and "OIC" not in u)
                        or "CIVILIAN" in u
                    ),
                ),
                (
                    "OMR",
                    lambda u: (
                        ("OMR" in u and "BRANCH OIC" not in u and not ("BRANCH" in u and "OIC" in u))
                        or "CARAVAN" in u
                    ),
                ),
                (
                    "Branch OIC",
                    lambda u: (("BRANCH" in u and "OIC" in u) or "SUPPORT" in u),
                ),
            ]
            for label, pred in specs:
                act = tgt = None
                for c in cols:
                    if "TOTAL" in c:
                        continue
                    u = c.replace("(", " ").replace(")", " ").upper()
                    if not pred(u):
                        continue
                    cu = c.upper()
                    if "ACTIVE" in cu and "TARGET" not in cu:
                        act = c
                    if "TARGET" in cu:
                        tgt = c
                if act and tgt:
                    pairs.append((label, act, tgt))
            return pairs

        def find_total_cols(cols):
            ta = next((c for c in cols if "TOTAL" in c and "ACTIVE" in c), None)
            tt = next((c for c in cols if "TOTAL" in c and "TARGET" in c and "ACTIVE" not in c), None)
            if tt is None:
                tt = next((c for c in cols if "TOTAL" in c and "TARGET" in c), None)
            return ta, tt

        def prepare_info_for_attrition_snippet(info_df):
            """Return processed INFO + company attrition column map (same idea as Attrition page)."""
            if info_df is None or info_df.empty:
                return None, None, None
            dfi = info_df.copy()
            dfi.columns = dfi.columns.str.upper().str.strip().str.replace(r"\s+", " ", regex=True)
            date_col = "DATE" if "DATE" in dfi.columns else next((c for c in dfi.columns if "DATE" in c), None)
            if not date_col:
                return None, None, None
            dfi[date_col] = pd.to_datetime(dfi[date_col], errors="coerce")
            dfi = dfi.dropna(subset=[date_col]).sort_values(date_col)
            for _c in list(dfi.columns):
                if _c == date_col:
                    continue
                dfi[_c] = pd.to_numeric(
                    dfi[_c].astype(str).str.replace("%", "", regex=False).str.replace(",", "", regex=False),
                    errors="coerce",
                )
            for _c in list(dfi.columns):
                if _c == date_col or "ATTRITION" not in _c.upper():
                    continue
                mx = dfi[_c].max(skipna=True)
                if pd.notna(mx) and float(mx) <= 1.0 + 1e-9:
                    dfi[_c] = dfi[_c] * 100.0

            cmap = {"FC": None, "PCNI": None, "SUKI": None}
            prio = {"FC": -1, "PCNI": -1, "SUKI": -1}

            def score_name(name):
                u = name.upper()
                s = 0
                if "FTM" in u:
                    s += 3
                if "ATTRITION" in u:
                    s += 2
                return s

            for c in dfi.columns:
                if c == date_col or "ATTRITION" not in c.upper():
                    continue
                u = c.upper()
                key = None
                if "PCNI" in u:
                    key = "PCNI"
                elif "SUKI" in u:
                    key = "SUKI"
                elif (u.startswith("FC ") or u.startswith("FC_") or re.search(r"\bFC\b", u)) and "PCNI" not in u:
                    key = "FC"
                if not key:
                    continue
                sc = score_name(c)
                if sc > prio[key]:
                    prio[key] = sc
                    cmap[key] = c
            return dfi, date_col, cmap

        def attrition_ftm_and_ytd(dfi, date_col, attr_col, year, month):
            if dfi is None or attr_col is None or attr_col not in dfi.columns:
                return np.nan, np.nan
            sdt = dfi[date_col]
            m = (sdt.dt.year == year) & (sdt.dt.month == month)
            ftm = float(dfi.loc[m, attr_col].iloc[-1]) if m.any() else np.nan
            m_ytd = (sdt.dt.year == year) & (sdt.dt.month <= month)
            ytd = float(dfi.loc[m_ytd, attr_col].mean()) if m_ytd.any() else np.nan
            return ftm, ytd

        st.title("🎯 Hiring vs Target")

        hiring_tables = {"FC": fc, "PCNI": pcni, "SUKI": suki}

        sel_company = st.selectbox("Company", ["FC", "PCNI", "SUKI"], index=0, key="hiring_company")

        raw_tbl = hiring_tables.get(sel_company)
        ht = normalize_hiring_sheet(raw_tbl)
        dfi_a = None
        dcol = None
        acol = None

        if ht is None:
            st.warning(
                f"No **{sel_company}** hiring sheet found. Expected one of: "
                "`FC Hiring Actual vs Target`, `PCNI Hiring Actual vs Target`, `SUKI Hiring Actual vs. Target`."
            )
        else:
            date_h = find_hiring_date_col(ht.columns.tolist())
            for c in ht.columns:
                if date_h and c == date_h:
                    ht[c] = pd.to_datetime(ht[c], errors="coerce")
                elif c != date_h:
                    ht[c] = pd.to_numeric(ht[c], errors="coerce")

            years = sorted(ht[date_h].dt.year.dropna().unique().astype(int).tolist()) if date_h else []
            if date_h and years:
                cy, cm = st.columns(2)
                with cy:
                    sel_y = st.selectbox("Year", years, index=len(years) - 1, key="hiring_y")
                with cm:
                    suby = ht[ht[date_h].dt.year == sel_y]
                    months_avail = sorted(suby[date_h].dt.month.dropna().unique().astype(int).tolist()) if not suby.empty else list(range(1, 13))
                    default_m = months_avail[-1] if months_avail else 12
                    sel_m = st.selectbox(
                        "Month",
                        list(range(1, 13)),
                        index=default_m - 1 if default_m in range(1, 13) else 0,
                        format_func=lambda m: pd.Timestamp(2000, m, 1).strftime("%B"),
                        key="hiring_m",
                    )
                row_mask = (ht[date_h].dt.year == sel_y) & (ht[date_h].dt.month == sel_m)
                if row_mask.any():
                    row = ht.loc[row_mask].iloc[-1]
                else:
                    st.info(f"No hiring row for **{sel_y}-{sel_m:02d}**. Showing latest available row.")
                    row = ht.sort_values(date_h).iloc[-1]
            else:
                row = ht.iloc[-1]
                sel_y = sel_m = None
                if date_h:
                    try:
                        ts = pd.to_datetime(row[date_h], errors="coerce")
                        if pd.notna(ts):
                            sel_y, sel_m = int(ts.year), int(ts.month)
                    except (TypeError, ValueError):
                        pass
                st.caption(
                    "No usable **date** column or no year values — using the **last row** as snapshot."
                    if not date_h or not years
                    else "Could not parse dates — using the **last row** as snapshot."
                )

            cats = find_category_pairs(ht.columns.tolist())
            t_act_col, t_tgt_col = find_total_cols(ht.columns.tolist())

            # --- Highlight totals ---
            st.markdown("##### ⭐ Headline hiring position (totals)")
            t1, t2, t3 = st.columns([1, 1, 2])
            with t1:
                if t_act_col and pd.notna(row.get(t_act_col)):
                    st.metric("**Total active employees**", f"{int(row[t_act_col]):,}")
                else:
                    st.metric("**Total active employees**", "—")
            with t2:
                if t_tgt_col and pd.notna(row.get(t_tgt_col)):
                    st.metric("**Total target employees**", f"{int(row[t_tgt_col]):,}")
                else:
                    st.metric("**Total target employees**", "—")
            with t3:
                if t_act_col and t_tgt_col and pd.notna(row.get(t_act_col)) and pd.notna(row.get(t_tgt_col)):
                    gap = float(row[t_act_col]) - float(row[t_tgt_col])
                    st.metric("Gap (active − target)", f"{gap:+,}", help="Positive = ahead of hiring target.")

            # --- Attrition from INFO (aligned company) ---
            st.markdown("##### 📉 Attrition context (from INFO sheet)")
            inf = prepare_info_for_attrition_snippet(df_info)
            if inf[0] is not None:
                dfi_a, dcol, cmap = inf
                acol = cmap.get(sel_company) if cmap else None
                if acol and sel_y and sel_m:
                    ftm_a, ytd_a = attrition_ftm_and_ytd(dfi_a, dcol, acol, sel_y, sel_m)
                    a1, a2 = st.columns(2)
                    with a1:
                        st.metric(
                            "Attrition — FTM (this month %)",
                            f"{ftm_a:.2f}%" if pd.notna(ftm_a) else "—",
                        )
                    with a2:
                        st.metric(
                            "Attrition — YTD avg (Jan → month %)",
                            f"{ytd_a:.2f}%" if pd.notna(ytd_a) else "—",
                            help="Average of monthly attrition % from January through the selected month.",
                        )
                else:
                    st.caption("Could not match an attrition series for this company on the INFO sheet.")
            else:
                st.caption("Add an **INFO** sheet with monthly attrition columns (e.g. *FC ATTRITION FTM*) to see attrition here.")

            # --- Category chart ---
            st.markdown("##### 📊 Active vs target by position type")
            if cats:
                names = []
                act_v = []
                tgt_v = []
                for label, ca, ct in cats:
                    names.append(label)
                    act_v.append(float(row[ca]) if pd.notna(row.get(ca)) else np.nan)
                    tgt_v.append(float(row[ct]) if pd.notna(row.get(ct)) else np.nan)
                plot_df = pd.DataFrame(
                    {"Position type": names * 2, "Series": ["Active"] * len(names) + ["Target"] * len(names), "Headcount": act_v + tgt_v}
                )
                fig_h = px.bar(
                    plot_df,
                    x="Position type",
                    y="Headcount",
                    color="Series",
                    barmode="group",
                    color_discrete_map={"Active": "#00eaff", "Target": "#a855f7"},
                )
                st.plotly_chart(hiring_chart_layout(fig_h, "Hiring actual vs target by category"), use_container_width=True)

                gap_vals = []
                gap_colors = []
                for a, t in zip(act_v, tgt_v):
                    if pd.isna(a) or pd.isna(t):
                        gap_vals.append(0)
                        gap_colors.append("#888")
                    else:
                        gap_vals.append(float(a - t))
                        gap_colors.append("#00eaff" if a >= t else "#ff6b6b")
                fig_gap = go.Figure(
                    data=[
                        go.Bar(
                            x=names,
                            y=gap_vals,
                            marker_color=gap_colors,
                            text=[f"{v:+.0f}" for v in gap_vals],
                            textposition="outside",
                        )
                    ]
                )
                fig_gap.update_yaxes(title="Active − target")
                st.plotly_chart(
                    hiring_chart_layout(fig_gap, "Gap by position type (positive = above target)"),
                    use_container_width=True,
                )
            else:
                st.info(
                    "Could not detect *Active/Target* pairs for **Branch MRs**, **OMR**, **Branch OIC** "
                    "(or legacy Civilian / Caravan / Support). Check column headers match your FC sheet."
                )

            # --- Storytelling hierarchy ---
            st.markdown("##### 📖 Executive narrative")
            story_parts = []
            if date_h and sel_y and sel_m:
                story_parts.append(
                    f"**{sel_company}** — **{pd.Timestamp(sel_y, sel_m, 1).strftime('%B %Y')}**."
                )
            else:
                story_parts.append(f"**{sel_company}** — latest snapshot row.")
            if t_act_col and t_tgt_col and pd.notna(row.get(t_act_col)) and pd.notna(row.get(t_tgt_col)):
                ta, tt = float(row[t_act_col]), float(row[t_tgt_col])
                story_parts.append(
                    f"Total active **{ta:,.0f}** vs target **{tt:,.0f}** "
                    f"({'ahead of plan' if ta >= tt else 'below plan'} by **{abs(ta - tt):,.0f}** FTE)."
                )
            if cats:
                for label, ca, ct in cats:
                    if pd.notna(row.get(ca)) and pd.notna(row.get(ct)):
                        a, t = float(row[ca]), float(row[ct])
                        story_parts.append(f"**{label}:** active {a:,.0f} vs target {t:,.0f}.")
            if (
                inf[0] is not None
                and dfi_a is not None
                and dcol
                and acol
                and sel_y is not None
                and sel_m is not None
            ):
                ftm_n, ytd_n = attrition_ftm_and_ytd(dfi_a, dcol, acol, sel_y, sel_m)
                if pd.notna(ftm_n):
                    ytd_str = f"{ytd_n:.2f}%" if pd.notna(ytd_n) else "n/a"
                    story_parts.append(
                        f"Monthly attrition (**FTM**) is **{ftm_n:.2f}%**; **YTD average** (Jan–selected month) is **{ytd_str}**."
                    )
            st.markdown(
                '<div class="glass"><p style="font-size:1.05rem;line-height:1.6">'
                + "<br><br>".join(story_parts)
                + "</p></div>",
                unsafe_allow_html=True,
            )

            with st.expander("Raw hiring data (this company)"):
                st.dataframe(ht, use_container_width=True)

    # =========================================================
    # 😊 ENPS
    # =========================================================
    if page == "😊 eNPS Survey":

        st.title("😊 eNPS Report")
        st.caption(
            "Upload **one or more** Excel files. Each workbook can include **multiple sheets** — "
            "everything is combined with **Source file** and **Sheet** columns."
        )

        def load_one_enps_workbook(uploaded_file):
            """Read all sheets from an Excel upload; tag rows with sheet name and file name."""
            name = uploaded_file.name
            try:
                book = pd.read_excel(uploaded_file, sheet_name=None)
            except Exception as e:
                raise RuntimeError(str(e)) from e
            if not isinstance(book, dict):
                df = book.copy()
                df["_SHEET"] = "(default)"
                df["_SOURCE_FILE"] = name
                return df
            frames = []
            for sheet_name, sdf in book.items():
                if sdf is None or (isinstance(sdf, pd.DataFrame) and sdf.empty):
                    continue
                sdf = sdf.copy()
                sdf["_SHEET"] = str(sheet_name)
                sdf["_SOURCE_FILE"] = name
                frames.append(sdf)
            if not frames:
                return pd.DataFrame({"_SOURCE_FILE": [name], "_SHEET": ["(empty)"]})
            return pd.concat(frames, ignore_index=True, sort=False)

        enps_files = st.file_uploader(
            "Upload eNPS file(s)",
            type=["xlsx", "xls"],
            accept_multiple_files=True,
            help="Select multiple .xlsx/.xls files at once (Ctrl+click or Shift+click).",
        )

        if enps_files:
            all_parts = []
            errors = []
            for f in enps_files:
                try:
                    all_parts.append(load_one_enps_workbook(f))
                except Exception as ex:
                    errors.append((f.name, str(ex)))
            if errors:
                for fn, msg in errors:
                    st.error(f"**{fn}** — {msg}")
            if all_parts:
                enps_all = pd.concat(all_parts, ignore_index=True, sort=False)
                m1, m2, m3 = st.columns(3)
                m1.metric("Files", len(enps_files))
                m2.metric("Total rows", f"{len(enps_all):,}")
                m3.metric("Columns", len(enps_all.columns))

                if "_SOURCE_FILE" in enps_all.columns:
                    src_counts = enps_all["_SOURCE_FILE"].value_counts()
                    with st.expander("Rows per file", expanded=False):
                        st.dataframe(
                            src_counts.rename_axis("Source file").reset_index(name="Rows"),
                            use_container_width=True,
                            hide_index=True,
                        )
                if "_SHEET" in enps_all.columns:
                    sh_counts = enps_all.groupby(["_SOURCE_FILE", "_SHEET"], dropna=False).size().reset_index(name="Rows")
                    with st.expander("Rows per sheet", expanded=False):
                        st.dataframe(sh_counts, use_container_width=True, hide_index=True)

                filt1, filt2 = st.columns(2)
                with filt1:
                    if "_SOURCE_FILE" in enps_all.columns:
                        ufs = ["(All)"] + sorted(enps_all["_SOURCE_FILE"].dropna().unique().astype(str).tolist())
                        pick_f = st.selectbox("Filter by source file", ufs, key="enps_filt_file")
                    else:
                        pick_f = "(All)"
                with filt2:
                    if "_SHEET" in enps_all.columns:
                        ush = ["(All)"] + sorted(enps_all["_SHEET"].dropna().unique().astype(str).tolist())
                        pick_s = st.selectbox("Filter by sheet", ush, key="enps_filt_sheet")
                    else:
                        pick_s = "(All)"

                view = enps_all
                if pick_f != "(All)" and "_SOURCE_FILE" in view.columns:
                    view = view[view["_SOURCE_FILE"].astype(str) == pick_f]
                if pick_s != "(All)" and "_SHEET" in view.columns:
                    view = view[view["_SHEET"].astype(str) == pick_s]

                def enps_find_column(columns, patterns_list, exclude_prefix="_"):
                    """Return first column whose upper name matches all substrings in one pattern group."""
                    for pats in patterns_list:
                        for c in columns:
                            if str(c).startswith(exclude_prefix):
                                continue
                            u = str(c).upper()
                            if all(p in u for p in pats):
                                return c
                    return None

                def enps_level_from_score(x):
                    if pd.isna(x):
                        return "No score"
                    try:
                        s = int(float(x))
                    except (TypeError, ValueError):
                        return "No score"
                    if s >= 9:
                        return "Promoter"
                    if s >= 7:
                        return "Passive"
                    return "Detractor"

                def enps_chart_layout(fig, title=None):
                    fig.update_layout(
                        template="plotly_dark",
                        paper_bgcolor="rgba(0,0,0,0)",
                        plot_bgcolor="rgba(0,0,0,0)",
                        font=dict(color="#e0f7ff"),
                        margin=dict(l=50, r=30, t=56, b=50),
                    )
                    if title:
                        fig.update_layout(title=dict(text=title, font=dict(size=15, color="#00eaff")))
                    return fig

                rep = view.copy()
                score_col = enps_find_column(
                    rep.columns,
                    [
                        ["RECOMMEND", "WORKPLACE"],
                        ["RECOMMEND", "10"],
                        ["LIKELY", "10"],
                        ["SCALE", "10"],
                        ["1 TO 10"],
                    ],
                )
                if not score_col:
                    score_col = enps_find_column(rep.columns, [["10"], ["SCALE"]])

                area_col = enps_find_column(rep.columns, [["AREA"], ["INDICATE", "AREA"]])
                branch_col = enps_find_column(rep.columns, [["BRANCH"]])
                reason_col = enps_find_column(
                    rep.columns,
                    [["PRIMARY", "REASON"],
                     ["REASON", "RATING"],
                     ["REASON FOR YOUR"]],
                )
                ts_col = enps_find_column(
                    rep.columns,
                    [["TIMESTAMP"], ["SUBMITTED"], ["DATE", "SUBMIT"]],
                )
                if not ts_col and len(rep.columns) > 0:
                    try:
                        cand = rep.columns[0]
                        _probe = pd.to_datetime(rep[cand].head(20), errors="coerce")
                        if _probe.notna().sum() >= max(3, len(_probe) // 3):
                            ts_col = cand
                    except Exception:
                        pass

                st.markdown("---")
                st.subheader("📊 eNPS analytics report")

                if not score_col:
                    st.warning(
                        "Could not find the **1–10 recommendation** column. "
                        "Name it with words like *scale*, *10*, *recommend*, or *workplace*."
                    )
                    st.dataframe(view, use_container_width=True, height=420)
                else:
                    rep["_SCORE"] = pd.to_numeric(rep[score_col], errors="coerce")
                    rep["Level"] = rep["_SCORE"].apply(enps_level_from_score)
                    valid = rep.dropna(subset=["_SCORE"])
                    n = len(valid)
                    if n == 0:
                        st.error("No numeric scores in the selected data.")
                        st.dataframe(rep, use_container_width=True, height=360)
                    else:
                        prom = int((valid["_SCORE"] >= 9).sum())
                        pas = int(((valid["_SCORE"] >= 7) & (valid["_SCORE"] <= 8)).sum())
                        det = int((valid["_SCORE"] <= 6).sum())
                        p_pct = 100.0 * prom / n
                        pas_pct = 100.0 * pas / n
                        d_pct = 100.0 * det / n
                        enps_score = (p_pct - d_pct)

                        st.markdown('<div class="glass">', unsafe_allow_html=True)
                        c1, c2, c3, c4, c5 = st.columns(5)
                        c1.metric("eNPS score", f"{enps_score:.1f}", help="(% Promoters − % Detractors) × 100")
                        c2.metric("Responses (valid)", f"{n:,}")
                        c3.metric("% Promoters (9–10)", f"{p_pct:.1f}%")
                        c4.metric("% Passives (7–8)", f"{pas_pct:.1f}%")
                        c5.metric("% Detractors (0–6)", f"{d_pct:.1f}%")
                        st.markdown("</div>", unsafe_allow_html=True)

                        st.caption(
                            f"Score column detected: **{score_col}** · "
                            "Levels: **Promoter** (9–10), **Passive** (7–8), **Detractor** (0–6)."
                        )

                        g1, g2 = st.columns(2)
                        with g1:
                            lvc = valid["Level"].value_counts()
                            ldf = lvc.reset_index()
                            ldf.columns = ["Level", "Count"]
                            fig_lvl = px.bar(
                                ldf,
                                x="Level",
                                y="Count",
                                color="Level",
                                color_discrete_map={
                                    "Promoter": "#34d399",
                                    "Passive": "#fbbf24",
                                    "Detractor": "#f87171",
                                    "No score": "#94a3b8",
                                },
                            )
                            fig_lvl.update_layout(showlegend=False)
                            st.plotly_chart(
                                enps_chart_layout(fig_lvl, "Respondents by eNPS level"),
                                use_container_width=True,
                            )
                        with g2:
                            fig_pie = px.pie(
                                values=[p_pct, pas_pct, d_pct],
                                names=["Promoters", "Passives", "Detractors"],
                                hole=0.45,
                                color_discrete_sequence=["#34d399", "#fbbf24", "#f87171"],
                            )
                            st.plotly_chart(
                                enps_chart_layout(fig_pie, "Share of respondents (%)"),
                                use_container_width=True,
                            )

                        st.subheader("📅 Quarterly report heatmap (from your eNPS file)")
                        st.caption(
                            "Built from the **Timestamp** on each response. "
                            "Shows average **1–10 score** by **calendar quarter** and **Area** or **Branch**."
                        )
                        hm_dim = st.radio(
                            "Quarterly heatmap splits by",
                            ["Area", "Branch"],
                            horizontal=True,
                            key="enps_hm_quarter_dim",
                        )
                        dim_c = area_col if hm_dim == "Area" else branch_col
                        if ts_col and dim_c:
                            hq = valid.copy()
                            hq["_TS"] = pd.to_datetime(hq[ts_col], errors="coerce")
                            hq = hq.dropna(subset=["_TS"])
                            if hq.empty:
                                st.warning("No valid dates in the timestamp column for quarterly view.")
                            else:
                                hq["_QUARTER"] = (
                                    hq["_TS"].dt.year.astype(str)
                                    + " Q"
                                    + hq["_TS"].dt.quarter.astype(str)
                                )
                                hq["_DIM"] = hq[dim_c].astype(str).str.strip().replace({"nan": "—", "None": "—"})
                                pvq = hq.pivot_table(
                                    index="_QUARTER",
                                    columns="_DIM",
                                    values="_SCORE",
                                    aggfunc="mean",
                                )
                                if not pvq.empty and pvq.notna().sum().sum() > 0:
                                    try:
                                        q_order = sorted(
                                            pvq.index.tolist(),
                                            key=lambda s: (int(str(s).split()[0]), int(str(s).split()[-1].replace("Q", ""))),
                                        )
                                        pvq = pvq.reindex(q_order)
                                        fig_q = px.imshow(
                                            pvq,
                                            labels=dict(
                                                x=hm_dim,
                                                y="Quarter",
                                                color="Avg score",
                                            ),
                                            aspect="auto",
                                            color_continuous_scale="Teal",
                                            zmin=0,
                                            zmax=10,
                                        )
                                        st.plotly_chart(
                                            enps_chart_layout(
                                                fig_q,
                                                f"Average score by quarter × {hm_dim} (report data)",
                                            ),
                                            use_container_width=True,
                                        )
                                    except Exception:
                                        st.caption("Could not render quarterly heatmap (try another dimension or check dates).")
                                else:
                                    st.caption("Not enough quarter × category data for a heatmap.")
                                tbl_q = (
                                    hq.groupby(["_QUARTER", "_DIM"], dropna=False)["_SCORE"]
                                    .agg(["mean", "count"])
                                    .reset_index()
                                    .rename(columns={"mean": "Avg score", "count": "Responses"})
                                )
                                if not tbl_q.empty:
                                    with st.expander(
                                        "Quarter × category table (avg score & response count)",
                                        expanded=False,
                                    ):
                                        st.dataframe(tbl_q, use_container_width=True, hide_index=True)
                        elif not ts_col:
                            st.warning(
                                "No **Timestamp** column found — add a *Timestamp* / *Date* column to enable **quarterly** heatmaps."
                            )
                        else:
                            st.warning(f"Select **{hm_dim}** is missing in the file — cannot build quarterly heatmap.")

                        with st.expander("Optional: Area × Branch heatmap (all periods combined)", expanded=False):
                            if area_col and branch_col:
                                hb = valid.copy()
                                hb["_A"] = hb[area_col].astype(str).str.strip().replace({"nan": "—"})
                                hb["_B"] = hb[branch_col].astype(str).str.strip().replace({"nan": "—"})
                                pv = hb.pivot_table(
                                    index="_A",
                                    columns="_B",
                                    values="_SCORE",
                                    aggfunc="mean",
                                )
                                if (
                                    not pv.empty
                                    and pv.shape[0] > 0
                                    and pv.shape[1] > 0
                                    and pv.notna().sum().sum() > 0
                                ):
                                    try:
                                        fig_hm = px.imshow(
                                            pv,
                                            labels=dict(x="Branch", y="Area", color="Avg score"),
                                            aspect="auto",
                                            color_continuous_scale="Teal",
                                            zmin=0,
                                            zmax=10,
                                        )
                                        st.plotly_chart(
                                            enps_chart_layout(fig_hm, "Average score — Area × Branch (all dates)"),
                                            use_container_width=True,
                                        )
                                    except Exception:
                                        st.caption("Could not render Area × Branch heatmap.")
                                else:
                                    st.caption("Not enough Area × Branch combinations.")
                            elif area_col:
                                by_a = valid.groupby(valid[area_col].astype(str), dropna=False)["_SCORE"].mean().sort_values(ascending=False)
                                fig_a = px.bar(
                                    x=by_a.index.astype(str),
                                    y=by_a.values,
                                    labels={"x": "Area", "y": "Avg score"},
                                    color=by_a.values,
                                    color_continuous_scale="Teal",
                                )
                                st.plotly_chart(
                                    enps_chart_layout(fig_a, "Average score by area"),
                                    use_container_width=True,
                                )
                            else:
                                st.caption("Add **Area** / **Branch** columns for this optional view.")

                        st.subheader("💬 Primary reason for rating")
                        if reason_col:
                            reasons = (
                                valid[reason_col]
                                .dropna()
                                .astype(str)
                                .str.strip()
                            )
                            reasons = reasons[reasons.str.len() > 0]
                            if not reasons.empty:
                                top_r = reasons.value_counts().head(18)
                                fig_r = px.bar(
                                    x=top_r.values,
                                    y=top_r.index.astype(str),
                                    orientation="h",
                                    labels={"x": "Count", "y": "Reason"},
                                )
                                st.plotly_chart(
                                    enps_chart_layout(fig_r, "Top reasons (text)"),
                                    use_container_width=True,
                                )
                            else:
                                st.caption("No text in the primary reason column for this selection.")
                        else:
                            st.caption("Could not detect a **primary reason** column (look for *reason* + *rating*).")

                        with st.expander("Full response table (with Level column)", expanded=False):
                            out_tbl = rep.copy()
                            if "_SCORE" in out_tbl.columns:
                                out_tbl = out_tbl.rename(columns={"_SCORE": "Score (1–10)"})
                            st.dataframe(out_tbl, use_container_width=True, height=420)
            elif not errors:
                st.info("No data rows found in the uploaded file(s).")

    # =========================================================
    # 🧠 EXECUTIVE STORY
    # =========================================================
    if page == "🧠 Executive Story":

        st.title("🧠 Executive AI Story")

        high_risk = len(df[df["RISK_SCORE"]>0.7])

        st.markdown(f"""
        ### 📊 Key Insight
        - Total Workforce: {len(df)}
        - High Risk Employees: {high_risk}
        - Avg Risk: {df['RISK_SCORE'].mean():.2f}

        ### 🧠 Interpretation
        Attrition is {'HIGH' if high_risk > len(df)*0.2 else 'STABLE'}

        ### 🎯 Recommendation
        Focus on:
        - Low tenure employees
        - High-risk departments
        - Engagement programs
        """)

else:
    st.info("Upload HR file")