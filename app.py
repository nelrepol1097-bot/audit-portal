import os
import time
import smtplib
from datetime import datetime
import pandas as pd
import plotly.express as px
import requests
import snowflake.connector
import streamlit as st
from openai import OpenAI
from streamlit_lottie import st_lottie
from streamlit_plotly_events import plotly_events
import plotly.graph_objects as go

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
            vals = [row[c] for c in insert_cols]
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

            vals = [row[c] for c in update_cols] + [rid]
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

# ---------------------------------------------------
# ACTIVITY / AUDIT LOG
# ---------------------------------------------------


def log_activity(page, action):
    try:
        conn, cursor = get_cursor()
        cursor.execute(
            """
            INSERT INTO USER_ACTIVITY_LOG
            (EMAIL, PAGE, ACTION, TIMESTAMP)
            VALUES (%s,%s,%s,%s)
            """,
            (
                st.session_state.get("user", "UNKNOWN"),
                page,
                action,
                datetime.now(),
            ),
        )
        conn.commit()
    except Exception:
        pass


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

        # ✅ BUTTON MUST WRAP EVERYTHING
        if st.button("Login"):

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

                    # ✅ CORRECT PLACEMENT
                    st.session_state.logged_in = True
                    st.session_state.user = email
                    st.session_state.page = "dashboard"

                    st.success("Login Successful")

                    safe_rerun()  # ✅ redirect immediately

                elif status == "PENDING":
                    st.warning("Your account is waiting for admin approval.")

                elif status == "REJECTED":
                    st.error("Your account was rejected.")
            else:
                st.error("Invalid email or password")

        st.divider()

        # ✅ FIX INDENTATION HERE ALSO
        if st.button("Create Account"):
            st.session_state.page = "register"
            safe_rerun()

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
        st.session_state.page = "login"   # ✅ ADD
        safe_rerun()                      # ✅ ADD

    if st.button("Back to Login"):
     st.session_state.page = "login"
     safe_rerun()   # ✅ ADD


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

    @st.cache_data(ttl=30, show_spinner=False)
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

    df = load_atp()

    if "atp_orig" not in st.session_state:
        st.session_state.atp_orig = df.copy()

    edited_df = st.data_editor(
        df,
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
                df=edited_df,
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

    @st.cache_data(ttl=30, show_spinner=False)
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

    df = load_bir()

    if "bir1906_orig" not in st.session_state:
        st.session_state.bir1906_orig = df.copy()

    edited_df = st.data_editor(
        df,
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
                df=edited_df,
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

        FIRE_SAFETY(COMPANY, AREA, BRANCH, FSIC_CERTIFICATE_DATE,VALID_UNTIL,STATUS, REMARKS)

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

    @st.cache_data(ttl=30, show_spinner=False)
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

    df = load_boa()

    if "boa_orig" not in st.session_state:
        st.session_state.boa_orig = df.copy()

    edited_df = st.data_editor(
        df,
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
                df=edited_df,
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

    @st.cache_data(ttl=30, show_spinner=False)
    def load_fire():
        conn, cursor = get_cursor()
        cursor.execute("SELECT * FROM FIRE_SAFETY")
        data = cursor.fetchall()
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
        ]
        return pd.DataFrame(data, columns=columns)

    df = load_fire()

    if "fire_orig" not in st.session_state:
        st.session_state.fire_orig = df.copy()

    edited_df = st.data_editor(
        df,
        num_rows="dynamic",
        use_container_width=True,
        key="fire_editor",
        column_config={
            "VALID_UNTIL": st.column_config.DateColumn("VALID_UNTIL"),
        },
    )

    col_save, col_undo, col_refresh = st.columns(3)

    with col_save:
        if st.button("💾 Save Changes", key="fire_save"):
            handle_save_with_id(
                df_name_prefix="fire",
                table_name="FIRE_SAFETY",
                df=edited_df,
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
            load_fire.clear()
            st.session_state.fire_orig = load_fire()
            st.success("Data saved successfully ✅")
            safe_rerun()

    with col_undo:
        if st.button("↩ Undo last delete", key="fire_undo"):
            ok = handle_undo_with_id(
                df_name_prefix="fire",
                table_name="FIRE_SAFETY",
                insert_sql_with_id="""
                    INSERT INTO FIRE_SAFETY
                    (ID,COMPANY,AREA,BRANCH,FSIC_CERTIFICATE_DATE,VALID_UNTIL,FSIC_FEE,REMARKS)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
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


# ---------------------------------------------------
# BRANCH TIN & ADDRESS
# ---------------------------------------------------
def branch_tin_address():
    st.title("🏢 Branch TIN & Address")

    # =========================
    # 🚀 FUTURISTIC UI STYLE
    # =========================
    st.markdown("""
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
        transition: all 0.3s ease;
    }

    button[kind="secondary"]:hover {
        transform: scale(1.05);
        box-shadow: 0 0 25px rgba(0,255,255,0.9);
    }

    button[kind="secondary"]:active {
        box-shadow: 0 0 40px rgba(0,255,255,1),
                    0 0 80px rgba(0,255,255,0.6);
        transform: scale(0.97);
    }
    </style>

    <div class="holo-title">⚡ BRANCH CONTROL PANEL ⚡</div>
    """, unsafe_allow_html=True)

    # =========================
    # 🔥 ALL BUTTONS IN ONE LINE
    # =========================
    col1, col2, col3, col4, col5 = st.columns(5)

    with col1:
        if st.button("SUKI Branch"):
            st.session_state.company = "SUKI"

    with col2:
        if st.button("PCNFCI Branch"):
            st.session_state.company = "PCNFCI"

    with col3:
        if st.button("FASTCASH Branch"):
            st.session_state.company = "FASTCASH"

    with col4:
        if st.button("🆕 NEW BRANCH ADDRESS"):
            st.session_state.view_mode = "NEW"

    with col5:
        if st.button("🔁 CHANGED ADDRESS"):
            st.session_state.view_mode = "CHANGED"

    if "view_mode" not in st.session_state:
        st.session_state.view_mode = "MAIN"

    # =========================
    # MAIN (UNCHANGED LOGIC)
    # =========================
    if "company" in st.session_state:
        company = st.session_state.company

        def load_data(company):
            conn, cursor = get_cursor()
            cursor.execute("""
                SELECT *
                FROM BRANCH_TIN_ADDRESS
                WHERE COMPANY = %s
            """, (company,))
            data = cursor.fetchall()

            cols = [
                "ID","COMPANY","TIN","BRANCH_CODE","RDO","AREA",
                "BRANCH_NAME","UPDATED_ADDRESS","STATUS",
                "DATE_OPEN","DATE_OF_CLOSURE"
            ]

            return pd.DataFrame(data, columns=cols)

        df = load_data(company)

        # =========================
        # 🟢 MAIN TABLE (UNCHANGED)
        # =========================
        st.subheader(f"{company} Full Branch Table")

        edited_df = st.data_editor(
            df,
            num_rows="dynamic",
            use_container_width=True,
            key=f"{company}_main_editor"
        )

        if st.button("💾 Save Changes (Main Table)"):
            edited_df = edited_df.where(pd.notnull(edited_df), None)

            handle_save_with_id(
                df_name_prefix=f"{company}_main",
                table_name="BRANCH_TIN_ADDRESS",
                df=edited_df,
                id_col="ID",
                insert_sql="""
                    INSERT INTO BRANCH_TIN_ADDRESS
                    (COMPANY,TIN,BRANCH_CODE,RDO,AREA,BRANCH_NAME,UPDATED_ADDRESS,STATUS,DATE_OPEN,DATE_OF_CLOSURE)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                update_sql="""
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
                        DATE_OF_CLOSURE=%s
                    WHERE ID=%s
                """,
                insert_cols=[
                    "COMPANY","TIN","BRANCH_CODE","RDO","AREA",
                    "BRANCH_NAME","UPDATED_ADDRESS","STATUS",
                    "DATE_OPEN","DATE_OF_CLOSURE"
                ],
                update_cols=[
                    "COMPANY","TIN","BRANCH_CODE","RDO","AREA",
                    "BRANCH_NAME","UPDATED_ADDRESS","STATUS",
                    "DATE_OPEN","DATE_OF_CLOSURE"
                ],
            )

            st.success("Saved Main Table ✅")
            safe_rerun()

        st.divider()

        # =========================
        # 🆕 NEW VIEW
        # =========================
        if st.session_state.view_mode == "NEW":
            st.subheader("🆕 NEW BRANCH ADDRESS")

            df_new = df[df["STATUS"] == "NEW"].copy()
            df_new["ADDRESS"] = df_new["UPDATED_ADDRESS"]

            st.data_editor(
                df_new[["COMPANY","BRANCH_NAME","ADDRESS","DATE_OPEN"]],
                num_rows="dynamic",
                use_container_width=True,
                key=f"{company}_new_editor"
            )

        # =========================
        # 🔁 CHANGED VIEW
        # =========================
        if st.session_state.view_mode == "CHANGED":
            st.subheader("🔁 CHANGED ADDRESS")

            df_changed = df[df["STATUS"] == "CHANGED"].copy()

            if "OLD_ADDRESS" not in df_changed.columns:
                df_changed["OLD_ADDRESS"] = ""

            df_changed["ADDRESS (NEW ADDRESS)"] = df_changed["UPDATED_ADDRESS"]

            st.data_editor(
                df_changed[["COMPANY","BRANCH_NAME","ADDRESS (NEW ADDRESS)","OLD_ADDRESS"]],
                num_rows="dynamic",
                use_container_width=True,
                key=f"{company}_changed_editor"
            )
# ---------------------------------------------------
# BUSINESS PERMITS (UPDATE-ONLY, NO DELETE)
# ---------------------------------------------------
def business_permits():
    st.title("🏢 Business Permits Report")

    tab1, tab2, tab3, tab4 = st.tabs(
        ["Overview", "Business Permit", "Brgy Permit", "Other Fees for Renew"]
    )

    @st.cache_data(ttl=30, show_spinner=False)
    def load_data(data_type):
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

    @st.cache_data(ttl=30, show_spinner=False)
    def load_overview():
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

    def save_overview(df):
        conn, cursor = get_cursor()
        for _, row in df.iterrows():
            cursor.execute(
                """
                UPDATE BUSINESS_PERMIT_OVERVIEW
                SET
                    DEADLINE=%s,
                    DEADLINE_EXTENSION=%s,
                    BRGY_PERMIT_2025=%s,
                    BUSINESS_PERMIT_2025=%s,
                    SEC_CERT=%s,
                    GROSS_SALES_CERT=%s
                WHERE COMPANY=%s AND AREA=%s AND BRANCH=%s
                """,
                (
                    row["DEADLINE"],
                    row["DEADLINE_EXTENSION"],
                    row["BRGY_PERMIT_2025"],
                    row["BUSINESS_PERMIT_2025"],
                    row["SEC_CERT"],
                    row["GROSS_SALES_CERT"],
                    row["COMPANY"],
                    row["AREA"],
                    row["BRANCH"],
                ),
            )
        conn.commit()

    def update_data(df, data_type):
        conn, cursor = get_cursor()
        for _, row in df.iterrows():
            cursor.execute(
                """
                UPDATE BUSINESS_PERMIT_TRACKER
                SET
                    CAF=%s,
                    PAYEE=%s,
                    PARTICULARS=%s,
                    MODE_OF_PAYMENT=%s,
                    AMOUNT=%s,
                    ACCOUNTING_DATE=%s,
                    TREASURY_DATE=%s,
                    STATUS=%s,
                    DATE_LIQUIDATION=%s,
                    DATE_SUBMISSION_ACCOUNTING=%s,
                    YEAR_COMPARISON=%s,
                    PERCENT_CHANGE=%s,
                    WITH_TAX_BILL=%s,
                    REASON_NOT_REQUESTING_FUND=%s
                WHERE
                    TYPE=%s AND COMPANY=%s AND AREA=%s AND BRANCH=%s
                """,
                (
                    row["CAF"],
                    row["PAYEE"],
                    row["PARTICULARS"],
                    row["MODE_OF_PAYMENT"],
                    row["AMOUNT"],
                    row["ACCOUNTING_DATE"],
                    row["TREASURY_DATE"],
                    row["STATUS"],
                    row["DATE_LIQUIDATION"],
                    row["DATE_SUBMISSION_ACCOUNTING"],
                    row["YEAR_COMPARISON"],
                    row["PERCENT_CHANGE"],
                    row["WITH_TAX_BILL"],
                    row["REASON_NOT_REQUESTING_FUND"],
                    data_type,
                    row["COMPANY"],
                    row["AREA"],
                    row["BRANCH"],
                ),
            )
        conn.commit()

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

        df = load_overview()
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
            load_overview.clear()
            st.success("Overview Updated ✅")
            safe_rerun()

    with tab2:
        st.subheader("Business Permit")
        df = load_data("BUSINESS_PERMIT")
        edited_df = render_editor(df, "business_tab")

        if st.button("💾 Save Business Permit"):
            update_data(edited_df, "BUSINESS_PERMIT")
            load_data.clear()
            st.success("Saved ✅")
            safe_rerun()

    with tab3:
        st.subheader("Barangay Permit")
        df = load_data("BRGY_PERMIT")
        edited_df = render_editor(df, "brgy_tab")

        if st.button("💾 Save Brgy Permit"):
            update_data(edited_df, "BRGY_PERMIT")
            load_data.clear()
            st.success("Saved ✅")
            safe_rerun()

    with tab4:
        st.subheader("Other Fees for Renew")
        df = load_data("OTHER_FEES")
        edited_df = render_editor(df, "other_tab")

        if st.button("💾 Save Other Fees"):
            update_data(edited_df, "OTHER_FEES")
            load_data.clear()
            st.success("Saved ✅")
            safe_rerun()


# ---------------------------------------------------
# TAX MAPPED
# ---------------------------------------------------
def tax_mapped():
    st.title("🏷 TAX MAPPED Report")

    @st.cache_data(ttl=30, show_spinner=False)
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

    df = load_tax_mapped()

    if "tax_mapped_orig" not in st.session_state:
        st.session_state.tax_mapped_orig = df.copy()

    edited_df = st.data_editor(
        df,
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
                df=edited_df,
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

    st.subheader("Upload Tax Mapped Sticker")

    row_id = st.number_input("Enter Row ID", step=1, min_value=1)
    uploaded_file = st.file_uploader("Upload Sticker Image")

    if uploaded_file is not None:
        st.image(uploaded_file, width=250)
        file_bytes = uploaded_file.getvalue()
        conn, cursor = get_cursor()
        cursor.execute(
            """
            UPDATE TAX_MAPPED
            SET STICKER_IMAGE = %s
            WHERE ID = %s
            """,
            (file_bytes, int(row_id)),
        )
        conn.commit()
        st.success("Sticker uploaded to database ✅")


# ---------------------------------------------------
# Secretary Certificates Page
# ---------------------------------------------------
def secretary_certificates():
    st.title("📄 Secretary Certificates Compliance")

    # ---------------------------------------------------
    # LOAD DATA (NO CACHE = REAL-TIME)
    # ---------------------------------------------------
    def load_sec():
        conn, cursor = get_cursor()
        cursor.execute("""
            SELECT
                ID, AREA, MONTH_YEAR, BRANCH, COMPANY, REMARKS,
                DATE_FORWARDED, STATUS, DATE_RECEIVED, FINAL_STATUS
            FROM SECRETARY_CERTIFICATES
        """)
        data = cursor.fetchall()

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

        return pd.DataFrame(data, columns=columns)

    df = load_sec()

    # ---------------------------------------------------
    # SESSION STATE
    # ---------------------------------------------------
    if "sec_orig" not in st.session_state:
        st.session_state.sec_orig = df.copy()

    # ---------------------------------------------------
    # DATA EDITOR
    # ---------------------------------------------------
    edited_df = st.data_editor(
        df,
        num_rows="dynamic",
        use_container_width=True,
        key="sec_editor",
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
        if st.button("💾 Save Changes"):

            log_activity("Secretary Certificates", "SAVE")

            # ✅ FIX NaN → NULL
            edited_df = edited_df.copy()
            edited_df = edited_df.where(pd.notnull(edited_df), None)

            handle_save_with_id(
                df_name_prefix="sec",
                table_name="SECRETARY_CERTIFICATES",
                df=edited_df,
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

            # ✅ REAL-TIME REFRESH (NO .clear())
            st.session_state.sec_orig = load_sec()

            st.success("Data saved successfully ✅")
            safe_rerun()

    # ---------------------------------------------------
    # UNDO
    # ---------------------------------------------------
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
                st.session_state.sec_orig = load_sec()
                st.success("Delete undone 🔄")
                safe_rerun()
            else:
                st.info("Nothing to undo")

    # ---------------------------------------------------
    # REFRESH
    # ---------------------------------------------------
    with col_refresh:
        if st.button("🔄 Refresh"):
            st.session_state.sec_orig = load_sec()
            safe_rerun()

def board_resolutions():
    st.title("📄 Board Resolutions Compliance")

    # ---------------------------------------------------
    # LOAD DATA (NO CACHE for real-time)
    # ---------------------------------------------------
    def load_board():
        conn, cursor = get_cursor()
        cursor.execute("""
            SELECT
                ID,
                COMPANY,
                REMARKS,
                DATE_FORWARDED,
                DATE_RECEIVED,
                STATUS
            FROM BOARD_RESOLUTIONS
        """)
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

    df = load_board()

    # ---------------------------------------------------
    # SESSION STATE
    # ---------------------------------------------------
    if "board_orig" not in st.session_state:
        st.session_state.board_orig = df.copy()

    # ---------------------------------------------------
    # DATA EDITOR
    # ---------------------------------------------------
    edited_df = st.data_editor(
        df,
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

            edited_df = edited_df.replace({pd.NA: None})

            handle_save_with_id(
                df_name_prefix="board",
                table_name="BOARD_RESOLUTIONS",
                df=edited_df,
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

            # ✅ REAL-TIME REFRESH (NO .clear())
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
            st.session_state.board_orig = load_board()
            safe_rerun()
            
def dashboard_analytics():

    # ========================= HOLOGRAPHIC UI =========================
    st.markdown("""
    <style>
    body {
        background: radial-gradient(circle at top, #0f172a, #020617 55%);
    }

    .title-glow {
        text-align:center;
        font-size:36px;
        color:#7af9ff;
        letter-spacing:0.18em;
        text-shadow:
            0 0 10px #7af9ff,
            0 0 30px #3b82f6,
            0 0 60px #a855f7;
        margin-bottom:8px;
    }

    .subtitle-chip {
        text-align:center;
        font-size:14px;
        color:#cbd5f5;
        text-transform:uppercase;
        letter-spacing:0.25em;
        opacity:0.8;
        margin-bottom:26px;
    }

    .kpi-card {
        background: linear-gradient(135deg, rgba(15,23,42,0.85), rgba(24,35,70,0.95));
        border-radius:18px;
        padding:16px 18px;
        border:1px solid rgba(148,163,255,0.4);
        box-shadow:
            0 0 0 1px rgba(15,118,255,0.2),
            0 18px 35px rgba(15,23,42,0.9);
        backdrop-filter: blur(18px);
        transition: all 0.28s ease;
    }

    .kpi-card:hover {
        transform: translateY(-4px) scale(1.02);
        box-shadow:
            0 0 22px rgba(56,189,248,0.7),
            0 24px 60px rgba(15,23,42,1);
    }

    .kpi-label {
        font-size:13px;
        text-transform:uppercase;
        letter-spacing:0.18em;
        color:#93c5fd;
    }

    .kpi-value {
        font-size:30px;
        font-weight:700;
        color:#e5f2ff;
        margin-top:4px;
    }

    .kpi-caption {
        font-size:11px;
        color:#9ca3af;
        margin-top:6px;
    }

    .holo-panel {
        background: radial-gradient(circle at top left, rgba(56,189,248,0.22), transparent 60%),
                    radial-gradient(circle at bottom right, rgba(129,140,248,0.22), transparent 60%),
                    rgba(15,23,42,0.92);
        border-radius:22px;
        padding:18px 20px;
        margin-top:18px;
        border:1px solid rgba(148,163,255,0.4);
        box-shadow:
            0 0 0 1px rgba(15,23,42,1),
            0 28px 55px rgba(15,23,42,1);
        backdrop-filter: blur(22px);
        position:relative;
        overflow:hidden;
    }

    .holo-panel::before {
        content:"";
        position:absolute;
        inset:-120%;
        background:
            repeating-linear-gradient(
                125deg,
                rgba(148,163,255,0.22) 0px,
                rgba(148,163,255,0.22) 1px,
                transparent 1px,
                transparent 4px
            );
        mix-blend-mode:soft-light;
        opacity:0.3;
        animation:holo-scan 14s linear infinite;
    }

    .holo-panel::after {
        content:"";
        position:absolute;
        inset:-20%;
        background:
            radial-gradient(circle at 0% 0%, rgba(56,189,248,0.45), transparent 55%),
            radial-gradient(circle at 100% 100%, rgba(244,114,182,0.45), transparent 55%);
        mix-blend-mode:screen;
        opacity:0.18;
        animation:holo-pulse 6s ease-in-out infinite;
    }

    @keyframes holo-scan {
        0%   { transform: translate3d(-10%, -10%, 0); }
        50%  { transform: translate3d(10%, 10%, 0); }
        100% { transform: translate3d(-10%, -10%, 0); }
    }

    @keyframes holo-pulse {
        0%, 100% { opacity:0.18; }
        50%      { opacity:0.35; }
    }

    .panel-header {
        font-size:16px;
        font-weight:600;
        color:#e5e7eb;
        display:flex;
        align-items:center;
        gap:8px;
        margin-bottom:8px;
        position:relative;
        z-index:1;
    }

    .panel-header-pill {
        width:8px;
        height:8px;
        border-radius:999px;
        background:radial-gradient(circle, #22c1c3, #4e46e5);
        box-shadow:0 0 12px rgba(34,193,195,0.85);
    }

    .panel-subtitle {
        font-size:11px;
        color:#9ca3af;
        text-transform:uppercase;
        letter-spacing:0.18em;
        margin-bottom:10px;
        position:relative;
        z-index:1;
    }

    .panel-body {
        position:relative;
        z-index:1;
    }
    </style>

    <div class="title-glow">COMPLIANCE COMMAND CENTER</div>
    <div class="subtitle-chip">HOLOGRAPHIC EXECUTIVE INFOGRAPHIC</div>
    """, unsafe_allow_html=True)

    st.title("")

    # ========================= LOAD BUSINESS PERMIT DATA =========================
    @st.cache_data(ttl=60)
    def load_bp_data():
        conn, cursor = get_cursor()
        cursor.execute("""
        SELECT COMPANY, AREA, BRANCH, DEADLINE, DEADLINE_EXTENSION,
               SEC_CERT, GROSS_SALES_CERT
        FROM BUSINESS_PERMIT_OVERVIEW
        """)
        cols = ["COMPANY","AREA","BRANCH","DEADLINE","DEADLINE_EXTENSION","SEC_CERT","GROSS_SALES_CERT"]
        return pd.DataFrame(cursor.fetchall(), columns=cols)

    df = load_bp_data()
    df.columns = df.columns.str.upper()
    today = datetime.today().date()

    # ========================= SAFE DEFAULT / BUILD =========================
    if df.empty:
        df = pd.DataFrame(columns=[
            "COMPANY","AREA","BRANCH",
            "FINAL_DEADLINE","SLA","RISK",
            "SEC_CERT","GROSS_SALES_CERT"
        ])
    else:
        df["DEADLINE"] = pd.to_datetime(df.get("DEADLINE"), errors='coerce').dt.date
        df["DEADLINE_EXTENSION"] = pd.to_datetime(df.get("DEADLINE_EXTENSION"), errors='coerce').dt.date
        df["FINAL_DEADLINE"] = df["DEADLINE_EXTENSION"].fillna(df["DEADLINE"])
        df["SLA"] = df["FINAL_DEADLINE"].apply(
            lambda d: "LATE" if pd.notna(d) and d < today else "ON_TIME"
        )
        df["SEC_CERT"] = df.get("SEC_CERT", "")
        df["GROSS_SALES_CERT"] = df.get("GROSS_SALES_CERT", None)
        df["RISK"] = (
            (df["SLA"] == "LATE") * 40 +
            (df["SEC_CERT"].fillna("") == "PENDING") * 30 +
            (df["GROSS_SALES_CERT"].isna()) * 30
        )

    # ========================= TOP KPIs =========================
    if df.empty or "SLA" not in df.columns:
        total = late = high_risk = rate = 0
    else:
        total = len(df)
        late = len(df[df["SLA"] == "LATE"])
        high_risk = len(df[df["RISK"] >= 70]) if "RISK" in df.columns else 0
        rate = ((total - late) / total * 100) if total else 0

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.markdown('<div class="kpi-card">', unsafe_allow_html=True)
        st.markdown('<div class="kpi-label">Total Branches</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="kpi-value">{total}</div>', unsafe_allow_html=True)
        st.markdown('<div class="kpi-caption">Tracked in Business Permit Overview</div>', unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)
    with c2:
        st.markdown('<div class="kpi-card">', unsafe_allow_html=True)
        st.markdown('<div class="kpi-label">On-Time Rate</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="kpi-value">{rate:.1f}%</div>', unsafe_allow_html=True)
        st.markdown('<div class="kpi-caption">Branches still within compliance SLA</div>', unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)
    with c3:
        st.markdown('<div class="kpi-card">', unsafe_allow_html=True)
        st.markdown('<div class="kpi-label">Late Branches</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="kpi-value">{late}</div>', unsafe_allow_html=True)
        st.markdown('<div class="kpi-caption">Past final deadline as of today</div>', unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)
    with c4:
        st.markdown('<div class="kpi-card">', unsafe_allow_html=True)
        st.markdown('<div class="kpi-label">High Risk</div>', unsafe_allow_html=True)
        st.markdown(f'<div class="kpi-value">{high_risk}</div>', unsafe_allow_html=True)
        st.markdown('<div class="kpi-caption">Composite risk ≥ 70 points</div>', unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)

    # ========================= GLOBAL DONE / PENDING KPI ACROSS MODULES =========================
    @st.cache_data(ttl=60, show_spinner=False)
    def load_status_kpis_local():
        conn, cursor = get_cursor()
        cursor.execute(
            """
            SELECT
                'SECRETARY_CERTIFICATES' AS MODULE,
                SUM(CASE WHEN UPPER(FINAL_STATUS) = 'DONE' THEN 1 ELSE 0 END) AS DONE,
                SUM(
                    CASE
                        WHEN FINAL_STATUS IS NULL OR TRIM(FINAL_STATUS) = '' THEN 1
                        WHEN UPPER(FINAL_STATUS) IN ('PENDING','PROCESS','PROCESSING','NOT YET DONE') THEN 1
                        ELSE 0
                    END
                ) AS PENDING
            FROM SECRETARY_CERTIFICATES

            UNION ALL

            SELECT
                'BOARD_RESOLUTIONS' AS MODULE,
                SUM(CASE WHEN UPPER(STATUS) = 'DONE' THEN 1 ELSE 0 END) AS DONE,
                SUM(
                    CASE
                        WHEN STATUS IS NULL OR TRIM(STATUS) = '' THEN 1
                        WHEN UPPER(STATUS) IN ('PENDING','PROCESS','PROCESSING','NOT YET DONE') THEN 1
                        ELSE 0
                    END
                ) AS PENDING
            FROM BOARD_RESOLUTIONS

            UNION ALL

            SELECT
                'BIR_1906_ATP' AS MODULE,
                SUM(CASE WHEN UPPER(STATUS) = 'DONE' THEN 1 ELSE 0 END) AS DONE,
                SUM(
                    CASE
                        WHEN STATUS IS NULL OR TRIM(STATUS) = '' THEN 1
                        WHEN UPPER(STATUS) IN ('PENDING','PROCESS','PROCESSING','NOT YET DONE') THEN 1
                        ELSE 0
                    END
                ) AS PENDING
            FROM BIR_1906_ATP

            UNION ALL

            SELECT
                'ATP_CERTIFICATES' AS MODULE,
                SUM(CASE WHEN UPPER(STATUS) = 'DONE' THEN 1 ELSE 0 END) AS DONE,
                SUM(
                    CASE
                        WHEN STATUS IS NULL OR TRIM(STATUS) = '' THEN 1
                        WHEN UPPER(STATUS) IN ('PENDING','PROCESS','PROCESSING','NOT YET DONE') THEN 1
                        ELSE 0
                    END
                ) AS PENDING
            FROM ATP_CERTIFICATES

            UNION ALL

            SELECT
                'BOA_STICKER' AS MODULE,
                SUM(CASE WHEN UPPER(STATUS) = 'DONE' THEN 1 ELSE 0 END) AS DONE,
                SUM(
                    CASE
                        WHEN STATUS IS NULL OR TRIM(STATUS) = '' THEN 1
                        WHEN UPPER(STATUS) IN ('PENDING','PROCESS','PROCESSING','NOT YET DONE') THEN 1
                        ELSE 0
                    END
                ) AS PENDING
            FROM BOA_STICKER
            """
        )
        cols = ["MODULE", "DONE", "PENDING"]
        return pd.DataFrame(cursor.fetchall(), columns=cols)

    status_df = load_status_kpis_local()

    st.markdown('<div class="holo-panel">', unsafe_allow_html=True)
    st.markdown('<div class="panel-header"><div class="panel-header-pill"></div>Cross‑Module Progress</div>', unsafe_allow_html=True)
    st.markdown('<div class="panel-subtitle">DONE vs PENDING / IN PROCESS / NOT YET DONE</div>', unsafe_allow_html=True)
    st.markdown('<div class="panel-body">', unsafe_allow_html=True)

    if not status_df.empty:
        total_done = int(status_df["DONE"].sum())
        total_pending = int(status_df["PENDING"].sum())
        grand_total = total_done + total_pending
        completion_rate = (total_done / grand_total * 100) if grand_total else 0

        k1, k2, k3 = st.columns(3)
        k1.metric("Total DONE", total_done)
        k2.metric("Pending / In‑Process / Blank", total_pending)
        k3.metric("Overall Completion Rate", f"{completion_rate:.1f}%")

        fig_status_mod = px.bar(
            status_df.melt(
                id_vars="MODULE",
                value_vars=["DONE", "PENDING"],
                var_name="STATE",
                value_name="COUNT",
            ),
            x="MODULE",
            y="COUNT",
            color="STATE",
            barmode="group",
        )
        fig_status_mod.update_layout(
            title=None,
            template="plotly_dark",
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#e5e7eb"),
            legend=dict(
                orientation="h",
                yanchor="bottom",
                y=1.02,
                xanchor="right",
                x=1
            ),
        )
        st.plotly_chart(fig_status_mod, use_container_width=True)
    else:
        st.info("No status data available for the selected modules.")

    st.markdown('</div></div>', unsafe_allow_html=True)

    # ========================= BUSINESS PERMIT INFOGRAPHIC =========================
    st.markdown('<div class="holo-panel">', unsafe_allow_html=True)
    st.markdown('<div class="panel-header"><div class="panel-header-pill"></div>Business Permit Health</div>', unsafe_allow_html=True)
    st.markdown('<div class="panel-subtitle">SLA STATUS · AREA PERFORMANCE · RISK MAP</div>', unsafe_allow_html=True)
    st.markdown('<div class="panel-body">', unsafe_allow_html=True)

    if not df.empty:
        # Status distribution
        status_count = df["SLA"].value_counts().reset_index()
        status_count.columns = ["STATUS", "COUNT"]

        fig_status = px.pie(
            status_count,
            names="STATUS",
            values="COUNT",
            hole=0.6,
            color="STATUS",
            color_discrete_map={"ON_TIME": "#22c55e", "LATE": "#f97316"},
        )
        fig_status.update_traces(textinfo="percent+label")
        fig_status.update_layout(
            template="plotly_dark",
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            showlegend=False,
        )

        # Area performance
        area_perf = df.groupby("AREA").agg(
            TOTAL=("BRANCH", "count"),
            LATE=("SLA", lambda x: (x == "LATE").sum())
        ).reset_index()
        area_perf["COMPLIANCE_RATE"] = (
            (area_perf["TOTAL"] - area_perf["LATE"]) / area_perf["TOTAL"] * 100
        )

        fig_area = px.bar(
            area_perf,
            x="AREA",
            y="COMPLIANCE_RATE",
            text="COMPLIANCE_RATE",
            color="COMPLIANCE_RATE",
            color_continuous_scale="Blues",
        )
        fig_area.update_traces(texttemplate='%{text:.1f}%', textposition='outside')
        fig_area.update_layout(
            template="plotly_dark",
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
        )

        # Layout: pie + bar + heatmap stacked visually
        c_left, c_right = st.columns([1, 1.2])
        with c_left:
            st.markdown("##### SLA Distribution")
            st.plotly_chart(fig_status, use_container_width=True)

        with c_right:
            st.markdown("##### Area Compliance Rate")
            st.plotly_chart(fig_area, use_container_width=True)

        st.markdown("##### Risk Heatmap")
        heatmap_df = df.pivot_table(
            index="AREA",
            columns="COMPANY",
            values="RISK",
            aggfunc="mean",
        )
        fig_heat = px.imshow(
            heatmap_df,
            text_auto=True,
            color_continuous_scale="RdYlGn_r",
        )
        fig_heat.update_layout(
            template="plotly_dark",
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(fig_heat, use_container_width=True)

    else:
        st.warning("No data available for Business Permit Insights")

    st.markdown('</div></div>', unsafe_allow_html=True)

    # ========================= AI RISK INTELLIGENCE & BREAKDOWN =========================
    st.markdown('<div class="holo-panel">', unsafe_allow_html=True)
    st.markdown('<div class="panel-header"><div class="panel-header-pill"></div>AI Risk Intelligence</div>', unsafe_allow_html=True)
    st.markdown('<div class="panel-subtitle">RISK TREND · PREDICTED LATE · BREAKDOWN</div>', unsafe_allow_html=True)
    st.markdown('<div class="panel-body">', unsafe_allow_html=True)

    if not df.empty:
        # Predicted late table
        df["PREDICTED_LATE"] = (df["RISK"] >= 50) | (df["SLA"] == "LATE")
        pred = df[df["PREDICTED_LATE"]]

        cA, cB = st.columns([1.1, 1.1])

        with cA:
            st.markdown("###### 🔮 Predicted Late Branches")
            st.metric("Predicted Late", len(pred))
            if not pred.empty:
                st.dataframe(pred[["COMPANY","AREA","BRANCH","RISK"]], use_container_width=True, height=210)

        with cB:
            st.markdown("###### 📈 Risk Trend")
            trend = df.groupby("FINAL_DEADLINE").size().reset_index(name="COUNT")
            fig_trend = px.line(trend, x="FINAL_DEADLINE", y="COUNT")
            fig_trend.update_layout(
                template="plotly_dark",
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
            )
            st.plotly_chart(fig_trend, use_container_width=True)

        # Futuristic average risk line
        st.markdown("###### 🌌 Average Risk Over Time")
        futuristic_df = df.groupby("FINAL_DEADLINE")["RISK"].mean().reset_index()
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=futuristic_df["FINAL_DEADLINE"],
            y=futuristic_df["RISK"],
            mode='lines',
            line=dict(color="rgba(56,189,248,0.4)", width=10),
            hoverinfo="skip",
            showlegend=False,
        ))
        fig.add_trace(go.Scatter(
            x=futuristic_df["FINAL_DEADLINE"],
            y=futuristic_df["RISK"],
            mode='lines+markers',
            line=dict(color="#38bdf8", width=3),
            marker=dict(size=7, color="#f97316"),
            name="Avg Risk",
        ))
        fig.update_layout(
            template="plotly_dark",
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#e5e7eb"),
        )
        st.plotly_chart(fig, use_container_width=True)

        # Waterfall breakdown
        st.markdown("###### 🌊 Risk Component Breakdown")
        pending = len(df[df["SEC_CERT"] == "PENDING"]) if "SEC_CERT" in df.columns else 0
        missing = len(df[df["GROSS_SALES_CERT"].isna()]) if "GROSS_SALES_CERT" in df.columns else 0
        fig2 = go.Figure(go.Waterfall(
            name="Risk",
            orientation="v",
            x=["Late","Pending SEC","Missing Gross Sales","Total"],
            y=[late, pending, missing, total]
        ))
        fig2.update_layout(
            template="plotly_dark",
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(fig2, use_container_width=True)
    else:
        st.info("No data available for AI risk intelligence.")

    st.markdown('</div>', unsafe_allow_html=True)

    # ========================= AREA DRILLDOWN =========================
    st.markdown('<div class="holo-panel">', unsafe_allow_html=True)
    st.markdown('<div class="panel-header"><div class="panel-header-pill"></div>Area Drilldown</div>', unsafe_allow_html=True)
    st.markdown('<div class="panel-subtitle">CLICK AN AREA TO SEE BRANCH DETAILS</div>', unsafe_allow_html=True)
    st.markdown('<div class="panel-body">', unsafe_allow_html=True)

    if not df.empty:
        area_df = df.groupby("AREA").size().reset_index(name="COUNT")
        fig_bar = px.bar(area_df, x="AREA", y="COUNT")
        fig_bar.update_layout(
            template="plotly_dark",
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
        )
        selected = plotly_events(fig_bar, click_event=True)
        st.plotly_chart(fig_bar, use_container_width=True)

        if selected:
            st.markdown("###### Selected Area Detail")
            st.dataframe(df[df["AREA"] == selected[0]["x"]], use_container_width=True)
    else:
        st.info("No area level data available.")

    st.markdown('</div>', unsafe_allow_html=True)

    # ========================= RAW DATA =========================
    st.subheader("📋 Underlying Data View")
    st.dataframe(df, use_container_width=True)
# ---------------------------------------------------
# MAIN DASHBOARD PAGE (sidebar navigation)
# ---------------------------------------------------
def dashboard():
    # ========================= HOLOGRAPHIC SIDEBAR STYLES =========================
    st.markdown(
        """
    <style>
    /* Sidebar container */
    section[data-testid="stSidebar"] {
        background: radial-gradient(circle at top, #020617 0, #020617 35%, #0b1120 100%);
        border-right: 1px solid rgba(148,163,255,0.35);
        box-shadow: 0 0 30px rgba(15,23,42,1);
    }

    /* Sidebar inner glass panel */
    .sidebar-glass {
        background: radial-gradient(circle at top left, rgba(56,189,248,0.16), transparent 60%),
                    radial-gradient(circle at bottom right, rgba(129,140,248,0.16), transparent 60%),
                    rgba(15,23,42,0.92);
        border-radius: 20px;
        padding: 14px 14px 18px 14px;
        border: 1px solid rgba(148,163,255,0.45);
        box-shadow:
            0 0 0 1px rgba(15,23,42,1),
            0 22px 45px rgba(15,23,42,1);
        backdrop-filter: blur(20px);
        position: relative;
        overflow: hidden;
    }

    .sidebar-glass::before {
        content:"";
        position:absolute;
        inset:-120%;
        background:
            repeating-linear-gradient(
                135deg,
                rgba(148,163,255,0.25) 0px,
                rgba(148,163,255,0.25) 1px,
                transparent 1px,
                transparent 4px
            );
        mix-blend-mode:soft-light;
        opacity:0.3;
        animation: sb-scan 16s linear infinite;
    }

    .sidebar-glass::after {
        content:"";
        position:absolute;
        inset:-15%;
        background:
            radial-gradient(circle at 0% 0%, rgba(56,189,248,0.4), transparent 60%),
            radial-gradient(circle at 100% 100%, rgba(244,114,182,0.42), transparent 60%);
        mix-blend-mode:screen;
        opacity:0.2;
        animation: sb-pulse 7s ease-in-out infinite;
    }

    @keyframes sb-scan {
        0%   { transform: translate3d(-12%, -12%, 0); }
        50%  { transform: translate3d(10%, 10%, 0); }
        100% { transform: translate3d(-12%, -12%, 0); }
    }

    @keyframes sb-pulse {
        0%,100% { opacity:0.18; }
        50%     { opacity:0.32; }
    }

    .sidebar-content {
        position:relative;
        z-index:1;
    }

    .sb-title {
        font-size:15px;
        font-weight:700;
        letter-spacing:0.22em;
        text-transform:uppercase;
        color:#e5f2ff;
        text-align:center;
        margin-bottom:4px;
    }

    .sb-subtitle {
        font-size:10px;
        letter-spacing:0.2em;
        text-transform:uppercase;
        text-align:center;
        color:#9ca3af;
        margin-bottom:12px;
    }

    /* Clock hologram */
    #clock {
        background: radial-gradient(circle at top, rgba(56,189,248,0.4), transparent 60%),
                    rgba(15,23,42,0.96);
        padding:10px 12px;
        border-radius:16px;
        text-align:center;
        font-size:12px;
        font-weight:600;
        color:#e5f2ff;
        border:1px solid rgba(56,189,248,0.7);
        box-shadow:
            0 0 14px rgba(56,189,248,0.8),
            0 0 35px rgba(15,23,42,1);
        margin-bottom:12px;
    }

    /* Radio label styling (navigation) */
    div[data-baseweb="radio"] > div {
        gap:6px;
    }

    div[data-baseweb="radio"] label {
        width:100%;
    }

    div[data-baseweb="radio"] label > div {
        border-radius:14px !important;
        padding:6px 10px !important;
        transition:all 0.22s ease;
        border:1px solid transparent;
        background:rgba(15,23,42,0.85);
    }

    /* Hover state */
    div[data-baseweb="radio"] label > div:hover {
        border-color:rgba(56,189,248,0.6);
        box-shadow:0 0 12px rgba(56,189,248,0.65);
        transform:translateX(2px);
        background:linear-gradient(135deg, rgba(15,23,42,0.9), rgba(30,64,175,0.9));
    }

    /* Selected item */
    div[data-baseweb="radio"] input[aria-checked="true"] + div {
        border-color:rgba(94,234,212,0.9) !important;
        box-shadow:
            0 0 18px rgba(94,234,212,0.95),
            0 0 40px rgba(15,23,42,1);
        background:linear-gradient(135deg, rgba(16,185,129,0.18), rgba(59,130,246,0.36));
        transform:translateX(3px);
    }

    /* Logged-in chip */
    .user-chip {
        margin-top:12px;
        padding:8px 10px;
        border-radius:14px;
        background:rgba(15,23,42,0.9);
        border:1px solid rgba(148,163,255,0.6);
        font-size:11px;
        color:#e5e7eb;
    }

    .user-chip span {
        font-size:10px;
        text-transform:uppercase;
        letter-spacing:0.18em;
        color:#9ca3af;
    }

    /* Logout button shock */
    .stButton button[kind="secondary"] {
        width:100%;
        border-radius:999px;
        border:1px solid rgba(248,113,113,0.8);
        background:radial-gradient(circle at 0 0, rgba(248,113,113,0.45), transparent 55%),
                   rgba(30,64,175,0.9);
        color:#fee2e2;
        font-weight:600;
        box-shadow:0 0 14px rgba(248,113,113,0.9);
        transition:all 0.23s ease;
    }

    .stButton button[kind="secondary"]:hover {
        box-shadow:
            0 0 26px rgba(248,113,113,1),
            0 0 60px rgba(15,23,42,1);
        transform:translateY(-1px) scale(1.02);
    }

    .stButton button[kind="secondary"]:active {
        transform:scale(0.97);
        box-shadow:0 0 40px rgba(248,113,113,1);
    }
    </style>
    """,
        unsafe_allow_html=True,
    )

    # ========================= LIVE CLOCK (HOLOGRAPHIC) =========================
    def live_clock_js():
        st.sidebar.markdown(
            """
        <div id="clock">Loading time...</div>

        <script>
        function updateClock() {
            const now = new Date();

            const options = {
                weekday: 'short',
                year: 'numeric',
                month: 'short',
                day: '2-digit'
            };

            const date = now.toLocaleDateString('en-US', options);
            const time = now.toLocaleTimeString('en-US', { hour12: true });

            document.getElementById('clock').innerHTML =
                date.toUpperCase() + " · " + time;
        }

        setInterval(updateClock, 1000);
        updateClock();
        </script>
        """,
            unsafe_allow_html=True,
        )

    # ========================= SIDEBAR CONTENT =========================
    with st.sidebar:
        st.markdown('<div class="sidebar-glass"><div class="sidebar-content">', unsafe_allow_html=True)
        st.markdown('<div class="sb-title">COMPLIANCE HUB</div>', unsafe_allow_html=True)
        st.markdown('<div class="sb-subtitle">HOLOGRAPHIC NAVIGATION MATRIX</div>', unsafe_allow_html=True)

        live_clock_js()

        menu = st.radio(
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
            label_visibility="collapsed",
        )

        st.markdown(
            f"""
            <div class="user-chip">
                <span>ACTIVE USER</span><br/>
                👤 {st.session_state.get("user", "Unknown")}
            </div>
            """,
            unsafe_allow_html=True,
        )

        st.divider()

        if st.button("🚪 Logout", key="logout_btn"):
            log_activity("SYSTEM", "LOGOUT")

            # clear everything first
            st.session_state.clear()
            # then restore routing flags
            st.session_state.logged_in = False
            st.session_state.page = "login"

            safe_rerun()

        st.markdown("</div></div>", unsafe_allow_html=True)

    # ========================= PAGE ROUTING & ACTIVITY LOG =========================
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

# ---------------------------------------------------
# PAGE ROUTING (LOGIN SYSTEM)
# ---------------------------------------------------

if not st.session_state.logged_in:
    if st.session_state.page == "register":
        register()
    else:
        login()
else:
    dashboard()
