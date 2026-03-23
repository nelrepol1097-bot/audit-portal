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
# --------------------------------------------------
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
    """,
        unsafe_allow_html=True,
    )

    # ---------- robust sanitizers ----------
    def _clean_scalar(v):
        if v is None:
            return None
        if v is pd.NA or v is pd.NaT:
            return None
        if isinstance(v, str):
            s = v.strip()
            if s == "" or s.lower() in ("none", "nan", "nat", "null"):
                return None
            return s
        try:
            if pd.isna(v):
                return None
        except Exception:
            pass
        return v

    def _sanitize_for_db(df: pd.DataFrame, date_cols=None) -> pd.DataFrame:
        if date_cols is None:
            date_cols = []

        out = df.copy().astype(object)

        # Clean all values first
        out = out.applymap(_clean_scalar)

        # Parse date columns strictly into python date or None
        for c in date_cols:
            if c in out.columns:
                out[c] = pd.to_datetime(out[c], errors="coerce").dt.date
                out[c] = out[c].apply(_clean_scalar)

        # Normalize ID: int or None
        if "ID" in out.columns:
            out["ID"] = pd.to_numeric(out["ID"], errors="coerce")
            out["ID"] = out["ID"].apply(lambda x: int(x) if pd.notna(x) else None)

        return out

    def _ensure_columns(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
        out = df.copy()
        for c in cols:
            if c not in out.columns:
                out[c] = None
        return out[cols]

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

    # ---------- top controls ----------
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

    @st.cache_data(ttl=30, show_spinner=False)
    def load_data(company_name: str):
        conn, cursor = get_cursor()
        cursor.execute(
            """
            SELECT
                ID, COMPANY, TIN, BRANCH_CODE, RDO, AREA,
                BRANCH_NAME, UPDATED_ADDRESS, STATUS,
                DATE_OPEN, DATE_OF_CLOSURE, REMARKS
            FROM BRANCH_TIN_ADDRESS
            WHERE COMPANY = %s
            """,
            (company_name,),
        )
        return pd.DataFrame(cursor.fetchall(), columns=full_cols)

    df = load_data(company)

    # ---------- merge helpers ----------
    def merge_new_view_to_full(df_full: pd.DataFrame, edited: pd.DataFrame) -> pd.DataFrame:
        base_map = {
            int(r["ID"]): r.to_dict()
            for _, r in df_full.iterrows()
            if pd.notna(r.get("ID"))
        }

        rows = []
        for _, er in edited.iterrows():
            eid = pd.to_numeric(er.get("ID"), errors="coerce")
            addr = er.get("ADDRESS")
            if _clean_scalar(addr) is None:
                addr = er.get("UPDATED_ADDRESS")

            if pd.notna(eid) and int(eid) in base_map:
                b = base_map[int(eid)].copy()
                b["COMPANY"] = company
                b["STATUS"] = "NEW"
                b["BRANCH_NAME"] = _clean_scalar(er.get("BRANCH_NAME")) or b.get("BRANCH_NAME")
                b["AREA"] = _clean_scalar(er.get("AREA")) or b.get("AREA")
                b["TIN"] = _clean_scalar(er.get("TIN")) or b.get("TIN")
                b["BRANCH_CODE"] = _clean_scalar(er.get("BRANCH_CODE")) or b.get("BRANCH_CODE")
                b["RDO"] = _clean_scalar(er.get("RDO")) or b.get("RDO")
                b["UPDATED_ADDRESS"] = _clean_scalar(addr)
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
        addr_col = "ADDRESS (NEW ADDRESS)"
        if addr_col not in edited.columns and "UPDATED_ADDRESS" in edited.columns:
            addr_col = "UPDATED_ADDRESS"

        for _, er in edited.iterrows():
            eid = pd.to_numeric(er.get("ID"), errors="coerce")
            new_addr = er.get(addr_col)
            old_addr = er.get("OLD_ADDRESS")
            extra = ""
            if _clean_scalar(old_addr) is not None:
                extra = f" | Previous address (from view): {old_addr}"

            if pd.notna(eid) and int(eid) in base_map:
                b = base_map[int(eid)].copy()
                b["COMPANY"] = company
                b["STATUS"] = "CHANGED"
                b["BRANCH_NAME"] = _clean_scalar(er.get("BRANCH_NAME")) or b.get("BRANCH_NAME")
                b["AREA"] = _clean_scalar(er.get("AREA")) or b.get("AREA")
                b["TIN"] = _clean_scalar(er.get("TIN")) or b.get("TIN")
                b["BRANCH_CODE"] = _clean_scalar(er.get("BRANCH_CODE")) or b.get("BRANCH_CODE")
                b["RDO"] = _clean_scalar(er.get("RDO")) or b.get("RDO")
                b["UPDATED_ADDRESS"] = _clean_scalar(new_addr)
                b["REMARKS"] = f"{REMARK_CHANGED_VIEW}{extra}"
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
                        "UPDATED_ADDRESS": new_addr,
                        "STATUS": "CHANGED",
                        "DATE_OPEN": er.get("DATE_OPEN"),
                        "DATE_OF_CLOSURE": er.get("DATE_OF_CLOSURE"),
                        "REMARKS": f"{REMARK_CHANGED_VIEW}{extra}",
                    }
                )

        out = pd.DataFrame(rows)
        out = _ensure_columns(out, full_cols)
        return _sanitize_for_db(out, date_cols=["DATE_OPEN", "DATE_OF_CLOSURE"])

    # ---------- MAIN table ----------
    st.subheader(f"{company} Full Branch Table")

    edited_df = st.data_editor(
        df,
        num_rows="dynamic",
        use_container_width=True,
        key=f"{company}_main_editor",
        column_config={
            "ID": st.column_config.NumberColumn("ID", format="%d", step=1),
            "DATE_OPEN": st.column_config.DateColumn("Date Open"),
            "DATE_OF_CLOSURE": st.column_config.DateColumn("Date of Closure"),
            "REMARKS": st.column_config.TextColumn("Remarks", max_chars=1000),
        },
    )

    key_prefix = f"branch_tin_{company.lower()}"
    orig_key = f"{key_prefix}_orig"
    if orig_key not in st.session_state:
        st.session_state[orig_key] = df.copy()

    if st.button("💾 Save Changes (Main Table)", key=f"{company}_save_main"):
        to_save = edited_df.copy()
        to_save["COMPANY"] = to_save["COMPANY"].fillna(company).replace("", company)
        to_save = _ensure_columns(to_save, full_cols)
        to_save = _sanitize_for_db(to_save, date_cols=["DATE_OPEN", "DATE_OF_CLOSURE"])

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

        load_data.clear()
        st.session_state[orig_key] = load_data(company)
        st.success("Saved Main Table ✅")
        safe_rerun()

    st.divider()

    # ---------- NEW view ----------
    if st.session_state.view_mode == "NEW":
        st.subheader("🆕 NEW BRANCH ADDRESS")

        df_new = df[df["STATUS"] == "NEW"].copy()
        if df_new.empty:
            df_new = pd.DataFrame(
                columns=[
                    "ID", "COMPANY", "BRANCH_NAME", "ADDRESS", "DATE_OPEN",
                    "AREA", "TIN", "BRANCH_CODE", "RDO"
                ]
            )
        else:
            df_new["ADDRESS"] = df_new["UPDATED_ADDRESS"]

        for c in ["ID", "COMPANY", "BRANCH_NAME", "ADDRESS", "DATE_OPEN", "AREA", "TIN", "BRANCH_CODE", "RDO"]:
            if c not in df_new.columns:
                df_new[c] = None

        edited_new = st.data_editor(
            df_new[["ID", "COMPANY", "BRANCH_NAME", "ADDRESS", "DATE_OPEN", "AREA", "TIN", "BRANCH_CODE", "RDO"]],
            num_rows="dynamic",
            use_container_width=True,
            key=f"{company}_new_editor",
            column_config={
                "ID": st.column_config.NumberColumn("ID", format="%d", step=1),
                "DATE_OPEN": st.column_config.DateColumn("Date Open"),
            },
        )

        if st.button("💾 Save Changes (NEW Branch View)", key=f"{company}_save_new"):
            to_save = merge_new_view_to_full(df, edited_new)
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

            load_data.clear()
            st.session_state[orig_key] = load_data(company)
            st.success("NEW branch view saved ✅")
            safe_rerun()

    # ---------- CHANGED view ----------
    if st.session_state.view_mode == "CHANGED":
        st.subheader("🔁 CHANGED ADDRESS")

        df_changed = df[df["STATUS"] == "CHANGED"].copy()
        if "OLD_ADDRESS" not in df_changed.columns:
            df_changed["OLD_ADDRESS"] = None
        df_changed["ADDRESS (NEW ADDRESS)"] = df_changed["UPDATED_ADDRESS"]

        for c in [
            "ID", "COMPANY", "BRANCH_NAME", "ADDRESS (NEW ADDRESS)",
            "OLD_ADDRESS", "AREA", "TIN", "BRANCH_CODE", "RDO"
        ]:
            if c not in df_changed.columns:
                df_changed[c] = None

        edited_ch = st.data_editor(
            df_changed[["ID", "COMPANY", "BRANCH_NAME", "ADDRESS (NEW ADDRESS)", "OLD_ADDRESS", "AREA", "TIN", "BRANCH_CODE", "RDO"]],
            num_rows="dynamic",
            use_container_width=True,
            key=f"{company}_changed_editor",
            column_config={
                "ID": st.column_config.NumberColumn("ID", format="%d", step=1),
            },
        )

        if st.button("💾 Save Changes (Changed Address View)", key=f"{company}_save_changed"):
            to_save = merge_changed_view_to_full(df, edited_ch)
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

            load_data.clear()
            st.session_state[orig_key] = load_data(company)
            st.success("Changed address view saved ✅")
            safe_rerun()
# -----------------------------------------------------
# BUsiness Permit
# -----------------------------------------------------

def business_permits():
    st.title("🏢 Business Permits Report")
    tab1, tab2, tab3, tab4 = st.tabs(
        ["Overview", "Business Permit", "Brgy Permit", "Other Fees for Renew"]
    )

    # Local value sanitizers so this function is self-contained.
    def _db_param(value):
        if value is None:
            return None
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
        v = _db_param(value)
        if v is None:
            return None
        if isinstance(v, datetime):
            return v.date()

        # Avoid NameError if `date` isn't imported
        try:
            from datetime import date as _date_type

            if isinstance(v, _date_type):
                return v
        except Exception:
            pass

        ts = pd.to_datetime(v, errors="coerce")
        if pd.isna(ts):
            return None
        return ts.date()

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
        skipped_rows = 0
        failed_rows = 0

        for _, row in df.iterrows():
            company = _db_param(row["COMPANY"])
            area = _db_param(row["AREA"])
            branch = _db_param(row["BRANCH"])

            # required keys
            if not company or not area or not branch:
                skipped_rows += 1
                continue

            try:
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
            except Exception:
                failed_rows += 1
                continue

        conn.commit()

        if skipped_rows:
            st.warning(
                f"Skipped {skipped_rows} row(s): COMPANY, AREA, and BRANCH are required."
            )
        if failed_rows:
            st.warning(f"{failed_rows} row(s) failed to save due to invalid values.")
        if (skipped_rows + failed_rows) == 0:
            st.success("Overview rows saved successfully ✅")

    def update_data(df, data_type):
        conn, cursor = get_cursor()
        skipped_rows = 0
        failed_rows = 0

        for _, row in df.iterrows():
            company = _db_param(row["COMPANY"])
            area = _db_param(row["AREA"])
            branch = _db_param(row["BRANCH"])

            # required keys
            if not company or not area or not branch:
                skipped_rows += 1
                continue

            try:
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
            except Exception:
                failed_rows += 1
                continue

        conn.commit()

        if skipped_rows:
            st.warning(
                f"Skipped {skipped_rows} row(s): COMPANY, AREA, and BRANCH are required."
            )
        if failed_rows:
            st.warning(f"{failed_rows} row(s) failed to save due to invalid values.")
        if (skipped_rows + failed_rows) == 0:
            st.success("Rows saved successfully ✅")

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
            safe_rerun()

    with tab2:
        st.subheader("Business Permit")
        df = load_data("BUSINESS_PERMIT")
        edited_df = render_editor(df, "business_tab")
        if st.button("💾 Save Business Permit"):
            update_data(edited_df, "BUSINESS_PERMIT")
            load_data.clear()
            safe_rerun()

    with tab3:
        st.subheader("Barangay Permit")
        df = load_data("BRGY_PERMIT")
        edited_df = render_editor(df, "brgy_tab")
        if st.button("💾 Save Brgy Permit"):
            update_data(edited_df, "BRGY_PERMIT")
            load_data.clear()
            safe_rerun()

    with tab4:
        st.subheader("Other Fees for Renew")
        df = load_data("OTHER_FEES")
        edited_df = render_editor(df, "other_tab")
        if st.button("💾 Save Other Fees"):
            update_data(edited_df, "OTHER_FEES")
            load_data.clear()
            safe_rerun()


# ---------------------------------------------------
# TAX MAPPED
# --------------------------------------------------
def tax_mapped():
    import base64

    st.title("🏷 TAX MAPPED Report")

    # =========================================================
    # LOAD DATA
    # =========================================================
    @st.cache_data(ttl=30, show_spinner=False)
    def load_tax_mapped():
        conn, cursor = get_cursor()
        cursor.execute("SELECT * FROM TAX_MAPPED")
        data = cursor.fetchall()
        columns = [
            "ID","AREA","BRANCH","DATE_TAX_MAPPED",
            "BIR_REMARKS","STICKER_IMAGE"
        ]
        return pd.DataFrame(data, columns=columns)

    df = load_tax_mapped()

    if "tax_mapped_orig" not in st.session_state:
        st.session_state.tax_mapped_orig = df.copy()

    # =========================================================
    # TABLE EDITOR
    # =========================================================
    st.subheader("📋 Tax Mapped Table")

    edited_df = st.data_editor(
        df,
        num_rows="dynamic",
        use_container_width=True,
        key="tax_mapped_editor",
        column_config={
            "DATE_TAX_MAPPED": st.column_config.DateColumn("Date Tax Mapped"),
        },
    )

    col1, col2, col3 = st.columns(3)

    # SAVE
    with col1:
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
                    SET AREA=%s, BRANCH=%s, DATE_TAX_MAPPED=%s, BIR_REMARKS=%s
                    WHERE ID=%s
                """,
                insert_cols=["AREA","BRANCH","DATE_TAX_MAPPED","BIR_REMARKS"],
                update_cols=["AREA","BRANCH","DATE_TAX_MAPPED","BIR_REMARKS"],
            )
            load_tax_mapped.clear()
            st.success("Table Updated ✅")
            safe_rerun()

    # UNDO
    with col2:
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
                    "ID","AREA","BRANCH","DATE_TAX_MAPPED","BIR_REMARKS","STICKER_IMAGE"
                ],
            )
            if ok:
                load_tax_mapped.clear()
                st.success("Undo successful 🔄")
                safe_rerun()
            else:
                st.info("Nothing to undo")

    # REFRESH
    with col3:
        if st.button("🔄 Refresh"):
            load_tax_mapped.clear()
            safe_rerun()

    # =========================================================
    # MULTI IMAGE UPLOAD (FIXED)
    # =========================================================
    st.divider()
    st.subheader("📤 Upload Sticker Images")

    uploaded_files = st.file_uploader(
        "Drag & Drop Images",
        type=["png", "jpg", "jpeg"],
        accept_multiple_files=True
    )

    if uploaded_files:
        for file in uploaded_files:

            colA, colB = st.columns([1, 2])

            with colA:
                st.image(file, width=120)

            with colB:
                row_id = st.number_input(
                    f"ID for {file.name}",
                    key=f"id_{file.name}",
                    step=1,
                    min_value=1
                )

                # ✅ UNIQUE BUTTON KEY (IMPORTANT FIX)
                if st.button(f"Upload {file.name}", key=f"upload_{file.name}"):

                    # 🔴 VALIDATION
                    if not row_id:
                        st.error("Enter valid ID")
                        continue

                    if file.size > 2 * 1024 * 1024:
                        st.error("Max file size is 2MB")
                        continue

                    if file.type not in ["image/png", "image/jpeg"]:
                        st.error("Only PNG/JPG allowed")
                        continue

                    try:
                        file_bytes = file.getvalue()
                        encoded = base64.b64encode(file_bytes).decode("utf-8")

                        conn, cursor = get_cursor()

                        cursor.execute(
                            """
                            UPDATE TAX_MAPPED
                            SET STICKER_IMAGE = %s
                            WHERE ID = %s
                            """,
                            (encoded, int(row_id)),
                        )

                        conn.commit()

                        if cursor.rowcount == 0:
                            st.warning(f"ID {row_id} not found")
                        else:
                            st.success(f"{file.name} uploaded ✅")

                        load_tax_mapped.clear()
                        safe_rerun()

                    except Exception as e:
                        st.error(f"Upload failed: {e}")

    # =========================================================
    # IMAGE GALLERY
    # =========================================================
    st.divider()
    st.subheader("🖼 Sticker Gallery")

    cols = st.columns(4)

    for i, row in df.iterrows():

        if not row["STICKER_IMAGE"]:
            continue

        try:
            img_bytes = base64.b64decode(row["STICKER_IMAGE"])
        except:
            continue

        with cols[i % 4]:
            st.image(img_bytes, use_container_width=True)
            st.caption(f"ID: {row['ID']} | {row['BRANCH']}")

            # REPLACE
            replace_file = st.file_uploader(
                f"Replace ID {row['ID']}",
                key=f"replace_{row['ID']}"
            )

            if replace_file:
                try:
                    encoded = base64.b64encode(replace_file.getvalue()).decode()

                    conn, cursor = get_cursor()
                    cursor.execute(
                        """
                        UPDATE TAX_MAPPED
                        SET STICKER_IMAGE = %s
                        WHERE ID = %s
                        """,
                        (encoded, int(row["ID"])),
                    )
                    conn.commit()

                    st.success("Replaced ✅")
                    load_tax_mapped.clear()
                    safe_rerun()

                except Exception as e:
                    st.error(f"Replace failed: {e}")

            # DELETE
            if st.button(f"🗑 Delete {row['ID']}", key=f"del_{row['ID']}"):
                try:
                    conn, cursor = get_cursor()
                    cursor.execute(
                        """
                        UPDATE TAX_MAPPED
                        SET STICKER_IMAGE = NULL
                        WHERE ID = %s
                        """,
                        (int(row["ID"]),),
                    )
                    conn.commit()

                    st.warning("Deleted ❌")
                    load_tax_mapped.clear()
                    safe_rerun()

                except Exception as e:
                    st.error(f"Delete failed: {e}")
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
    <div class="subtitle-chip">REPORT</div>
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
        df["DEADLINE"] = pd.to_datetime(df.get("DEADLINE"), errors="coerce")
df["DEADLINE_EXTENSION"] = pd.to_datetime(df.get("DEADLINE_EXTENSION"), errors="coerce")

df["FINAL_DEADLINE"] = (
    df["DEADLINE_EXTENSION"]
    .fillna(df["DEADLINE"])
    .dt.date
)

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

        # ========================= FIRE SAFETY HOLOGRAPHIC REPORT =========================
    @st.cache_data(ttl=60, show_spinner=False)
    def load_fire_report_data():
        conn, cursor = get_cursor()
        cursor.execute("""
            SELECT
                COMPANY,
                AREA,
                BRANCH,
                FSIC_CERTIFICATE_DATE,
                VALID_UNTIL,
                STATUS,
                REMARKS
            FROM FIRE_SAFETY
        """)
        cols = [
            "COMPANY",
            "AREA",
            "BRANCH",
            "FSIC_CERTIFICATE_DATE",
            "VALID_UNTIL",
            "STATUS",
            "REMARKS",
        ]
        return pd.DataFrame(cursor.fetchall(), columns=cols)

    fire_df = load_fire_report_data()

    st.markdown('<div class="holo-panel">', unsafe_allow_html=True)
    st.markdown('<div class="panel-header"><div class="panel-header-pill"></div>Fire Safety Compliance Report</div>', unsafe_allow_html=True)
    st.markdown('<div class="panel-subtitle">VALIDITY · STATUS · REMARKS INTELLIGENCE</div>', unsafe_allow_html=True)
    st.markdown('<div class="panel-body">', unsafe_allow_html=True)

    if not fire_df.empty:
        # Normalize values
        fire_df["VALID_UNTIL"] = pd.to_datetime(fire_df["VALID_UNTIL"], errors="coerce").dt.date
        fire_df["STATUS"] = fire_df["STATUS"].fillna("").astype(str).str.strip().str.upper()
        fire_df["REMARKS"] = fire_df["REMARKS"].fillna("NO REMARKS").astype(str).str.strip()

        today = datetime.today().date()
        fire_df["DAYS_TO_EXPIRY"] = fire_df["VALID_UNTIL"].apply(
            lambda d: (d - today).days if pd.notna(d) else None
        )

        # Validity state
        def validity_state(days_left):
            if days_left is None:
                return "NO VALIDITY DATE"
            if days_left < 0:
                return "EXPIRED"
            if days_left <= 30:
                return "EXPIRING <=30 DAYS"
            if days_left <= 90:
                return "EXPIRING 31-90 DAYS"
            return "VALID >90 DAYS"

        fire_df["VALIDITY_STATE"] = fire_df["DAYS_TO_EXPIRY"].apply(validity_state)

        # KPI counts
        total_fire = len(fire_df)
        expired = (fire_df["VALIDITY_STATE"] == "EXPIRED").sum()
        exp_30 = (fire_df["VALIDITY_STATE"] == "EXPIRING <=30 DAYS").sum()
        valid_90 = (fire_df["VALIDITY_STATE"] == "VALID >90 DAYS").sum()

        f1, f2, f3, f4 = st.columns(4)
        f1.metric("Total Fire Records", int(total_fire))
        f2.metric("Expired", int(expired))
        f3.metric("Expiring <=30 Days", int(exp_30))
        f4.metric("Valid >90 Days", int(valid_90))

        # Charts row 1: validity donut + status bar
        c_left, c_right = st.columns([1, 1.2])

        with c_left:
            st.markdown("##### Certificate Validity Distribution")
            validity_count = fire_df["VALIDITY_STATE"].value_counts().reset_index()
            validity_count.columns = ["VALIDITY_STATE", "COUNT"]

            fig_validity = px.pie(
                validity_count,
                names="VALIDITY_STATE",
                values="COUNT",
                hole=0.55,
                color="VALIDITY_STATE",
                color_discrete_map={
                    "EXPIRED": "#ef4444",
                    "EXPIRING <=30 DAYS": "#f59e0b",
                    "EXPIRING 31-90 DAYS": "#3b82f6",
                    "VALID >90 DAYS": "#22c55e",
                    "NO VALIDITY DATE": "#9ca3af",
                },
            )
            fig_validity.update_traces(textinfo="percent+label")
            fig_validity.update_layout(
                template="plotly_dark",
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                showlegend=False,
            )
            st.plotly_chart(fig_validity, use_container_width=True)

        with c_right:
            st.markdown("##### Status Overview")
            status_count = fire_df["STATUS"].replace("", "BLANK").value_counts().reset_index()
            status_count.columns = ["STATUS", "COUNT"]

            fig_status_fire = px.bar(
                status_count,
                x="STATUS",
                y="COUNT",
                color="COUNT",
                color_continuous_scale="Blues",
                text="COUNT",
            )
            fig_status_fire.update_traces(textposition="outside")
            fig_status_fire.update_layout(
                template="plotly_dark",
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                xaxis_title="STATUS",
                yaxis_title="COUNT",
            )
            st.plotly_chart(fig_status_fire, use_container_width=True)

        # Charts row 2: remarks analysis + area risk
        d_left, d_right = st.columns([1, 1.2])

        with d_left:
            st.markdown("##### Top Remarks")
            remarks_count = fire_df["REMARKS"].value_counts().head(10).reset_index()
            remarks_count.columns = ["REMARKS", "COUNT"]

            fig_remarks = px.bar(
                remarks_count.sort_values("COUNT", ascending=True),
                x="COUNT",
                y="REMARKS",
                orientation="h",
                color="COUNT",
                color_continuous_scale="Tealgrn",
            )
            fig_remarks.update_layout(
                template="plotly_dark",
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                yaxis_title="REMARKS",
                xaxis_title="COUNT",
            )
            st.plotly_chart(fig_remarks, use_container_width=True)

        with d_right:
            st.markdown("##### Expired / Expiring by Area")
            area_exp = (
                fire_df.assign(
                    ALERT=fire_df["VALIDITY_STATE"].isin(["EXPIRED", "EXPIRING <=30 DAYS"])
                )
                .groupby("AREA", dropna=False)["ALERT"]
                .sum()
                .reset_index(name="ALERT_COUNT")
            )

            if not area_exp.empty:
                fig_area_alert = px.bar(
                    area_exp,
                    x="AREA",
                    y="ALERT_COUNT",
                    color="ALERT_COUNT",
                    color_continuous_scale="Reds",
                    text="ALERT_COUNT",
                )
                fig_area_alert.update_traces(textposition="outside")
                fig_area_alert.update_layout(
                    template="plotly_dark",
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                    xaxis_title="AREA",
                    yaxis_title="EXPIRED + <=30 DAYS",
                )
                st.plotly_chart(fig_area_alert, use_container_width=True)

        # Critical table
        st.markdown("##### 🚨 Priority Branches (Expired or Expiring <=30 Days)")
        critical_df = fire_df[
            fire_df["VALIDITY_STATE"].isin(["EXPIRED", "EXPIRING <=30 DAYS"])
        ].copy()

        if critical_df.empty:
            st.success("No expired or near-expiry fire safety certificates.")
        else:
            critical_df = critical_df.sort_values(
                by=["DAYS_TO_EXPIRY"], ascending=True, na_position="last"
            )
            st.dataframe(
                critical_df[
                    ["COMPANY", "AREA", "BRANCH", "VALID_UNTIL", "DAYS_TO_EXPIRY", "STATUS", "REMARKS", "VALIDITY_STATE"]
                ],
                use_container_width=True,
            )

    else:
        st.warning("No FIRE_SAFETY data available for report.")

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

        # ========================= TIN & ADDRESS HOLOGRAPHIC REPORT =========================
    @st.cache_data(ttl=60, show_spinner=False)
    def load_tin_address_report():
        conn, cursor = get_cursor()
        cursor.execute("""
            SELECT
                COMPANY,
                AREA,
                BRANCH_NAME,
                STATUS,
                DATE_OPEN,
                DATE_OF_CLOSURE
            FROM BRANCH_TIN_ADDRESS
        """)
        cols = [
            "COMPANY",
            "AREA",
            "BRANCH_NAME",
            "STATUS",
            "DATE_OPEN",
            "DATE_OF_CLOSURE",
        ]
        return pd.DataFrame(cursor.fetchall(), columns=cols)

    tin_df = load_tin_address_report()

    st.markdown('<div class="holo-panel">', unsafe_allow_html=True)
    st.markdown('<div class="panel-header"><div class="panel-header-pill"></div>TIN & Address Network Intelligence</div>', unsafe_allow_html=True)
    st.markdown('<div class="panel-subtitle">BRANCH FOOTPRINT · NEW / CHANGED ADDRESSES · CLOSURES</div>', unsafe_allow_html=True)
    st.markdown('<div class="panel-body">', unsafe_allow_html=True)

    if not tin_df.empty:
        # Normalize
        tin_df["COMPANY"] = tin_df["COMPANY"].fillna("").astype(str).str.strip().str.upper()
        tin_df["STATUS"] = tin_df["STATUS"].fillna("").astype(str).str.strip().str.upper()
        tin_df["DATE_OPEN"] = pd.to_datetime(tin_df["DATE_OPEN"], errors="coerce")
        tin_df["DATE_OF_CLOSURE"] = pd.to_datetime(tin_df["DATE_OF_CLOSURE"], errors="coerce")

        # KPI metrics
        total_branches_tin = len(tin_df)
        new_branches = (tin_df["STATUS"] == "NEW").sum()
        changed_addr = (tin_df["STATUS"] == "CHANGED").sum()
        closed_branches = (tin_df["STATUS"] == "CLOSED").sum()

        t1, t2, t3, t4 = st.columns(4)
        t1.metric("Total Branch Records", int(total_branches_tin))
        t2.metric("NEW Branch Address", int(new_branches))
        t3.metric("CHANGED Address", int(changed_addr))
        t4.metric("Closed Branches", int(closed_branches))

        # ---------- Branch footprint per company ----------
        st.markdown("##### Branch Footprint by Company")
        comp_counts = tin_df.groupby("COMPANY")["BRANCH_NAME"].nunique().reset_index(name="BRANCH_COUNT")

        fig_comp = px.bar(
            comp_counts,
            x="COMPANY",
            y="BRANCH_COUNT",
            color="BRANCH_COUNT",
            color_continuous_scale="Viridis",
            text="BRANCH_COUNT",
        )
        fig_comp.update_traces(textposition="outside")
        fig_comp.update_layout(
            template="plotly_dark",
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            xaxis_title="COMPANY",
            yaxis_title="Distinct Branches",
        )
        st.plotly_chart(fig_comp, use_container_width=True)

        # ---------- NEW / CHANGED / CLOSED mix per company ----------
        st.markdown("##### Status Mix per Company (NEW · CHANGED · CLOSED · ACTIVE)")
        tin_df["STATUS_GROUP"] = tin_df["STATUS"].replace(
            {"": "ACTIVE"}  # treat blank as ACTIVE
        )

        status_mix = (
            tin_df
            .groupby(["COMPANY", "STATUS_GROUP"])["BRANCH_NAME"]
            .nunique()
            .reset_index(name="COUNT")
        )

        fig_mix = px.bar(
            status_mix,
            x="COMPANY",
            y="COUNT",
            color="STATUS_GROUP",
            barmode="stack",
        )
        fig_mix.update_layout(
            template="plotly_dark",
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            xaxis_title="COMPANY",
            yaxis_title="Branch Count",
        )
        st.plotly_chart(fig_mix, use_container_width=True)

        # ---------- Holographic timeline: openings vs closures ----------
        st.markdown("##### Holographic Branch Timeline (Open vs Closed)")

        # Openings per month
        open_ts = (
            tin_df.dropna(subset=["DATE_OPEN"])
            .assign(OPEN_MONTH=lambda d: d["DATE_OPEN"].dt.to_period("M").dt.to_timestamp())
            .groupby("OPEN_MONTH")["BRANCH_NAME"].nunique()
            .reset_index(name="OPENED")
        )

        # Closures per month
        close_ts = (
            tin_df.dropna(subset=["DATE_OF_CLOSURE"])
            .assign(CLOSE_MONTH=lambda d: d["DATE_OF_CLOSURE"].dt.to_period("M").dt.to_timestamp())
            .groupby("CLOSE_MONTH")["BRANCH_NAME"].nunique()
            .reset_index(name="CLOSED")
        )

        timeline = pd.merge(
            open_ts.rename(columns={"OPEN_MONTH": "MONTH"}),
            close_ts.rename(columns={"CLOSE_MONTH": "MONTH"}),
            on="MONTH",
            how="outer",
        ).sort_values("MONTH")

        timeline[["OPENED", "CLOSED"]] = timeline[["OPENED", "CLOSED"]].fillna(0)

        fig_timeline = go.Figure()
        fig_timeline.add_trace(go.Scatter(
            x=timeline["MONTH"],
            y=timeline["OPENED"],
            mode="lines+markers",
            name="Opened",
            line=dict(color="#22c55e", width=3),
            marker=dict(size=7),
        ))
        fig_timeline.add_trace(go.Scatter(
            x=timeline["MONTH"],
            y=timeline["CLOSED"],
            mode="lines+markers",
            name="Closed",
            line=dict(color="#ef4444", width=3),
            marker=dict(size=7),
        ))
        fig_timeline.update_layout(
            template="plotly_dark",
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            xaxis_title="Month",
            yaxis_title="Branch Count",
        )
        st.plotly_chart(fig_timeline, use_container_width=True)

        # ---------- Advanced insight table ----------
        st.markdown("##### 🔍 Advanced Insight – NEW / CHANGED / CLOSED Details")

        insight_df = tin_df[
            tin_df["STATUS"].isin(["NEW", "CHANGED", "CLOSED"])
        ].copy()

        if insight_df.empty:
            st.info("No NEW / CHANGED / CLOSED branch records yet.")
        else:
            # Sort: newest changes first
            insight_df["LAST_EVENT_DATE"] = insight_df[["DATE_OPEN", "DATE_OF_CLOSURE"]].max(axis=1)
            insight_df = insight_df.sort_values("LAST_EVENT_DATE", ascending=False)

            st.dataframe(
                insight_df[
                    ["COMPANY", "AREA", "BRANCH_NAME", "STATUS", "DATE_OPEN", "DATE_OF_CLOSURE"]
                ],
                use_container_width=True,
            )

    else:
        st.warning("No TIN & Address data available for report.")

    st.markdown('</div></div>', unsafe_allow_html=True)

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
        st.markdown('<div class="sb-subtitle">Navigation Pane</div>', unsafe_allow_html=True)

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
