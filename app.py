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
            "FSIC_VALIDITY",
            "FSIC_FEE",
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
            "FSIC_VALIDITY": st.column_config.DateColumn("FSIC Validity"),
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
            ok = handle_undo_with_id(
                df_name_prefix="fire",
                table_name="FIRE_SAFETY",
                insert_sql_with_id="""
                    INSERT INTO FIRE_SAFETY
                    (ID,COMPANY,AREA,BRANCH,FSIC_VALIDITY,FSIC_FEE,REMARKS)
                    VALUES (%s,%s,%s,%s,%s,%s,%s)
                """,
                cols_with_id=[
                    "ID",
                    "COMPANY",
                    "AREA",
                    "BRANCH",
                    "FSIC_VALIDITY",
                    "FSIC_FEE",
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

    col1, col2, col3 = st.columns(3)

    with col1:
        if st.button("SUKI Branch"):
            st.session_state.company = "SUKI"

    with col2:
        if st.button("PCNFCI Branch"):
            st.session_state.company = "PCNFCI"

    with col3:
        if st.button("FASTCASH Branch"):
            st.session_state.company = "FASTCASH"

    if "company" in st.session_state:
        selected_company = st.session_state.company
        st.subheader(f"{selected_company} Branch List")

        @st.cache_data(ttl=30, show_spinner=False)
        def load_branch_tin(company):
            conn, cursor = get_cursor()
            query = """
            SELECT *
            FROM BRANCH_TIN_ADDRESS
            WHERE COMPANY = %s
            """
            cursor.execute(query, (company,))
            data = cursor.fetchall()
            columns = [
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
            return pd.DataFrame(data, columns=columns)

        df = load_branch_tin(selected_company)

        key_prefix = f"branch_tin_{selected_company.lower()}"
        orig_key = f"{key_prefix}_orig"

        if orig_key not in st.session_state:
            st.session_state[orig_key] = df.copy()

        edited_df = st.data_editor(
            df,
            num_rows="dynamic",
            use_container_width=True,
            key=f"{key_prefix}_editor",
            column_config={
                "DATE_OPEN": st.column_config.DateColumn("Date Open"),
                "DATE_OF_CLOSURE": st.column_config.DateColumn("Date of Closure"),
            },
        )

        col_save, col_undo, col_refresh = st.columns(3)

        with col_save:
            if st.button("💾 Save Changes", key=f"{key_prefix}_save"):
                handle_save_with_id(
                    df_name_prefix=key_prefix,
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
                    ],
                    update_cols=[
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
                    ],
                )
                load_branch_tin.clear()
                st.session_state[orig_key] = load_branch_tin(selected_company)
                st.success("Data saved successfully ✅")
                safe_rerun()

        with col_undo:
            if st.button("↩ Undo last delete", key=f"{key_prefix}_undo"):
                ok = handle_undo_with_id(
                    df_name_prefix=key_prefix,
                    table_name="BRANCH_TIN_ADDRESS",
                    insert_sql_with_id="""
                        INSERT INTO BRANCH_TIN_ADDRESS
                        (ID,COMPANY,TIN,BRANCH_CODE,RDO,AREA,BRANCH_NAME,UPDATED_ADDRESS,STATUS,DATE_OPEN,DATE_OF_CLOSURE)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
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
                    ],
                )
                if ok:
                    load_branch_tin.clear()
                    st.session_state[orig_key] = load_branch_tin(selected_company)
                    st.success("Delete undone 🔄")
                    safe_rerun()
                else:
                    st.info("Nothing to undo")

        with col_refresh:
            if st.button("🔄 Refresh", key=f"{key_prefix}_refresh"):
                load_branch_tin.clear()
                st.session_state[orig_key] = load_branch_tin(selected_company)
                safe_rerun()


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

    @st.cache_data(ttl=30, show_spinner=False)
    def load_sec():
        conn, cursor = get_cursor()
        cursor.execute(
            """
            SELECT
                ID, AREA, MONTH_YEAR, BRANCH, REMARKS,
                DATE_FORWARDED, STATUS, DATE_RECEIVED, FINAL_STATUS
            FROM SECRETARY_CERTIFICATES
        """
        )
        data = cursor.fetchall()
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
        return pd.DataFrame(data, columns=columns)

    df = load_sec()

    if "sec_orig" not in st.session_state:
        st.session_state.sec_orig = df.copy()

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

    with col_save:
        if st.button("💾 Save Changes"):
            log_activity("Secretary Certificates", "SAVE")

            handle_save_with_id(
                df_name_prefix="sec",
                table_name="SECRETARY_CERTIFICATES",
                df=edited_df,
                id_col="ID",
                insert_sql="""
                    INSERT INTO SECRETARY_CERTIFICATES
                    (AREA,MONTH_YEAR,BRANCH,REMARKS,DATE_FORWARDED,STATUS,DATE_RECEIVED,FINAL_STATUS)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                update_sql="""
                    UPDATE SECRETARY_CERTIFICATES
                    SET
                        AREA=%s,
                        MONTH_YEAR=%s,
                        BRANCH=%s,
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
                    (ID,AREA,MONTH_YEAR,BRANCH,REMARKS,DATE_FORWARDED,STATUS,DATE_RECEIVED,FINAL_STATUS)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                cols_with_id=[
                    "ID",
                    "AREA",
                    "MONTH_YEAR",
                    "BRANCH",
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


# ---------------------------------------------------
# DASHBOARD ANALYTICS (Power BI + charts)
# ---------------------------------------------------
def dashboard_analytics():
    st.title("📊 Compliance Executive Dashboard")

    # =========================
    # DATA LOADERS (CACHED)
    # =========================
    @st.cache_data(ttl=60, show_spinner=False)
    def load_business_status():
        conn, cursor = get_cursor()
        cursor.execute(
            """
            SELECT STATUS, COUNT(*) AS CNT
            FROM BUSINESS_PERMIT
            GROUP BY STATUS
        """
        )
        return pd.DataFrame(cursor.fetchall(), columns=["STATUS", "COUNT"])

    @st.cache_data(ttl=60, show_spinner=False)
    def load_area_distribution():
        conn, cursor = get_cursor()
        cursor.execute(
            """
            SELECT AREA, COUNT(*) AS CNT
            FROM BRANCH_TIN_ADDRESS
            GROUP BY AREA
        """
        )
        return pd.DataFrame(cursor.fetchall(), columns=["AREA", "COUNT"])

    @st.cache_data(ttl=60, show_spinner=False)
    def load_overview_sla():
        conn, cursor = get_cursor()
        cursor.execute(
            """
            SELECT
                COMPANY,
                AREA,
                BRANCH,
                DEADLINE,
                DEADLINE_EXTENSION,
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

    @st.cache_data(ttl=60, show_spinner=False)
    def load_pending_permits():
        conn, cursor = get_cursor()
        cursor.execute(
            """
            SELECT COMPANY, AREA, BRANCH, STATUS
            FROM BUSINESS_PERMIT
            WHERE STATUS != 'DONE'
        """
        )
        return pd.DataFrame(
            cursor.fetchall(), columns=["COMPANY", "AREA", "BRANCH", "STATUS"]
        )

    @st.cache_data(ttl=60, show_spinner=False)
    def load_fire_safety_status():
        conn, cursor = get_cursor()
        cursor.execute(
            """
            SELECT AREA, BRANCH, FSIC_VALIDITY
            FROM FIRE_SAFETY
            """
        )
        df = pd.DataFrame(cursor.fetchall(), columns=["AREA", "BRANCH", "FSIC_VALIDITY"])

        if not df.empty:
            df["FSIC_VALIDITY"] = pd.to_datetime(df["FSIC_VALIDITY"]).dt.date
            today = datetime.today().date()

            def fsic_status(d):
                if pd.isna(d):
                    return "NO_FSIC"
                if d < today:
                    return "EXPIRED"
                return "VALID"

            df["FSIC_STATUS"] = df["FSIC_VALIDITY"].apply(fsic_status)

        return df

    @st.cache_data(ttl=60, show_spinner=False)
    def load_tax_mapped_simple():
        conn, cursor = get_cursor()
        cursor.execute(
            """
            SELECT AREA, BRANCH, DATE_TAX_MAPPED, BIR_REMARKS
            FROM TAX_MAPPED
        """
        )
        return pd.DataFrame(
            cursor.fetchall(), columns=["AREA", "BRANCH", "DATE_TAX_MAPPED", "BIR_REMARKS"]
        )

    # =========================
    # CORE DATA + DERIVED FIELDS
    # =========================
    df_status = load_business_status()
    df_area = load_area_distribution()
    df_overview = load_overview_sla()
    df_pending = load_pending_permits()
    df_fire = load_fire_safety_status()
    df_tax = load_tax_mapped_simple()

    today = datetime.today().date()

        # SLA status per branch (on‑time / late)
    if not df_overview.empty:
        df_overview_work = df_overview.copy()
        for c in ["DEADLINE", "DEADLINE_EXTENSION"]:
            df_overview_work[c] = pd.to_datetime(df_overview_work[c]).dt.date

        df_overview_work["EFFECTIVE_DEADLINE"] = df_overview_work["DEADLINE_EXTENSION"].fillna(
            df_overview_work["DEADLINE"]
        )

        df_overview_work["SLA_STATUS"] = df_overview_work["EFFECTIVE_DEADLINE"].apply(
            lambda d: "NO_DEADLINE"
            if pd.isna(d)
            else ("LATE" if d < today else "ON_TIME")
        )
    else:
        # empty dataframe with the same columns plus the ones we add later
        df_overview_work = df_overview.copy()
        df_overview_work["EFFECTIVE_DEADLINE"] = pd.NaT
        df_overview_work["SLA_STATUS"] = []

    # Risk scoring (simple rule‑based: 0–100)
    def compute_risk(row):
        score = 0
        # base: pending business permits
        if not df_pending.empty:
            has_pending = (
                (df_pending["COMPANY"] == row["COMPANY"])
                & (df_pending["AREA"] == row["AREA"])
                & (df_pending["BRANCH"] == row["BRANCH"])
            ).any()
            if has_pending:
                score += 40

        # SLA late
        if row.get("SLA_STATUS") == "LATE":
            score += 40

        # SEC cert pending / processing
        if row.get("SEC_CERT") in ["PENDING", "PROCESSING"]:
            score += 10

        # missing gross sales cert
        if pd.isna(row.get("GROSS_SALES_CERT")):
            score += 10

        return min(score, 100)

    if not df_overview_work.empty:
        df_overview_work["RISK_SCORE"] = df_overview_work.apply(compute_risk, axis=1)

        def risk_bucket(score):
            if score >= 70:
                return "RED"
            if score >= 40:
                return "YELLOW"
            return "GREEN"

        df_overview_work["RISK_BUCKET"] = df_overview_work["RISK_SCORE"].apply(risk_bucket)
    else:
        df_overview_work["RISK_SCORE"] = []
        df_overview_work["RISK_BUCKET"] = []

    # =========================
    # KPI ROW
    # =========================
    total_branches = int(df_area["COUNT"].sum()) if not df_area.empty else 0
    done_permits = int(
        df_status.loc[df_status["STATUS"] == "DONE", "COUNT"].sum()
    ) if not df_status.empty else 0
    total_permits = int(df_status["COUNT"].sum()) if not df_status.empty else 0
    completion_rate = (done_permits / total_permits * 100) if total_permits else 0

        if not df_overview_work.empty and "SLA_STATUS" in df_overview_work.columns:
        late_branches = int(
            df_overview_work.loc[df_overview_work["SLA_STATUS"] == "LATE"].shape[0]
        )
    else:
        late_branches = 0

    if not df_overview_work.empty and "RISK_BUCKET" in df_overview_work.columns:
        high_risk = int(
            df_overview_work.loc[df_overview_work["RISK_BUCKET"] == "RED"].shape[0]
        )
    else:
        high_risk = 0

    col_kpi1, col_kpi2, col_kpi3, col_kpi4 = st.columns(4)
    col_kpi1.metric("Total Branches", f"{total_branches}")
    col_kpi2.metric("Permit Completion", f"{completion_rate:.1f} %")
    col_kpi3.metric("Late vs SLA", f"{late_branches}")
    col_kpi4.metric("High‑Risk Branches (RED)", f"{high_risk}")

    st.divider()

    # =========================
    # LAYOUT: LEFT (charts) / RIGHT (table + drill‑down)
    # =========================
    left_col, right_col = st.columns([2, 1.6])

    # ----- LEFT: charts -----
    with left_col:
        st.subheader("Portfolio View")

        # Business permit status
        if not df_status.empty:
            fig1 = px.pie(
                df_status,
                values="COUNT",
                names="STATUS",
                title="Business Permit Status",
                hole=0.4,
            )
            st.plotly_chart(fig1, use_container_width=True)

        # Area distribution + average risk
        if not df_area.empty:
            if not df_overview_work.empty:
                risk_by_area = (
                    df_overview_work.groupby("AREA")["RISK_SCORE"].mean().reset_index()
                )
                df_area_risk = df_area.merge(risk_by_area, on="AREA", how="left")
            else:
                df_area_risk = df_area.copy()
                df_area_risk["RISK_SCORE"] = 0

            fig2 = px.bar(
                df_area_risk,
                x="AREA",
                y="COUNT",
                color="RISK_SCORE",
                color_continuous_scale=["#2ecc71", "#f1c40f", "#e74c3c"],
                title="Branches by Area (color = avg risk)",
            )
            st.plotly_chart(fig2, use_container_width=True)

        # SLA status chart
    if not df_overview_work.empty and "SLA_STATUS" in df_overview_work.columns:
            sla_counts = df_overview_work["SLA_STATUS"].value_counts().reset_index()
            sla_counts.columns = ["SLA_STATUS", "COUNT"]

            fig3 = px.bar(
                sla_counts,
                x="SLA_STATUS",
                y="COUNT",
                title="SLA Status Distribution",
                color="SLA_STATUS",
                color_discrete_map={
                    "ON_TIME": "#2ecc71",
                    "LATE": "#e74c3c",
                    "NO_DEADLINE": "#95a5a6",
                },
            )
            st.plotly_chart(fig3, use_container_width=True)

    # ----- RIGHT: drill‑down table -----
    with right_col:
        st.subheader("Drill‑Down: Branch Details")

        # pick by area then branch (acts like click‑through)
        areas = sorted(df_overview_work["AREA"].dropna().unique()) if not df_overview_work.empty else []
        selected_area = st.selectbox("Select Area", ["All"] + list(areas))

        if selected_area == "All":
            df_branch_view = df_overview_work.copy()
        else:
            df_branch_view = df_overview_work[df_overview_work["AREA"] == selected_area]

        # join pending + risk bucket for richer view
        if not df_branch_view.empty and not df_pending.empty:
            df_branch_view = df_branch_view.merge(
                df_pending[["COMPANY", "AREA", "BRANCH", "STATUS"]]
                .rename(columns={"STATUS": "PERMIT_STATUS"}),
                on=["COMPANY", "AREA", "BRANCH"],
                how="left",
            )

        cols_show = [
            "COMPANY",
            "AREA",
            "BRANCH",
            "EFFECTIVE_DEADLINE",
            "SLA_STATUS",
            "PERMIT_STATUS",
            "SEC_CERT",
            "RISK_SCORE",
            "RISK_BUCKET",
        ]
        cols_show = [c for c in cols_show if c in df_branch_view.columns]

        st.dataframe(
            df_branch_view[cols_show].sort_values("RISK_SCORE", ascending=False),
            use_container_width=True,
        )

    st.divider()

    # =========================
    # AI INSIGHTS / RISK NARRATIVE
    # =========================
    st.subheader("🤖 AI Risk & Compliance Insights")

    if st.button("Generate AI Summary"):
        # compact numeric context
        ctx = {
            "total_branches": total_branches,
            "completion_rate": round(completion_rate, 1),
            "late_branches": late_branches,
            "high_risk": high_risk,
        }

        # small top‑N risk table as text
        top_risk = (
            df_overview_work.sort_values("RISK_SCORE", ascending=False)
            .head(10)[["COMPANY", "AREA", "BRANCH", "RISK_SCORE", "RISK_BUCKET"]]
            .to_dict(orient="records")
            if not df_overview_work.empty
            else []
        )

        prompt = f"""
        You are a senior compliance risk officer.

        Here is the current compliance snapshot (aggregated):
        {ctx}

        Top 10 highest-risk branches (if any), with scores 0-100:
        {top_risk}

        Tasks:
        1. Give a concise executive-level summary (2-3 bullets).
        2. Call out the top areas / patterns of concern.
        3. Suggest 3-5 concrete next actions in priority order.
        Be direct and business-oriented. Do not repeat raw numbers verbatim.
        """

        with st.spinner("Analyzing portfolio..."):
            try:
                resp = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role": "user", "content": prompt}],
                )
                ai_text = resp.choices[0].message.content
                st.markdown(ai_text)
            except Exception as e:
                st.error(f"AI analysis failed: {e}")


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
            "BOA Stickers",
            "Fire Safety",
            "Board Resolutions",
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
    }

    if menu == "Board Resolutions":
        st.title("📄 Board Resolutions Report")
    else:
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
