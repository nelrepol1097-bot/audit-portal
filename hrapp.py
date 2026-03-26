import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LinearRegression
import time
import re
import base64
import json
from io import BytesIO
from datetime import datetime
from pathlib import Path
import streamlit.components.v1 as components

try:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    HAS_PYTHON_DOCX = True
except ImportError:
    HAS_PYTHON_DOCX = False

st.set_page_config(layout="wide")

# Project folder (holds PIP_Form Revised.docx, MR Productivity_Sample Data.xlsx, etc.)
HR_APP_DIR = Path(__file__).resolve().parent
PIP_FORM_TEMPLATE_PATH = HR_APP_DIR / "PIP_Form Revised.docx"
MR_PRODUCTIVITY_SAMPLE_PATH = HR_APP_DIR / "MR Productivity_Sample Data.xlsx"


@st.cache_data(show_spinner=False)
def load_hr_workbook(file_bytes):
    """Cache Excel sheet loading so UI interactions don't re-read workbook every rerun."""
    core = pd.read_excel(BytesIO(file_bytes), sheet_name="HR DATABASE")

    def read_optional(sheet_name):
        try:
            return pd.read_excel(BytesIO(file_bytes), sheet_name=sheet_name)
        except Exception:
            return None

    info = read_optional("INFO")
    fc_tbl = read_optional("FC Hiring Actual vs Target")
    pcni_tbl = read_optional("PCNI Hiring Actual vs Target")
    suki_tbl = read_optional("SUKI Hiring Actual vs. Target")
    return core, info, fc_tbl, pcni_tbl, suki_tbl


def _scorecard_snapshots_to_monthly_ftm(snapshot_list):
    """
    One row per employee per calendar month from scorecard snapshots.
    FTM weighted result = sum of 'Weighted Achievement %' for that save.
    Uses REPORTING_MONTH on the snapshot when present; otherwise Saved at month.
    """
    if not snapshot_list:
        return pd.DataFrame()
    big = pd.concat(snapshot_list, ignore_index=True)
    need = {"Employee", "Saved at", "Weighted Achievement %"}
    if not need.issubset(big.columns):
        return pd.DataFrame()
    big = big.copy()
    big["Weighted Achievement %"] = pd.to_numeric(big["Weighted Achievement %"], errors="coerce").fillna(0.0)
    big["Saved at"] = pd.to_datetime(big["Saved at"], errors="coerce")
    big = big.dropna(subset=["Saved at"])

    def _first_reporting_month(s):
        s2 = s.dropna()
        if s2.empty:
            return np.nan
        return s2.iloc[0]

    if "REPORTING_MONTH" in big.columns:
        per = (
            big.groupby(["Employee", "Saved at"], dropna=False)
            .agg(
                FTM_WEIGHTED_RESULT=("Weighted Achievement %", "sum"),
                REPORTING_MONTH=("REPORTING_MONTH", _first_reporting_month),
            )
            .reset_index()
        )
    else:
        per = (
            big.groupby(["Employee", "Saved at"], dropna=False)["Weighted Achievement %"]
            .sum()
            .reset_index(name="FTM_WEIGHTED_RESULT")
        )
        per["REPORTING_MONTH"] = np.nan

    def _to_period(row):
        rm = row["REPORTING_MONTH"]
        if rm is not None and not (isinstance(rm, float) and pd.isna(rm)) and str(rm).strip():
            try:
                s = str(rm).strip()[:7]
                if len(s) == 7 and s[4] == "-":
                    return pd.Period(s, freq="M")
            except (ValueError, TypeError):
                pass
            t = pd.to_datetime(rm, errors="coerce")
            if pd.notna(t):
                return pd.Period(t, freq="M")
        return pd.Period(row["Saved at"], freq="M")

    per["Year-Month"] = per.apply(_to_period, axis=1)
    per = per.sort_values("Saved at")
    monthly = per.groupby(["Employee", "Year-Month"], as_index=False).last()
    monthly["YearMonthStr"] = monthly["Year-Month"].astype(str)
    return monthly


def _enrich_mr_productivity_monthly(monthly: pd.DataFrame, ep: float) -> pd.DataFrame:
    """Apply MR bands, PIP trigger (2 consecutive below OR 2 of last 3 below), 3-mo PIP review, 2nd PIP ≤12 mo."""
    m = monthly.sort_values("Year-Month").reset_index(drop=True)
    if m.empty:
        return m
    f = m["FTM_WEIGHTED_RESULT"].astype(float)
    ep = float(ep) if ep else 100.0
    ratio = f / ep
    ratio = ratio.replace([np.inf, -np.inf], np.nan).fillna(0.0)
    m = m.copy()
    m["EP_ref"] = ep
    m["Pct_of_EP"] = ratio * 100.0
    m["MR_Band"] = np.where(
        ratio >= 1.0,
        "Above Standard → Retain",
        np.where(
            ratio >= 0.8,
            "Within Standard → Developmental Coaching & Training",
            "Below Standard → PIP trigger + Corrective Coaching",
        ),
    )
    below = (ratio < 0.8).astype(bool)
    m["Below_Standard"] = below
    m["Within_Or_Better"] = (ratio >= 0.8).astype(bool)

    trig = []
    for i in range(len(m)):
        ok = False
        if i >= 1 and bool(below.iloc[i]) and bool(below.iloc[i - 1]):
            ok = True
        if i >= 2 and int(below.iloc[i - 2]) + int(below.iloc[i - 1]) + int(below.iloc[i]) >= 2:
            ok = True
        trig.append(ok)
    m["PIP_Trigger"] = trig

    second_term = []
    triggers_so_far = []
    for i in range(len(m)):
        if not trig[i]:
            second_term.append(False)
            continue
        period = m["Year-Month"].iloc[i]
        bad = False
        for tp in triggers_so_far:
            gap = period - tp
            if gap is not None and 0 < int(gap) <= 12:
                bad = True
                break
        second_term.append(bad)
        triggers_so_far.append(period)
    m["Second_PIP_Within_12mo_Terminate"] = second_term

    pip_review = []
    for i in range(len(m)):
        if not trig[i]:
            pip_review.append("")
            continue
        if i + 2 >= len(m):
            pip_review.append("PIP: add more months in history for full 3-month PIP window review")
            continue
        window = m.iloc[i : i + 3]
        good = int(window["Within_Or_Better"].sum())
        if good >= 2:
            pip_review.append(
                "PIP exit rule: ≥2 of 3 PIP months Within Standard or better — may exit PIP successfully"
            )
        else:
            pip_review.append(
                "PIP exit rule: fewer than 2 of 3 PIP months Within Standard or better — TERMINATE per policy"
            )
    m["PIP_3Mo_Review"] = pip_review
    return m


def _build_mr_productivity_sample_docx(monthly_display: pd.DataFrame, ep_note: str):
    """Form-style Word summary (MR productivity monitoring sample)."""
    doc = Document()
    h = doc.add_heading("MR Productivity Monitoring — Summary", 0)
    h.alignment = WD_ALIGN_PARAGRAPH.CENTER
    doc.add_paragraph(
        "Data sourced from Employee Scorecard Monitoring (FTM weighted result vs Expected Production)."
    )
    doc.add_paragraph(f"EP (Expected Production) reference: {ep_note}")
    doc.add_paragraph("")
    doc.add_paragraph("Complete the sections below as applicable:")
    tbl = doc.add_table(rows=1, cols=8)
    hdr = tbl.rows[0].cells
    headers = [
        "Employee",
        "Month",
        "FTM weighted %",
        "EP %",
        "% of EP",
        "Classification",
        "PIP trigger?",
        "Notes / actions",
    ]
    for i, t in enumerate(headers):
        hdr[i].text = t
        for p in hdr[i].paragraphs:
            for r in p.runs:
                r.bold = True
    for _, row in monthly_display.iterrows():
        cells = tbl.add_row().cells
        cells[0].text = str(row.get("Employee", ""))
        cells[1].text = str(row.get("YearMonthStr", ""))
        cells[2].text = f"{row.get('FTM_WEIGHTED_RESULT', 0):.2f}"
        cells[3].text = f"{row.get('EP_ref', 0):.1f}"
        cells[4].text = f"{row.get('Pct_of_EP', 0):.1f}"
        cells[5].text = str(row.get("MR_Band", ""))
        cells[6].text = "Yes" if row.get("PIP_Trigger") else "No"
        tail = []
        if row.get("PIP_3Mo_Review"):
            tail.append(row.get("PIP_3Mo_Review"))
        if row.get("Second_PIP_Within_12mo_Terminate"):
            tail.append("Second PIP trigger within 12 months — TERMINATE per policy.")
        cells[7].text = " | ".join(tail) if tail else "_____________________________"
    doc.add_paragraph("")
    p = doc.add_paragraph(
        "Signatures / approvals: _________________________  Date: _______________"
    )
    p = doc.add_paragraph("HR / Manager remarks: _________________________________________________")
    bio = BytesIO()
    doc.save(bio)
    bio.seek(0)
    return bio


def _pip_template_fill_scorecard_table(tbl, calc_df):
    """Fill buddy-up scorecard grid (table 3) in PIP_Form Revised.docx."""
    if tbl is None or calc_df is None or calc_df.empty:
        return
    cdf = calc_df.reset_index(drop=True)
    n = min(len(cdf), max(0, len(tbl.rows) - 1))
    colmap = [
        ("Week 1 Actual Achievement", 4),
        ("Week 1 Score %", 5),
        ("Week 2 Actual Achievement", 6),
        ("Week 2 Score %", 7),
        ("Week 3 Actual Achievement", 8),
        ("Week 3 Score %", 9),
        ("Week 4 Actual Achievement", 10),
        ("Week 4 Score %", 11),
        ("TOTAL ACHIEVEMENT FTM %", 12),
    ]
    for i in range(n):
        row = cdf.iloc[i]
        cells = tbl.rows[i + 1].cells
        if len(cells) < 13:
            continue
        for cname, ci in colmap:
            v = row.get(cname)
            if v is None or (isinstance(v, float) and pd.isna(v)):
                cells[ci].text = ""
            else:
                try:
                    cells[ci].text = f"{float(v):.2f}"
                except (TypeError, ValueError):
                    cells[ci].text = str(v)


def _apply_pip_revised_template(doc, employee_name: str, context: dict, scorecard_calc_df=None):
    """Populate employee table, production lines, and optional scorecard grid in the official PIP layout."""
    t0 = doc.tables[0]

    def put(r, c, val):
        if r < len(t0.rows):
            row = t0.rows[r]
            if c < len(row.cells):
                row.cells[c].text = "" if val is None else str(val)

    put(0, 1, employee_name)
    put(0, 6, context.get("branch", ""))
    put(1, 1, context.get("date_hired", ""))
    put(1, 6, context.get("position", ""))
    put(2, 1, context.get("manager", ""))
    put(2, 6, context.get("department", ""))
    put(3, 1, context.get("pip_enrollment", ""))
    put(4, 2, context.get("pip_from", ""))
    put(4, 4, context.get("pip_to", ""))

    for para in doc.paragraphs:
        line = para.text.strip()
        if line.startswith("Target Monthly Production:"):
            para.text = (
                f"Target Monthly Production: EP {context.get('ep', '')}% — "
                f"latest FTM weighted {context.get('ftm', '')}% "
                f"({context.get('pct_ep', '')}% of EP); {context.get('band', '')}"
            )
        elif line.startswith("Month 1 Production:"):
            para.text = f"Month 1 Production: {context.get('month1_prod', '_______')}"
        elif line.startswith("Month 2 Production:"):
            para.text = f"Month 2 Production: {context.get('month2_prod', '_______')}"
        elif line.startswith("Month 3 Production:"):
            para.text = f"Month 3 Production: {context.get('month3_prod', '_______')}"

    if scorecard_calc_df is not None and len(doc.tables) > 3:
        _pip_template_fill_scorecard_table(doc.tables[3], scorecard_calc_df)


def _build_pip_form_docx(employee_name: str, context: dict, scorecard_calc_df=None):
    """
    Prefers **PIP_Form Revised.docx** saved next to hrapp.py (your template).
    Falls back to a minimal generated form if the file is missing.
    """
    if HAS_PYTHON_DOCX and PIP_FORM_TEMPLATE_PATH.is_file():
        doc = Document(str(PIP_FORM_TEMPLATE_PATH))
        _apply_pip_revised_template(doc, employee_name, context, scorecard_calc_df)
        bio = BytesIO()
        doc.save(bio)
        bio.seek(0)
        return bio

    doc = Document()
    h = doc.add_heading("Performance Improvement Plan (PIP) — Revised", 0)
    h.alignment = WD_ALIGN_PARAGRAPH.CENTER
    doc.add_paragraph("Complete all fields. Attach scorecard / MR productivity evidence as needed.")
    doc.add_paragraph("")

    def row_form(label, value=""):
        t = doc.add_table(rows=1, cols=2)
        c0, c1 = t.rows[0].cells
        c0.text = label
        c1.text = value if value else "_______________________________"

    row_form("Employee name", employee_name)
    row_form("Department / Branch", context.get("dept", ""))
    row_form("Position", context.get("position", ""))
    row_form("Reporting month (review period)", context.get("month", ""))
    row_form("FTM weighted result (%)", context.get("ftm", ""))
    row_form("Expected Production EP (%)", context.get("ep", ""))
    row_form("Achievement vs EP (%)", context.get("pct_ep", ""))
    row_form("MR classification", context.get("band", ""))
    row_form("PIP trigger basis", context.get("trigger_basis", ""))
    row_form("Corrective coaching plan (dates / owner)", "")
    row_form("Metrics to improve (measurable)", "")
    row_form("Check-in dates (mo 1 / 2 / 3)", "_______ / _______ / _______")
    row_form(
        "PIP outcome after 3 months (HR use)",
        "Met / extend / separation (checklist as needed).",
    )
    doc.add_paragraph("")
    doc.add_paragraph(
        "Employee acknowledgement: __________________________  Date: _______________"
    )
    doc.add_paragraph(
        "Manager acknowledgement: ___________________________  Date: _______________"
    )
    doc.add_paragraph("HR acknowledgement: _____________________________  Date: _______________")
    bio = BytesIO()
    doc.save(bio)
    bio.seek(0)
    return bio


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
    "📋 Employee Scorecard Monitoring",
    "📈 MR Productivity Monitoring",
    "🧠 Executive Story"
])

# =========================================================
# 📂 FILE
# =========================================================
file = st.file_uploader("Upload HR Master Excel", type=["xlsx"])

if file:

    # LOAD CORE (cached)
    file_bytes = file.getvalue()
    file_sig = f"{len(file_bytes)}:{hash(file_bytes)}"
    df, df_info, fc, pcni, suki = load_hr_workbook(file_bytes)
    df.columns = df.columns.str.replace("\n"," ").str.strip().str.upper()
    df = df.loc[:, ~df.columns.duplicated()]

    # NORMALIZE INFO (already loaded from cache)
    if df_info is not None:
        df_info.columns = df_info.columns.str.upper().str.strip()

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
    col_tenure_bracket = find_col(["TENURE BRACKET", "TENURE BAND", "SERVICE BRACKET"])
    _ex_tenure = {c for c in [col_tenure_bracket] if c}
    col_tenure = find_col(
        ["TENURE MO", "TENURE(MO", "MONTH IN TENURE", "TENURE IN MONTH", "MONTHS OF TENURE"],
        exclude_cols=_ex_tenure,
    )
    if not col_tenure:
        col_tenure = find_col(["TENURE"], exclude_cols=_ex_tenure)
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
    col_hire_date = find_col(["DATE HIRED", "HIRE DATE", "DATE OF HIRE", "JOINING DATE", "DATE JOINED", "START DATE"])
    col_resign_date = find_col(["RESIGNATION DATE", "DATE RESIGNED", "RESIGNED DATE", "SEPARATION DATE", "TERMINATION DATE", "END DATE"])
    col_leave_reason = find_col(["REASON FOR LEAVING", "REASON OF LEAVING", "EXIT REASON", "SEPARATION REASON", "REASON FOR RESIGNATION", "REASON"])
    col_ep = find_col(["EXPECTED PRODUCTION", "EP", "TARGET PRODUCTION", "PRODUCTION TARGET", "MR EP"])

    def detect_hr_date_column(frame):
        """Pick the best column for calendar filtering (hire / join / start / effective dates)."""
        exclude_name = ("TENURE", "AGE", "SALARY", "AMOUNT", "ZIP", "MOBILE", "PHONE", "COUNT")
        best_c = None
        best_score = -1.0
        for col in frame.columns:
            u = str(col).upper()
            if str(col).startswith("_"):
                continue
            if any(x in u for x in exclude_name) and "DATE" not in u:
                continue
            probe = pd.to_datetime(frame[col], errors="coerce", utc=False)
            frac = float(probe.notna().mean())
            if frac < 0.12:
                continue
            score = frac * 42.0
            for kw, w in [
                ("HIRE", 48),
                ("JOIN", 42),
                ("START", 30),
                ("EFFECTIVE", 30),
                ("COMMENCE", 30),
                ("AS OF", 24),
                ("DATE", 14),
                ("BIRTH", 10),
                ("RESIGN", 14),
                ("END DATE", 12),
            ]:
                if kw in u:
                    score += w
                    break
            if score > best_score:
                best_score = score
                best_c = col
        return best_c

    col_date_filter = detect_hr_date_column(df)
    if col_date_filter:
        _dt = pd.to_datetime(df[col_date_filter], errors="coerce")
        try:
            if getattr(_dt.dt, "tz", None) is not None:
                _dt = _dt.dt.tz_convert("UTC").dt.tz_localize(None)
        except (TypeError, ValueError, AttributeError):
            pass
        df["_HR_FILTER_DATE"] = _dt.dt.normalize()
    else:
        col_date_filter = None

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

    def tenure_months_numeric(val):
        """Parse tenure as months: prefer numeric cells, else first integer in text (e.g. '24 months')."""
        if val is None or (isinstance(val, float) and pd.isna(val)):
            return np.nan
        num = pd.to_numeric(val, errors="coerce")
        if pd.notna(num):
            return float(num)
        try:
            digits = re.findall(r"\d+", str(val))
            if not digits:
                return np.nan
            return float(int(digits[0]))
        except (ValueError, TypeError):
            return np.nan

    if col_tenure:
        df["TENURE_NUM"] = df[col_tenure].apply(tenure_months_numeric)

    if "TENURE_NUM" in df.columns:
        _tb_edges = [0, 12, 24, 36, 48, 60, 120, 100_000]
        _tb_labels = [
            "0–12 mo",
            "12–24 mo",
            "24–36 mo",
            "36–48 mo",
            "48–60 mo",
            "60–120 mo",
            "120+ mo",
        ]
        df["_TENURE_BRACKET_AUTO"] = pd.cut(
            df["TENURE_NUM"],
            bins=_tb_edges,
            labels=_tb_labels,
            include_lowest=True,
        ).astype(str).replace({"nan": ""})

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
        # Reuse risk results for same file upload to keep interaction fast.
        if (
            st.session_state.get("_risk_cache_sig") == file_sig
            and "_risk_cache_vals" in st.session_state
            and len(st.session_state["_risk_cache_vals"]) == len(df)
        ):
            df["RISK_SCORE"] = st.session_state["_risk_cache_vals"]
        else:
            model = RandomForestClassifier(
                n_estimators=80,
                max_depth=10,
                random_state=42,
                n_jobs=-1,
            )
            model.fit(df_ml[features], df_ml["IS_ATTRITION"])
            df["RISK_SCORE"] = model.predict_proba(df[features].fillna(0))[:,1]
            st.session_state["_risk_cache_sig"] = file_sig
            st.session_state["_risk_cache_vals"] = df["RISK_SCORE"].tolist()
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

        date_sel = None
        date_mode = "Range"
        date_filter_info = ""
        include_blank_dates = False

        row_top = st.columns([1.35, 1.25])
        with row_top[0]:
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
            robot_upload = st.file_uploader(
                "Robot image (optional)",
                type=["png", "jpg", "jpeg", "webp"],
                key="main_robot_img_upload",
                help="Upload a robot image if you want a custom assistant look.",
            )
            if "robot_voice_profile" not in st.session_state:
                st.session_state.robot_voice_profile = "Executive Female (Natural)"
            st.selectbox(
                "Robot voice profile",
                ["Executive Female (Natural)", "Professional Female", "Chopper-like", "Neutral"],
                key="robot_voice_profile",
                help="Changes the speaking style for robot panel and chat replies.",
            )
        with row_top[1]:
            if col_date_filter and "_HR_FILTER_DATE" in df.columns:
                vd_all = df["_HR_FILTER_DATE"].dropna()
                if vd_all.empty:
                    st.caption(f"Column **{col_date_filter}** has no valid dates for filtering.")
                else:
                    d_lo = vd_all.min().date()
                    d_hi = vd_all.max().date()
                    st.markdown(
                        f'<p style="margin:0 0 0.35rem 0;font-size:0.9rem;color:#7eeeff;">📅 Date column: '
                        f'<span style="color:#e0f7ff;">{col_date_filter}</span></p>',
                        unsafe_allow_html=True,
                    )
                    date_mode = st.radio(
                        "Date filter mode",
                        ["Range", "Year", "Year + Month", "Exact Day"],
                        horizontal=True,
                        index=0,
                        key="main_dashboard_date_mode",
                    )
                    date_sel = st.date_input(
                        "Filter by date (range)",
                        value=(d_lo, d_hi),
                        min_value=d_lo,
                        max_value=d_hi,
                        key="main_dashboard_date_range",
                        help="Choose **start** and **end** on the calendar (year, month, day). "
                        "Pick the same day twice for a single day. Applies to the date column shown above.",
                    )
                    years_avail = sorted(vd_all.dt.year.unique().tolist())
                    y_pick = st.selectbox(
                        "Year",
                        years_avail,
                        index=len(years_avail) - 1,
                        key="main_dashboard_date_year_pick",
                    )
                    m_pick = st.selectbox(
                        "Month",
                        list(range(1, 13)),
                        index=0,
                        format_func=lambda m: pd.Timestamp(2000, m, 1).strftime("%B"),
                        key="main_dashboard_date_month_pick",
                    )
                    vd_y = vd_all[vd_all.dt.year == int(y_pick)]
                    days_avail = (
                        sorted(vd_y[vd_y.dt.month == int(m_pick)].dt.day.unique().tolist())
                        if not vd_y.empty
                        else []
                    )
                    d_pick = st.selectbox(
                        "Day",
                        days_avail if days_avail else [1],
                        index=0,
                        key="main_dashboard_date_day_pick",
                    )
                    include_blank_dates = st.checkbox(
                        "Include rows with missing dates",
                        value=False,
                        key="main_dashboard_date_include_blank",
                        help="If unchecked, rows with blank/invalid dates are excluded when the range is narrower than full data.",
                    )
            else:
                st.caption("Add a **date** column (e.g. *Date Hired*, *Joining Date*) to enable the calendar filter.")
        robot_img_uri = ""
        if robot_upload is not None:
            try:
                robot_img_uri = (
                    "data:image/png;base64,"
                    + base64.b64encode(robot_upload.getvalue()).decode("ascii")
                )
            except Exception:
                robot_img_uri = ""
        if not robot_img_uri:
            chopper_candidates = [
                r"C:\Users\User\Desktop\HR APP\chopper.png",
                r"C:\Users\User\Desktop\HR APP\chopper.jpg",
                r"C:\Users\User\Desktop\HR APP\chopper.jpeg",
                r"C:\Users\User\Desktop\HR APP\chopper.webp",
                r"C:\Users\User\.cursor\projects\C-Users-User-AppData-Local-Temp-989fffe9-f0f3-435d-9641-116288540b94\assets\c__Users_User_AppData_Roaming_Cursor_User_workspaceStorage_1773732824934_images_image-09a03331-c5a0-4ad4-a3d1-48f7fb2db8df.png",
            ]
            for cp in chopper_candidates:
                try:
                    p = Path(cp)
                    if not p.exists():
                        continue
                    ext = p.suffix.lower().replace(".", "")
                    mime = "image/png" if ext not in ("jpg", "jpeg", "webp") else f"image/{ext if ext != 'jpg' else 'jpeg'}"
                    with open(cp, "rb") as f:
                        robot_img_uri = f"data:{mime};base64," + base64.b64encode(f.read()).decode("ascii")
                    break
                except Exception:
                    robot_img_uri = ""

        if not robot_img_uri:
            robot_img_uri = (
                "data:image/svg+xml;utf8,"
                "<svg xmlns='http://www.w3.org/2000/svg' width='700' height='320'>"
                "<defs><linearGradient id='g' x1='0' y1='0' x2='0' y2='1'>"
                "<stop offset='0%' stop-color='%2300eaff' stop-opacity='.28'/>"
                "<stop offset='100%' stop-color='%23000' stop-opacity='0'/></linearGradient></defs>"
                "<rect width='100%' height='100%' fill='%23091b29'/><rect width='100%' height='100%' fill='url(%23g)'/>"
                "<g fill='none' stroke='%2300eaff' stroke-width='4' transform='translate(350,160)'>"
                "<circle cx='0' cy='-72' r='30'/><line x1='0' y1='-42' x2='0' y2='56'/>"
                "<line x1='-60' y1='-6' x2='60' y2='-6'/><line x1='0' y1='56' x2='-40' y2='130'/>"
                "<line x1='0' y1='56' x2='40' y2='130'/></g>"
                "<text x='50%' y='91%' dominant-baseline='middle' text-anchor='middle' "
                "font-family='Arial' font-size='18' fill='%239befff'>Upload chopper.png for custom robot</text>"
                "</svg>"
            )

        with row_top[0]:
            components.html(
                f"""
                <style>
                  .robot-panel {{
                    position: relative; height: 270px; margin: 0.25rem 0 0.25rem 0;
                    border-radius: 10px; overflow: hidden;
                    border: 1px solid rgba(0,234,255,.36);
                    background:
                      radial-gradient(circle at 50% 16%, rgba(140, 230, 255, .22), rgba(6,16,28,.95) 56%),
                      linear-gradient(180deg, rgba(4,12,20,.45), rgba(4,12,20,.9));
                    box-shadow: inset 0 0 28px rgba(0,234,255,.14), 0 0 16px rgba(0,234,255,.16);
                  }}
                  .robot-panel::after {{
                    content: "";
                    position: absolute;
                    inset: 0;
                    pointer-events: none;
                    background:
                      radial-gradient(circle at 50% 78%, rgba(0, 234, 255, .16), transparent 34%),
                      linear-gradient(180deg, rgba(0,234,255,.06) 0%, transparent 38%, rgba(0,234,255,.07) 100%);
                    mix-blend-mode: screen;
                  }}
                  .robot-panel::before {{
                    content: "";
                    position: absolute;
                    inset: -30% -20% auto -20%;
                    height: 68%;
                    background: radial-gradient(circle, rgba(0,234,255,.34), transparent 64%);
                    animation: holoPulse 2.5s ease-in-out infinite;
                    pointer-events: none;
                  }}
                  .robot-scan {{
                    position:absolute; inset:0;
                    background: repeating-linear-gradient(to bottom, rgba(0,234,255,.08) 0 1px, transparent 1px 7px);
                    pointer-events:none; mix-blend-mode:screen;
                  }}
                  .robot-particles {{
                    position:absolute; inset:0;
                    background:
                      radial-gradient(circle at 18% 72%, rgba(170,240,255,.45) 0 1px, transparent 2px),
                      radial-gradient(circle at 38% 32%, rgba(170,240,255,.45) 0 1px, transparent 2px),
                      radial-gradient(circle at 64% 60%, rgba(170,240,255,.35) 0 1px, transparent 2px),
                      radial-gradient(circle at 82% 28%, rgba(170,240,255,.45) 0 1px, transparent 2px),
                      radial-gradient(circle at 50% 45%, rgba(170,240,255,.35) 0 1px, transparent 2px);
                    opacity: .55;
                    filter: blur(.15px);
                    animation: drift 7s linear infinite;
                    pointer-events:none;
                  }}
                  .robot-flicker {{
                    position:absolute; inset:0;
                    background: linear-gradient(180deg, rgba(0,234,255,.08), transparent 40%, rgba(0,234,255,.06));
                    mix-blend-mode: screen;
                    pointer-events:none;
                    animation: flicker 1.9s steps(2, end) infinite;
                  }}
                  .robot-stage {{
                    position:absolute; left:50%; top:50%;
                    transform: translate(-50%, -50%);
                    width:min(68%, 470px); height:220px;
                    display:flex; align-items:center; justify-content:center;
                    pointer-events:none;
                  }}
                  .robot-stage::before {{
                    content:"";
                    position:absolute; inset: 6% 12%;
                    border-radius: 18px;
                    background:
                      radial-gradient(circle at 50% 50%, rgba(0,234,255,.2), rgba(0,234,255,.04) 56%, transparent 78%),
                      linear-gradient(180deg, rgba(0,234,255,.06), rgba(0,0,0,.08));
                    box-shadow: inset 0 0 24px rgba(0,234,255,.14);
                    filter: blur(.2px);
                    z-index: 0;
                  }}
                  .robot-inner {{
                    width:100%; height:100%;
                    display:flex; align-items:center; justify-content:center;
                    animation: stageFloat 3.8s ease-in-out infinite;
                    z-index: 1;
                  }}
                  .robot-img {{
                    width: 96%;
                    height: 96%;
                    object-fit: contain;
                    object-position: center center;
                    filter: grayscale(0.06) saturate(1.06) brightness(1.06) contrast(1.04)
                            drop-shadow(0 0 8px rgba(0,234,255,.85))
                            drop-shadow(0 0 22px rgba(0,234,255,.42));
                    opacity: 0.88;
                    mix-blend-mode: screen;
                    -webkit-mask-image: radial-gradient(ellipse at 50% 54%, black 45%, rgba(0,0,0,.9) 56%, rgba(0,0,0,.55) 68%, transparent 84%);
                    mask-image: radial-gradient(ellipse at 50% 54%, black 45%, rgba(0,0,0,.9) 56%, rgba(0,0,0,.55) 68%, transparent 84%);
                    animation: imgBob 3.0s ease-in-out infinite;
                  }}
                  .robot-glow {{
                    position:absolute; left:50%; bottom:7px; transform:translateX(-50%);
                    width: 230px; height: 20px; border-radius:50%;
                    border:1px solid rgba(0,234,255,.45); box-shadow:0 0 16px rgba(0,234,255,.35), inset 0 0 8px rgba(0,234,255,.28);
                  }}
                  .robot-ring {{
                    position:absolute; left:50%; bottom:18px; transform:translateX(-50%);
                    width: 300px; height: 34px; border-radius:50%;
                    border: 1px solid rgba(0,234,255,.35);
                    box-shadow: 0 0 14px rgba(0,234,255,.25);
                    animation: ringPulse 2.6s ease-in-out infinite;
                  }}
                  .robot-wave {{
                    position:absolute; left:50%; bottom:17px; transform:translateX(-50%);
                    width: 360px; height: 42px; border-radius:50%;
                    border: 1px solid rgba(130,225,255,.25);
                    opacity:.65;
                    animation: waveExpand 3.1s ease-out infinite;
                  }}
                  .robot-title {{
                    position:absolute; left:14px; top:10px;
                    font:700 12px Orbitron,Arial,sans-serif; letter-spacing:.08em;
                    color:#00eaff; text-shadow:0 0 12px rgba(0,234,255,.8);
                  }}
                  .robot-btn {{
                    position:absolute; right:10px; top:8px;
                    background:rgba(0,234,255,.12); color:#bff8ff; border:1px solid rgba(0,234,255,.52);
                    border-radius:8px; padding:4px 9px; cursor:pointer; font:600 11px Rajdhani,Arial,sans-serif;
                  }}
                  .robot-help {{
                    position:absolute; left:14px; bottom:8px; color:#9fefff; font:600 11px Rajdhani,Arial,sans-serif;
                    text-shadow:0 0 8px rgba(0,234,255,.35);
                  }}
                  .robot-btn2 {{
                    position:absolute; right:10px; top:36px;
                    background:rgba(168,85,247,.15); color:#f2d8ff; border:1px solid rgba(168,85,247,.6);
                    border-radius:8px; padding:4px 9px; cursor:pointer; font:600 11px Rajdhani,Arial,sans-serif;
                  }}
                  @keyframes imgBob {{
                    0%,100% {{ transform: translateY(0px) scale(1.00); opacity: 0.86; }}
                    50% {{ transform: translateY(-9px) scale(1.03); opacity: 0.96; }}
                  }}
                  @keyframes holoPulse {{
                    0%,100% {{ opacity: .35; transform: scale(.98); }}
                    50% {{ opacity: .85; transform: scale(1.04); }}
                  }}
                  @keyframes flicker {{
                    0%,100% {{ opacity: .35; }}
                    50% {{ opacity: .58; }}
                  }}
                  @keyframes drift {{
                    0% {{ transform: translateY(6px); opacity:.35; }}
                    50% {{ transform: translateY(-6px); opacity:.7; }}
                    100% {{ transform: translateY(6px); opacity:.35; }}
                  }}
                  @keyframes stageFloat {{
                    0%,100% {{ transform: translateY(0px); }}
                    25% {{ transform: translateY(-4px); }}
                    50% {{ transform: translateY(-8px); }}
                    75% {{ transform: translateY(-3px); }}
                  }}
                  @keyframes ringPulse {{
                    0%,100% {{ transform: translateX(-50%) scale(0.98); opacity:.65; }}
                    50% {{ transform: translateX(-50%) scale(1.03); opacity:1; }}
                  }}
                  @keyframes waveExpand {{
                    0% {{ transform: translateX(-50%) scale(.7); opacity:.55; }}
                    100% {{ transform: translateX(-50%) scale(1.22); opacity:0; }}
                  }}
                </style>
                <div class="robot-panel">
                  <div class="robot-scan"></div>
                  <div class="robot-particles"></div>
                  <div class="robot-flicker"></div>
                  <div class="robot-title">HR HOLOGRAPHIC ASSISTANT</div>
                  <button class="robot-btn" onclick="speakGM()">Speak: Good Morning</button>
                  <button class="robot-btn2" onclick="speakReport()">Speak: Report</button>
                  <div class="robot-stage"><div class="robot-inner"><img class="robot-img" src="{robot_img_uri}" alt="HR Robot" /></div></div>
                  <div class="robot-ring"></div>
                  <div class="robot-wave"></div>
                  <div class="robot-glow"></div>
                  <div class="robot-help">Tip: save image as C:\\Users\\User\\Desktop\\HR APP\\chopper.png</div>
                </div>
                <script>
                  const VOICE_PROFILE = {json.dumps(st.session_state.robot_voice_profile)};
                  function getVoiceConfig(profile) {{
                    if (profile === "Executive Female (Natural)") {{
                      return {{ rate: 0.95, pitch: 1.02, kw: /microsoft aria|aria online|jenny|sara|samantha|zira|female|en-us|natural/i }};
                    }}
                    if (profile === "Chopper-like") {{
                      return {{ rate: 0.98, pitch: 1.45, kw: /zira|samantha|female|en-us|google us english|michelle/i }};
                    }}
                    if (profile === "Professional Female") {{
                      return {{ rate: 0.96, pitch: 1.08, kw: /zira|samantha|female|en-us|google us english|aria|jenny/i }};
                    }}
                    return {{ rate: 1.0, pitch: 1.0, kw: /en/i }};
                  }}
                  function pickChopperVoice() {{
                    const vs = window.speechSynthesis.getVoices() || [];
                    const cfg = getVoiceConfig(VOICE_PROFILE);
                    if (!vs.length) return null;
                    const rankVoice = (v) => {{
                      const txt = ((v.name || "") + " " + (v.lang || "")).toLowerCase();
                      let score = 0;
                      if (cfg.kw.test(txt)) score += 120;
                      if (/natural|online/.test(txt)) score += 45;
                      if (/aria|jenny|zira|samantha|female/.test(txt)) score += 30;
                      if (/en-us/.test(txt)) score += 18;
                      if (/en/.test(txt)) score += 6;
                      return score;
                    }};
                    const ranked = [...vs].sort((a, b) => rankVoice(b) - rankVoice(a));
                    const best = ranked[0];
                    return (best && rankVoice(best) > 0) ? best : (vs.find(v => /en/i.test(v.lang||'')) || null);
                  }}
                  function speakGM() {{
                    try {{
                      const u = new SpeechSynthesisUtterance("Good morning. Welcome to the HR Executive Dashboard.");
                      const cfg = getVoiceConfig(VOICE_PROFILE);
                      u.rate = cfg.rate; u.pitch = cfg.pitch; u.volume = 1.0;
                      const v = pickChopperVoice();
                      if (v) u.voice = v;
                      window.speechSynthesis.cancel();
                      window.speechSynthesis.speak(u);
                    }} catch (e) {{}}
                  }}
                  function speakReport() {{
                    try {{
                      const t = "Here is your HR report. You can ask me for summary, tenure bracket, top company, top branch, and top department.";
                      const u = new SpeechSynthesisUtterance(t);
                      const cfg = getVoiceConfig(VOICE_PROFILE);
                      u.rate = cfg.rate; u.pitch = cfg.pitch; u.volume = 1.0;
                      const v = pickChopperVoice();
                      if (v) u.voice = v;
                      window.speechSynthesis.cancel();
                      window.speechSynthesis.speak(u);
                    }} catch (e) {{}}
                  }}
                </script>
                """,
                height=290,
                scrolling=False,
            )

        search = st.text_input("Search Employee")
        filtered_df = df.copy()
        comp = "All"

        if col_name and search:
            filtered_df = filtered_df[
                filtered_df[col_name].astype(str).str.contains(search, case=False, na=False)
            ]

        if col_company:
            opts = ["All"] + sorted(df[col_company].dropna().unique().astype(str).tolist())
            if "main_dashboard_company" not in st.session_state:
                st.session_state.main_dashboard_company = "All"
            if st.session_state.main_dashboard_company not in opts:
                st.session_state.main_dashboard_company = "All"
            comp = st.selectbox("Company", opts, key="main_dashboard_company")
            if comp != "All":
                filtered_df = filtered_df[filtered_df[col_company].astype(str) == comp]

        if col_date_filter and "_HR_FILTER_DATE" in filtered_df.columns and date_sel is not None:
            vd = filtered_df["_HR_FILTER_DATE"].dropna()
            if not vd.empty:
                dser = filtered_df["_HR_FILTER_DATE"]
                if date_mode == "Year":
                    in_range = dser.dt.year == int(y_pick)
                    date_filter_info = f"Year={int(y_pick)}"
                elif date_mode == "Year + Month":
                    in_range = (dser.dt.year == int(y_pick)) & (dser.dt.month == int(m_pick))
                    date_filter_info = f"Year={int(y_pick)}, Month={int(m_pick):02d}"
                elif date_mode == "Exact Day":
                    in_range = (
                        (dser.dt.year == int(y_pick))
                        & (dser.dt.month == int(m_pick))
                        & (dser.dt.day == int(d_pick))
                    )
                    date_filter_info = f"Date={int(y_pick)}-{int(m_pick):02d}-{int(d_pick):02d}"
                else:
                    if isinstance(date_sel, tuple) and len(date_sel) == 2:
                        d_start, d_end = date_sel[0], date_sel[1]
                    else:
                        d_start = d_end = date_sel
                    ts_a = pd.Timestamp(d_start).normalize()
                    ts_b = pd.Timestamp(d_end).normalize()
                    in_range = (dser >= ts_a) & (dser <= ts_b)
                    date_filter_info = f"Range={ts_a.date()} to {ts_b.date()}"
                if include_blank_dates:
                    filtered_df = filtered_df.loc[in_range | dser.isna()].copy()
                else:
                    filtered_df = filtered_df.loc[in_range].copy()

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

        if "robot_chat" not in st.session_state:
            st.session_state.robot_chat = [
                {
                    "role": "assistant",
                    "content": "Hello, I am your HR robot assistant. Ask me for a summary, tenure brackets, top company, branch, department, or say 'show data'.",
                }
            ]
        if "robot_auto_speak" not in st.session_state:
            st.session_state.robot_auto_speak = False
        if "robot_pending_speech" not in st.session_state:
            st.session_state.robot_pending_speech = ""
        if "robot_last_table_request" not in st.session_state:
            st.session_state.robot_last_table_request = None
        if "robot_last_employee_name" not in st.session_state:
            st.session_state.robot_last_employee_name = ""

        def robot_answer(query, fdf):
            q = str(query).strip().lower()
            if not q:
                return "Please type a question so I can help."

            def extract_name_from_query(qtxt):
                patterns = [
                    r"(?:employee details of|show employee details of|details of)\s+(.+)$",
                    r"(?:what year.*(?:employee|for)\s+)(.+?)(?:\s+hire|\s+hired|\s+resign|\s+resigned|$)",
                    r"(?:what year)\s+(.+?)(?:\s+hire|\s+hired|\s+resign|\s+resigned|$)",
                    r"(?:reason for leaving of)\s+(.+)$",
                    r"(?:leaving reason of)\s+(.+)$",
                    r"(?:for)\s+(.+)$",
                ]
                for pat in patterns:
                    m = re.search(pat, qtxt)
                    if m:
                        cand = m.group(1).strip(" .,:;")
                        if cand:
                            return cand
                return ""

            def find_emp_rows(name_like):
                if not col_name or not name_like:
                    return fdf.iloc[0:0]
                name_series = fdf[col_name].astype(str).str.strip()
                hit = fdf[name_series.str.lower().str.contains(re.escape(name_like.lower()), na=False)]
                if not hit.empty:
                    return hit
                # Fallback to full HR dataset so questions still work even when dashboard filters hide the person.
                full_series = df[col_name].astype(str).str.strip()
                return df[full_series.str.lower().str.contains(re.escape(name_like.lower()), na=False)]

            def get_year_from_col(row, col):
                if not col or col not in row.index:
                    return None
                dt = pd.to_datetime(row[col], errors="coerce")
                return int(dt.year) if pd.notna(dt) else None

            if col_dept and (
                ("employee" in q and ("detail" in q or "datail" in q or "report" in q) and ("from" in q or "of" in q))
                or ("department" in q and ("show" in q or "report" in q))
            ):
                m_dep = re.search(r"(?:from|of)\s+(.+)$", q)
                if m_dep:
                    dep_raw = m_dep.group(1).strip()
                    dep_raw = re.sub(r"\(.*?department.*?\)", "", dep_raw, flags=re.I).strip()
                    dep_raw = re.sub(r"\s+", " ", dep_raw).strip(" .,:;")
                    if dep_raw:
                        dser = fdf[col_dept].astype(str).str.strip()
                        hit = fdf[dser.str.lower().str.contains(re.escape(dep_raw), na=False)]
                        if hit.empty:
                            return (
                                f"I could not find department '{dep_raw}' in the current filtered data. "
                                "Try exact department spelling."
                            )
                        return f"show_data_dept::{dep_raw}"
            if ("employee details of" in q or "show employee details of" in q or "details of" in q) and col_name:
                m = re.search(r"(?:employee details of|show employee details of|details of)\s+(.+)$", q)
                target_name = m.group(1).strip() if m else ""
                if target_name:
                    st.session_state.robot_last_employee_name = target_name
                    name_series = fdf[col_name].astype(str).str.strip()
                    hit = fdf[name_series.str.lower().str.contains(re.escape(target_name), na=False)]
                    if hit.empty:
                        return (
                            f"I could not find employee '{target_name}'. "
                            "Try exact spelling or ask 'show employee details' to see the table."
                        )
                    preview = hit.head(5)
                    show_cols = [c for c in [col_name, col_company, col_dept, col_branch, col_status] if c and c in preview.columns]
                    lines = []
                    for _, rr in preview[show_cols].iterrows():
                        parts = [str(rr[c]) for c in show_cols if pd.notna(rr[c]) and str(rr[c]).strip() != ""]
                        if parts:
                            lines.append(" | ".join(parts))
                    if lines:
                        return (
                            f"I found {len(hit)} matching record(s):\n- "
                            + "\n- ".join(lines)
                            + "\nAsk 'show employee details' if you want the table view."
                        )
                    return f"I found {len(hit)} matching record(s) for '{target_name}'. Ask 'show employee details' for table view."
            if (
                ("hire" in q or "hired" in q or "joined" in q or "resign" in q or "resigned" in q)
                or ("reason for leaving" in q or "leaving reason" in q or ("reason" in q and "leave" in q))
            ):
                target_name = extract_name_from_query(q) or st.session_state.get("robot_last_employee_name", "")
                if not target_name:
                    return "Please include the employee name, e.g. 'What year was Jakeville M Casaria hired?'"
                hit = find_emp_rows(target_name)
                if hit.empty:
                    return f"I could not find employee '{target_name}' in the current filtered data."
                st.session_state.robot_last_employee_name = target_name
                row = hit.iloc[0]
                person = str(row[col_name]) if col_name in row.index else target_name
                out = [f"Employee: {person}."]
                if "hire" in q or "hired" in q or "joined" in q:
                    hy = get_year_from_col(row, col_hire_date) or get_year_from_col(row, col_date_filter)
                    out.append(
                        f"Hire year: {hy}." if hy else "Hire year: not available (missing hire date column/value)."
                    )
                if "resign" in q or "resigned" in q:
                    ry = get_year_from_col(row, col_resign_date)
                    out.append(
                        f"Resigned year: {ry}." if ry else "Resigned year: not available (missing resignation date column/value)."
                    )
                if "reason for leaving" in q or "leaving reason" in q or ("reason" in q and "leave" in q):
                    if col_leave_reason and col_leave_reason in row.index:
                        rv = str(row[col_leave_reason]).strip()
                        if rv and rv.lower() not in ("nan", "none"):
                            out.append(f"Reason for leaving: {rv}.")
                        else:
                            out.append("Reason for leaving: not available.")
                    else:
                        out.append("Reason for leaving: not available (no leave-reason column detected).")
                return " ".join(out)
            if "summary" in q or "report" in q or "dashboard" in q:
                top_company_txt = ""
                if col_company and not fdf.empty:
                    vc_comp = fdf[col_company].astype(str).str.strip().replace({"nan": ""})
                    vc_comp = vc_comp[vc_comp != ""].value_counts()
                    if not vc_comp.empty:
                        top_company_txt = f" Top company: {vc_comp.index[0]} ({int(vc_comp.iloc[0])})."
                top_dept_txt = ""
                if col_dept and not fdf.empty:
                    vc_dept = fdf[col_dept].astype(str).str.strip().replace({"nan": ""})
                    vc_dept = vc_dept[vc_dept != ""].value_counts()
                    if not vc_dept.empty:
                        top_dept_txt = f" Top department: {vc_dept.index[0]} ({int(vc_dept.iloc[0])})."
                return (
                    f"Current filtered report: total {len(fdf):,} employee records, "
                    f"active {active:,}, attrition headcount {attrition_n:,}."
                    f"{top_company_txt}{top_dept_txt} "
                    "Ask: 'show employee details' to open the table."
                )
            if (
                ("top" in q or "highest" in q or "most" in q)
                and ("reason for leaving" in q or "reasons for leaving" in q or ("reason" in q and "leave" in q))
            ):
                if not col_leave_reason or col_leave_reason not in fdf.columns:
                    return "I cannot find a leave-reason column in this file."
                mtop = re.search(r"\btop\s+(\d{1,2})\b", q)
                n_top = int(mtop.group(1)) if mtop else 15
                n_top = max(1, min(n_top, 20))
                rs = fdf[col_leave_reason].astype(str).str.strip()
                rs = rs.replace({"nan": "", "None": ""})
                rs = rs[rs.str.len() > 0]
                if rs.empty:
                    return "No reason-for-leaving values found in the current filtered data."
                top_r = rs.value_counts().head(n_top)
                lines = [f"{i}. {k} ({int(v)})" for i, (k, v) in enumerate(top_r.items(), start=1)]
                return f"Top {len(lines)} reasons for leaving:\n" + "\n".join(lines)
            if "employee details" in q or "employee detail" in q or "show employees" in q:
                return "show_data"
            if "tenure" in q and ("bracket" in q or "band" in q):
                if "_TENURE_BRACKET_AUTO" in fdf.columns:
                    s = fdf["_TENURE_BRACKET_AUTO"].astype(str).str.strip()
                    s = s[s != ""]
                    if s.empty:
                        return "I cannot find tenure bracket values in the current filtered data."
                    top = s.value_counts().head(5)
                    txt = "; ".join([f"{k}: {int(v)}" for k, v in top.items()])
                    return f"Top tenure brackets now are {txt}."
                return "I cannot compute tenure brackets because TENURE (months) is missing."
            if ("top" in q or "highest" in q) and "company" in q and col_company:
                vc = fdf[col_company].astype(str).str.strip().value_counts()
                if vc.empty:
                    return "No company values found in this filtered data."
                name = str(vc.index[0])
                val = int(vc.iloc[0])
                return f"Top company in the current filter is {name} with {val} employees."
            if ("top" in q or "highest" in q) and "branch" in q and col_branch:
                vc = fdf[col_branch].astype(str).str.strip().value_counts()
                if vc.empty:
                    return "No branch values found in this filtered data."
                name = str(vc.index[0])
                val = int(vc.iloc[0])
                return f"Top branch in the current filter is {name} with {val} employees."
            if ("top" in q or "highest" in q) and ("department" in q or "dept" in q) and col_dept:
                vc = fdf[col_dept].astype(str).str.strip().value_counts()
                if vc.empty:
                    return "No department values found in this filtered data."
                name = str(vc.index[0])
                val = int(vc.iloc[0])
                return f"Top department in the current filter is {name} with {val} employees."
            if "show data" in q or "table" in q:
                return "show_data"
            return (
                "I can help with summary report, employee details, tenure brackets, top company/branch/department, or show data. "
                "Try: 'Give me summary report', 'show employee details', or 'Top tenure bracket'."
            )

        def robot_apply_command(query_text):
            q = str(query_text).strip().lower()
            if not q:
                return None
            month_map = {
                "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
                "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
            }
            m = re.search(r"(set|filter|change).*(year)\s+(\d{4})", q)
            if m:
                yr = int(m.group(3))
                years_avail = sorted(df["_HR_FILTER_DATE"].dropna().dt.year.unique().tolist()) if "_HR_FILTER_DATE" in df.columns else []
                if years_avail and yr in years_avail:
                    st.session_state.main_dashboard_date_mode = "Year"
                    st.session_state.main_dashboard_date_year_pick = yr
                    return f"Done. I set date filter to Year {yr}."
                return f"I cannot set year to {yr}. Available years: {years_avail[:8]}{'...' if len(years_avail) > 8 else ''}"

            m = re.search(r"(set|filter|change).*(month)\s+([a-z]+|\d{1,2})", q)
            if m:
                raw = m.group(3)
                mo = int(raw) if raw.isdigit() else month_map.get(raw)
                if mo and 1 <= mo <= 12:
                    st.session_state.main_dashboard_date_mode = "Year + Month"
                    st.session_state.main_dashboard_date_month_pick = mo
                    return f"Done. I set month filter to {pd.Timestamp(2000, mo, 1).strftime('%B')}."
                return "I cannot read that month. Try month name like January."

            if "active only" in q or "show active" in q:
                st.session_state.main_dashboard_workforce = "Active"
                return "Done. Workforce filter is now Active."
            if "show inactive" in q or "inactive only" in q:
                st.session_state.main_dashboard_workforce = "Inactive"
                return "Done. Workforce filter is now Inactive."
            if "show all workforce" in q or "workforce all" in q or "show all employees" in q:
                st.session_state.main_dashboard_workforce = "All"
                return "Done. Workforce filter is now All."

            if ("set company" in q or "filter company" in q or "company " in q) and col_company:
                opts = ["All"] + sorted(df[col_company].dropna().unique().astype(str).tolist())
                for o in opts:
                    if str(o).lower() in q:
                        st.session_state.main_dashboard_company = o
                        return f"Done. Company filter set to {o}."
                return "I could not match that company name. Say exact company text from the list."
            return None

        with st.expander("🤖 Talk to HR Robot", expanded=False):
            st.checkbox("Auto-speak robot replies", key="robot_auto_speak")
            for msg in st.session_state.robot_chat[-8:]:
                with st.chat_message(msg["role"]):
                    st.write(msg["content"])

            user_q = st.chat_input("Ask the robot about your filtered HR data...", key="hr_robot_chat_input")
            if user_q:
                st.session_state.robot_chat.append({"role": "user", "content": user_q})
                cmd_ans = robot_apply_command(user_q)
                ans = cmd_ans if cmd_ans else robot_answer(user_q, filtered_df)
                robot_text = ans
                if isinstance(ans, str) and ans.startswith("show_data_dept::"):
                    dep_q = ans.split("::", 1)[1].strip()
                    st.session_state.robot_last_table_request = {"kind": "dept", "query": dep_q}
                    robot_text = f"Showing employee report for department matching '{dep_q}' (first 50 rows) below."
                elif ans == "show_data":
                    st.session_state.robot_last_table_request = {"kind": "all"}
                    robot_text = "Showing first 50 rows of your current filtered data below."
                st.session_state.robot_chat.append(
                    {
                        "role": "assistant",
                        "content": robot_text,
                    }
                )
                if st.session_state.robot_auto_speak:
                    st.session_state.robot_pending_speech = robot_text
                st.rerun()

            if st.session_state.robot_chat:
                last_assistant = next(
                    (m["content"] for m in reversed(st.session_state.robot_chat) if m["role"] == "assistant"),
                    "",
                )
                if last_assistant:
                    if st.button("🔊 Speak last robot reply", key="speak_last_robot_reply"):
                        spoken = json.dumps(last_assistant)
                        profile = json.dumps(st.session_state.get("robot_voice_profile", "Executive Female (Natural)"))
                        components.html(
                            f"""
                            <script>
                              try {{
                                const PROFILE = {profile};
                                function cfg(p) {{
                                  if (p === "Executive Female (Natural)") return {{rate:0.95,pitch:1.02,kw:/microsoft aria|aria online|jenny|sara|samantha|zira|female|en-us|natural/i}};
                                  if (p === "Chopper-like") return {{rate:0.98,pitch:1.45,kw:/zira|samantha|female|en-us|google us english|michelle/i}};
                                  if (p === "Professional Female") return {{rate:0.96,pitch:1.08,kw:/zira|samantha|female|en-us|google us english|aria|jenny/i}};
                                  return {{rate:1.0,pitch:1.0,kw:/en/i}};
                                }}
                                const u = new SpeechSynthesisUtterance({spoken});
                                const c = cfg(PROFILE);
                                u.rate = c.rate; u.pitch = c.pitch; u.volume = 1.0;
                                const vs = window.speechSynthesis.getVoices() || [];
                                const v = vs.find(v => c.kw.test((v.name||'') + ' ' + (v.lang||''))) || vs.find(v => /en/i.test(v.lang||''));
                                if (v) u.voice = v;
                                window.speechSynthesis.cancel();
                                window.speechSynthesis.speak(u);
                              }} catch (e) {{}}
                            </script>
                            """,
                            height=0,
                            scrolling=False,
                        )
            if st.session_state.robot_pending_speech:
                spoken_auto = json.dumps(st.session_state.robot_pending_speech)
                profile_auto = json.dumps(st.session_state.get("robot_voice_profile", "Executive Female (Natural)"))
                components.html(
                    f"""
                    <script>
                      try {{
                        const PROFILE = {profile_auto};
                        function cfg(p) {{
                          if (p === "Executive Female (Natural)") return {{rate:0.95,pitch:1.02,kw:/microsoft aria|aria online|jenny|sara|samantha|zira|female|en-us|natural/i}};
                          if (p === "Chopper-like") return {{rate:0.98,pitch:1.45,kw:/zira|samantha|female|en-us|google us english|michelle/i}};
                          if (p === "Professional Female") return {{rate:0.96,pitch:1.08,kw:/zira|samantha|female|en-us|google us english|aria|jenny/i}};
                          return {{rate:1.0,pitch:1.0,kw:/en/i}};
                        }}
                        const u = new SpeechSynthesisUtterance({spoken_auto});
                        const c = cfg(PROFILE);
                        u.rate = c.rate; u.pitch = c.pitch; u.volume = 1.0;
                        const vs = window.speechSynthesis.getVoices() || [];
                        const v = vs.find(v => c.kw.test((v.name||'') + ' ' + (v.lang||''))) || vs.find(v => /en/i.test(v.lang||''));
                        if (v) u.voice = v;
                        window.speechSynthesis.cancel();
                        window.speechSynthesis.speak(u);
                      }} catch (e) {{}}
                    </script>
                    """,
                    height=0,
                    scrolling=False,
                )
                st.session_state.robot_pending_speech = ""
            if st.session_state.robot_last_table_request:
                req = st.session_state.robot_last_table_request
                if req.get("kind") == "dept" and col_dept:
                    dep_q = str(req.get("query", "")).strip().lower()
                    if dep_q:
                        _d = filtered_df[col_dept].astype(str).str.strip()
                        _hit = filtered_df[_d.str.lower().str.contains(re.escape(dep_q), na=False)]
                        st.dataframe(_hit.head(50), use_container_width=True)
                elif req.get("kind") == "all":
                    st.dataframe(filtered_df.head(50), use_container_width=True)

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

        if (col_tenure and "TENURE_NUM" in filtered_df.columns) or col_tenure_bracket:
            st.markdown("##### ⏱️ Active workforce — tenure")
            if col_status:
                act_for_tenure = filtered_df.loc[active_mask].copy()
                st.caption(
                    "Tenure charts include **active** employees only (same search / company filters as above)."
                )
            else:
                act_for_tenure = filtered_df.copy()
                st.caption(
                    "No **CURRENT STATUS** column — tenure charts use **all** filtered rows (not active-only)."
                )

            def bracket_sort_key(lbl):
                s = str(lbl).upper().strip()
                m = re.search(r"(\d+)", s)
                return (int(m.group(1)), s) if m else (99999, s)

            # Fixed month bands for heatmaps (strictly increasing bin edges)
            TENURE_BIN_EDGES = [0, 12, 24, 36, 48, 60, 120, 100_000]
            TENURE_BIN_LABELS = [
                "0–12 mo",
                "12–24 mo",
                "24–36 mo",
                "36–48 mo",
                "48–60 mo",
                "60–120 mo",
                "120+ mo",
            ]
            # Slate / periwinkle → indigo (sample heatmap style: light cool tones → deep blue-violet)
            TENURE_HEATMAP_COLORSCALE = [
                [0.0, "#f1f3f9"],
                [0.15, "#e4e8f4"],
                [0.35, "#c9cfdf"],
                [0.55, "#919bc9"],
                [0.75, "#6f77b0"],
                [1.0, "#4a4f8f"],
            ]

            def style_tenure_heatmap(fig, show_text=True):
                """Match classic heatmap look: pale grid cells, indigo highs, optional count labels."""
                if show_text:
                    try:
                        fig.update_traces(texttemplate="%{z:.0f}", textfont=dict(size=11, color="#1a1d2e"))
                    except Exception:
                        pass
                try:
                    fig.update_traces(xgap=2, ygap=2)
                except Exception:
                    pass
                fig.update_traces(
                    colorbar=dict(
                        tickfont=dict(color="#c5eef9"),
                        title=dict(text="Count", font=dict(color="#e0f7ff", size=12)),
                    )
                )
                return fig

            if col_status and act_for_tenure.empty:
                st.info("No active employees in the current selection — switch workforce to **All** or **Active**, or adjust filters.")
            else:
                has_months = (
                    col_tenure
                    and "TENURE_NUM" in act_for_tenure.columns
                    and act_for_tenure["TENURE_NUM"].notna().any()
                )
                # Resolve bracket source:
                # If tenure months exist, force auto bracket from months to avoid date-like bracket columns.
                bracket_source_col = None
                if has_months and "_TENURE_BRACKET_AUTO" in act_for_tenure.columns:
                    auto_txt = act_for_tenure["_TENURE_BRACKET_AUTO"].astype(str).str.strip()
                    if auto_txt.ne("").any():
                        bracket_source_col = "_TENURE_BRACKET_AUTO"
                        if col_tenure_bracket:
                            st.caption(
                                f"Using auto tenure buckets from **{col_tenure}** to keep bracket labels clean "
                                f"(instead of **{col_tenure_bracket}**)."
                            )
                if bracket_source_col is None and col_tenure_bracket:
                    br_raw = act_for_tenure[col_tenure_bracket]
                    br_txt = br_raw.astype(str).str.strip()
                    has_vals = br_raw.notna().any() and br_txt.ne("").any()
                    if has_vals:
                        bracket_source_col = col_tenure_bracket
                has_bracket = bracket_source_col is not None

                has_pos_group_vals = bool(
                    col_pos_group
                    and col_pos_group in act_for_tenure.columns
                    and act_for_tenure[col_pos_group].astype(str).str.strip().replace({"nan": "", "None": ""}).ne("").any()
                )

                if has_months and (has_bracket or has_pos_group_vals):
                    hm_df = act_for_tenure.dropna(subset=["TENURE_NUM"]).copy()
                    if has_pos_group_vals:
                        hm_df["_PG"] = hm_df[col_pos_group].astype(str).str.strip().replace({"nan": "", "None": ""})
                    else:
                        hm_df["_PG"] = hm_df[bracket_source_col].astype(str).str.strip().replace({"nan": "", "None": ""})
                    hm_df = hm_df[hm_df["_PG"].str.len() > 0]
                    if hm_df.empty:
                        st.caption("No rows with both tenure months and position grouping for heatmaps.")
                    else:
                        hm_df["_MB"] = pd.cut(
                            hm_df["TENURE_NUM"],
                            bins=TENURE_BIN_EDGES,
                            labels=TENURE_BIN_LABELS,
                            include_lowest=True,
                        )
                        hm_df = hm_df.dropna(subset=["_MB"])
                        if hm_df.empty:
                            st.caption("Could not bin tenure months for heatmap.")
                        else:
                            pv_mb = hm_df.pivot_table(
                                index="_PG",
                                columns="_MB",
                                aggfunc="size",
                                fill_value=0,
                            )
                            row_ord = sorted(pv_mb.index.tolist(), key=bracket_sort_key)
                            pv_hm1 = pv_mb.reindex(row_ord)
                            col_ord = [c for c in TENURE_BIN_LABELS if c in pv_hm1.columns]
                            pv_hm1 = pv_hm1.reindex(columns=col_ord)
                            first_x_label = "Tenure (months, binned)"
                            first_y_label = "Position grouping" if has_pos_group_vals else "Tenure bracket"
                            first_title = (
                                "Heatmap — position grouping × months (binned)"
                                if has_pos_group_vals
                                else "Heatmap — tenure bracket × months (binned)"
                            )

                            fig_hm1 = px.imshow(
                                pv_hm1,
                                labels=dict(
                                    x=first_x_label,
                                    y=first_y_label,
                                    color="Active headcount",
                                ),
                                aspect="auto",
                                color_continuous_scale=TENURE_HEATMAP_COLORSCALE,
                                zmin=0,
                            )
                            fig_hm1.update_xaxes(side="bottom")
                            style_tenure_heatmap(fig_hm1)
                            h1, h2 = st.columns(2)
                            h1.plotly_chart(
                                chart_layout(
                                    fig_hm1,
                                    first_title,
                                ),
                                use_container_width=True,
                            )

                            pv_sp = None
                            split_title = ""
                            split_col = None
                            if col_company:
                                split_col = col_company
                                split_title = "Company"
                            elif col_branch:
                                split_col = col_branch
                                split_title = "Branch"
                            elif col_dept:
                                split_col = col_dept
                                split_title = "Department"
                            elif col_gender:
                                split_col = col_gender
                                split_title = "Gender"

                            if split_col:
                                top_dim = (
                                    hm_df[split_col]
                                    .astype(str)
                                    .str.strip()
                                    .replace({"nan": "", "None": ""})
                                )
                                top_dim = top_dim[top_dim.str.len() > 0]
                                if not top_dim.empty:
                                    keep = top_dim.value_counts().head(14).index.tolist()
                                    hm2 = hm_df[hm_df[split_col].astype(str).isin(keep)].copy()
                                    pv_sp = hm2.pivot_table(
                                        index="_PG",
                                        columns=split_col,
                                        aggfunc="size",
                                        fill_value=0,
                                    )
                                    pv_sp = pv_sp.reindex(sorted(pv_sp.index.tolist(), key=bracket_sort_key))
                                    fig_hm2 = px.imshow(
                                        pv_sp,
                                        labels=dict(
                                            x=split_title,
                                            y=first_y_label,
                                            color="Active headcount",
                                        ),
                                        aspect="auto",
                                        color_continuous_scale=TENURE_HEATMAP_COLORSCALE,
                                        zmin=0,
                                    )
                                    fig_hm2.update_xaxes(side="bottom")
                                    style_tenure_heatmap(fig_hm2)
                                    h2.plotly_chart(
                                        chart_layout(
                                            fig_hm2,
                                            (
                                                f"Heatmap — position grouping × {split_title} (top {len(keep)})"
                                                if has_pos_group_vals
                                                else f"Heatmap — tenure bracket × {split_title} (top {len(keep)})"
                                            ),
                                        ),
                                        use_container_width=True,
                                    )
                                else:
                                    h2.caption(f"No **{split_title}** values to cross-tab.")
                            else:
                                h2.caption("Add **Company**, **Branch**, **Department**, or **Gender** for a second heatmap.")

                            with st.expander("Tenure heatmap data (matrix tables)", expanded=False):
                                if pv_mb is not None:
                                    st.caption(
                                        "Position grouping × month bins (counts)"
                                        if has_pos_group_vals
                                        else "Bracket × month bins (counts)"
                                    )
                                    st.dataframe(pv_mb, use_container_width=True)
                                else:
                                    st.caption("Bracket × position grouping (counts)")
                                    st.dataframe(pv_hm1, use_container_width=True)
                                if pv_sp is not None:
                                    st.caption(
                                        (
                                            f"Position grouping × {split_title} (counts)"
                                            if has_pos_group_vals
                                            else f"Bracket × {split_title} (counts)"
                                        )
                                    )
                                    st.dataframe(pv_sp, use_container_width=True)
                elif has_months:
                    hm_df = act_for_tenure.dropna(subset=["TENURE_NUM"]).copy()
                    hm_df["_MB"] = pd.cut(
                        hm_df["TENURE_NUM"],
                        bins=TENURE_BIN_EDGES,
                        labels=TENURE_BIN_LABELS,
                        include_lowest=True,
                    )
                    hm_df = hm_df.dropna(subset=["_MB"])
                    if hm_df.empty:
                        st.caption("Could not bin tenure months.")
                    else:
                        vc_m = hm_df["_MB"].value_counts()
                        col_order = [c for c in TENURE_BIN_LABELS if c in vc_m.index]
                        vc_m = vc_m.reindex(col_order, fill_value=0)
                        pv1 = pd.DataFrame([vc_m.values], columns=vc_m.index.astype(str), index=["Active headcount"])
                        fig_hm = px.imshow(
                            pv1,
                            labels=dict(x="Tenure (months, binned)", y="", color="Active headcount"),
                            aspect="auto",
                            color_continuous_scale=TENURE_HEATMAP_COLORSCALE,
                            zmin=0,
                        )
                        style_tenure_heatmap(fig_hm)
                        st.plotly_chart(
                            chart_layout(fig_hm, "Heatmap — active employees by tenure month band (no bracket column)"),
                            use_container_width=True,
                        )
                    if col_tenure_bracket and not has_bracket:
                        st.caption(f"**{col_tenure_bracket}** has no values for active employees in this selection.")
                elif has_bracket:
                    vc_br = (
                        act_for_tenure[bracket_source_col]
                        .dropna()
                        .astype(str)
                        .str.strip()
                    )
                    vc_br = vc_br[vc_br.str.len() > 0]
                    if not vc_br.empty:
                        ord_idx = sorted(vc_br.unique().tolist(), key=bracket_sort_key)
                        ctab_br = vc_br.value_counts().reindex(ord_idx).dropna()
                        pv_b = pd.DataFrame([ctab_br.values], columns=ctab_br.index.astype(str), index=["Active headcount"])
                        fig_hb = px.imshow(
                            pv_b,
                            labels=dict(x="Tenure bracket", y="", color="Active headcount"),
                            aspect="auto",
                            color_continuous_scale=TENURE_HEATMAP_COLORSCALE,
                            zmin=0,
                        )
                        style_tenure_heatmap(fig_hb)
                        st.plotly_chart(
                            chart_layout(fig_hb, "Heatmap — active employees by tenure bracket (no months column)"),
                            use_container_width=True,
                        )
                    if col_tenure and not has_months:
                        st.caption(
                            f"**{col_tenure}** has no parseable month values for active employees in this selection."
                        )
                else:
                    st.caption(
                        "No tenure months or bracket values for active employees in this selection — check **TENURE** / **TENURE BRACKET** cells."
                    )

        with st.expander("📥 Download tenure report (Excel) — active employees & bracket / months", expanded=False):
            st.caption(
                "File matches your **current filters** (search, company, calendar date range, workforce). "
                "Counts and rows use **active** employees when **CURRENT STATUS** exists."
            )
            act_export = filtered_df.loc[active_mask].copy() if col_status else filtered_df.copy()
            if col_status and act_export.empty:
                st.warning("No active employees in the current selection — adjust filters or switch workforce to **All** / **Active**.")
            else:
                ds_part = date_filter_info
                if col_date_filter and date_sel is not None and not ds_part:
                    if isinstance(date_sel, tuple) and len(date_sel) == 2:
                        ds_part = f"{date_sel[0]} → {date_sel[1]}"
                    else:
                        ds_part = str(date_sel)
                info_data = {
                    "Item": [
                        "Exported at",
                        "Workforce filter",
                        "Company",
                        "Search text",
                        "Date column used",
                        "Date range applied",
                        "Rows exported (active / filtered)",
                        "Status column",
                    ],
                    "Value": [
                        datetime.now().strftime("%Y-%m-%d %H:%M"),
                        str(workforce_filter),
                        str(comp),
                        search if search else "(none)",
                        str(col_date_filter) if col_date_filter else "(none)",
                        ds_part if ds_part else "(full range or no date filter)",
                        f"{len(act_export):,}",
                        "Yes — active only" if col_status else "No — all filtered rows",
                    ],
                }
                df_info_export = pd.DataFrame(info_data)

                drop_internal = [
                    c
                    for c in act_export.columns
                    if str(c).startswith("_HR_") or str(c) == "IS_ATTRITION"
                ]
                detail_export = act_export.drop(columns=drop_internal, errors="ignore").copy()
                if "TENURE_NUM" in detail_export.columns:
                    detail_export = detail_export.rename(columns={"TENURE_NUM": "Tenure (months)"})

                bio = BytesIO()
                try:
                    with pd.ExcelWriter(bio, engine="openpyxl") as writer:
                        df_info_export.to_excel(writer, sheet_name="Export_info", index=False)
                        detail_export.to_excel(writer, sheet_name="Employee_detail", index=False)

                        export_br_col = None
                        if "bracket_source_col" in locals() and bracket_source_col:
                            export_br_col = bracket_source_col
                        elif col_tenure_bracket:
                            export_br_col = col_tenure_bracket
                        elif "_TENURE_BRACKET_AUTO" in act_export.columns:
                            export_br_col = "_TENURE_BRACKET_AUTO"

                        if export_br_col:
                            br_raw = act_export[export_br_col].astype(str).str.strip()
                            br_raw = br_raw.replace({"nan": "", "None": ""})
                            valid_b = br_raw[br_raw.str.len() > 0]
                            if not valid_b.empty:
                                summ_b = valid_b.value_counts().reset_index()
                                summ_b.columns = ["Tenure bracket", "Active headcount"]
                                summ_b = summ_b.sort_values(
                                    "Tenure bracket",
                                    key=lambda s: s.map(
                                        lambda x: (
                                            int(m.group(1))
                                            if (m := re.search(r"(\d+)", str(x).upper()))
                                            else 99999
                                        )
                                    ),
                                )
                                summ_b.to_excel(writer, sheet_name="Bracket_counts", index=False)
                            else:
                                pd.DataFrame(
                                    {"Note": ["No tenure bracket values for active rows in this filter."]}
                                ).to_excel(writer, sheet_name="Bracket_counts", index=False)

                        if (
                            export_br_col
                            and "TENURE_NUM" in act_export.columns
                            and act_export["TENURE_NUM"].notna().any()
                        ):
                            gx = act_export.dropna(subset=["TENURE_NUM"]).copy()
                            gx["_BR"] = gx[export_br_col].astype(str).str.strip()
                            gx["_BR"] = gx["_BR"].replace({"nan": "", "None": ""})
                            gx = gx[gx["_BR"].str.len() > 0]
                            if not gx.empty:
                                cross = (
                                    gx.groupby(["TENURE_NUM", "_BR"], dropna=False)
                                    .size()
                                    .reset_index(name="Active headcount")
                                )
                                cross = cross.rename(
                                    columns={
                                        "TENURE_NUM": "Tenure (months)",
                                        "_BR": "Tenure bracket",
                                    }
                                )
                                cross = cross.sort_values(
                                    ["Tenure (months)", "Tenure bracket"]
                                )
                                cross.to_excel(writer, sheet_name="Months_by_bracket", index=False)
                            else:
                                pd.DataFrame(
                                    {
                                        "Note": [
                                            "No rows with both tenure months and bracket for this filter."
                                        ]
                                    }
                                ).to_excel(writer, sheet_name="Months_by_bracket", index=False)
                        elif "TENURE_NUM" in act_export.columns and act_export["TENURE_NUM"].notna().any():
                            tm_only = (
                                act_export.dropna(subset=["TENURE_NUM"])[["TENURE_NUM"]]
                                .rename(columns={"TENURE_NUM": "Tenure (months)"})
                                .groupby("Tenure (months)", as_index=False)
                                .size()
                                .rename(columns={"size": "Active headcount"})
                            )
                            tm_only.to_excel(writer, sheet_name="Months_by_bracket", index=False)

                    bio.seek(0)
                    fn = f"HR_Active_Tenure_{datetime.now().strftime('%Y%m%d_%H%M')}.xlsx"
                    st.download_button(
                        label="Download Excel report",
                        data=bio,
                        file_name=fn,
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        key="download_tenure_excel",
                    )
                except Exception as ex:
                    st.error(f"Could not build Excel file: {ex}")
                    csv_buf = detail_export.to_csv(index=False).encode("utf-8-sig")
                    st.download_button(
                        label="Download CSV (employee detail only)",
                        data=csv_buf,
                        file_name=f"HR_Active_Tenure_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
                        mime="text/csv",
                        key="download_tenure_csv_fallback",
                    )

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
            _disp = filtered_df.drop(
                columns=[c for c in filtered_df.columns if str(c).startswith("_HR_")],
                errors="ignore",
            )
            st.dataframe(_disp, use_container_width=True)

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
    # 📋 EMPLOYEE SCORECARD MONITORING
    # =========================================================
    if page == "📋 Employee Scorecard Monitoring":
        st.title("📋 Employee Scorecard Monitoring")

        if not col_name:
            st.warning("No employee name column detected. Add a **NAME** column in HR DATABASE.")
        else:
            work_df = df.copy()
            if col_status:
                scorecard_scope = st.radio(
                    "Scope",
                    ["All", "Active only", "Inactive only"],
                    horizontal=True,
                    index=0,
                    key="scorecard_scope",
                )
                mask_sc = compute_active_mask(work_df[col_status])
                if scorecard_scope == "Active only":
                    work_df = work_df.loc[mask_sc].copy()
                elif scorecard_scope == "Inactive only":
                    work_df = work_df.loc[~mask_sc].copy()

            if work_df.empty:
                st.info("No rows available for this scorecard scope.")
            else:
                name_series = work_df[col_name].astype(str).str.strip()
                name_series = name_series[name_series != ""]
                all_names = sorted(name_series.dropna().unique().tolist())
                if not all_names:
                    st.info("No employee names available.")
                else:
                    q = st.text_input(
                        "Find employee",
                        key="scorecard_find_employee",
                        placeholder="Type employee name...",
                    ).strip().lower()
                    filtered_names = [n for n in all_names if q in n.lower()] if q else all_names
                    if not filtered_names:
                        st.warning("No employee matched your search.")
                    else:
                        picked_name = st.selectbox(
                            "Select employee",
                            filtered_names,
                            key="scorecard_employee_select",
                        )
                        emp_rows = work_df[work_df[col_name].astype(str).str.strip() == picked_name].copy()
                        if emp_rows.empty:
                            st.info("No records found for selected employee.")
                        else:
                            if col_date_filter and "_HR_FILTER_DATE" in emp_rows.columns:
                                emp_rows = emp_rows.sort_values("_HR_FILTER_DATE")
                            latest = emp_rows.iloc[-1]

                            def safe_get(col, default="—"):
                                if not col or col not in emp_rows.columns:
                                    return default
                                v = latest[col]
                                if pd.isna(v):
                                    return default
                                sv = str(v).strip()
                                return sv if sv else default

                            risk_val = float(latest["RISK_SCORE"]) if "RISK_SCORE" in emp_rows.columns and pd.notna(latest["RISK_SCORE"]) else 0.0
                            if risk_val >= 0.70:
                                risk_band = "High"
                            elif risk_val >= 0.40:
                                risk_band = "Medium"
                            else:
                                risk_band = "Low"

                            c1, c2, c3, c4 = st.columns(4)
                            c1.metric("Employee", picked_name)
                            c2.metric("Status", safe_get(col_status))
                            c3.metric("Risk score", f"{risk_val:.2f}")
                            c4.metric("Risk band", risk_band)

                            c5, c6, c7, c8 = st.columns(4)
                            c5.metric("Company", safe_get(col_company))
                            c6.metric("Department", safe_get(col_dept))
                            c7.metric("Branch", safe_get(col_branch))
                            if "TENURE_NUM" in emp_rows.columns and pd.notna(latest.get("TENURE_NUM", np.nan)):
                                c8.metric("Tenure (months)", f"{float(latest['TENURE_NUM']):.0f}")
                            else:
                                c8.metric("Tenure (months)", "—")

                            st.markdown("#### Weekly scorecard card")
                            st.caption(
                                "Employee is selected from HR DATABASE (no manual typing), so input mistakes on name are prevented."
                            )
                            st.info(
                                "Input rule: Fill only **Week 1-4 Actual Achievement**. "
                                "**Weekly Target** is instruction text and **Weight %** is scoring guide. "
                                "Enter achieved points using the weight guide (example full achievement: 40, 12, 12, 6, 7.5, 7.5, 7.5, 7.5). "
                                "**Score %**, **TOTAL ACHIEVEMENT FTM %**, and **Weighted Achievement %** are auto-computed."
                            )

                            if "scorecard_input_cache" not in st.session_state:
                                st.session_state.scorecard_input_cache = {}
                            if "scorecard_data_cache" not in st.session_state:
                                st.session_state.scorecard_data_cache = {}
                            cache_key = f"sc::{picked_name}"

                            if cache_key not in st.session_state.scorecard_data_cache:
                                scorecard_rows = [
                                    {"Category": "Production", "Metric": "UDI Target", "Weekly Target": "100% of weekly target", "Weight %": 40.0},
                                    {"Category": "Activity", "Metric": "Client Visits", "Weekly Target": "15-20 Clients daily", "Weight %": 12.0},
                                    {"Category": "Activity", "Metric": "Contact Rate", "Weekly Target": "At least 50% of visited clients daily", "Weight %": 12.0},
                                    {"Category": "Activity", "Metric": "Flyering", "Weekly Target": "Minimum 5 contacts daily", "Weight %": 6.0},
                                    {"Category": "Pipeline", "Metric": "Borrower Reactivation (Renewal)", "Weekly Target": "At least 4 per month (Renewal)", "Weight %": 7.5},
                                    {"Category": "Pipeline", "Metric": "Active Pipeline", "Weekly Target": "7-10 Accounts with 1-2 accounts converted per week", "Weight %": 7.5},
                                    {"Category": "Compliance", "Metric": "Attendance", "Weekly Target": "Zero absence", "Weight %": 7.5},
                                    {"Category": "Compliance", "Metric": "Zero Complaint", "Weekly Target": "No validated client complaint", "Weight %": 7.5},
                                ]
                                seed_df = pd.DataFrame(scorecard_rows)
                                for w in [1, 2, 3, 4]:
                                    seed_df[f"Week {w} Actual Achievement"] = 0.0
                                seed_df["Notes"] = ""
                                st.session_state.scorecard_data_cache[cache_key] = seed_df

                            card_df = st.session_state.scorecard_data_cache[cache_key].copy()

                            if "scorecard_calc_cache" not in st.session_state:
                                st.session_state.scorecard_calc_cache = {}

                            with st.form(key=f"scorecard_form::{picked_name}", clear_on_submit=False):
                                edited = st.data_editor(
                                    card_df,
                                    use_container_width=True,
                                    hide_index=True,
                                    num_rows="fixed",
                                    key=f"scorecard_card_editor::{picked_name}",
                                    column_config={
                                        "Category": st.column_config.TextColumn(disabled=True),
                                        "Metric": st.column_config.TextColumn(disabled=True),
                                        "Weight %": st.column_config.NumberColumn(disabled=True, format="%.1f", help="Percentage contribution to final weighted score."),
                                    "Weekly Target": st.column_config.TextColumn(disabled=True, help="Instruction/guide on expected weekly output."),
                                        "Week 1 Actual Achievement": st.column_config.NumberColumn(format="%.2f"),
                                        "Week 2 Actual Achievement": st.column_config.NumberColumn(format="%.2f"),
                                        "Week 3 Actual Achievement": st.column_config.NumberColumn(format="%.2f"),
                                        "Week 4 Actual Achievement": st.column_config.NumberColumn(format="%.2f"),
                                        "Notes": st.column_config.TextColumn(),
                                    },
                                )
                                compute_clicked = st.form_submit_button("Compute scorecard")

                            if compute_clicked:
                                st.session_state.scorecard_data_cache[cache_key] = edited.copy()
                                st.session_state.scorecard_input_cache[cache_key] = edited.copy()
                                calc = edited.copy()
                                for col in ["Week 1 Actual Achievement", "Week 2 Actual Achievement", "Week 3 Actual Achievement", "Week 4 Actual Achievement", "Weight %"]:
                                    calc[col] = pd.to_numeric(calc[col], errors="coerce").fillna(0.0)

                                for w in [1, 2, 3, 4]:
                                    wk_col = f"Week {w} Actual Achievement"
                                    # Week input is achieved points using Weight % guide.
                                    # Full achievement for a row means week input equals that row's Weight %.
                                    # Convert to score percent then apply weight.
                                    denom = calc["Weight %"].replace(0, np.nan)
                                    ratio_pct = (calc[wk_col] / denom) * 100.0
                                    score = np.nan_to_num(np.clip(ratio_pct, 0.0, 100.0), nan=0.0)
                                    calc[f"Week {w} Score %"] = score

                                calc["TOTAL ACHIEVEMENT FTM %"] = calc[
                                    [f"Week {w} Score %" for w in [1, 2, 3, 4]]
                                ].mean(axis=1)
                                calc["Weighted Achievement %"] = calc["TOTAL ACHIEVEMENT FTM %"] * (calc["Weight %"] / 100.0)

                                total_weighted = float(calc["Weighted Achievement %"].sum())
                                wk_totals = {
                                    w: float((calc[f"Week {w} Score %"] * (calc["Weight %"] / 100.0)).sum())
                                    for w in [1, 2, 3, 4]
                                }
                                by_cat = (
                                    calc.groupby("Category", dropna=False)["Weighted Achievement %"]
                                    .sum()
                                    .reset_index()
                                    .sort_values("Weighted Achievement %", ascending=False)
                                )
                                st.session_state.scorecard_calc_cache[cache_key] = {
                                    "calc": calc.copy(),
                                    "by_cat": by_cat.copy(),
                                    "total_weighted": total_weighted,
                                    "wk_totals": wk_totals,
                                }

                            calc_pack = st.session_state.scorecard_calc_cache.get(cache_key)
                            if calc_pack:
                                calc = calc_pack["calc"]
                                by_cat = calc_pack["by_cat"]
                                total_weighted = float(calc_pack["total_weighted"])
                                wk_totals = calc_pack["wk_totals"]

                                s1, s2, s3 = st.columns(3)
                                s1.metric("Selected employee", picked_name)
                                s2.metric("Total weighted score", f"{total_weighted:.2f}%")
                                s3.metric("Rows tracked", f"{len(calc)}")

                                r1, r2, r3, r4, r5 = st.columns(5)
                                r1.metric("Week 1 weighted", f"{wk_totals[1]:.2f}%")
                                r2.metric("Week 2 weighted", f"{wk_totals[2]:.2f}%")
                                r3.metric("Week 3 weighted", f"{wk_totals[3]:.2f}%")
                                r4.metric("Week 4 weighted", f"{wk_totals[4]:.2f}%")
                                r5.metric("FTM weighted result", f"{total_weighted:.2f}%")
                                if total_weighted <= 0:
                                    st.warning(
                                        "No computed result yet. Fill **Weekly Target** and either **Actual** or week achievement fields."
                                    )

                                with st.expander("Computed scorecard report", expanded=True):
                                    report_cols = [
                                        "Category",
                                        "Metric",
                                        "Weight %",
                                        "Weekly Target",
                                        "Week 1 Actual Achievement",
                                        "Week 1 Score %",
                                        "Week 2 Actual Achievement",
                                        "Week 2 Score %",
                                        "Week 3 Actual Achievement",
                                        "Week 3 Score %",
                                        "Week 4 Actual Achievement",
                                        "Week 4 Score %",
                                        "TOTAL ACHIEVEMENT FTM %",
                                        "Weighted Achievement %",
                                        "Notes",
                                    ]
                                    st.dataframe(calc[report_cols], use_container_width=True, hide_index=True)
                                    st.caption("Category contribution to final weighted score")
                                    st.dataframe(by_cat, use_container_width=True, hide_index=True)
                            else:
                                st.caption("Click **Compute scorecard** after input to render results.")

                            reporting_month_input = st.text_input(
                                "Reporting month for MR productivity (YYYY-MM)",
                                value=datetime.now().strftime("%Y-%m"),
                                key=f"scorecard_reporting_month::{picked_name}",
                                help="Used when aggregating monthly FTM in MR Productivity Monitoring. "
                                "Defaults to calendar month; change if this scorecard is for another period.",
                            )
                            if "scorecard_saved_snapshots" not in st.session_state:
                                st.session_state.scorecard_saved_snapshots = []
                            if st.button("Save scorecard snapshot", key=f"save_scorecard_snapshot::{picked_name}"):
                                calc_pack = st.session_state.scorecard_calc_cache.get(cache_key)
                                if not calc_pack:
                                    st.warning("Compute scorecard first before saving snapshot.")
                                else:
                                    rm_txt = (reporting_month_input or "").strip()
                                    try:
                                        pd.Period(rm_txt, freq="M")
                                        rm_out = rm_txt[:7] if len(rm_txt) >= 7 else datetime.now().strftime("%Y-%m")
                                    except (ValueError, TypeError):
                                        rm_out = datetime.now().strftime("%Y-%m")
                                        st.warning(f"Invalid YYYY-MM — saved under **{rm_out}** instead.")
                                    snapshot = calc_pack["calc"].copy()
                                    snapshot.insert(0, "Employee", picked_name)
                                    snapshot.insert(1, "Saved at", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                                    snapshot["REPORTING_MONTH"] = rm_out
                                    st.session_state.scorecard_saved_snapshots.append(snapshot)
                                    st.success("Scorecard snapshot saved.")

                            if st.session_state.scorecard_saved_snapshots:
                                snap_all = pd.concat(st.session_state.scorecard_saved_snapshots, ignore_index=True)
                                with st.expander("Saved scorecard snapshots", expanded=False):
                                    st.dataframe(snap_all, use_container_width=True, hide_index=True)

                            st.markdown("#### Scorecard profile")
                            profile_cols = [c for c in [col_name, col_company, col_dept, col_branch, col_position, col_status, col_age] if c and c in emp_rows.columns]
                            if "TENURE_NUM" in emp_rows.columns:
                                profile_cols.append("TENURE_NUM")
                            if "RISK_SCORE" in emp_rows.columns:
                                profile_cols.append("RISK_SCORE")
                            profile_tbl = emp_rows[profile_cols].copy().tail(10)
                            st.dataframe(profile_tbl, use_container_width=True)

                            st.markdown("#### Monitoring")
                            if col_dept and col_dept in emp_rows.columns:
                                dept_val = safe_get(col_dept, "")
                                if dept_val and dept_val != "—":
                                    peers = work_df[work_df[col_dept].astype(str).str.strip() == dept_val].copy()
                                    if not peers.empty and "RISK_SCORE" in peers.columns:
                                        peer_cols = [c for c in [col_name, col_status, col_position] if c and c in peers.columns] + ["RISK_SCORE"]
                                        peers = peers.sort_values("RISK_SCORE", ascending=False)
                                        st.caption(f"Top risk employees in **{dept_val}**")
                                        st.dataframe(peers[peer_cols].head(20), use_container_width=True)
                                    else:
                                        st.caption("No peer risk data available for this department.")
                                else:
                                    st.caption("Department not available for selected employee.")

    # =========================================================
    # 📈 MR PRODUCTIVITY MONITORING
    # =========================================================
    if page == "📈 MR Productivity Monitoring":
        st.title("📈 MR Productivity Monitoring")
        st.caption(
            "Pulls **FTM weighted result** from snapshots saved under **Employee Scorecard Monitoring**. "
            "Each row is one reporting month per employee (latest snapshot wins if you save twice for the same month)."
        )
        if MR_PRODUCTIVITY_SAMPLE_PATH.is_file():
            with st.expander("MR Productivity sample workbook (`MR Productivity_Sample Data.xlsx`)", expanded=False):
                st.caption(f"Local file: `{MR_PRODUCTIVITY_SAMPLE_PATH}` — reference layout / production fields (not auto-imported into the MR table).")
                try:
                    _mr_prev = pd.read_excel(MR_PRODUCTIVITY_SAMPLE_PATH, sheet_name="Sheet1", header=2)
                    st.dataframe(_mr_prev.head(40), use_container_width=True, height=480)
                except Exception as _ex:
                    st.warning(f"Could not read sample Sheet1 (header row 3): {_ex}")
        if PIP_FORM_TEMPLATE_PATH.is_file():
            st.success("PIP Word export will use your template **PIP_Form Revised.docx** in this folder.")
        ep_global = st.number_input(
            "Default Expected Production (EP) %",
            min_value=1.0,
            max_value=500.0,
            value=100.0,
            step=1.0,
            key="mr_ep_global",
            help="FTM weighted result is compared to EP (100 = full expected production). "
            "If HR DATABASE has EP / Expected Production column, it overrides this per employee.",
        )
        if col_ep:
            st.caption(f"Per-employee EP detected: **{col_ep}** (when cell is numeric and non-zero).")

        snaps = st.session_state.get("scorecard_saved_snapshots") or []
        monthly_src = _scorecard_snapshots_to_monthly_ftm(snaps)
        if monthly_src.empty:
            st.warning(
                "No usable scorecard snapshots yet. Open **Employee Scorecard Monitoring**, **Compute scorecard**, "
                "confirm **Reporting month (YYYY-MM)**, then **Save scorecard snapshot**. "
                "Repeat for multiple months to evaluate rolling PIP rules."
            )
        else:

            def _mr_ep_lookup(emp_name: str) -> float:
                if not col_name or df.empty:
                    return float(ep_global)
                hit = df[df[col_name].astype(str).str.strip() == str(emp_name).strip()]
                if hit.empty or not col_ep or col_ep not in df.columns:
                    return float(ep_global)
                v = pd.to_numeric(hit.iloc[-1][col_ep], errors="coerce")
                if pd.isna(v) or float(v) == 0:
                    return float(ep_global)
                return float(v)

            enriched_parts = []
            for emp, sub in monthly_src.groupby("Employee"):
                enriched_parts.append(_enrich_mr_productivity_monthly(sub.copy(), _mr_ep_lookup(emp)))
            full_m = pd.concat(enriched_parts, ignore_index=True) if enriched_parts else pd.DataFrame()

            st.markdown("#### Monthly results & PIP logic")
            show_cols = [
                "Employee",
                "YearMonthStr",
                "FTM_WEIGHTED_RESULT",
                "EP_ref",
                "Pct_of_EP",
                "MR_Band",
                "Below_Standard",
                "PIP_Trigger",
                "PIP_3Mo_Review",
                "Second_PIP_Within_12mo_Terminate",
            ]
            disp = full_m[[c for c in show_cols if c in full_m.columns]].copy()
            st.dataframe(disp, use_container_width=True, hide_index=True)

            st.markdown("#### Policy reference")
            st.markdown(
                """
- **Above Standard:** ≥ 100% of EP → **Retain**
- **Within Standard:** 80%–99% of EP → **Developmental coaching & training**
- **Below Standard:** < 80% of EP → **PIP trigger + corrective coaching**
- **PIP trigger:** **2 consecutive** months below standard **or** **2 of the last 3** months below (rolling)
- **PIP duration / review window:** the **next 3 consecutive months** in this timeline (after a trigger row)
- **PIP exit:** at least **2 of those 3** months must be **Within Standard or better**; otherwise **terminate**
- **Second PIP trigger within 12 months:** **terminate**
"""
            )

            c_mr1, c_mr2, c_mr3 = st.columns(3)
            with c_mr1:
                st.download_button(
                    "Download CSV (MR table)",
                    data=disp.to_csv(index=False).encode("utf-8-sig"),
                    file_name="mr_productivity_monitoring.csv",
                    mime="text/csv",
                    key="mr_csv_dl",
                )
            with c_mr2:
                xbio = BytesIO()
                try:
                    with pd.ExcelWriter(xbio, engine="openpyxl") as writer:
                        full_m.to_excel(writer, sheet_name="MR_Productivity", index=False)
                    xbio.seek(0)
                    st.download_button(
                        "Download Excel (MR table)",
                        data=xbio.getvalue(),
                        file_name="mr_productivity_monitoring.xlsx",
                        key="mr_xlsx_dl",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    )
                except ImportError:
                    st.caption("Install **openpyxl** for Excel export (`pip install openpyxl`).")
            with c_mr3:
                if HAS_PYTHON_DOCX:
                    ep_note = f"default {ep_global:.1f}%"
                    if col_ep:
                        ep_note += f"; column **{col_ep}** when set per employee"
                    wdoc = _build_mr_productivity_sample_docx(disp, ep_note)
                    st.download_button(
                        "Download Word — MR summary (form style)",
                        data=wdoc.getvalue(),
                        file_name="MR_Productivity_Monitoring_Summary.docx",
                        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        key="mr_word_summary",
                    )
                else:
                    st.caption("Install **python-docx** for Word export.")

            st.markdown("#### PIP form (fill-style Word)")
            emp_picks = sorted(full_m["Employee"].dropna().unique().tolist()) if not full_m.empty else []
            if emp_picks and HAS_PYTHON_DOCX:
                pick_pip = st.selectbox("Employee for PIP form export", emp_picks, key="mr_pip_form_emp")
                sub_p = full_m[full_m["Employee"] == pick_pip].sort_values("Year-Month")
                last_r = sub_p.iloc[-1]
                dept_v, pos_v, branch_v, date_hired_v = "", "", "", ""
                if col_name and pick_pip and not df.empty:
                    er = df[df[col_name].astype(str).str.strip() == str(pick_pip).strip()]
                    if not er.empty:
                        lr = er.iloc[-1]
                        if col_dept and col_dept in df.columns:
                            dept_v = str(lr.get(col_dept, "") or "")
                        if col_position and col_position in df.columns:
                            pos_v = str(lr.get(col_position, "") or "")
                        if col_branch and col_branch in df.columns:
                            branch_v = str(lr.get(col_branch, "") or "")
                        if col_hire_date and col_hire_date in df.columns:
                            hd = lr.get(col_hire_date)
                            if pd.notna(hd):
                                date_hired_v = str(hd)[:19]
                trig_basis = []
                if last_r.get("PIP_Trigger"):
                    trig_basis.append("Rule fired on this row (2 consecutive below or 2 of last 3 below).")
                if last_r.get("Below_Standard"):
                    trig_basis.append("Month classified below standard vs EP.")
                hist_ftm = []
                for _, rr in sub_p.tail(3).iterrows():
                    hist_ftm.append(f"{float(rr['FTM_WEIGHTED_RESULT']):.2f}%")
                while len(hist_ftm) < 3:
                    hist_ftm.insert(0, "—")
                ym = last_r.get("Year-Month")
                try:
                    pip_from = str(ym)
                    pip_to = str(ym + 2)
                except Exception:
                    pip_from = str(last_r.get("YearMonthStr", ""))
                    pip_to = ""
                ctx = {
                    "dept": dept_v,
                    "department": dept_v,
                    "position": pos_v,
                    "branch": " / ".join(x for x in [dept_v, branch_v] if x),
                    "date_hired": date_hired_v,
                    "manager": "",
                    "pip_enrollment": datetime.now().strftime("%Y-%m-%d"),
                    "pip_from": pip_from,
                    "pip_to": pip_to,
                    "month": str(last_r.get("YearMonthStr", "")),
                    "ftm": f"{float(last_r.get('FTM_WEIGHTED_RESULT', 0)):.2f}",
                    "ep": f"{float(last_r.get('EP_ref', ep_global)):.1f}",
                    "pct_ep": f"{float(last_r.get('Pct_of_EP', 0)):.1f}",
                    "band": str(last_r.get("MR_Band", "")),
                    "trigger_basis": " ".join(trig_basis) if trig_basis else " _________________________ ",
                    "month1_prod": hist_ftm[0],
                    "month2_prod": hist_ftm[1],
                    "month3_prod": hist_ftm[2],
                }
                _calc_pack = st.session_state.get("scorecard_calc_cache", {}).get(f"sc::{pick_pip}")
                _calc_df = _calc_pack.get("calc") if isinstance(_calc_pack, dict) else None
                pip_doc = _build_pip_form_docx(pick_pip, ctx, _calc_df)
                st.download_button(
                    "Download PIP form (Word)",
                    data=pip_doc.getvalue(),
                    file_name=f"PIP_Form_{pick_pip.replace(' ', '_')}.docx",
                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    key="mr_pip_word",
                )
                if PIP_FORM_TEMPLATE_PATH.is_file():
                    st.caption(
                        "Opens your **PIP_Form Revised.docx** layout: header table, production lines, "
                        "and scorecard grid (if you computed the scorecard for this employee in this session)."
                    )
                else:
                    st.caption("Add **PIP_Form Revised.docx** next to `hrapp.py` to use your official template.")
            elif not emp_picks:
                pass
            else:
                st.info("Install **python-docx** for PIP Word export: `pip install python-docx`")

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
