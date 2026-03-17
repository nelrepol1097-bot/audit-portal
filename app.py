import time
import streamlit as st
import snowflake.connector
from datetime import datetime
import requests
from streamlit_lottie import st_lottie
import pandas as pd
import plotly.express as px
from openai import OpenAI
from streamlit_autorefresh import st_autorefresh
import streamlit as st
except Exception as e:
    print(e)

# ---------------------------------------------------
# PAGE CONFIG
# ---------------------------------------------------

st.set_page_config(page_title="Audit Portal", page_icon="🔎", layout="wide")

# ---------------------------------------------------
# ANIMATED BACKGROUND
# ---------------------------------------------------

st.markdown("""
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
""", unsafe_allow_html=True)

# ---------------------------------------------------
# LOAD ANIMATION
# ---------------------------------------------------

def load_lottie(url):
    r = requests.get(url)
    if r.status_code != 200:
        return None
    return r.json()

lottie_login = load_lottie(
    "https://assets10.lottiefiles.com/packages/lf20_jcikwtux.json"
)

# ---------------------------------------------------
# SNOWFLAKE CONNECTION
# ---------------------------------------------------

def get_connection():
    try:
        return snowflake.connector.connect(
            user="jmcasaria",
            password=st.secrets["snowflake"]["password"],
            account="NSXAGQQ-WJ05543",
            warehouse="COMPUTE_WH",
            database="CFB_ANALYST_JAKE_DB",
            schema="PUBLIC",
            role="ANALYST_JAKE_ROLE"
        )
    except Exception as e:
        st.error(f"Connection failed: {e}")
        st.stop()
    return conn

conn = get_connection()
cursor = conn.cursor()

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
# LIVE CLOCK (single render; auto-refresh handles updates)
# ---------------------------------------------------

def live_clock():
    now = datetime.now().strftime("%A, %B %d %Y | %H:%M:%S")
    st.markdown(
        f"""
        <div class="clock-box">
        {now}
        </div>
        """,
        unsafe_allow_html=True
    )

# ---------------------------------------------------
# RERUN HELPER (handles old/new versions)
# ---------------------------------------------------

def safe_rerun():
    try:
        st.rerun()
    except AttributeError:
        st.experimental_rerun()

# ---------------------------------------------------
# HELPER: generic delete/undo pattern for ID-based tables
# ---------------------------------------------------

def handle_save_with_id(df_name_prefix, table_name, df, id_col,
                        insert_sql, update_sql, insert_cols, update_cols):
    orig_key = f"{df_name_prefix}_orig"
    deleted_key = f"{df_name_prefix}_deleted"

    orig = st.session_state.get(orig_key, df.copy())

    orig_ids = set(orig[id_col].dropna().astype(int))
    edited_ids = set(df[id_col].dropna().astype(int))

    # deletions
    deleted_ids = orig_ids - edited_ids
    if deleted_ids:
        st.session_state[deleted_key] = orig[orig[id_col].isin(deleted_ids)].copy()

    # INSERT new rows
    new_rows = df[df[id_col].isna()]
    for _, row in new_rows.iterrows():
        vals = [row[c] for c in insert_cols]
        cursor.execute(insert_sql, vals)

    # UPDATE existing rows
    existing_ids = orig_ids & edited_ids
    existing_rows = df[df[id_col].isin(existing_ids)]
    for _, row in existing_rows.iterrows():
        vals = [row[c] for c in update_cols] + [int(row[id_col])]
        cursor.execute(update_sql, vals)

    # DELETE removed rows
    for del_id in deleted_ids:
        cursor.execute(f"DELETE FROM {table_name} WHERE {id_col}=%s", (int(del_id),))

    conn.commit()


def handle_undo_with_id(df_name_prefix, table_name, insert_sql_with_id, cols_with_id):
    deleted_key = f"{df_name_prefix}_deleted"
    if deleted_key not in st.session_state:
        return False

    deleted_df = st.session_state[deleted_key]
    if deleted_df is None or deleted_df.empty:
        return False

    for _, row in deleted_df.iterrows():
        vals = [row[c] for c in cols_with_id]
        cursor.execute(insert_sql_with_id, vals)

    conn.commit()
    st.session_state[deleted_key] = pd.DataFrame()
    return True

# ---------------------------------------------------
# ACTIVITY LOG
# ---------------------------------------------------

def log_activity(page, action):
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO USER_ACTIVITY_LOG
            (EMAIL, PAGE, ACTION, TIMESTAMP)
            VALUES (%s,%s,%s,%s)
        """, (
            st.session_state.get("user", "UNKNOWN"),
            page,
            action,
            datetime.now()
        ))
        conn.commit()
    except Exception:
        # best-effort logging; ignore failures
        pass

# ---------------------------------------------------
# LOGIN PAGE
# ---------------------------------------------------

def login():
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
                        (email, login_time)
                    )
                    conn.commit()
                    st.session_state.logged_in = True
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

        st.markdown('</div>', unsafe_allow_html=True)
        st.session_state.user = email

# ---------------------------------------------------
# REGISTER PAGE
# ---------------------------------------------------

def register():
    live_clock()

    st.title("Create Account")

    name = st.text_input("Full Name")
    email = st.text_input("Email")
    password_input = st.text_input("Password", type="password")

    if st.button("Register"):
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
    st.title("👨‍💼 Admin Approval Panel")

    cursor.execute("""
    SELECT ID,NAME,EMAIL
    FROM USERS
    WHERE STATUS='PENDING'
    """)

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
                        (user[0],)
                    )
                    conn.commit()
                    st.success("User approved")

            with col2:
                if st.button(f"Reject {user[0]}"):
                    cursor.execute(
                        "UPDATE USERS SET STATUS='REJECTED' WHERE ID=%s",
                        (user[0],)
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
            "STATUS"
        ]
        return pd.DataFrame(data, columns=columns)

    df = load_atp()

    if "atp_orig" not in st.session_state:
        st.session_state.atp_orig = df.copy()

    edited_df = st.data_editor(
        df,
        num_rows="dynamic",
        use_container_width=True,
        key="atp_editor"
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
                    "STATUS"
                ],
                update_cols=[
                    "COMPANY",
                    "AREA",
                    "BRANCH",
                    "SERVICE_INVOICE_SERIAL_NO",
                    "BIR_DATE_RECEIVED_FIRST_STAMP",
                    "BIR_DATE_RECEIVED_LAST_STAMP",
                    "STATUS"
                ]
            )
            load_atp.clear()
            st.session_state.atp_orig = load_atp()
            st.success("Data saved successfully")
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
                    "STATUS"
                ]
            )
            if ok:
                load_atp.clear()
                st.session_state.atp_orig = load_atp()
                st.success("Delete undone")
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
        cursor.execute("SELECT * FROM BIR_1906_ATP")
        data = cursor.fetchall()
        columns = [
            "ID",
            "COMPANY",
            "BRANCH",
            "SERVICE_INVOICE_SERIAL_NO",
            "BIR_DATE_RECEIVED",
            "STATUS"
        ]
        return pd.DataFrame(data, columns=columns)

    df = load_bir()

    if "bir1906_orig" not in st.session_state:
        st.session_state.bir1906_orig = df.copy()

    edited_df = st.data_editor(
        df,
        num_rows="dynamic",
        use_container_width=True,
        key="bir1906_editor"
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
                    "STATUS"
                ],
                update_cols=[
                    "COMPANY",
                    "BRANCH",
                    "SERVICE_INVOICE_SERIAL_NO",
                    "BIR_DATE_RECEIVED",
                    "STATUS"
                ]
            )
            load_bir.clear()
            st.session_state.bir1906_orig = load_bir()
            st.success("Data saved successfully")
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
                    "STATUS"
                ]
            )
            if ok:
                load_bir.clear()
                st.session_state.bir1906_orig = load_bir()
                st.success("Delete undone")
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

        Convert the question into a Snowflake SQL query.

        Only use SELECT queries.

        Database schema:
        {schema}

        Question:
        {question}

        Return SQL only.
        """

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}]
        )

        sql_query = response.choices[0].message.content.strip()

        st.subheader("Generated SQL")
        st.code(sql_query)

        try:
            cursor.execute(sql_query)
            data = cursor.fetchall()

            df = pd.DataFrame(data)

            st.subheader("Result")
            st.dataframe(df, use_container_width=True)

            if len(df.columns) >= 2:
                fig = px.bar(df, x=df.columns[0], y=df.columns[1])
                st.plotly_chart(fig, use_container_width=True)

        except Exception as e:
            st.error(f"Query failed: {e}")

# ---------------------------------------------------
# BOA STICKER
# ---------------------------------------------------

def boa_sticker():
    st.title("📄 BOA Sticker")

    @st.cache_data(ttl=30, show_spinner=False)
    def load_boa():
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
            "STATUS"
        ]
        return pd.DataFrame(data, columns=columns)

    df = load_boa()

    if "boa_orig" not in st.session_state:
        st.session_state.boa_orig = df.copy()

    edited_df = st.data_editor(
        df,
        num_rows="dynamic",
        use_container_width=True,
        key="boa_editor"
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
                    "STATUS"
                ],
                update_cols=[
                    "COMPANY",
                    "BRANCH",
                    "TIN",
                    "BOOK_TO_REGISTER",
                    "VOLUME_NUMBER",
                    "DATE_FORWARDED_TO_BRANCH",
                    "STATUS"
                ]
            )
            load_boa.clear()
            st.session_state.boa_orig = load_boa()
            st.success("Data saved successfully")
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
                    "STATUS"
                ]
            )
            if ok:
                load_boa.clear()
                st.session_state.boa_orig = load_boa()
                st.success("Delete undone")
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
        cursor.execute("SELECT * FROM FIRE_SAFETY")
        data = cursor.fetchall()
        columns = [
            "ID",
            "COMPANY",
            "AREA",
            "BRANCH",
            "FSIC_VALIDITY",
            "FSIC_FEE",
            "REMARKS"
        ]
        return pd.DataFrame(data, columns=columns)

    df = load_fire()

    if "fire_orig" not in st.session_state:
        st.session_state.fire_orig = df.copy()

    edited_df = st.data_editor(
        df,
        num_rows="dynamic",
        use_container_width=True,
        key="fire_editor"
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
                    "REMARKS"
                ],
                update_cols=[
                    "COMPANY",
                    "AREA",
                    "BRANCH",
                    "FSIC_VALIDITY",
                    "FSIC_FEE",
                    "REMARKS"
                ]
            )
            load_fire.clear()
            st.session_state.fire_orig = load_fire()
            st.success("Data saved successfully")
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
                    "REMARKS"
                ]
            )
            if ok:
                load_fire.clear()
                st.session_state.fire_orig = load_fire()
                st.success("Delete undone")
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
        st.subheader(f"{st.session_state.company} Branch List")

        @st.cache_data(ttl=30, show_spinner=False)
        def load_branch_tin(company):
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
                "DATE_OF_CLOSURE"
            ]
            return pd.DataFrame(data, columns=columns)

        df = load_branch_tin(st.session_state.company)

        key_prefix = f"branch_tin_{st.session_state.company.lower()}"

        orig_key = f"{key_prefix}_orig"
        if orig_key not in st.session_state:
            st.session_state[orig_key] = df.copy()

        edited_df = st.data_editor(
            df,
            num_rows="dynamic",
            use_container_width=True,
            key=f"{key_prefix}_editor"
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
                        "DATE_OF_CLOSURE"
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
                        "DATE_OF_CLOSURE"
                    ]
                )
                load_branch_tin.clear()
                st.session_state[orig_key] = load_branch_tin(st.session_state.company)
                st.success("Data saved successfully")
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
                        "DATE_OF_CLOSURE"
                    ]
                )
                if ok:
                    load_branch_tin.clear()
                    st.session_state[orig_key] = load_branch_tin(st.session_state.company)
                    st.success("Delete undone")
                    safe_rerun()
                else:
                    st.info("Nothing to undo")

        with col_refresh:
            if st.button("🔄 Refresh", key=f"{key_prefix}_refresh"):
                load_branch_tin.clear()
                st.session_state[orig_key] = load_branch_tin(st.session_state.company)
                safe_rerun()

# ---------------------------------------------------
# BUSINESS PERMITS (UPDATE-ONLY, NO DELETE)
# ---------------------------------------------------

def business_permits():
    st.title("🏢 Business Permits Report")

    tab1, tab2, tab3, tab4 = st.tabs([
        "Overview",
        "Business Permit",
        "Brgy Permit",
        "Other Fees for Renew"
    ])

    # OVERVIEW
    with tab1:
        st.subheader("Overview")

        cursor.execute("SELECT * FROM BUSINESS_PERMIT_OVERVIEW")
        data = cursor.fetchall()
        columns = [
            "COMPANY",
            "AREA",
            "BRANCH",
            "DEADLINE",
            "DEADLINE_EXTENSION",
            "BRGY_PERMIT_2025",
            "BUSINESS_PERMIT_2025",
            "SEC_CERT",
            "GROSS_SALES_CERT"
        ]
        df = pd.DataFrame(data, columns=columns)
        st.dataframe(df, use_container_width=True)

    # BUSINESS PERMIT
    with tab2:
        st.subheader("Business Permit")

        cursor.execute("SELECT * FROM BUSINESS_PERMIT")
        data = cursor.fetchall()
        columns = [
            "COMPANY",
            "AREA",
            "BRANCH",
            "CAF",
            "PAYEE",
            "MODE_OF_PAYMENT",
            "AMOUNT",
            "ACCOUNTING",
            "TREASURY",
            "STATUS",
            "LIQUIDATION",
            "SUBMISSION_TO_ACCOUNTING"
        ]
        df = pd.DataFrame(data, columns=columns)

        edited_df = st.data_editor(
            df,
            num_rows="dynamic",
            use_container_width=True,
            key="business_permit_editor"
        )

        if st.button("💾 Save Business Permit", key="save_business_permit"):
            for _, row in edited_df.iterrows():
                cursor.execute(
                    """
                    UPDATE BUSINESS_PERMIT
                    SET
                        CAF=%s,
                        PAYEE=%s,
                        MODE_OF_PAYMENT=%s,
                        AMOUNT=%s,
                        ACCOUNTING=%s,
                        TREASURY=%s,
                        STATUS=%s,
                        LIQUIDATION=%s,
                        SUBMISSION_TO_ACCOUNTING=%s
                    WHERE COMPANY=%s AND AREA=%s AND BRANCH=%s
                    """,
                    (
                        row["CAF"],
                        row["PAYEE"],
                        row["MODE_OF_PAYMENT"],
                        row["AMOUNT"],
                        row["ACCOUNTING"],
                        row["TREASURY"],
                        row["STATUS"],
                        row["LIQUIDATION"],
                        row["SUBMISSION_TO_ACCOUNTING"],
                        row["COMPANY"],
                        row["AREA"],
                        row["BRANCH"]
                    )
                )
            conn.commit()
            st.success("Business Permit Updated")

    # BRGY PERMIT
    with tab3:
        st.subheader("Barangay Permit")

        cursor.execute("SELECT * FROM BRGY_PERMIT")
        data = cursor.fetchall()
        columns = [
            "COMPANY",
            "AREA",
            "BRANCH",
            "CAF",
            "PAYEE",
            "MODE_OF_PAYMENT",
            "AMOUNT",
            "ACCOUNTING",
            "TREASURY",
            "STATUS",
            "LIQUIDATION",
            "SUBMISSION_TO_ACCOUNTING"
        ]
        df = pd.DataFrame(data, columns=columns)

        edited_df = st.data_editor(
            df,
            num_rows="dynamic",
            use_container_width=True,
            key="brgy_permit_editor"
        )

        if st.button("💾 Save Brgy Permit", key="save_brgy_permit"):
            for _, row in edited_df.iterrows():
                cursor.execute(
                    """
                    UPDATE BRGY_PERMIT
                    SET
                        CAF=%s,
                        PAYEE=%s,
                        MODE_OF_PAYMENT=%s,
                        AMOUNT=%s,
                        ACCOUNTING=%s,
                        TREASURY=%s,
                        STATUS=%s,
                        LIQUIDATION=%s,
                        SUBMISSION_TO_ACCOUNTING=%s
                    WHERE COMPANY=%s AND AREA=%s AND BRANCH=%s
                    """,
                    (
                        row["CAF"],
                        row["PAYEE"],
                        row["MODE_OF_PAYMENT"],
                        row["AMOUNT"],
                        row["ACCOUNTING"],
                        row["TREASURY"],
                        row["STATUS"],
                        row["LIQUIDATION"],
                        row["SUBMISSION_TO_ACCOUNTING"],
                        row["COMPANY"],
                        row["AREA"],
                        row["BRANCH"]
                    )
                )
            conn.commit()
            st.success("Barangay Permit Updated")

    # OTHER FEES
    with tab4:
        st.subheader("Other Fees for Renew")

        cursor.execute("SELECT * FROM OTHER_FEES_RENEW")
        data = cursor.fetchall()
        columns = [
            "COMPANY",
            "AREA",
            "BRANCH",
            "CAF",
            "PAYEE",
            "MODE_OF_PAYMENT",
            "AMOUNT",
            "ACCOUNTING",
            "TREASURY",
            "STATUS",
            "LIQUIDATION",
            "SUBMISSION_TO_ACCOUNTING"
        ]
        df = pd.DataFrame(data, columns=columns)

        edited_df = st.data_editor(
            df,
            num_rows="dynamic",
            use_container_width=True,
            key="other_fees_editor"
        )

        if st.button("💾 Save Other Fees", key="save_other_fees"):
            for _, row in edited_df.iterrows():
                cursor.execute(
                    """
                    UPDATE OTHER_FEES_RENEW
                    SET
                        CAF=%s,
                        PAYEE=%s,
                        MODE_OF_PAYMENT=%s,
                        AMOUNT=%s,
                        ACCOUNTING=%s,
                        TREASURY=%s,
                        STATUS=%s,
                        LIQUIDATION=%s,
                        SUBMISSION_TO_ACCOUNTING=%s
                    WHERE COMPANY=%s AND AREA=%s AND BRANCH=%s
                    """,
                    (
                        row["CAF"],
                        row["PAYEE"],
                        row["MODE_OF_PAYMENT"],
                        row["AMOUNT"],
                        row["ACCOUNTING"],
                        row["TREASURY"],
                        row["STATUS"],
                        row["LIQUIDATION"],
                        row["SUBMISSION_TO_ACCOUNTING"],
                        row["COMPANY"],
                        row["AREA"],
                        row["BRANCH"]
                    )
                )
            conn.commit()
            st.success("Other Fees Updated")

# ---------------------------------------------------
# TAX MAPPED
# ---------------------------------------------------

def tax_mapped():
    st.title("🏷 TAX MAPPED Report")

    @st.cache_data(ttl=30, show_spinner=False)
    def load_tax_mapped():
        cursor.execute("SELECT * FROM TAX_MAPPED")
        data = cursor.fetchall()
        columns = [
            "ID",
            "AREA",
            "BRANCH",
            "DATE_TAX_MAPPED",
            "BIR_REMARKS",
            "STICKER_IMAGE"
        ]
        return pd.DataFrame(data, columns=columns)

    df = load_tax_mapped()

    if "tax_mapped_orig" not in st.session_state:
        st.session_state.tax_mapped_orig = df.copy()

    edited_df = st.data_editor(
        df,
        num_rows="dynamic",
        use_container_width=True,
        key="tax_mapped_editor"
    )

    col_save, col_undo, col_refresh = st.columns(3)

    with col_save:
        if st.button("💾 Save Table", key="tax_mapped_save"):
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
                    "BIR_REMARKS"
                ],
                update_cols=[
                    "AREA",
                    "BRANCH",
                    "DATE_TAX_MAPPED",
                    "BIR_REMARKS"
                ]
            )
            load_tax_mapped.clear()
            st.session_state.tax_mapped_orig = load_tax_mapped()
            st.success("Table Updated")
            safe_rerun()

    with col_undo:
        if st.button("↩ Undo last delete", key="tax_mapped_undo"):
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
                    "STICKER_IMAGE"
                ]
            )
            if ok:
                load_tax_mapped.clear()
                st.session_state.tax_mapped_orig = load_tax_mapped()
                st.success("Delete undone")
                safe_rerun()
            else:
                st.info("Nothing to undo")

    with col_refresh:
        if st.button("🔄 Refresh", key="tax_mapped_refresh"):
            load_tax_mapped.clear()
            st.session_state.tax_mapped_orig = load_tax_mapped()
            safe_rerun()

    # IMAGE UPLOAD
    st.subheader("Upload Tax Mapped Sticker (Optional)")

    row_id = st.number_input("Enter Row ID", step=1)
    uploaded_file = st.file_uploader("Upload Sticker Image")

    if uploaded_file is not None:
        st.image(uploaded_file, width=250)

        file_path = f"images/{uploaded_file.name}"
        with open(file_path, "wb") as f:
            f.write(uploaded_file.getbuffer())

        cursor.execute(
            """
            UPDATE TAX_MAPPED
            SET STICKER_IMAGE = %s
            WHERE ID = %s
            """,
            (file_path, row_id)
        )
        conn.commit()
        st.success("Sticker uploaded successfully")

# ---------------------------------------------------
# Secretary Certificates Page
# ---------------------------------------------------

def secretary_certificates():
    st.title("📄 Secretary Certificates Compliance")

    @st.cache_data(ttl=30, show_spinner=False)
    def load_sec():
        cursor.execute("""
            SELECT
                ID, AREA, MONTH_YEAR, BRANCH, REMARKS,
                DATE_FORWARDED, STATUS, DATE_RECEIVED, FINAL_STATUS
            FROM SECRETARY_CERTIFICATES
        """)
        data = cursor.fetchall()
        columns = [
            "ID","AREA","MONTH_YEAR","BRANCH","REMARKS",
            "DATE_FORWARDED","STATUS","DATE_RECEIVED","FINAL_STATUS"
        ]
        return pd.DataFrame(data, columns=columns)

    df = load_sec()

    if "sec_orig" not in st.session_state:
        st.session_state.sec_orig = df.copy()

    edited_df = st.data_editor(
        df,
        num_rows="dynamic",
        use_container_width=True,
        key="sec_editor"
    )

    col_save, col_undo, col_refresh = st.columns(3)

    # SAVE
    with col_save:
        if st.button("💾 Save Changes", key="sec_save"):
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
                    "FINAL_STATUS"
                ],
                update_cols=[
                    "AREA",
                    "MONTH_YEAR",
                    "BRANCH",
                    "REMARKS",
                    "DATE_FORWARDED",
                    "STATUS",
                    "DATE_RECEIVED",
                    "FINAL_STATUS"
                ]
            )
            load_sec.clear()
            st.session_state.sec_orig = load_sec()
            st.success("Data saved successfully")
            safe_rerun()

    # UNDO DELETE
    with col_undo:
        if st.button("↩ Undo last delete", key="sec_undo"):
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
                    "FINAL_STATUS"
                ]
            )
            if ok:
                load_sec.clear()
                st.session_state.sec_orig = load_sec()
                st.success("Delete undone")
                safe_rerun()
            else:
                st.info("Nothing to undo")

    # REFRESH
    with col_refresh:
        if st.button("🔄 Refresh", key="sec_refresh"):
            load_sec.clear()
            st.session_state.sec_orig = load_sec()
            safe_rerun()

# ---------------------------------------------------
# DASHBOARD
# ---------------------------------------------------

def dashboard_analytics():
    st.title("📊 Compliance Dashboard")

    powerbi_url = "https://app.powerbi.com/view?r=eyJrIjoiNmZhOGI5NjAtN2FjMC00NGUyLWFjOGUtMmFhYjg4NGY0ZThkIiwidCI6ImRmODY3OWNkLWE4MGUtNDVkOC05OWFjLWM4M2VkN2ZmOTVhMCJ9"

    st.components.v1.iframe(
        powerbi_url,
        height=900,
        width=1600,
        scrolling=True
    )

    # BUSINESS PERMIT STATUS CHART
    cursor.execute("""
    SELECT STATUS, COUNT(*)
    FROM BUSINESS_PERMIT
    GROUP BY STATUS
    """)
    data = cursor.fetchall()
    df = pd.DataFrame(data, columns=["STATUS", "COUNT"])
    fig = px.pie(
        df,
        values="COUNT",
        names="STATUS",
        title="Business Permit Status"
    )
    st.plotly_chart(fig, use_container_width=True)

    st.divider()

    # COMPLIANCE BY AREA
    cursor.execute("""
    SELECT AREA, COUNT(*)
    FROM BRANCH_TIN_ADDRESS
    GROUP BY AREA
    """)
    data = cursor.fetchall()
    df = pd.DataFrame(data, columns=["AREA", "COUNT"])
    fig = px.bar(
        df,
        x="AREA",
        y="COUNT",
        title="Branches by Area"
    )
    st.plotly_chart(fig, use_container_width=True)

    st.divider()

    # PENDING COMPLIANCE TABLE
    st.subheader("Pending Business Permits")

    cursor.execute("""
    SELECT COMPANY, AREA, BRANCH, STATUS
    FROM BUSINESS_PERMIT
    WHERE STATUS!='DONE'
    """)
    data = cursor.fetchall()
    df = pd.DataFrame(data, columns=["COMPANY", "AREA", "BRANCH", "STATUS"])
    st.dataframe(df, use_container_width=True)

def dashboard():
    # auto-refresh every 1s so the clock stays live
   st_autorefresh(interval=3000, key="clock_refresh") 

    live_clock()

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
            "Admin Panel"
        ]
    )

    # log page open
   if "last_log" not in st.session_state:
    log_activity(menu, "OPEN_PAGE")
    st.session_state.last_log = datetime.now()

    if menu == "Dashboard":
        dashboard_analytics()
    elif menu == "ATP Certificates":
        atp_certificates()
    elif menu == "Secretary Certificates":
        secretary_certificates()
    elif menu == "BIR 1906":
        bir_1906_atp()
    elif menu == "Business Permits":
        business_permits()
    elif menu == "BOA Stickers":
        boa_sticker()
    elif menu == "Fire Safety":
        fire_safety()
    elif menu == "Board Resolutions":
        st.title("Board Resolutions Report")
    elif menu == "TIN & Address":
        branch_tin_address()
    elif menu == "AI Compliance Copilot":
        ai_copilot()
    elif menu == "Tax Mapped":
        tax_mapped()
    elif menu == "Admin Panel":
        admin_panel()

    st.sidebar.divider()

    if st.sidebar.button("🚪 Logout"):
        st.session_state.logged_in = False
        st.session_state.page = "login"

# ---------------------------------------------------
# PAGE ROUTING
# ---------------------------------------------------

if not st.session_state.logged_in:
    if st.session_state.page == "login":
        login()
    elif st.session_state.page == "register":
        register()
else:
    dashboard()
