import os
import threading
import time
import smtplib
from datetime import date, datetime
import pandas as pd
import base64
import plotly.express as px
import plotly.graph_objects as go
import requests
import snowflake.connector
import streamlit as st
from openai import OpenAI
from streamlit_lottie import st_lottie

try:
    from streamlit_plotly_events import plotly_events
except ImportError:
    plotly_events = None

try:
    import streamlit as st  # redundant but harmless
except Exception as e:
    print(e)

# ---------------------------------------------------
# PAGE CONFIG
# ---------------------------------------------------

st.set_page_config(page_title="Audit Portal", page_icon="🔎", layout="wide")

# ---------------------------------------------------
# ANIMATED BACKGROUND
# ---------------------------------------------------

st.markdown(
    """
<style>
.audit-img{
    animation: float 6s ease-in-out infinite;
}

@keyframes float{
    0%{transform: translateY(0px);}
    50%{transform: translateY(-20px);}
    100%{transform: translateY(0px);}
}

.stApp {
    background: linear-gradient(-45deg, #0f2027, #203a43, #2c5364, #1c1c1c);
    background-size: 400% 400%;
    animation: gradient 15s ease infinite;
}

@keyframes gradient {
    0% {background-position: 0% 50%;}
    50% {background-position: 100% 50%;}
    100% {background-position: 0% 50%;}
}

.login-card {
    background-color: rgba(255,255,255,0.05);
    padding: 40px;
    border-radius: 15px;
    backdrop-filter: blur(10px);
    box-shadow: 0px 0px 25px rgba(0,0,0,0.4);
}

.clock-box{
    background:#3d5960;
    padding:18px;
    border-radius:20px;
    text-align:center;
    font-size:20px;
    font-weight:bold;
    color:white;
}
</style>
""",
    unsafe_allow_html=True,
)

# ---------------------------------------------------
# LOAD ANIMATION
# ---------------------------------------------------


def load_lottie(url: str):
    r = requests.get(url)
    if r.status_code != 200:
        return None
    return r.json()


lottie_login = load_lottie("https://assets10.lottiefiles.com/packages/lf20_jcikwtux.json")

# ---------------------------------------------------
# SNOWFLAKE CONNECTION
# ---------------------------------------------------


@st.cache_resource
def get_connection():
    return snowflake.connector.connect(
        user="jmcasaria",
        password=st.secrets["snowflake"]["password"],
        account="NSXAGQQ-WJ05543",
        warehouse="COMPUTE_WH",
        database="CFB_ANALYST_JAKE_DB",
        schema="PUBLIC",
        role="ANALYST_JAKE_ROLE",
    )


def get_cursor():
    conn = get_connection()
    return conn, conn.cursor()


# Cached loaders must live at module scope; nested @st.cache_data inside page functions
# does not dedupe across reruns, so every pane switch could re-hit Snowflake.
_DATA_CACHE_TTL = 180
_DASHBOARD_CACHE_TTL = 120


@st.cache_data(ttl=_DATA_CACHE_TTL, show_spinner=False)
def load_atp():
    conn, cursor = get_cursor()
    cursor.execute("SELECT * FROM ATP_CERTIFICATES")
    data = cursor.fetchall()
    columns = [
        "ID",
        "COMPANY",
        "AREA",
        "BRANCH",
        "SERVICE_INVOICE_SERIAL_NO",
        "BIR_DATE_RECEIVED_FIRST_STAMP",
        "BIR_DATE_RECEIVED_LAST_STAMP",
        "STATUS",
    ]
    return pd.DataFrame(data, columns=columns)


@st.cache_data(ttl=_DATA_CACHE_TTL, show_spinner=False)
def load_bir():
    conn, cursor = get_cursor()
    cursor.execute("SELECT * FROM BIR_1906_ATP")
    data = cursor.fetchall()
    columns = [
        "ID",
        "COMPANY",
        "BRANCH",
        "SERVICE_INVOICE_SERIAL_NO",
        "BIR_DATE_RECEIVED",
        "STATUS",
    ]
    return pd.DataFrame(data, columns=columns)


@st.cache_data(ttl=_DATA_CACHE_TTL, show_spinner=False)
def load_boa():
    conn, cursor = get_cursor()
    cursor.execute("SELECT * FROM BOA_STICKER")
    data = cursor.fetchall()
    columns = [
        "ID",
        "COMPANY",
        "BRANCH",
        "TIN",
        "BOOK_TO_REGISTER",
        "VOLUME_NUMBER",
        "DATE_FORWARDED_TO_BRANCH",
        "STATUS",
    ]
    return pd.DataFrame(data, columns=columns)


@st.cache_data(ttl=_DATA_CACHE_TTL, show_spinner=False)
def load_fire():
    """Supports either FSIC_CERTIFICATE_DATE / VALID_UNTIL / STATUS or FSIC_VALIDITY layout."""
    conn, cursor = get_cursor()
    columns = None
    try:
        cursor.execute(
            """
            SELECT ID, COMPANY, AREA, BRANCH,
                   FSIC_CERTIFICATE_DATE, VALID_UNTIL, FSIC_FEE, STATUS, REMARKS, FSIC_IMAGE
            FROM FIRE_SAFETY
            """
        )
        columns = [
            "ID",
            "COMPANY",
            "AREA",
            "BRANCH",
            "FSIC_CERTIFICATE_DATE",
            "VALID_UNTIL",
            "FSIC_FEE",
            "STATUS",
            "REMARKS",
            "FSIC_IMAGE",
        ]
    except Exception:
        try:
            conn, cursor = get_cursor()
            cursor.execute(
                """
                SELECT ID, COMPANY, AREA, BRANCH, FSIC_VALIDITY, FSIC_FEE, REMARKS, FSIC_IMAGE
                FROM FIRE_SAFETY
                """
            )
            columns = [
                "ID",
                "COMPANY",
                "AREA",
                "BRANCH",
                "FSIC_VALIDITY",
                "FSIC_FEE",
                "REMARKS",
                "FSIC_IMAGE",
            ]
        except Exception:
            conn, cursor = get_cursor()
            cursor.execute(
                """
                SELECT ID, COMPANY, AREA, BRANCH, FSIC_VALIDITY, FSIC_FEE, REMARKS
                FROM FIRE_SAFETY
                """
            )
            columns = [
                "ID",
                "COMPANY",
                "AREA",
                "BRANCH",
                "FSIC_VALIDITY",
                "FSIC_FEE",
                "REMARKS",
            ]
    data = cursor.fetchall()
    df = pd.DataFrame(data, columns=columns)
    if "FSIC_IMAGE" not in df.columns:
        df["FSIC_IMAGE"] = None
    return df


@st.cache_data(ttl=_DATA_CACHE_TTL, show_spinner=False)
def load_branch_tin(company):
    conn, cursor = get_cursor()
    base_cols = [
        "ID",
        "COMPANY",
        "TIN",
        "BRANCH_CODE",
        "RDO",
        "AREA",
        "BRANCH_NAME",
        "UPDATED_ADDRESS",
        "STATUS",
        "DATE_OPEN",
        "DATE_OF_CLOSURE",
    ]
    try:
        cursor.execute(
            """
            SELECT
                ID, COMPANY, TIN, BRANCH_CODE, RDO, AREA, BRANCH_NAME,
                UPDATED_ADDRESS, STATUS, DATE_OPEN, DATE_OF_CLOSURE, REMARKS
            FROM BRANCH_TIN_ADDRESS
            WHERE COMPANY = %s
            """,
            (company,),
        )
        columns = base_cols + ["REMARKS"]
    except Exception:
        conn, cursor = get_cursor()
        cursor.execute(
            """
            SELECT
                ID, COMPANY, TIN, BRANCH_CODE, RDO, AREA, BRANCH_NAME,
                UPDATED_ADDRESS, STATUS, DATE_OPEN, DATE_OF_CLOSURE
            FROM BRANCH_TIN_ADDRESS
            WHERE COMPANY = %s
            """,
            (company,),
        )
        columns = base_cols
    data = cursor.fetchall()
    df = pd.DataFrame(data, columns=columns)
    if "REMARKS" not in df.columns:
        df["REMARKS"] = None
    return df


@st.cache_data(ttl=_DATA_CACHE_TTL, show_spinner=False)
def load_business_permit_tracker(data_type):
    conn, cursor = get_cursor()
    cursor.execute(
        """
        SELECT
            COMPANY, AREA, BRANCH,
            CAF, PAYEE, PARTICULARS,
            MODE_OF_PAYMENT, AMOUNT,
            ACCOUNTING_DATE, TREASURY_DATE,
            STATUS,
            DATE_LIQUIDATION,
            DATE_SUBMISSION_ACCOUNTING,
            YEAR_COMPARISON,
            PERCENT_CHANGE,
            WITH_TAX_BILL,
            REASON_NOT_REQUESTING_FUND
        FROM BUSINESS_PERMIT_TRACKER
        WHERE TYPE = %s
        """,
        (data_type,),
    )
    cols = [
        "COMPANY",
        "AREA",
        "BRANCH",
        "CAF",
        "PAYEE",
        "PARTICULARS",
        "MODE_OF_PAYMENT",
        "AMOUNT",
        "ACCOUNTING_DATE",
        "TREASURY_DATE",
        "STATUS",
        "DATE_LIQUIDATION",
        "DATE_SUBMISSION_ACCOUNTING",
        "YEAR_COMPARISON",
        "PERCENT_CHANGE",
        "WITH_TAX_BILL",
        "REASON_NOT_REQUESTING_FUND",
    ]
    return pd.DataFrame(cursor.fetchall(), columns=cols)


@st.cache_data(ttl=_DATA_CACHE_TTL, show_spinner=False)
def load_business_permit_overview():
    conn, cursor = get_cursor()
    cursor.execute(
        """
        SELECT
            COMPANY,
            AREA,
            BRANCH,
            DEADLINE,
            DEADLINE_EXTENSION,
            BRGY_PERMIT_2025,
            BUSINESS_PERMIT_2025,
            SEC_CERT,
            GROSS_SALES_CERT
        FROM BUSINESS_PERMIT_OVERVIEW
        """
    )
    cols = [
        "COMPANY",
        "AREA",
        "BRANCH",
        "DEADLINE",
        "DEADLINE_EXTENSION",
        "BRGY_PERMIT_2025",
        "BUSINESS_PERMIT_2025",
        "SEC_CERT",
        "GROSS_SALES_CERT",
    ]
    return pd.DataFrame(cursor.fetchall(), columns=cols)


@st.cache_data(ttl=_DATA_CACHE_TTL, show_spinner=False)
def load_tax_mapped():
    conn, cursor = get_cursor()
    cursor.execute("SELECT * FROM TAX_MAPPED")
    data = cursor.fetchall()
    columns = [
        "ID",
        "AREA",
        "BRANCH",
        "DATE_TAX_MAPPED",
        "BIR_REMARKS",
        "STICKER_IMAGE",
    ]
    return pd.DataFrame(data, columns=columns)


@st.cache_data(ttl=_DATA_CACHE_TTL, show_spinner=False)
def load_sec():
    conn, cursor = get_cursor()
    db_has_company = False
    try:
        cursor.execute(
            """
            SELECT
                ID, AREA, MONTH_YEAR, BRANCH, COMPANY, REMARKS,
                DATE_FORWARDED, STATUS, DATE_RECEIVED, FINAL_STATUS
            FROM SECRETARY_CERTIFICATES
            """
        )
        columns = [
            "ID",
            "AREA",
            "MONTH_YEAR",
            "BRANCH",
            "COMPANY",
            "REMARKS",
            "DATE_FORWARDED",
            "STATUS",
            "DATE_RECEIVED",
            "FINAL_STATUS",
        ]
        db_has_company = True
    except Exception:
        conn, cursor = get_cursor()
        cursor.execute(
            """
            SELECT
                ID, AREA, MONTH_YEAR, BRANCH, REMARKS,
                DATE_FORWARDED, STATUS, DATE_RECEIVED, FINAL_STATUS
            FROM SECRETARY_CERTIFICATES
            """
        )
        columns = [
            "ID",
            "AREA",
            "MONTH_YEAR",
            "BRANCH",
            "REMARKS",
            "DATE_FORWARDED",
            "STATUS",
            "DATE_RECEIVED",
            "FINAL_STATUS",
        ]
    data = cursor.fetchall()
    df = pd.DataFrame(data, columns=columns)
    if "COMPANY" not in df.columns:
        df["COMPANY"] = None
    df.attrs["db_has_company_column"] = db_has_company
    return df


@st.cache_data(ttl=_DATA_CACHE_TTL, show_spinner=False)
def load_board():
    conn, cursor = get_cursor()
    cursor.execute(
        """
        SELECT
            ID,
            COMPANY,
            REMARKS,
            DATE_FORWARDED,
            DATE_RECEIVED,
            STATUS
        FROM BOARD_RESOLUTIONS
        """
    )
    data = cursor.fetchall()
    columns = [
        "ID",
        "COMPANY",
        "REMARKS",
        "DATE_FORWARDED",
        "DATE_RECEIVED",
        "STATUS",
    ]
    return pd.DataFrame(data, columns=columns)


@st.cache_data(ttl=_DASHBOARD_CACHE_TTL, show_spinner=False)
def load_dashboard_business_status():
    conn, cursor = get_cursor()
    cursor.execute(
        """
        SELECT STATUS, COUNT(*)
        FROM BUSINESS_PERMIT
        GROUP BY STATUS
        """
    )
    return pd.DataFrame(cursor.fetchall(), columns=["STATUS", "COUNT"])


@st.cache_data(ttl=_DASHBOARD_CACHE_TTL, show_spinner=False)
def load_dashboard_area_distribution():
    conn, cursor = get_cursor()
    cursor.execute(
        """
        SELECT AREA, COUNT(*)
        FROM BRANCH_TIN_ADDRESS
        GROUP BY AREA
        """
    )
    return pd.DataFrame(cursor.fetchall(), columns=["AREA", "COUNT"])


@st.cache_data(ttl=_DASHBOARD_CACHE_TTL, show_spinner=False)
def load_dashboard_overview_sla():
    conn, cursor = get_cursor()
    cursor.execute(
        """
        SELECT COMPANY, AREA, BRANCH,
               DEADLINE, DEADLINE_EXTENSION,
               BUSINESS_PERMIT_2025,
               BRGY_PERMIT_2025,
               SEC_CERT,
               GROSS_SALES_CERT
        FROM BUSINESS_PERMIT_OVERVIEW
        """
    )
    cols = [
        "COMPANY",
        "AREA",
        "BRANCH",
        "DEADLINE",
        "DEADLINE_EXTENSION",
        "BUSINESS_PERMIT_2025",
        "BRGY_PERMIT_2025",
        "SEC_CERT",
        "GROSS_SALES_CERT",
    ]
    return pd.DataFrame(cursor.fetchall(), columns=cols)


@st.cache_data(ttl=_DASHBOARD_CACHE_TTL, show_spinner=False)
def load_dashboard_pending():
    conn, cursor = get_cursor()
    cursor.execute(
        """
        SELECT COMPANY, AREA, BRANCH, STATUS
        FROM BUSINESS_PERMIT
        WHERE STATUS != 'DONE'
        """
    )
    return pd.DataFrame(cursor.fetchall(), columns=["COMPANY", "AREA", "BRANCH", "STATUS"])


def filter_dataframe_controls(df: pd.DataFrame, key_prefix: str, categorical_cols: list) -> pd.DataFrame:
    """Multiselect filters + optional text search; returns a filtered view (editor still edits visible rows)."""
    if df is None or df.empty:
        return df
    present = [c for c in categorical_cols if c in df.columns]
    out = df
    with st.expander("Filter table", expanded=False):
        fcols = st.columns(min(4, max(1, len(present))))
        for i, col in enumerate(present):
            with fcols[i % len(fcols)]:
                opts = sorted(out[col].dropna().astype(str).unique().tolist())
                if len(opts) > 150:
                    needle = st.text_input(
                        f"{col} contains",
                        key=f"{key_prefix}_txt_{col}",
                        placeholder="Substring…",
                    )
                    if needle:
                        out = out[
                            out[col]
                            .astype(str)
                            .str.contains(needle, case=False, na=False)
                        ]
                else:
                    picked = st.multiselect(
                        col,
                        options=opts,
                        key=f"{key_prefix}_ms_{col}",
                        help="Leave empty to show all",
                    )
                    if picked:
                        out = out[out[col].astype(str).isin(picked)]
        q = st.text_input(
            "Search (any text column)",
            key=f"{key_prefix}_glob_search",
            placeholder="Optional…",
        )
        if q:
            mask = False
            for col in out.columns:
                if col in ("STICKER_IMAGE", "FSIC_IMAGE"):
                    continue
                if out[col].dtype == object or pd.api.types.is_string_dtype(out[col]):
                    mask = mask | out[col].astype(str).str.contains(q, case=False, na=False)
            out = out[mask]
    return out


def reconcile_editor_after_filter(
    base_df: pd.DataFrame,
    view_df: pd.DataFrame,
    edited_df: pd.DataFrame,
    id_col: str,
) -> pd.DataFrame:
    """
    When data_editor was given a filtered view, rebuild the full row set for save logic:
    unchanged rows stay from base_df; visible rows use edits; rows removed from the editor
    while visible are treated as deletes; new rows (no id) are appended.
    """
    if base_df is None or base_df.empty:
        return edited_df.copy() if edited_df is not None else pd.DataFrame()
    if edited_df is None:
        return base_df.copy()
    merged = base_df.copy()
    if id_col not in merged.columns or id_col not in view_df.columns:
        return edited_df.copy()

    vis_ids = pd.to_numeric(view_df[id_col], errors="coerce").dropna().astype(int)
    visible_ids = set(vis_ids.tolist())

    ed = edited_df.copy()
    ed[id_col] = pd.to_numeric(ed[id_col], errors="coerce")
    edited_ids = set(ed.loc[ed[id_col].notna(), id_col].astype(int).tolist())

    removed_visible = visible_ids - edited_ids
    if removed_visible:
        mid = pd.to_numeric(merged[id_col], errors="coerce")
        merged = merged[~mid.isin(list(removed_visible))]

    for _, row in ed.iterrows():
        rid = row[id_col]
        if pd.isna(rid):
            merged = pd.concat([merged, pd.DataFrame([row])], ignore_index=True)
            continue
        rid = int(rid)
        mid = pd.to_numeric(merged[id_col], errors="coerce")
        loc = merged.index[mid == rid]
        if len(loc):
            idx = loc[0]
            for c in ed.columns:
                if c in merged.columns:
                    merged.at[idx, c] = row[c]
        else:
            merged = pd.concat([merged, pd.DataFrame([row])], ignore_index=True)
    return merged


# ---------------------------------------------------
# AI CLIENT
# ---------------------------------------------------

password = st.secrets["snowflake"]["password"]
client = OpenAI(api_key=st.secrets["openai"]["api_key"])

# ---------------------------------------------------
# SESSION STATE
# ---------------------------------------------------

if "logged_in" not in st.session_state:
    st.session_state.logged_in = False

if "page" not in st.session_state:
    st.session_state.page = "login"

# ---------------------------------------------------
# LIVE CLOCK (top of pages)
# ---------------------------------------------------


def live_clock():
    now = datetime.now().strftime("%A, %B %d %Y | %H:%M:%S")
    st.markdown(
        f"""
        <div class="clock-box">
        {now}
        </div>
        """,
        unsafe_allow_html=True,
    )


# ---------------------------------------------------
# RERUN HELPER
# ---------------------------------------------------


def safe_rerun():
    try:
        st.rerun()
    except AttributeError:
        st.experimental_rerun()


# ---------------------------------------------------
# GENERIC SAVE / DELETE / UNDO HELPERS
# ---------------------------------------------------
def _db_param(value):
    """Coerce UI blanks to None so Snowflake DATE/TIMESTAMP columns get NULL, not ''."""
    if value is None:
        return None
    # Handle pandas NA/NaT explicitly before string checks.
    if value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, str):
        s = value.strip()
        if s == "" or s.lower() == "none":
            return None
        return value
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value


def _db_date(value):
    """Coerce Streamlit/pandas dates to Python date or None for Snowflake DATE columns."""
    v = _db_param(value)
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    ts = pd.to_datetime(v, errors="coerce")
    if pd.isna(ts):
        return None
    return ts.date()


def handle_save_with_id(
    df_name_prefix: str,
    table_name: str,
    df: pd.DataFrame,
    id_col: str,
    insert_sql: str,
    update_sql: str,
    insert_cols: list,
    update_cols: list,
):
    conn, cursor = get_cursor()

    # ---------------------------------------------------
    # PREP DATA
    # ---------------------------------------------------
    df_work = df.copy()
    # Ensure pandas NA/NaT are converted to plain Python None for DB bindings.
    df_work = df_work.astype(object)
    df_work = df_work.where(pd.notnull(df_work), None)
    df_work[id_col] = pd.to_numeric(df_work[id_col], errors="coerce")

    # Original dataframe
    orig_key = f"{df_name_prefix}_orig"
    orig = st.session_state.get(orig_key, pd.DataFrame(columns=df.columns)).copy()

    if not orig.empty:
        orig[id_col] = pd.to_numeric(orig[id_col], errors="coerce")

    orig_ids = set(orig[id_col].dropna().astype(int)) if not orig.empty else set()

    # ---------------------------------------------------
    # INSERT / UPDATE
    # ---------------------------------------------------
    new_rows = df_work[df_work[id_col].isna() | (~df_work[id_col].isin(orig_ids))]
    existing_rows = df_work[df_work[id_col].notna() & (df_work[id_col].isin(orig_ids))]
    existing_ids = set(existing_rows[id_col].astype(int))

    # ---------------------------------------------------
    # DELETE DETECTION
    # ---------------------------------------------------
    deleted_ids = orig_ids - existing_ids

    if not orig.empty:
        orig_indexed = orig.set_index(id_col)
    else:
        orig_indexed = pd.DataFrame().set_index(pd.Index([]))

    # ---------------------------------------------------
    # INSERT
    # ---------------------------------------------------
    for _, row in new_rows.iterrows():
        try:
            vals = [_db_param(row[c]) for c in insert_cols]
            cursor.execute(insert_sql, vals)
            log_audit(table_name, None, "ALL", None, str(vals), "INSERT")
        except Exception as e:
            st.error(f"INSERT ERROR: {e}")

    # ---------------------------------------------------
    # UPDATE
    # ---------------------------------------------------
    for _, row in existing_rows.iterrows():
        try:
            rid = int(row[id_col])

            if rid in orig_indexed.index:
                old_row = orig_indexed.loc[rid]
                for col in update_cols:
                    old_val = old_row[col]
                    new_val = row[col]
                    if str(old_val) != str(new_val):
                        log_audit(table_name, rid, col, old_val, new_val, "UPDATE")

            vals = [_db_param(row[c]) for c in update_cols] + [rid]
            cursor.execute(update_sql, vals)
        except Exception as e:
            st.error(f"UPDATE ERROR (ID {row[id_col]}): {e}")

    # ---------------------------------------------------
    # DELETE
    # ---------------------------------------------------
    deleted_key = f"{df_name_prefix}_deleted"
    deleted_buffer = st.session_state.get(deleted_key, pd.DataFrame())

    if not orig.empty and deleted_ids:
        deleted_rows = orig[orig[id_col].isin(list(deleted_ids))]

        for _, drow in deleted_rows.iterrows():
            try:
                del_id = int(drow[id_col])
                log_audit(table_name, del_id, "ALL", "ROW", "DELETED", "DELETE")
                cursor.execute(
                    f"DELETE FROM {table_name} WHERE {id_col}=%s",
                    (del_id,),
                )
            except Exception as e:
                st.error(f"DELETE ERROR (ID {drow[id_col]}): {e}")

        if deleted_buffer is None or deleted_buffer.empty:
            deleted_buffer = deleted_rows.copy()
        else:
            deleted_buffer = pd.concat([deleted_buffer, deleted_rows], ignore_index=True)

        st.session_state[deleted_key] = deleted_buffer

    conn.commit()


def handle_undo_with_id(
    df_name_prefix: str,
    table_name: str,
    insert_sql_with_id: str,
    cols_with_id: list,
) -> bool:
    """Restore the most recently deleted row from the session buffer."""
    deleted_key = f"{df_name_prefix}_deleted"
    buf = st.session_state.get(deleted_key)
    if buf is None or (isinstance(buf, pd.DataFrame) and buf.empty):
        return False
    last = buf.iloc[-1]
    conn, cursor = get_cursor()
    try:
        vals = [last.get(c) for c in cols_with_id]
        cursor.execute(insert_sql_with_id, vals)
        conn.commit()
        st.session_state[deleted_key] = buf.iloc[:-1].copy()
        return True
    except Exception as e:
        st.error(f"UNDO ERROR: {e}")
        return False


# ---------------------------------------------------
# ACTIVITY / AUDIT LOG
# ---------------------------------------------------


def log_activity(page, action):
    """Do not block pane switches on Snowflake; logging uses its own short-lived connection."""

    email = st.session_state.get("user", "UNKNOWN")
    ts = datetime.now()
    try:
        pw = st.secrets["snowflake"]["password"]
    except Exception:
        return

    def _run():
        try:
            conn = snowflake.connector.connect(
                user="jmcasaria",
                password=pw,
                account="NSXAGQQ-WJ05543",
                warehouse="COMPUTE_WH",
                database="CFB_ANALYST_JAKE_DB",
                schema="PUBLIC",
                role="ANALYST_JAKE_ROLE",
            )
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO USER_ACTIVITY_LOG
                (EMAIL, PAGE, ACTION, TIMESTAMP)
                VALUES (%s,%s,%s,%s)
                """,
                (email, page, action, ts),
            )
            conn.commit()
            cur.close()
            conn.close()
        except Exception:
            pass

    threading.Thread(target=_run, daemon=True).start()


def log_audit(table, record_id, column, old, new, action):
    try:
        conn, cursor = get_cursor()
        cursor.execute(
            """
            INSERT INTO AUDIT_LOG
            (USER_EMAIL,TABLE_NAME,RECORD_ID,COLUMN_NAME,OLD_VALUE,NEW_VALUE,ACTION,TIMESTAMP)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                st.session_state.get("user", "UNKNOWN"),
                table,
                record_id,
                column,
                str(old),
                str(new),
                action,
                datetime.now(),
            ),
        )
        conn.commit()
    except Exception:
        pass


# ---------------------------------------------------
# EMAIL ALERT (not currently used, but kept)
# ---------------------------------------------------


def send_alert(message):
    try:
        server = smtplib.SMTP("smtp.gmail.com", 587)
        server.starttls()
        server.login("your_email@gmail.com", "app_password")
        server.sendmail(
            "your_email@gmail.com",
            "admin@email.com",
            message,
        )
        server.quit()
    except Exception:
        pass


# ---------------------------------------------------
# LOGIN PAGE
# ---------------------------------------------------


def login():
    conn, cursor = get_cursor()

    live_clock()

    col1, col2 = st.columns([1.2, 1])

    with col1:
        st_lottie(lottie_login, height=420)

    with col2:
        st.markdown('<div class="login-card">', unsafe_allow_html=True)

        st.title("🔎 AUDIT Data Portal")

        email = st.text_input("Email")
        password_input = st.text_input("Password", type="password")

        if st.button("Login"):
            if conn is None or cursor is None:
                conn = get_connection()
                cursor = conn.cursor()

            query = """
            SELECT STATUS
            FROM USERS
            WHERE EMAIL=%s AND PASSWORD=%s
            """
            cursor.execute(query, (email, password_input))
            result = cursor.fetchone()

            if result:
                status = result[0]

                if status == "APPROVED":
                    login_time = datetime.now()
                    cursor.execute(
                        """
                        INSERT INTO LOGIN_HISTORY
                        (EMAIL,LOGIN_TIME)
                        VALUES (%s,%s)
                        """,
                        (email, login_time),
                    )
                    conn.commit()
                    st.session_state.logged_in = True
                    st.session_state.user = email
                    st.success("Login Successful")

                elif status == "PENDING":
                    st.warning("Your account is waiting for admin approval.")

                elif status == "REJECTED":
                    st.error("Your account was rejected.")
            else:
                st.error("Invalid email or password")

        st.divider()

        if st.button("Create Account"):
            st.session_state.page = "register"

        st.markdown("</div>", unsafe_allow_html=True)


# ---------------------------------------------------
# REGISTER PAGE
# ---------------------------------------------------


def register():
    conn, cursor = get_cursor()

    live_clock()

    st.title("Create Account")

    name = st.text_input("Full Name")
    email = st.text_input("Email")
    password_input = st.text_input("Password", type="password")

    if st.button("Register"):
        if conn is None or cursor is None:
            conn = get_connection()
            cursor = conn.cursor()

        query = """
        INSERT INTO USERS
        (NAME,EMAIL,PASSWORD,STATUS,DATE_REGISTERED)
        VALUES (%s,%s,%s,'PENDING',%s)
        """
        cursor.execute(query, (name, email, password_input, datetime.now()))
        conn.commit()
        st.success("Account created successfully.")
        st.info("Wait for admin approval.")

    if st.button("Back to Login"):
        st.session_state.page = "login"


# ---------------------------------------------------
# ADMIN PANEL
# ---------------------------------------------------


def admin_panel():
    conn, cursor = get_cursor()

    if conn is None or cursor is None:
        conn = get_connection()
        cursor = conn.cursor()

    st.title("👨‍💼 Admin Approval Panel")

    cursor.execute(
        """
    SELECT ID,NAME,EMAIL
    FROM USERS
    WHERE STATUS='PENDING'
    """
    )
    users = cursor.fetchall()

    if users:
        for user in users:
            st.write(f"Name: {user[1]}")
            st.write(f"Email: {user[2]}")

            col1, col2 = st.columns(2)

            with col1:
                if st.button(f"Approve {user[0]}"):
                    cursor.execute(
                        "UPDATE USERS SET STATUS='APPROVED' WHERE ID=%s",
                        (user[0],),
                    )
                    conn.commit()
                    st.success("User approved")

            with col2:
                if st.button(f"Reject {user[0]}"):
                    cursor.execute(
                        "UPDATE USERS SET STATUS='REJECTED' WHERE ID=%s",
                        (user[0],),
                    )
                    conn.commit()
                    st.error("User rejected")

            st.divider()
    else:
        st.info("No pending users.")


# ---------------------------------------------------
# ATP CERTIFICATES PAGE
# ---------------------------------------------------


def atp_certificates():
    st.title("📄 ATP Certificates Compliance")

    df = load_atp()

    if "atp_orig" not in st.session_state:
        st.session_state.atp_orig = df.copy()

    df_view = filter_dataframe_controls(
        df,
        "atp",
        ["COMPANY", "AREA", "BRANCH", "STATUS", "SERVICE_INVOICE_SERIAL_NO"],
    )

    edited_partial = st.data_editor(
        df_view,
        num_rows="dynamic",
        use_container_width=True,
        key="atp_editor",
        column_config={
            "BIR_DATE_RECEIVED_FIRST_STAMP": st.column_config.DateColumn("First Stamp"),
            "BIR_DATE_RECEIVED_LAST_STAMP": st.column_config.DateColumn("Last Stamp"),
        },
    )

    col_save, col_undo, col_refresh = st.columns(3)

    with col_save:
        if st.button("💾 Save Changes", key="atp_save"):
            handle_save_with_id(
                df_name_prefix="atp",
                table_name="ATP_CERTIFICATES",
                df=reconcile_editor_after_filter(df, df_view, edited_partial, "ID"),
                id_col="ID",
                insert_sql="""
                    INSERT INTO ATP_CERTIFICATES
                    (COMPANY,AREA,BRANCH,SERVICE_INVOICE_SERIAL_NO,
                     BIR_DATE_RECEIVED_FIRST_STAMP,
                     BIR_DATE_RECEIVED_LAST_STAMP,
                     STATUS)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)
                """,
                update_sql="""
                    UPDATE ATP_CERTIFICATES
                    SET
                        COMPANY=%s,
                        AREA=%s,
                        BRANCH=%s,
                        SERVICE_INVOICE_SERIAL_NO=%s,
                        BIR_DATE_RECEIVED_FIRST_STAMP=%s,
                        BIR_DATE_RECEIVED_LAST_STAMP=%s,
                        STATUS=%s
                    WHERE ID=%s
                """,
                insert_cols=[
                    "COMPANY",
                    "AREA",
                    "BRANCH",
                    "SERVICE_INVOICE_SERIAL_NO",
                    "BIR_DATE_RECEIVED_FIRST_STAMP",
                    "BIR_DATE_RECEIVED_LAST_STAMP",
                    "STATUS",
                ],
                update_cols=[
                    "COMPANY",
                    "AREA",
                    "BRANCH",
                    "SERVICE_INVOICE_SERIAL_NO",
                    "BIR_DATE_RECEIVED_FIRST_STAMP",
                    "BIR_DATE_RECEIVED_LAST_STAMP",
                    "STATUS",
                ],
            )
            load_atp.clear()
            st.session_state.atp_orig = load_atp()
            st.success("Data saved successfully ✅")
            safe_rerun()

    with col_undo:
        if st.button("↩ Undo last delete", key="atp_undo"):
            ok = handle_undo_with_id(
                df_name_prefix="atp",
                table_name="ATP_CERTIFICATES",
                insert_sql_with_id="""
                    INSERT INTO ATP_CERTIFICATES
                    (ID,COMPANY,AREA,BRANCH,SERVICE_INVOICE_SERIAL_NO,
                     BIR_DATE_RECEIVED_FIRST_STAMP,
                     BIR_DATE_RECEIVED_LAST_STAMP,
                     STATUS)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                cols_with_id=[
                    "ID",
                    "COMPANY",
                    "AREA",
                    "BRANCH",
                    "SERVICE_INVOICE_SERIAL_NO",
                    "BIR_DATE_RECEIVED_FIRST_STAMP",
                    "BIR_DATE_RECEIVED_LAST_STAMP",
                    "STATUS",
                ],
            )
            if ok:
                load_atp.clear()
                st.session_state.atp_orig = load_atp()
                st.success("Delete undone 🔄")
                safe_rerun()
            else:
                st.info("Nothing to undo")

    with col_refresh:
        if st.button("🔄 Refresh", key="atp_refresh"):
            load_atp.clear()
            st.session_state.atp_orig = load_atp()
            safe_rerun()


# ---------------------------------------------------
# BIR 1906 ATP
# ---------------------------------------------------


def bir_1906_atp():
    st.title("📄 BIR 1906 ATP")

    df = load_bir()

    if "bir1906_orig" not in st.session_state:
        st.session_state.bir1906_orig = df.copy()

    df_view = filter_dataframe_controls(
        df,
        "bir1906",
        ["COMPANY", "BRANCH", "STATUS", "SERVICE_INVOICE_SERIAL_NO"],
    )

    edited_partial = st.data_editor(
        df_view,
        num_rows="dynamic",
        use_container_width=True,
        key="bir1906_editor",
        column_config={
            "BIR_DATE_RECEIVED": st.column_config.DateColumn("BIR Date Received"),
        },
    )

    col_save, col_undo, col_refresh = st.columns(3)

    with col_save:
        if st.button("💾 Save Changes", key="bir1906_save"):
            handle_save_with_id(
                df_name_prefix="bir1906",
                table_name="BIR_1906_ATP",
                df=reconcile_editor_after_filter(df, df_view, edited_partial, "ID"),
                id_col="ID",
                insert_sql="""
                    INSERT INTO BIR_1906_ATP
                    (COMPANY,BRANCH,SERVICE_INVOICE_SERIAL_NO,BIR_DATE_RECEIVED,STATUS)
                    VALUES (%s,%s,%s,%s,%s)
                """,
                update_sql="""
                    UPDATE BIR_1906_ATP
                    SET
                        COMPANY=%s,
                        BRANCH=%s,
                        SERVICE_INVOICE_SERIAL_NO=%s,
                        BIR_DATE_RECEIVED=%s,
                        STATUS=%s
                    WHERE ID=%s
                """,
                insert_cols=[
                    "COMPANY",
                    "BRANCH",
                    "SERVICE_INVOICE_SERIAL_NO",
                    "BIR_DATE_RECEIVED",
                    "STATUS",
                ],
                update_cols=[
                    "COMPANY",
                    "BRANCH",
                    "SERVICE_INVOICE_SERIAL_NO",
                    "BIR_DATE_RECEIVED",
                    "STATUS",
                ],
            )
            load_bir.clear()
            st.session_state.bir1906_orig = load_bir()
            st.success("Data saved successfully ✅")
            safe_rerun()

    with col_undo:
        if st.button("↩ Undo last delete", key="bir1906_undo"):
            ok = handle_undo_with_id(
                df_name_prefix="bir1906",
                table_name="BIR_1906_ATP",
                insert_sql_with_id="""
                    INSERT INTO BIR_1906_ATP
                    (ID,COMPANY,BRANCH,SERVICE_INVOICE_SERIAL_NO,BIR_DATE_RECEIVED,STATUS)
                    VALUES (%s,%s,%s,%s,%s,%s)
                """,
                cols_with_id=[
                    "ID",
                    "COMPANY",
                    "BRANCH",
                    "SERVICE_INVOICE_SERIAL_NO",
                    "BIR_DATE_RECEIVED",
                    "STATUS",
                ],
            )
            if ok:
                load_bir.clear()
                st.session_state.bir1906_orig = load_bir()
                st.success("Delete undone 🔄")
                safe_rerun()
            else:
                st.info("Nothing to undo")

    with col_refresh:
        if st.button("🔄 Refresh", key="bir1906_refresh"):
            load_bir.clear()
            st.session_state.bir1906_orig = load_bir()
            safe_rerun()


# ---------------------------------------------------
# AI COMPLIANCE COPILOT
# ---------------------------------------------------


def ai_copilot():
    st.title("🤖 Compliance AI Copilot")

    st.write("Ask questions about your compliance data.")

    question = st.chat_input("Ask about the database...")

    if question:
        st.chat_message("user").write(question)

        schema = """
        DATABASE TABLES:

        ATP_CERTIFICATES(COMPANY, AREA, BRANCH, STATUS)

        FIRE_SAFETY(COMPANY, AREA, BRANCH, FSIC_VALIDITY, REMARKS)

        BUSINESS_PERMIT(COMPANY, AREA, BRANCH, STATUS)

        TAX_MAPPED(AREA, BRANCH, DATE_TAX_MAPPED, BIR_REMARKS)

        SECRETARY_CERTIFICATES(AREA, MONTH_YEAR, COMPANY, BRANCH, STATUS)

        BRANCH_TIN_ADDRESS(COMPANY, AREA, BRANCH_NAME)
        """

        prompt = f"""
        You are a compliance data analyst.

        Convert the question into a SAFE Snowflake SQL query.

        RULES:
        - ONLY SELECT statements
        - NO DELETE, UPDATE, INSERT, DROP
        - NO semicolons (;)
        - LIMIT results to 100 rows

        Database schema:
        {schema}

        Question:
        {question}

        Return SQL only.
        """

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
        )

        sql_query = response.choices[0].message.content.strip()

        forbidden = ["DELETE", "UPDATE", "INSERT", "DROP", "ALTER"]

        if any(word in sql_query.upper() for word in forbidden):
            st.error("❌ Unsafe query detected!")
            return

        if not sql_query.strip().upper().startswith("SELECT"):
            st.error("❌ Only SELECT queries are allowed")
            return

        if "LIMIT" not in sql_query.upper():
            sql_query += " LIMIT 100"

        st.subheader("Generated SQL")
        st.code(sql_query)

        try:
            conn, cursor = get_cursor()
            cursor.execute(sql_query)
            data = cursor.fetchall()
            df = pd.DataFrame(data)

            st.subheader("Result")
            st.dataframe(df, use_container_width=True)

            if len(df.columns) >= 2:
                try:
                    fig = px.bar(df, x=df.columns[0], y=df.columns[1])
                    st.plotly_chart(fig, use_container_width=True)
                except Exception:
                    pass

        except Exception as e:
            st.error(f"Query failed: {e}")


# ---------------------------------------------------
# BOA STICKER
# ---------------------------------------------------


def boa_sticker():
    st.title("📄 BOA Sticker")

    df = load_boa()

    if "boa_orig" not in st.session_state:
        st.session_state.boa_orig = df.copy()

    df_view = filter_dataframe_controls(
        df,
        "boa",
        ["COMPANY", "BRANCH", "STATUS", "TIN", "BOOK_TO_REGISTER"],
    )

    edited_partial = st.data_editor(
        df_view,
        num_rows="dynamic",
        use_container_width=True,
        key="boa_editor",
        column_config={
            "DATE_FORWARDED_TO_BRANCH": st.column_config.DateColumn("Date Forwarded"),
        },
    )

    col_save, col_undo, col_refresh = st.columns(3)

    with col_save:
        if st.button("💾 Save Changes", key="boa_save"):
            handle_save_with_id(
                df_name_prefix="boa",
                table_name="BOA_STICKER",
                df=reconcile_editor_after_filter(df, df_view, edited_partial, "ID"),
                id_col="ID",
                insert_sql="""
                    INSERT INTO BOA_STICKER
                    (COMPANY,BRANCH,TIN,BOOK_TO_REGISTER,VOLUME_NUMBER,DATE_FORWARDED_TO_BRANCH,STATUS)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)
                """,
                update_sql="""
                    UPDATE BOA_STICKER
                    SET
                        COMPANY=%s,
                        BRANCH=%s,
                        TIN=%s,
                        BOOK_TO_REGISTER=%s,
                        VOLUME_NUMBER=%s,
                        DATE_FORWARDED_TO_BRANCH=%s,
                        STATUS=%s
                    WHERE ID=%s
                """,
                insert_cols=[
                    "COMPANY",
                    "BRANCH",
                    "TIN",
                    "BOOK_TO_REGISTER",
                    "VOLUME_NUMBER",
                    "DATE_FORWARDED_TO_BRANCH",
                    "STATUS",
                ],
                update_cols=[
                    "COMPANY",
                    "BRANCH",
                    "TIN",
                    "BOOK_TO_REGISTER",
                    "VOLUME_NUMBER",
                    "DATE_FORWARDED_TO_BRANCH",
                    "STATUS",
                ],
            )
            load_boa.clear()
            st.session_state.boa_orig = load_boa()
            st.success("Data saved successfully ✅")
            safe_rerun()

    with col_undo:
        if st.button("↩ Undo last delete", key="boa_undo"):
            ok = handle_undo_with_id(
                df_name_prefix="boa",
                table_name="BOA_STICKER",
                insert_sql_with_id="""
                    INSERT INTO BOA_STICKER
                    (ID,COMPANY,BRANCH,TIN,BOOK_TO_REGISTER,VOLUME_NUMBER,DATE_FORWARDED_TO_BRANCH,STATUS)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                cols_with_id=[
                    "ID",
                    "COMPANY",
                    "BRANCH",
                    "TIN",
                    "BOOK_TO_REGISTER",
                    "VOLUME_NUMBER",
                    "DATE_FORWARDED_TO_BRANCH",
                    "STATUS",
                ],
            )
            if ok:
                load_boa.clear()
                st.session_state.boa_orig = load_boa()
                st.success("Delete undone 🔄")
                safe_rerun()
            else:
                st.info("Nothing to undo")

    with col_refresh:
        if st.button("🔄 Refresh", key="boa_refresh"):
            load_boa.clear()
            st.session_state.boa_orig = load_boa()
            safe_rerun()


# ---------------------------------------------------
# FIRE SAFETY
# ---------------------------------------------------


def fire_safety():
    st.title("📄 Fire Safety")

    df_full = load_fire()
    use_cert_layout = "VALID_UNTIL" in df_full.columns

    if "fire_orig" not in st.session_state:
        st.session_state.fire_orig = df_full.copy()

    df_grid = df_full.drop(columns=["FSIC_IMAGE"], errors="ignore")

    ffilter = ["COMPANY", "AREA", "BRANCH", "REMARKS"]
    if "STATUS" in df_grid.columns:
        ffilter.append("STATUS")
    df_view = filter_dataframe_controls(df_grid, "fire", ffilter)

    col_cfg = {}
    if use_cert_layout:
        if "FSIC_CERTIFICATE_DATE" in df_grid.columns:
            col_cfg["FSIC_CERTIFICATE_DATE"] = st.column_config.DateColumn(
                "Certificate date"
            )
        col_cfg["VALID_UNTIL"] = st.column_config.DateColumn("Valid until")
    else:
        col_cfg["FSIC_VALIDITY"] = st.column_config.DateColumn("FSIC Validity")

    edited_partial = st.data_editor(
        df_view,
        num_rows="dynamic",
        use_container_width=True,
        key="fire_editor",
        column_config=col_cfg,
    )

    col_save, col_undo, col_refresh = st.columns(3)

    with col_save:
        if st.button("💾 Save Changes", key="fire_save"):
            if use_cert_layout:
                handle_save_with_id(
                    df_name_prefix="fire",
                    table_name="FIRE_SAFETY",
                    df=reconcile_editor_after_filter(
                        df_full, df_view, edited_partial, "ID"
                    ),
                    id_col="ID",
                    insert_sql="""
                    INSERT INTO FIRE_SAFETY
                    (COMPANY,AREA,BRANCH,FSIC_CERTIFICATE_DATE,VALID_UNTIL,FSIC_FEE,STATUS,REMARKS)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                    update_sql="""
                    UPDATE FIRE_SAFETY
                    SET
                        COMPANY=%s,
                        AREA=%s,
                        BRANCH=%s,
                        FSIC_CERTIFICATE_DATE=%s,
                        VALID_UNTIL=%s,
                        FSIC_FEE=%s,
                        STATUS=%s,
                        REMARKS=%s
                    WHERE ID=%s
                """,
                    insert_cols=[
                        "COMPANY",
                        "AREA",
                        "BRANCH",
                        "FSIC_CERTIFICATE_DATE",
                        "VALID_UNTIL",
                        "FSIC_FEE",
                        "STATUS",
                        "REMARKS",
                    ],
                    update_cols=[
                        "COMPANY",
                        "AREA",
                        "BRANCH",
                        "FSIC_CERTIFICATE_DATE",
                        "VALID_UNTIL",
                        "FSIC_FEE",
                        "STATUS",
                        "REMARKS",
                    ],
                )
            else:
                handle_save_with_id(
                    df_name_prefix="fire",
                    table_name="FIRE_SAFETY",
                    df=reconcile_editor_after_filter(
                        df_full, df_view, edited_partial, "ID"
                    ),
                    id_col="ID",
                    insert_sql="""
                    INSERT INTO FIRE_SAFETY
                    (COMPANY,AREA,BRANCH,FSIC_VALIDITY,FSIC_FEE,REMARKS)
                    VALUES (%s,%s,%s,%s,%s,%s)
                """,
                    update_sql="""
                    UPDATE FIRE_SAFETY
                    SET
                        COMPANY=%s,
                        AREA=%s,
                        BRANCH=%s,
                        FSIC_VALIDITY=%s,
                        FSIC_FEE=%s,
                        REMARKS=%s
                    WHERE ID=%s
                """,
                    insert_cols=[
                        "COMPANY",
                        "AREA",
                        "BRANCH",
                        "FSIC_VALIDITY",
                        "FSIC_FEE",
                        "REMARKS",
                    ],
                    update_cols=[
                        "COMPANY",
                        "AREA",
                        "BRANCH",
                        "FSIC_VALIDITY",
                        "FSIC_FEE",
                        "REMARKS",
                    ],
                )
            load_fire.clear()
            st.session_state.fire_orig = load_fire()
            st.success("Data saved successfully ✅")
            safe_rerun()

    with col_undo:
        if st.button("↩ Undo last delete", key="fire_undo"):
            if use_cert_layout:
                ok = handle_undo_with_id(
                    df_name_prefix="fire",
                    table_name="FIRE_SAFETY",
                    insert_sql_with_id="""
                    INSERT INTO FIRE_SAFETY
                    (ID,COMPANY,AREA,BRANCH,FSIC_CERTIFICATE_DATE,VALID_UNTIL,FSIC_FEE,STATUS,REMARKS,FSIC_IMAGE)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                    cols_with_id=[
                        "ID",
                        "COMPANY",
                        "AREA",
                        "BRANCH",
                        "FSIC_CERTIFICATE_DATE",
                        "VALID_UNTIL",
                        "FSIC_FEE",
                        "STATUS",
                        "REMARKS",
                        "FSIC_IMAGE",
                    ],
                )
            else:
                ok = handle_undo_with_id(
                    df_name_prefix="fire",
                    table_name="FIRE_SAFETY",
                    insert_sql_with_id="""
                    INSERT INTO FIRE_SAFETY
                    (ID,COMPANY,AREA,BRANCH,FSIC_VALIDITY,FSIC_FEE,REMARKS,FSIC_IMAGE)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                    cols_with_id=[
                        "ID",
                        "COMPANY",
                        "AREA",
                        "BRANCH",
                        "FSIC_VALIDITY",
                        "FSIC_FEE",
                        "REMARKS",
                        "FSIC_IMAGE",
                    ],
                )
            if ok:
                load_fire.clear()
                st.session_state.fire_orig = load_fire()
                st.success("Delete undone 🔄")
                safe_rerun()
            else:
                st.info("Nothing to undo")

    with col_refresh:
        if st.button("🔄 Refresh", key="fire_refresh"):
            load_fire.clear()
            st.session_state.fire_orig = load_fire()
            safe_rerun()

    st.subheader("Upload FSIC / compliance photo (history)")
    st.caption(
        "Each upload goes to FIRE_SAFETY_IMAGE_HISTORY. Older files are kept. "
        "FIRE_SAFETY.FSIC_IMAGE is always the latest photo for that row."
    )

    with st.form("fire_safety_image_upload"):
        f_row = st.number_input(
            "FIRE_SAFETY row ID", step=1, min_value=1, key="fire_up_row"
        )
        f_as_of = st.date_input(
            "As-of date for this photo (often FSIC validity / inspection date)",
            value=fire_safety_default_as_of_date(int(f_row)),
            key="fire_up_asof",
        )
        f_file = st.file_uploader(
            "Image file",
            type=["png", "jpg", "jpeg", "webp", "gif"],
            key="fire_up_file",
        )
        f_sub = st.form_submit_button("Append to history & set as latest")
    if f_sub:
        if f_file is None:
            st.warning("Choose an image file first.")
        else:
            try:
                append_fire_safety_image_history(
                    int(f_row),
                    f_file.getvalue(),
                    f_as_of,
                    st.session_state.get("user", "UNKNOWN"),
                )
                load_fire.clear()
                st.session_state.fire_orig = load_fire()
                st.success("Saved to history; latest image updated for this row.")
                st.image(f_file, width=280)
            except Exception as e:
                st.error(
                    f"Could not save (add FSIC_IMAGE column + history table?). {e}\n\n"
                    "Run `snowflake_image_history_ddl.sql` in this folder."
                )

    st.subheader("Photo history & preview")
    with st.expander("Browse past uploads for a row", expanded=False):
        fh_row = st.number_input(
            "Row ID to inspect", step=1, min_value=1, key="fire_hist_row"
        )
        try:
            fhdf = fetch_fire_safety_image_history_meta(int(fh_row))
            st.dataframe(fhdf, use_container_width=True)
            if not fhdf.empty:
                flabels = {
                    int(row["ID"]): f"{row['AS_OF_DATE']} — {row['CREATED_AT']} — {row['IMAGE_BYTES'] or 0} bytes"
                    for _, row in fhdf.iterrows()
                }
                fpick = st.selectbox(
                    "Preview version",
                    options=list(flabels.keys()),
                    format_func=lambda i: flabels[i],
                    key="fire_hist_pick",
                )
                fblob = fetch_fire_safety_history_image(int(fpick))
                if fblob:
                    st.image(fblob, width=360)
        except Exception as e:
            st.error(
                f"History query failed. Run `snowflake_image_history_ddl.sql` first. {e}"
            )


# ---------------------------------------------------
# BRANCH TIN & ADDRESS
# ---------------------------------------------------


def branch_tin_address():
    st.title("🏢 Branch TIN & Address")

    REMARK_NEW_VIEW = "Source: NEW BRANCH ADDRESS view"
    REMARK_CHANGED_VIEW = "Source: CHANGED ADDRESS view"

    st.markdown(
        """
    <style>
    .holo-title {
        text-align:center;
        font-size:18px;
        font-weight:bold;
        color:#00ffff;
        text-shadow: 0 0 10px #00ffff, 0 0 20px #00ffff;
        margin-bottom:10px;
    }
    button[kind="secondary"] {
        width: 100%;
        border-radius: 12px !important;
        background: linear-gradient(135deg, #0f2027, #203a43, #2c5364);
        color: #00ffff !important;
        border: 1px solid rgba(0,255,255,0.5);
        box-shadow: 0 0 10px rgba(0,255,255,0.4);
    }
    </style>
    <div class="holo-title">⚡ BRANCH CONTROL PANEL ⚡</div>
    """,
        unsafe_allow_html=True,
    )

    def _clean_scalar(v):
        if v is None or v is pd.NA or v is pd.NaT:
            return None
        if isinstance(v, str):
            s = v.strip()
            if s == "" or s.lower() in ("none", "nan", "nat", "null"):
                return None
            return s
        try:
            if pd.isna(v):
                return None
        except (TypeError, ValueError):
            pass
        return v

    def _sanitize_for_db(df: pd.DataFrame, date_cols=None) -> pd.DataFrame:
        if date_cols is None:
            date_cols = []
        out = df.copy().astype(object)
        for col in out.columns:
            out[col] = out[col].map(_clean_scalar)
        for c in date_cols:
            if c in out.columns:
                out[c] = pd.to_datetime(out[c], errors="coerce").dt.date
                out[c] = out[c].map(_clean_scalar)
        if "ID" in out.columns:
            out["ID"] = pd.to_numeric(out["ID"], errors="coerce")
            out["ID"] = out["ID"].apply(lambda x: int(x) if pd.notna(x) else None)
        return out

    full_cols = [
        "ID",
        "COMPANY",
        "TIN",
        "BRANCH_CODE",
        "RDO",
        "AREA",
        "BRANCH_NAME",
        "UPDATED_ADDRESS",
        "STATUS",
        "DATE_OPEN",
        "DATE_OF_CLOSURE",
        "REMARKS",
    ]
    insert_cols = [
        "COMPANY",
        "TIN",
        "BRANCH_CODE",
        "RDO",
        "AREA",
        "BRANCH_NAME",
        "UPDATED_ADDRESS",
        "STATUS",
        "DATE_OPEN",
        "DATE_OF_CLOSURE",
        "REMARKS",
    ]
    update_cols = insert_cols.copy()
    insert_sql = """
        INSERT INTO BRANCH_TIN_ADDRESS
        (COMPANY,TIN,BRANCH_CODE,RDO,AREA,BRANCH_NAME,UPDATED_ADDRESS,STATUS,DATE_OPEN,DATE_OF_CLOSURE,REMARKS)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """
    update_sql = """
        UPDATE BRANCH_TIN_ADDRESS
        SET
            COMPANY=%s,
            TIN=%s,
            BRANCH_CODE=%s,
            RDO=%s,
            AREA=%s,
            BRANCH_NAME=%s,
            UPDATED_ADDRESS=%s,
            STATUS=%s,
            DATE_OPEN=%s,
            DATE_OF_CLOSURE=%s,
            REMARKS=%s
        WHERE ID=%s
    """

    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        if st.button("SUKI Branch"):
            st.session_state.company = "SUKI"
    with c2:
        if st.button("PCNFCI Branch"):
            st.session_state.company = "PCNFCI"
    with c3:
        if st.button("FASTCASH Branch"):
            st.session_state.company = "FASTCASH"
    with c4:
        if st.button("🆕 NEW BRANCH ADDRESS"):
            st.session_state.view_mode = "NEW"
    with c5:
        if st.button("🔁 CHANGED ADDRESS"):
            st.session_state.view_mode = "CHANGED"

    if "view_mode" not in st.session_state:
        st.session_state.view_mode = "MAIN"

    if "company" not in st.session_state:
        st.info("Select a company first.")
        return

    company = st.session_state.company
    st.subheader(f"{company} Branch List")

    df = load_branch_tin(company)

    key_prefix = f"branch_tin_{company.lower()}"
    orig_key = f"{key_prefix}_orig"

    if orig_key not in st.session_state:
        st.session_state[orig_key] = df.copy()

    st.subheader("Full branch table")
    df_view = filter_dataframe_controls(
        df,
        key_prefix,
        ["AREA", "BRANCH_NAME", "STATUS", "RDO", "TIN", "REMARKS"],
    )

    edited_partial = st.data_editor(
        df_view,
        num_rows="dynamic",
        use_container_width=True,
        key=f"{key_prefix}_editor",
        column_config={
            "DATE_OPEN": st.column_config.DateColumn("Date Open"),
            "DATE_OF_CLOSURE": st.column_config.DateColumn("Date of Closure"),
            "REMARKS": st.column_config.TextColumn("Remarks", max_chars=1000),
        },
    )

    col_save, col_undo, col_refresh = st.columns(3)

    with col_save:
        if st.button("💾 Save Changes (Main Table)", key=f"{key_prefix}_save"):
            to_save = reconcile_editor_after_filter(
                df, df_view, edited_partial, "ID"
            ).copy()
            for date_col in ["DATE_OPEN", "DATE_OF_CLOSURE"]:
                if date_col in to_save.columns:
                    to_save[date_col] = pd.to_datetime(
                        to_save[date_col], errors="coerce"
                    ).dt.date
            to_save = to_save.where(pd.notnull(to_save), None)

            handle_save_with_id(
                df_name_prefix=key_prefix,
                table_name="BRANCH_TIN_ADDRESS",
                df=to_save,
                id_col="ID",
                insert_sql=insert_sql,
                update_sql=update_sql,
                insert_cols=insert_cols,
                update_cols=update_cols,
            )
            load_branch_tin.clear()
            st.session_state[orig_key] = load_branch_tin(company)
            st.success("Saved main table ✅")
            safe_rerun()

    with col_undo:
        if st.button("↩ Undo last delete", key=f"{key_prefix}_undo"):
            ok = handle_undo_with_id(
                df_name_prefix=key_prefix,
                table_name="BRANCH_TIN_ADDRESS",
                insert_sql_with_id="""
                        INSERT INTO BRANCH_TIN_ADDRESS
                        (ID,COMPANY,TIN,BRANCH_CODE,RDO,AREA,BRANCH_NAME,UPDATED_ADDRESS,STATUS,DATE_OPEN,DATE_OF_CLOSURE,REMARKS)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                cols_with_id=[
                    "ID",
                    "COMPANY",
                    "TIN",
                    "BRANCH_CODE",
                    "RDO",
                    "AREA",
                    "BRANCH_NAME",
                    "UPDATED_ADDRESS",
                    "STATUS",
                    "DATE_OPEN",
                    "DATE_OF_CLOSURE",
                    "REMARKS",
                ],
            )
            if ok:
                load_branch_tin.clear()
                st.session_state[orig_key] = load_branch_tin(company)
                st.success("Delete undone 🔄")
                safe_rerun()
            else:
                st.info("Nothing to undo")

    with col_refresh:
        if st.button("🔄 Refresh", key=f"{key_prefix}_refresh"):
            load_branch_tin.clear()
            st.session_state[orig_key] = load_branch_tin(company)
            safe_rerun()

    def _ensure_columns(df_in: pd.DataFrame, cols: list) -> pd.DataFrame:
        out = df_in.copy()
        for c in cols:
            if c not in out.columns:
                out[c] = None
        return out[cols]

    def merge_new_view_to_full(df_full: pd.DataFrame, edited: pd.DataFrame) -> pd.DataFrame:
        base_map = {
            int(r["ID"]): r.to_dict()
            for _, r in df_full.iterrows()
            if pd.notna(r.get("ID"))
        }
        rows = []
        for _, er in edited.iterrows():
            eid = pd.to_numeric(er.get("ID"), errors="coerce")
            addr = _clean_scalar(er.get("ADDRESS"))
            if addr is None:
                addr = _clean_scalar(er.get("UPDATED_ADDRESS"))
            if pd.notna(eid) and int(eid) in base_map:
                b = base_map[int(eid)].copy()
                b["COMPANY"] = company
                b["STATUS"] = "NEW"
                b["BRANCH_NAME"] = _clean_scalar(er.get("BRANCH_NAME")) or b.get(
                    "BRANCH_NAME"
                )
                b["AREA"] = _clean_scalar(er.get("AREA")) or b.get("AREA")
                b["TIN"] = _clean_scalar(er.get("TIN")) or b.get("TIN")
                b["BRANCH_CODE"] = _clean_scalar(er.get("BRANCH_CODE")) or b.get(
                    "BRANCH_CODE"
                )
                b["RDO"] = _clean_scalar(er.get("RDO")) or b.get("RDO")
                b["UPDATED_ADDRESS"] = addr
                b["DATE_OPEN"] = er.get("DATE_OPEN", b.get("DATE_OPEN"))
                b["REMARKS"] = REMARK_NEW_VIEW
                rows.append(b)
            else:
                rows.append(
                    {
                        "ID": None,
                        "COMPANY": company,
                        "TIN": er.get("TIN"),
                        "BRANCH_CODE": er.get("BRANCH_CODE"),
                        "RDO": er.get("RDO"),
                        "AREA": er.get("AREA"),
                        "BRANCH_NAME": er.get("BRANCH_NAME"),
                        "UPDATED_ADDRESS": addr,
                        "STATUS": "NEW",
                        "DATE_OPEN": er.get("DATE_OPEN"),
                        "DATE_OF_CLOSURE": er.get("DATE_OF_CLOSURE"),
                        "REMARKS": REMARK_NEW_VIEW,
                    }
                )
        out = pd.DataFrame(rows)
        out = _ensure_columns(out, full_cols)
        return _sanitize_for_db(out, date_cols=["DATE_OPEN", "DATE_OF_CLOSURE"])

    def merge_changed_view_to_full(df_full: pd.DataFrame, edited: pd.DataFrame) -> pd.DataFrame:
        base_map = {
            int(r["ID"]): r.to_dict()
            for _, r in df_full.iterrows()
            if pd.notna(r.get("ID"))
        }
        rows = []
        for _, er in edited.iterrows():
            eid = pd.to_numeric(er.get("ID"), errors="coerce")
            branch_val = _clean_scalar(er.get("BRANCH")) or _clean_scalar(
                er.get("BRANCH_NAME")
            )
            new_addr = _clean_scalar(er.get("ADDRESS (NEW ADDRESS)")) or _clean_scalar(
                er.get("UPDATED_ADDRESS")
            )
            old_addr = _clean_scalar(er.get("ADDRESS (OLD ADDRESS)")) or _clean_scalar(
                er.get("OLD_ADDRESS")
            )
            extra = f" | Previous address (from view): {old_addr}" if old_addr else ""
            if pd.notna(eid) and int(eid) in base_map:
                b = base_map[int(eid)].copy()
                b["COMPANY"] = _clean_scalar(er.get("COMPANY")) or company
                b["STATUS"] = "CHANGED"
                b["BRANCH_NAME"] = branch_val or b.get("BRANCH_NAME")
                b["UPDATED_ADDRESS"] = new_addr or b.get("UPDATED_ADDRESS")
                b["REMARKS"] = f"{REMARK_CHANGED_VIEW}{extra}"
                rows.append(b)
            else:
                rows.append(
                    {
                        "ID": None,
                        "COMPANY": _clean_scalar(er.get("COMPANY")) or company,
                        "TIN": None,
                        "BRANCH_CODE": None,
                        "RDO": None,
                        "AREA": None,
                        "BRANCH_NAME": branch_val,
                        "UPDATED_ADDRESS": new_addr,
                        "STATUS": "CHANGED",
                        "DATE_OPEN": None,
                        "DATE_OF_CLOSURE": None,
                        "REMARKS": f"{REMARK_CHANGED_VIEW}{extra}",
                    }
                )
        out = pd.DataFrame(rows)
        out = _ensure_columns(out, full_cols)
        return _sanitize_for_db(out, date_cols=["DATE_OPEN", "DATE_OF_CLOSURE"])

    def _merge_partial_into_full(df_full: pd.DataFrame, df_partial: pd.DataFrame) -> pd.DataFrame:
        full = _ensure_columns(df_full, full_cols).copy().astype(object)
        part = _ensure_columns(df_partial, full_cols).copy().astype(object)
        full["ID"] = pd.to_numeric(full["ID"], errors="coerce")
        part["ID"] = pd.to_numeric(part["ID"], errors="coerce")
        if full.empty:
            merged = part
        else:
            merged = full.copy()
            id_to_idx = {}
            for i, rid in enumerate(merged["ID"]):
                if pd.notna(rid):
                    id_to_idx[int(rid)] = i
            for _, prow in part.iterrows():
                pid = prow.get("ID")
                if pd.notna(pid) and int(pid) in id_to_idx:
                    merged.loc[id_to_idx[int(pid)], full_cols] = prow[full_cols].values
                else:
                    merged = pd.concat(
                        [merged, pd.DataFrame([prow[full_cols]])], ignore_index=True
                    )
        return _sanitize_for_db(merged, date_cols=["DATE_OPEN", "DATE_OF_CLOSURE"])

    st.divider()
    if st.session_state.view_mode == "NEW":
        st.subheader("🆕 NEW BRANCH ADDRESS")
        df_new = df[df["STATUS"].astype(str).str.strip().str.upper() == "NEW"].copy()
        if df_new.empty:
            df_new = pd.DataFrame(
                columns=[
                    "ID",
                    "COMPANY",
                    "BRANCH_NAME",
                    "ADDRESS",
                    "DATE_OPEN",
                    "AREA",
                    "TIN",
                    "BRANCH_CODE",
                    "RDO",
                ]
            )
        else:
            df_new["ADDRESS"] = df_new["UPDATED_ADDRESS"]
        for c in [
            "ID",
            "COMPANY",
            "BRANCH_NAME",
            "ADDRESS",
            "DATE_OPEN",
            "AREA",
            "TIN",
            "BRANCH_CODE",
            "RDO",
        ]:
            if c not in df_new.columns:
                df_new[c] = None
        edited_new = st.data_editor(
            df_new[
                [
                    "ID",
                    "COMPANY",
                    "BRANCH_NAME",
                    "ADDRESS",
                    "DATE_OPEN",
                    "AREA",
                    "TIN",
                    "BRANCH_CODE",
                    "RDO",
                ]
            ],
            num_rows="dynamic",
            use_container_width=True,
            key=f"{company}_new_editor",
            column_config={
                "ID": st.column_config.NumberColumn("ID", format="%d", step=1),
                "DATE_OPEN": st.column_config.DateColumn("Date Open"),
            },
        )
        if st.button("💾 Save Changes (NEW Branch View)", key=f"{company}_save_new"):
            partial_save = merge_new_view_to_full(df, edited_new)
            to_save = _merge_partial_into_full(df, partial_save)
            handle_save_with_id(
                df_name_prefix=key_prefix,
                table_name="BRANCH_TIN_ADDRESS",
                df=to_save,
                id_col="ID",
                insert_sql=insert_sql,
                update_sql=update_sql,
                insert_cols=insert_cols,
                update_cols=update_cols,
            )
            load_branch_tin.clear()
            st.session_state[orig_key] = load_branch_tin(company)
            st.success("NEW branch view saved ✅")
            safe_rerun()

    if st.session_state.view_mode == "CHANGED":
        st.subheader("🔁 CHANGED ADDRESS")
        df_changed = df[
            df["STATUS"].astype(str).str.strip().str.upper() == "CHANGED"
        ].copy()
        if df_changed.empty:
            df_changed = pd.DataFrame(
                columns=[
                    "ID",
                    "COMPANY",
                    "BRANCH",
                    "ADDRESS (NEW ADDRESS)",
                    "ADDRESS (OLD ADDRESS)",
                ]
            )
        else:
            df_changed["BRANCH"] = df_changed["BRANCH_NAME"]
            df_changed["ADDRESS (NEW ADDRESS)"] = df_changed["UPDATED_ADDRESS"]
            if "ADDRESS (OLD ADDRESS)" not in df_changed.columns:
                df_changed["ADDRESS (OLD ADDRESS)"] = None
        for c in [
            "ID",
            "COMPANY",
            "BRANCH",
            "ADDRESS (NEW ADDRESS)",
            "ADDRESS (OLD ADDRESS)",
        ]:
            if c not in df_changed.columns:
                df_changed[c] = None
        edited_ch = st.data_editor(
            df_changed[
                [
                    "ID",
                    "COMPANY",
                    "BRANCH",
                    "ADDRESS (NEW ADDRESS)",
                    "ADDRESS (OLD ADDRESS)",
                ]
            ],
            num_rows="dynamic",
            use_container_width=True,
            key=f"{company}_changed_editor",
            column_config={
                "ID": st.column_config.NumberColumn("ID", format="%d", step=1),
            },
        )
        if st.button("💾 Save Changes (Changed Address View)", key=f"{company}_save_changed"):
            partial_save = merge_changed_view_to_full(df, edited_ch)
            to_save = _merge_partial_into_full(df, partial_save)
            handle_save_with_id(
                df_name_prefix=key_prefix,
                table_name="BRANCH_TIN_ADDRESS",
                df=to_save,
                id_col="ID",
                insert_sql=insert_sql,
                update_sql=update_sql,
                insert_cols=insert_cols,
                update_cols=update_cols,
            )
            load_branch_tin.clear()
            st.session_state[orig_key] = load_branch_tin(company)
            st.success("Changed address view saved ✅")
            safe_rerun()


# ---------------------------------------------------
# BUSINESS PERMITS (UPDATE-ONLY, NO DELETE)
# ---------------------------------------------------
def business_permits():
    st.title("🏢 Business Permits Report")

    tab1, tab2, tab3, tab4 = st.tabs(
        ["Overview", "Business Permit", "Brgy Permit", "Other Fees for Renew"]
    )

    def save_overview(df):
        conn, cursor = get_cursor()
        skipped_rows = 0
        for _, row in df.iterrows():
            company = _db_param(row["COMPANY"])
            area = _db_param(row["AREA"])
            branch = _db_param(row["BRANCH"])
            if not company or not area or not branch:
                skipped_rows += 1
                continue

            cursor.execute(
                """
                MERGE INTO BUSINESS_PERMIT_OVERVIEW t
                USING (
                    SELECT
                        %s AS COMPANY,
                        %s AS AREA,
                        %s AS BRANCH,
                        %s AS DEADLINE,
                        %s AS DEADLINE_EXTENSION,
                        %s AS BRGY_PERMIT_2025,
                        %s AS BUSINESS_PERMIT_2025,
                        %s AS SEC_CERT,
                        %s AS GROSS_SALES_CERT
                ) s
                ON t.COMPANY = s.COMPANY
                AND t.AREA = s.AREA
                AND t.BRANCH = s.BRANCH
                WHEN MATCHED THEN UPDATE SET
                    DEADLINE = s.DEADLINE,
                    DEADLINE_EXTENSION = s.DEADLINE_EXTENSION,
                    BRGY_PERMIT_2025 = s.BRGY_PERMIT_2025,
                    BUSINESS_PERMIT_2025 = s.BUSINESS_PERMIT_2025,
                    SEC_CERT = s.SEC_CERT,
                    GROSS_SALES_CERT = s.GROSS_SALES_CERT
                WHEN NOT MATCHED THEN INSERT
                    (COMPANY, AREA, BRANCH, DEADLINE, DEADLINE_EXTENSION, BRGY_PERMIT_2025, BUSINESS_PERMIT_2025, SEC_CERT, GROSS_SALES_CERT)
                VALUES
                    (s.COMPANY, s.AREA, s.BRANCH, s.DEADLINE, s.DEADLINE_EXTENSION, s.BRGY_PERMIT_2025, s.BUSINESS_PERMIT_2025, s.SEC_CERT, s.GROSS_SALES_CERT)
                """,
                (
                    company,
                    area,
                    branch,
                    _db_date(row["DEADLINE"]),
                    _db_date(row["DEADLINE_EXTENSION"]),
                    _db_param(row["BRGY_PERMIT_2025"]),
                    _db_param(row["BUSINESS_PERMIT_2025"]),
                    _db_param(row["SEC_CERT"]),
                    _db_param(row["GROSS_SALES_CERT"]),
                ),
            )
        conn.commit()
        if skipped_rows:
            st.warning(
                f"Skipped {skipped_rows} row(s): COMPANY, AREA, and BRANCH are required."
            )

    def update_data(df, data_type):
        conn, cursor = get_cursor()
        skipped_rows = 0
        for _, row in df.iterrows():
            company = _db_param(row["COMPANY"])
            area = _db_param(row["AREA"])
            branch = _db_param(row["BRANCH"])
            if not company or not area or not branch:
                skipped_rows += 1
                continue

            cursor.execute(
                """
                MERGE INTO BUSINESS_PERMIT_TRACKER t
                USING (
                    SELECT
                        %s AS TYPE,
                        %s AS COMPANY,
                        %s AS AREA,
                        %s AS BRANCH,
                        %s AS CAF,
                        %s AS PAYEE,
                        %s AS PARTICULARS,
                        %s AS MODE_OF_PAYMENT,
                        %s AS AMOUNT,
                        %s AS ACCOUNTING_DATE,
                        %s AS TREASURY_DATE,
                        %s AS STATUS,
                        %s AS DATE_LIQUIDATION,
                        %s AS DATE_SUBMISSION_ACCOUNTING,
                        %s AS YEAR_COMPARISON,
                        %s AS PERCENT_CHANGE,
                        %s AS WITH_TAX_BILL,
                        %s AS REASON_NOT_REQUESTING_FUND
                ) s
                ON t.TYPE = s.TYPE
                AND t.COMPANY = s.COMPANY
                AND t.AREA = s.AREA
                AND t.BRANCH = s.BRANCH
                WHEN MATCHED THEN UPDATE SET
                    CAF = s.CAF,
                    PAYEE = s.PAYEE,
                    PARTICULARS = s.PARTICULARS,
                    MODE_OF_PAYMENT = s.MODE_OF_PAYMENT,
                    AMOUNT = s.AMOUNT,
                    ACCOUNTING_DATE = s.ACCOUNTING_DATE,
                    TREASURY_DATE = s.TREASURY_DATE,
                    STATUS = s.STATUS,
                    DATE_LIQUIDATION = s.DATE_LIQUIDATION,
                    DATE_SUBMISSION_ACCOUNTING = s.DATE_SUBMISSION_ACCOUNTING,
                    YEAR_COMPARISON = s.YEAR_COMPARISON,
                    PERCENT_CHANGE = s.PERCENT_CHANGE,
                    WITH_TAX_BILL = s.WITH_TAX_BILL,
                    REASON_NOT_REQUESTING_FUND = s.REASON_NOT_REQUESTING_FUND
                WHEN NOT MATCHED THEN INSERT
                    (TYPE, COMPANY, AREA, BRANCH, CAF, PAYEE, PARTICULARS, MODE_OF_PAYMENT, AMOUNT, ACCOUNTING_DATE, TREASURY_DATE, STATUS, DATE_LIQUIDATION, DATE_SUBMISSION_ACCOUNTING, YEAR_COMPARISON, PERCENT_CHANGE, WITH_TAX_BILL, REASON_NOT_REQUESTING_FUND)
                VALUES
                    (s.TYPE, s.COMPANY, s.AREA, s.BRANCH, s.CAF, s.PAYEE, s.PARTICULARS, s.MODE_OF_PAYMENT, s.AMOUNT, s.ACCOUNTING_DATE, s.TREASURY_DATE, s.STATUS, s.DATE_LIQUIDATION, s.DATE_SUBMISSION_ACCOUNTING, s.YEAR_COMPARISON, s.PERCENT_CHANGE, s.WITH_TAX_BILL, s.REASON_NOT_REQUESTING_FUND)
                """,
                (
                    _db_param(data_type),
                    company,
                    area,
                    branch,
                    _db_param(row["CAF"]),
                    _db_param(row["PAYEE"]),
                    _db_param(row["PARTICULARS"]),
                    _db_param(row["MODE_OF_PAYMENT"]),
                    _db_param(row["AMOUNT"]),
                    _db_date(row["ACCOUNTING_DATE"]),
                    _db_date(row["TREASURY_DATE"]),
                    _db_param(row["STATUS"]),
                    _db_date(row["DATE_LIQUIDATION"]),
                    _db_date(row["DATE_SUBMISSION_ACCOUNTING"]),
                    _db_param(row["YEAR_COMPARISON"]),
                    _db_param(row["PERCENT_CHANGE"]),
                    _db_param(row["WITH_TAX_BILL"]),
                    _db_param(row["REASON_NOT_REQUESTING_FUND"]),
                ),
            )
        conn.commit()
        if skipped_rows:
            st.warning(
                f"Skipped {skipped_rows} row(s): COMPANY, AREA, and BRANCH are required."
            )

    def render_editor(df, key):
        return st.data_editor(
            df,
            num_rows="dynamic",
            use_container_width=True,
            key=key,
            column_config={
                "CAF": "CAF #",
                "PARTICULARS": "Particulars",
                "MODE_OF_PAYMENT": "Mode of Payment",
                "AMOUNT": st.column_config.NumberColumn("Amount", format="%.2f"),
                "ACCOUNTING_DATE": st.column_config.DateColumn("Accounting Date"),
                "TREASURY_DATE": st.column_config.DateColumn("Treasury Date"),
                "DATE_LIQUIDATION": st.column_config.DateColumn("Date Liquidation"),
                "DATE_SUBMISSION_ACCOUNTING": st.column_config.DateColumn(
                    "Submission Date"
                ),
                "YEAR_COMPARISON": "2025 vs 2026",
                "PERCENT_CHANGE": "% Change",
                "WITH_TAX_BILL": "With Tax Bill?",
                "REASON_NOT_REQUESTING_FUND": "Reason",
            },
        )

    with tab1:
        st.subheader("Overview Form")

        df = load_business_permit_overview()
        df = filter_dataframe_controls(
            df,
            "bp_overview",
            ["COMPANY", "AREA", "BRANCH", "SEC_CERT"],
        )
        edited_df = st.data_editor(
            df,
            num_rows="dynamic",
            use_container_width=True,
            key="overview_editor",
            column_config={
                "DEADLINE": st.column_config.DateColumn("Deadline"),
                "DEADLINE_EXTENSION": st.column_config.DateColumn("Extension"),
                "BRGY_PERMIT_2025": st.column_config.NumberColumn(
                    "Brgy Permit", format="%.2f"
                ),
                "BUSINESS_PERMIT_2025": st.column_config.NumberColumn(
                    "Business Permit", format="%.2f"
                ),
                "GROSS_SALES_CERT": st.column_config.NumberColumn(
                    "Gross Sales", format="%.2f"
                ),
                "SEC_CERT": st.column_config.SelectboxColumn(
                    "SEC CERT", options=["DONE", "PENDING", "PROCESSING"]
                ),
            },
        )

        if st.button("💾 Save Overview"):
            save_overview(edited_df)
            load_business_permit_overview.clear()
            st.success("Overview Updated ✅")
            safe_rerun()

    with tab2:
        st.subheader("Business Permit")
        df = load_business_permit_tracker("BUSINESS_PERMIT")
        df = filter_dataframe_controls(
            df,
            "bp_bus",
            ["COMPANY", "AREA", "BRANCH", "STATUS", "CAF"],
        )
        edited_df = render_editor(df, "business_tab")

        if st.button("💾 Save Business Permit"):
            update_data(edited_df, "BUSINESS_PERMIT")
            load_business_permit_tracker.clear()
            st.success("Saved ✅")
            safe_rerun()

    with tab3:
        st.subheader("Barangay Permit")
        df = load_business_permit_tracker("BRGY_PERMIT")
        df = filter_dataframe_controls(
            df,
            "bp_brgy",
            ["COMPANY", "AREA", "BRANCH", "STATUS", "CAF"],
        )
        edited_df = render_editor(df, "brgy_tab")

        if st.button("💾 Save Brgy Permit"):
            update_data(edited_df, "BRGY_PERMIT")
            load_business_permit_tracker.clear()
            st.success("Saved ✅")
            safe_rerun()

    with tab4:
        st.subheader("Other Fees for Renew")
        df = load_business_permit_tracker("OTHER_FEES")
        df = filter_dataframe_controls(
            df,
            "bp_other",
            ["COMPANY", "AREA", "BRANCH", "STATUS", "CAF"],
        )
        edited_df = render_editor(df, "other_tab")

        if st.button("💾 Save Other Fees"):
            update_data(edited_df, "OTHER_FEES")
            load_business_permit_tracker.clear()
            st.success("Saved ✅")
            safe_rerun()


def append_tax_mapped_sticker_history(
    tax_mapped_id: int, image_bytes: bytes, as_of_date, uploaded_by: str
):
    conn, cursor = get_cursor()
    cursor.execute(
        """
        INSERT INTO TAX_MAPPED_STICKER_HISTORY
        (TAX_MAPPED_ID, IMAGE_DATA, AS_OF_DATE, UPLOADED_BY)
        VALUES (%s, %s, %s, %s)
        """,
        (int(tax_mapped_id), image_bytes, as_of_date, uploaded_by),
    )
    cursor.execute(
        """
        UPDATE TAX_MAPPED
        SET STICKER_IMAGE = %s
        WHERE ID = %s
        """,
        (image_bytes, int(tax_mapped_id)),
    )
    conn.commit()


def fetch_tax_mapped_sticker_history_meta(tax_mapped_id: int) -> pd.DataFrame:
    conn, cursor = get_cursor()
    cursor.execute(
        """
        SELECT ID, AS_OF_DATE, CREATED_AT, UPLOADED_BY,
               OCTET_LENGTH(IMAGE_DATA) AS IMAGE_BYTES
        FROM TAX_MAPPED_STICKER_HISTORY
        WHERE TAX_MAPPED_ID = %s
        ORDER BY AS_OF_DATE DESC, CREATED_AT DESC
        """,
        (int(tax_mapped_id),),
    )
    rows = cursor.fetchall()
    if not rows:
        return pd.DataFrame(
            columns=["ID", "AS_OF_DATE", "CREATED_AT", "UPLOADED_BY", "IMAGE_BYTES"]
        )
    return pd.DataFrame(
        rows,
        columns=["ID", "AS_OF_DATE", "CREATED_AT", "UPLOADED_BY", "IMAGE_BYTES"],
    )


def fetch_tax_mapped_sticker_history_image(history_id: int):
    conn, cursor = get_cursor()
    cursor.execute(
        """
        SELECT IMAGE_DATA
        FROM TAX_MAPPED_STICKER_HISTORY
        WHERE ID = %s
        """,
        (int(history_id),),
    )
    row = cursor.fetchone()
    return row[0] if row else None


def append_fire_safety_image_history(
    fire_safety_id: int, image_bytes: bytes, as_of_date, uploaded_by: str
):
    conn, cursor = get_cursor()
    cursor.execute(
        """
        INSERT INTO FIRE_SAFETY_IMAGE_HISTORY
        (FIRE_SAFETY_ID, IMAGE_DATA, AS_OF_DATE, UPLOADED_BY)
        VALUES (%s, %s, %s, %s)
        """,
        (int(fire_safety_id), image_bytes, as_of_date, uploaded_by),
    )
    cursor.execute(
        """
        UPDATE FIRE_SAFETY
        SET FSIC_IMAGE = %s
        WHERE ID = %s
        """,
        (image_bytes, int(fire_safety_id)),
    )
    conn.commit()


def fetch_fire_safety_image_history_meta(fire_safety_id: int) -> pd.DataFrame:
    conn, cursor = get_cursor()
    cursor.execute(
        """
        SELECT ID, AS_OF_DATE, CREATED_AT, UPLOADED_BY,
               OCTET_LENGTH(IMAGE_DATA) AS IMAGE_BYTES
        FROM FIRE_SAFETY_IMAGE_HISTORY
        WHERE FIRE_SAFETY_ID = %s
        ORDER BY AS_OF_DATE DESC, CREATED_AT DESC
        """,
        (int(fire_safety_id),),
    )
    rows = cursor.fetchall()
    if not rows:
        return pd.DataFrame(
            columns=["ID", "AS_OF_DATE", "CREATED_AT", "UPLOADED_BY", "IMAGE_BYTES"]
        )
    return pd.DataFrame(
        rows,
        columns=["ID", "AS_OF_DATE", "CREATED_AT", "UPLOADED_BY", "IMAGE_BYTES"],
    )


def fetch_fire_safety_history_image(history_id: int):
    conn, cursor = get_cursor()
    cursor.execute(
        """
        SELECT IMAGE_DATA
        FROM FIRE_SAFETY_IMAGE_HISTORY
        WHERE ID = %s
        """,
        (int(history_id),),
    )
    row = cursor.fetchone()
    return row[0] if row else None


def tax_mapped_default_as_of_date(row_id: int):
    conn, cursor = get_cursor()
    cursor.execute(
        "SELECT DATE_TAX_MAPPED FROM TAX_MAPPED WHERE ID = %s",
        (int(row_id),),
    )
    row = cursor.fetchone()
    if row and row[0] is not None:
        d = row[0]
        if hasattr(d, "date"):
            return d.date()
        return d
    return date.today()


def fire_safety_default_as_of_date(row_id: int):
    conn, cursor = get_cursor()
    for q in (
        "SELECT VALID_UNTIL FROM FIRE_SAFETY WHERE ID = %s",
        "SELECT FSIC_CERTIFICATE_DATE FROM FIRE_SAFETY WHERE ID = %s",
        "SELECT FSIC_VALIDITY FROM FIRE_SAFETY WHERE ID = %s",
    ):
        try:
            cursor.execute(q, (int(row_id),))
            row = cursor.fetchone()
            if row and row[0] is not None:
                d = row[0]
                return d.date() if hasattr(d, "date") else d
        except Exception:
            continue
    return date.today()


# ---------------------------------------------------
# TAX MAPPED
# ---------------------------------------------------
def tax_mapped():
    st.title("🏷 TAX MAPPED Report")

    df_full = load_tax_mapped()

    if "tax_mapped_orig" not in st.session_state:
        st.session_state.tax_mapped_orig = df_full.copy()

    df_grid = df_full.drop(columns=["STICKER_IMAGE"], errors="ignore")

    df_view = filter_dataframe_controls(
        df_grid,
        "tax_mapped",
        ["AREA", "BRANCH", "BIR_REMARKS"],
    )

    edited_partial = st.data_editor(
        df_view,
        num_rows="dynamic",
        use_container_width=True,
        key="tax_mapped_editor",
        column_config={
            "DATE_TAX_MAPPED": st.column_config.DateColumn("Date Tax Mapped"),
        },
    )

    col_save, col_undo, col_refresh = st.columns(3)

    with col_save:
        if st.button("💾 Save Table"):
            handle_save_with_id(
                df_name_prefix="tax_mapped",
                table_name="TAX_MAPPED",
                df=reconcile_editor_after_filter(
                    df_full, df_view, edited_partial, "ID"
                ),
                id_col="ID",
                insert_sql="""
                    INSERT INTO TAX_MAPPED
                    (AREA,BRANCH,DATE_TAX_MAPPED,BIR_REMARKS)
                    VALUES (%s,%s,%s,%s)
                """,
                update_sql="""
                    UPDATE TAX_MAPPED
                    SET
                        AREA=%s,
                        BRANCH=%s,
                        DATE_TAX_MAPPED=%s,
                        BIR_REMARKS=%s
                    WHERE ID=%s
                """,
                insert_cols=[
                    "AREA",
                    "BRANCH",
                    "DATE_TAX_MAPPED",
                    "BIR_REMARKS",
                ],
                update_cols=[
                    "AREA",
                    "BRANCH",
                    "DATE_TAX_MAPPED",
                    "BIR_REMARKS",
                ],
            )
            load_tax_mapped.clear()
            st.session_state.tax_mapped_orig = load_tax_mapped()
            st.success("Table Updated ✅")
            safe_rerun()

    with col_undo:
        if st.button("↩ Undo last delete"):
            ok = handle_undo_with_id(
                df_name_prefix="tax_mapped",
                table_name="TAX_MAPPED",
                insert_sql_with_id="""
                    INSERT INTO TAX_MAPPED
                    (ID,AREA,BRANCH,DATE_TAX_MAPPED,BIR_REMARKS,STICKER_IMAGE)
                    VALUES (%s,%s,%s,%s,%s,%s)
                """,
                cols_with_id=[
                    "ID",
                    "AREA",
                    "BRANCH",
                    "DATE_TAX_MAPPED",
                    "BIR_REMARKS",
                    "STICKER_IMAGE",
                ],
            )
            if ok:
                load_tax_mapped.clear()
                st.session_state.tax_mapped_orig = load_tax_mapped()
                st.success("Delete undone 🔄")
                safe_rerun()
            else:
                st.info("Nothing to undo")

    with col_refresh:
        if st.button("🔄 Refresh"):
            load_tax_mapped.clear()
            st.session_state.tax_mapped_orig = load_tax_mapped()
            safe_rerun()

    st.subheader("Upload Tax Mapped sticker (history)")
    st.caption(
        "Each upload is stored in TAX_MAPPED_STICKER_HISTORY. Older files are kept. "
        "TAX_MAPPED.STICKER_IMAGE always shows the latest upload for that row."
    )

    with st.form("tax_mapped_sticker_upload"):
        u_row = st.number_input("TAX_MAPPED row ID", step=1, min_value=1, key="tax_up_row")
        u_as_of = st.date_input(
            "As-of date for this photo (e.g. visit / mapping date)",
            value=tax_mapped_default_as_of_date(int(u_row)),
            key="tax_up_asof",
        )
        u_file = st.file_uploader(
            "Image file",
            type=["png", "jpg", "jpeg", "webp", "gif"],
            key="tax_up_file",
        )
        submitted = st.form_submit_button("Append to history & set as latest")
    if submitted:
        if u_file is None:
            st.warning("Choose an image file first.")
        else:
            try:
                append_tax_mapped_sticker_history(
                    int(u_row),
                    u_file.getvalue(),
                    u_as_of,
                    st.session_state.get("user", "UNKNOWN"),
                )
                load_tax_mapped.clear()
                st.session_state.tax_mapped_orig = load_tax_mapped()
                st.success("Saved to history; latest sticker updated for this row.")
                st.image(u_file, width=280)
            except Exception as e:
                st.error(
                    f"Could not save (did you run the DDL?). {e}\n\n"
                    "Create tables using `snowflake_image_history_ddl.sql` in this folder."
                )

    st.subheader("Sticker history & preview")
    with st.expander("Browse past uploads for a row", expanded=False):
        h_row = st.number_input(
            "Row ID to inspect", step=1, min_value=1, key="tax_hist_row"
        )
        try:
            hdf = fetch_tax_mapped_sticker_history_meta(int(h_row))
            st.dataframe(hdf, use_container_width=True)
            if not hdf.empty:
                labels = {
                    int(row["ID"]): f"{row['AS_OF_DATE']} — {row['CREATED_AT']} — {row['IMAGE_BYTES'] or 0} bytes"
                    for _, row in hdf.iterrows()
                }
                pick = st.selectbox(
                    "Preview version",
                    options=list(labels.keys()),
                    format_func=lambda i: labels[i],
                    key="tax_hist_pick",
                )
                blob = fetch_tax_mapped_sticker_history_image(int(pick))
                if blob:
                    st.image(blob, width=360)
        except Exception as e:
            st.error(
                f"History query failed. Run `snowflake_image_history_ddl.sql` first. {e}"
            )


# ---------------------------------------------------
# Secretary Certificates Page
# ---------------------------------------------------
def secretary_certificates():
    st.title("📄 Secretary Certificates Compliance")

    df = load_sec()

    if "sec_orig" not in st.session_state:
        st.session_state.sec_orig = df.copy()

    df_view = filter_dataframe_controls(
        df,
        "sec",
        ["AREA", "BRANCH", "COMPANY", "MONTH_YEAR", "STATUS", "FINAL_STATUS"],
    )

    edited_partial = st.data_editor(
        df_view,
        num_rows="dynamic",
        use_container_width=True,
        key="sec_editor",
        column_config={
            "DATE_FORWARDED": st.column_config.DateColumn("Date Forwarded"),
            "DATE_RECEIVED": st.column_config.DateColumn("Date Received"),
        },
    )

    col_save, col_undo, col_refresh = st.columns(3)

    with col_save:
        if st.button("💾 Save Changes"):
            log_activity("Secretary Certificates", "SAVE")

            handle_save_with_id(
                df_name_prefix="sec",
                table_name="SECRETARY_CERTIFICATES",
                df=reconcile_editor_after_filter(df, df_view, edited_partial, "ID"),
                id_col="ID",
                insert_sql="""
                    INSERT INTO SECRETARY_CERTIFICATES
                    (AREA,MONTH_YEAR,BRANCH,COMPANY,REMARKS,DATE_FORWARDED,STATUS,DATE_RECEIVED,FINAL_STATUS)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                update_sql="""
                    UPDATE SECRETARY_CERTIFICATES
                    SET
                        AREA=%s,
                        MONTH_YEAR=%s,
                        BRANCH=%s,
                        COMPANY=%s,
                        REMARKS=%s,
                        DATE_FORWARDED=%s,
                        STATUS=%s,
                        DATE_RECEIVED=%s,
                        FINAL_STATUS=%s
                    WHERE ID=%s
                """,
                insert_cols=[
                    "AREA",
                    "MONTH_YEAR",
                    "BRANCH",
                    "COMPANY",
                    "REMARKS",
                    "DATE_FORWARDED",
                    "STATUS",
                    "DATE_RECEIVED",
                    "FINAL_STATUS",
                ],
                update_cols=[
                    "AREA",
                    "MONTH_YEAR",
                    "BRANCH",
                    "COMPANY",
                    "REMARKS",
                    "DATE_FORWARDED",
                    "STATUS",
                    "DATE_RECEIVED",
                    "FINAL_STATUS",
                ],
            )

            load_sec.clear()
            st.session_state.sec_orig = load_sec()
            st.success("Data saved successfully ✅")
            safe_rerun()

    with col_undo:
        if st.button("↩ Undo last delete"):
            ok = handle_undo_with_id(
                df_name_prefix="sec",
                table_name="SECRETARY_CERTIFICATES",
                insert_sql_with_id="""
                    INSERT INTO SECRETARY_CERTIFICATES
                    (ID,AREA,MONTH_YEAR,BRANCH,COMPANY,REMARKS,DATE_FORWARDED,STATUS,DATE_RECEIVED,FINAL_STATUS)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                cols_with_id=[
                    "ID",
                    "AREA",
                    "MONTH_YEAR",
                    "BRANCH",
                    "COMPANY",
                    "REMARKS",
                    "DATE_FORWARDED",
                    "STATUS",
                    "DATE_RECEIVED",
                    "FINAL_STATUS",
                ],
            )
            if ok:
                load_sec.clear()
                st.session_state.sec_orig = load_sec()
                st.success("Delete undone 🔄")
                safe_rerun()
            else:
                st.info("Nothing to undo")

    with col_refresh:
        if st.button("🔄 Refresh"):
            load_sec.clear()
            st.session_state.sec_orig = load_sec()
            safe_rerun()

def board_resolutions():
    st.title("📄 Board Resolutions Compliance")

    df = load_board()

    if "board_orig" not in st.session_state:
        st.session_state.board_orig = df.copy()

    df_view = filter_dataframe_controls(
        df,
        "board",
        ["COMPANY", "STATUS", "REMARKS"],
    )

    edited_partial = st.data_editor(
        df_view,
        num_rows="dynamic",
        use_container_width=True,
        key="board_editor",
        column_config={
            "DATE_FORWARDED": st.column_config.DateColumn("Date Forwarded"),
            "DATE_RECEIVED": st.column_config.DateColumn("Date Received"),
        },
    )

    col_save, col_undo, col_refresh = st.columns(3)

    # ---------------------------------------------------
    # SAVE
    # ---------------------------------------------------
    with col_save:
        if st.button("💾 Save Changes", key="board_save"):

            log_activity("Board Resolutions", "SAVE")

            to_save = reconcile_editor_after_filter(
                df, df_view, edited_partial, "ID"
            ).replace({pd.NA: None})

            handle_save_with_id(
                df_name_prefix="board",
                table_name="BOARD_RESOLUTIONS",
                df=to_save,
                id_col="ID",
                insert_sql="""
                    INSERT INTO BOARD_RESOLUTIONS
                    (COMPANY, REMARKS, DATE_FORWARDED, DATE_RECEIVED, STATUS)
                    VALUES (%s,%s,%s,%s,%s)
                """,
                update_sql="""
                    UPDATE BOARD_RESOLUTIONS
                    SET
                        COMPANY=%s,
                        REMARKS=%s,
                        DATE_FORWARDED=%s,
                        DATE_RECEIVED=%s,
                        STATUS=%s
                    WHERE ID=%s
                """,
                insert_cols=[
                    "COMPANY",
                    "REMARKS",
                    "DATE_FORWARDED",
                    "DATE_RECEIVED",
                    "STATUS",
                ],
                update_cols=[
                    "COMPANY",
                    "REMARKS",
                    "DATE_FORWARDED",
                    "DATE_RECEIVED",
                    "STATUS",
                ],
            )

            load_board.clear()
            st.session_state.board_orig = load_board()

            st.success("Data saved successfully ✅")
            safe_rerun()

    # ---------------------------------------------------
    # UNDO
    # ---------------------------------------------------
    with col_undo:
        if st.button("↩ Undo last delete", key="board_undo"):

            ok = handle_undo_with_id(
                df_name_prefix="board",
                table_name="BOARD_RESOLUTIONS",
                insert_sql_with_id="""
                    INSERT INTO BOARD_RESOLUTIONS
                    (ID, COMPANY, REMARKS, DATE_FORWARDED, DATE_RECEIVED, STATUS)
                    VALUES (%s,%s,%s,%s,%s,%s)
                """,
                cols_with_id=[
                    "ID",
                    "COMPANY",
                    "REMARKS",
                    "DATE_FORWARDED",
                    "DATE_RECEIVED",
                    "STATUS",
                ],
            )

            if ok:
                load_board.clear()
                st.session_state.board_orig = load_board()
                st.success("Delete undone 🔄")
                safe_rerun()
            else:
                st.info("Nothing to undo")

    # ---------------------------------------------------
    # REFRESH
    # ---------------------------------------------------
    with col_refresh:
        if st.button("🔄 Refresh", key="board_refresh"):
            load_board.clear()
            st.session_state.board_orig = load_board()
            safe_rerun()
# ---------------------------------------------------
# DASHBOARD ANALYTICS (Power BI + charts)
# ---------------------------------------------------
def dashboard_analytics():
    st.title("📊 Compliance Executive Dashboard")

    df_status = load_dashboard_business_status()
    df_area = load_dashboard_area_distribution()
    df_overview = load_dashboard_overview_sla()
    df_pending = load_dashboard_pending()

    today = datetime.today().date()

    # =========================
    # SLA LOGIC
    # =========================
    if not df_overview.empty:
        df_overview_work = df_overview.copy()

        for c in ["DEADLINE", "DEADLINE_EXTENSION"]:
            df_overview_work[c] = pd.to_datetime(
                df_overview_work[c], errors="coerce"
            )

        df_overview_work["EFFECTIVE_DEADLINE"] = (
            df_overview_work["DEADLINE_EXTENSION"]
            .fillna(df_overview_work["DEADLINE"])
            .dt.date
        )

        df_overview_work["SLA_STATUS"] = df_overview_work["EFFECTIVE_DEADLINE"].apply(
            lambda d: "NO_DEADLINE" if pd.isna(d) else ("LATE" if d < today else "ON_TIME")
        )
    else:
        df_overview_work = pd.DataFrame()

    # =========================
    # RISK SCORING
    # =========================
    def compute_risk(row):
        score = 0

        if not df_pending.empty:
            has_pending = (
                (df_pending["COMPANY"] == row["COMPANY"]) &
                (df_pending["AREA"] == row["AREA"]) &
                (df_pending["BRANCH"] == row["BRANCH"])
            ).any()
            if has_pending:
                score += 40

        if row.get("SLA_STATUS") == "LATE":
            score += 40

        if row.get("SEC_CERT") in ["PENDING", "PROCESSING"]:
            score += 10

        if pd.isna(row.get("GROSS_SALES_CERT")):
            score += 10

        return min(score, 100)

    if not df_overview_work.empty:
        df_overview_work["RISK_SCORE"] = df_overview_work.apply(compute_risk, axis=1)

        def risk_bucket(x):
            if x >= 70: return "RED"
            if x >= 40: return "YELLOW"
            return "GREEN"

        df_overview_work["RISK_BUCKET"] = df_overview_work["RISK_SCORE"].apply(risk_bucket)

    # =========================
    # KPI
    # =========================
    total_branches = int(df_area["COUNT"].sum()) if not df_area.empty else 0
    done = int(df_status[df_status["STATUS"] == "DONE"]["COUNT"].sum()) if not df_status.empty else 0
    total = int(df_status["COUNT"].sum()) if not df_status.empty else 0
    rate = (done / total * 100) if total else 0

    if not df_overview_work.empty and "SLA_STATUS" in df_overview_work:
        late = int(df_overview_work[df_overview_work["SLA_STATUS"] == "LATE"].shape[0])
    else:
        late = 0

    if not df_overview_work.empty and "RISK_BUCKET" in df_overview_work:
        high_risk = int(df_overview_work[df_overview_work["RISK_BUCKET"] == "RED"].shape[0])
    else:
        high_risk = 0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Branches", total_branches)
    c2.metric("Completion %", f"{rate:.1f}%")
    c3.metric("Late SLA", late)
    c4.metric("High Risk", high_risk)

    st.divider()

    # =========================
    # CHARTS + TABLE
    # =========================
    left, right = st.columns([2, 1.5])

    with left:
        if not df_status.empty:
            st.plotly_chart(
                px.pie(df_status, values="COUNT", names="STATUS"),
                width="stretch"
            )

        if not df_area.empty:
            st.plotly_chart(
                px.bar(df_area, x="AREA", y="COUNT"),
                width="stretch"
            )

        if not df_overview_work.empty:
            sla = df_overview_work["SLA_STATUS"].value_counts().reset_index()
            sla.columns = ["STATUS", "COUNT"]

            st.plotly_chart(
                px.bar(sla, x="STATUS", y="COUNT"),
                width="stretch"
            )

    with right:
        st.subheader("Branch Drilldown")

        if not df_overview_work.empty:
            df_dd = filter_dataframe_controls(
                df_overview_work,
                "dash_drill",
                ["AREA", "COMPANY", "BRANCH", "SLA_STATUS", "RISK_BUCKET"],
            )
            st.dataframe(df_dd, width="stretch")

    st.divider()

    # =========================
    # AI INSIGHTS
    # =========================
    if st.button("🤖 Generate AI Insights"):
        prompt = f"""
        Total branches: {total_branches}
        Completion rate: {rate:.1f}%
        Late branches: {late}
        High risk: {high_risk}

        Give executive insights and actions.
        """

        try:
            res = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
            )
            st.write(res.choices[0].message.content)
        except Exception as e:
            st.error(e)

# ---------------------------------------------------
# MAIN DASHBOARD PAGE (sidebar navigation)
# ---------------------------------------------------
def dashboard():
    def live_clock_js():
        st.markdown(
            """
        <div id="clock" style="
            background:#3d5960;
            padding:18px;
            border-radius:20px;
            text-align:center;
            font-size:20px;
            font-weight:bold;
            color:white;">
        </div>

        <script>
        function updateClock() {
            const now = new Date();

            const options = {
                weekday: 'long',
                year: 'numeric',
                month: 'long',
                day: 'numeric'
            };

            const date = now.toLocaleDateString('en-US', options);
            const time = now.toLocaleTimeString();

            document.getElementById('clock').innerHTML = date + " | " + time;
        }

        setInterval(updateClock, 1000);
        updateClock();
        </script>
        """,
            unsafe_allow_html=True,
        )

    live_clock_js()

    st.sidebar.title("📊 Compliance Menu")

    menu = st.sidebar.radio(
        "Navigation",
        [
            "Dashboard",
            "ATP Certificates",
            "Secretary Certificates",
            "BIR 1906",
            "Business Permits",
            "Board Resolutions",
            "BOA Stickers",
            "Fire Safety",
            "TIN & Address",
            "Tax Mapped",
            "AI Compliance Copilot",
            "Admin Panel",
        ],
    )

    if "current_page" not in st.session_state:
        st.session_state.current_page = menu
        log_activity(menu, "OPEN_PAGE")
    elif st.session_state.current_page != menu:
        log_activity(menu, "SWITCH_PAGE")
        st.session_state.current_page = menu

    pages = {
        "Dashboard": dashboard_analytics,
        "ATP Certificates": atp_certificates,
        "Secretary Certificates": secretary_certificates,
        "BIR 1906": bir_1906_atp,
        "Business Permits": business_permits,
        "BOA Stickers": boa_sticker,
        "Fire Safety": fire_safety,
        "TIN & Address": branch_tin_address,
        "Tax Mapped": tax_mapped,
        "AI Compliance Copilot": ai_copilot,
        "Admin Panel": admin_panel,
        "Board Resolutions": board_resolutions,
    }

    pages[menu]()

    st.sidebar.divider()
    st.sidebar.markdown(
        f"""
        👤 Logged in as:  
        **{st.session_state.get("user", "Unknown")}**
        """
    )

    if st.sidebar.button("🚪 Logout"):
        log_activity("SYSTEM", "LOGOUT")
        st.session_state.logged_in = False
        st.session_state.page = "login"
        st.session_state.clear()
        safe_rerun()


# ---------------------------------------------------
# PAGE ROUTING (LOGIN SYSTEM)
# ---------------------------------------------------

if not st.session_state.logged_in:
    if st.session_state.page == "login":
        login()
    elif st.session_state.page == "register":
        register()
else:
    dashboard()
