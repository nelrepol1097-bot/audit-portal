import streamlit as st
import snowflake.connector
from datetime import datetime
import requests
from streamlit_lottie import st_lottie
import pandas as pd
import plotly.express as px
from openai import OpenAI


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
    conn = snowflake.connector.connect(
        user="jmcasaria",
        password = st.secrets["snowflake"]["password"],
        account="NSXAGQQ-WJ05543",
        warehouse="COMPUTE_WH",
        database="CFB_ANALYST_JAKE_DB",
        schema="PUBLIC",
        role="ANALYST_JAKE_ROLE"
    )
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
# LIVE CLOCK
# ---------------------------------------------------

def live_clock():

    now = datetime.now().strftime("%A, %B %d %Y  |  %H:%M:%S")

    st.markdown(
        f"""
        <div class="clock-box">
        {now}
        </div>
        """,
        unsafe_allow_html=True
    )

# ---------------------------------------------------
# LOGIN PAGE
# ---------------------------------------------------

def login():

    live_clock()

    col1, col2 = st.columns([1.2,1])

    with col1:
        st_lottie(lottie_login, height=420)

    with col2:

        st.markdown('<div class="login-card">', unsafe_allow_html=True)

        st.title("🔎 AUDIT Data Portal")

        email = st.text_input("Email")
        password = st.text_input("Password", type="password")

        if st.button("Login"):

            query = """
            SELECT STATUS
            FROM USERS
            WHERE EMAIL=%s AND PASSWORD=%s
            """

            cursor.execute(query,(email,password))
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
                        (email,login_time)
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

# ---------------------------------------------------
# REGISTER PAGE
# ---------------------------------------------------

def register():

    live_clock()

    st.title("Create Account")

    name = st.text_input("Full Name")
    email = st.text_input("Email")
    password = st.text_input("Password", type="password")

    if st.button("Register"):

        query = """
        INSERT INTO USERS
        (NAME,EMAIL,PASSWORD,STATUS,DATE_REGISTERED)
        VALUES (%s,%s,%s,'PENDING',%s)
        """

        cursor.execute(query,(name,email,password,datetime.now()))
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

            col1,col2 = st.columns(2)

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

    cursor.execute("SELECT * FROM ATP_Certificates")

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

    df = pd.DataFrame(data, columns=columns)

    edited_df = st.data_editor(
        df,
        num_rows="dynamic",
        use_container_width=True
    )

    if st.button("💾 Save Changes"):

     for index, row in edited_df.iterrows():

        # INSERT if new row
        if pd.isna(row["ID"]):

            cursor.execute("""
            INSERT INTO ATP_CERTIFICATES
            (COMPANY,AREA,BRANCH,SERVICE_INVOICE_SERIAL_NO,
             BIR_DATE_RECEIVED_FIRST_STAMP,
             BIR_DATE_RECEIVED_LAST_STAMP,
             STATUS)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                row["COMPANY"],
                row["AREA"],
                row["BRANCH"],
                row["SERVICE_INVOICE_SERIAL_NO"],
                row["BIR_DATE_RECEIVED_FIRST_STAMP"],
                row["BIR_DATE_RECEIVED_LAST_STAMP"],
                row["STATUS"]
            ))

        # UPDATE existing row
        else:

            cursor.execute("""
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
            (
                row["COMPANY"],
                row["AREA"],
                row["BRANCH"],
                row["SERVICE_INVOICE_SERIAL_NO"],
                row["BIR_DATE_RECEIVED_FIRST_STAMP"],
                row["BIR_DATE_RECEIVED_LAST_STAMP"],
                row["STATUS"],
                row["ID"]
            ))

    conn.commit()
    st.success("Data saved successfully")
# ---------------------------------------------------
# BIR 1906 1TP
# ---------------------------------------------------

def bir_1906_atp():

    st.title("📄 BIR 1906 ATP")

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

    df = pd.DataFrame(data, columns=columns)

    edited_df = st.data_editor(
        df,
        num_rows="dynamic",
        use_container_width=True
    )

    if st.button("💾 Save Changes"):

     for index, row in edited_df.iterrows():

        # INSERT new row
        if pd.isna(row["ID"]):

            cursor.execute("""
            INSERT INTO BIR_1906_ATP
            (COMPANY,BRANCH,SERVICE_INVOICE_SERIAL_NO,BIR_DATE_RECEIVED,STATUS)
            VALUES (%s,%s,%s,%s,%s)
            """,
            (
                row["COMPANY"],
                row["BRANCH"],
                row["SERVICE_INVOICE_SERIAL_NO"],
                row["BIR_DATE_RECEIVED"],
                row["STATUS"]
            ))

        # UPDATE existing row
        else:

            cursor.execute("""
            UPDATE BIR_1906_ATP
            SET
                COMPANY=%s,
                BRANCH=%s,
                SERVICE_INVOICE_SERIAL_NO=%s,
                BIR_DATE_RECEIVED=%s,
                STATUS=%s
            WHERE ID=%s
            """,
            (
                row["COMPANY"],
                row["BRANCH"],
                row["SERVICE_INVOICE_SERIAL_NO"],
                row["BIR_DATE_RECEIVED"],
                row["STATUS"],
                row["ID"]
            ))

    conn.commit()

    st.success("Data saved successfully")
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

        SECRETARY_CERTIFICATE(COMPANY, AREA, BRANCH, STATUS)

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

        except:
            st.error("Query failed")
# ---------------------------------------------------
# BOA STICKER
# ---------------------------------------------------

def boa_sticker():

    st.title("📄 BOA Sticker")

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

    df = pd.DataFrame(data, columns=columns)

    edited_df = st.data_editor(
        df,
        num_rows="dynamic",
        use_container_width=True
    )

    if st.button("💾 Save Changes"):

     for index, row in edited_df.iterrows():

        # INSERT new row
        if pd.isna(row["ID"]):

            cursor.execute("""
            INSERT INTO BOA_STICKER
            (COMPANY,BRANCH,TIN,BOOK_TO_REGISTER,VOLUME_NUMBER,DATE_FORWARDED_TO_BRANCH,STATUS)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                row["COMPANY"],
                row["BRANCH"],
                row["TIN"],
                row["BOOK_TO_REGISTER"],
                row["VOLUME_NUMBER"],
                row["DATE_FORWARDED_TO_BRANCH"],
                row["STATUS"]
            ))

        # UPDATE existing row
        else:

            cursor.execute("""
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
            (
                row["COMPANY"],
                row["BRANCH"],
                row["TIN"],
                row["BOOK_TO_REGISTER"],
                row["VOLUME_NUMBER"],
                row["DATE_FORWARDED_TO_BRANCH"],
                row["STATUS"],
                row["ID"]
            ))

    conn.commit()
    st.success("Data saved successfully")

# ---------------------------------------------------
# FIRE SAFETY
# ---------------------------------------------------

def fire_safety():

    st.title("📄 Fire Safety")

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

    df = pd.DataFrame(data, columns=columns)

    edited_df = st.data_editor(
        df,
        num_rows="dynamic",
        use_container_width=True
    )

    if st.button("💾 Save Changes"):

     for index, row in edited_df.iterrows():

        # INSERT new row
        if pd.isna(row["ID"]):

            cursor.execute("""
            INSERT INTO FIRE_SAFETY
            (COMPANY,AREA,BRANCH,FSIC_VALIDITY,FSIC_FEE,REMARKS)
            VALUES (%s,%s,%s,%s,%s,%s)
            """,
            (
                row["COMPANY"],
                row["AREA"],
                row["BRANCH"],
                row["FSIC_VALIDITY"],
                row["FSIC_FEE"],
                row["REMARKS"]
            ))

        # UPDATE existing row
        else:

            cursor.execute("""
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
            (
                row["COMPANY"],
                row["AREA"],
                row["BRANCH"],
                row["FSIC_VALIDITY"],
                row["FSIC_FEE"],
                row["REMARKS"],
                row["ID"]
            ))

    conn.commit()
    st.success("Data saved successfully")

def branch_tin_address():

    st.title("🏢 Branch TIN & Address")

    col1,col2,col3 = st.columns(3)

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

        query = """
        SELECT *
        FROM BRANCH_TIN_ADDRESS
        WHERE COMPANY = %s
        """

        cursor.execute(query,(st.session_state.company,))
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

        df = pd.DataFrame(data, columns=columns)

        edited_df = st.data_editor(
            df,
            num_rows="dynamic",
            use_container_width=True
        )

        if st.button("💾 Save Changes"):

         for index, row in edited_df.iterrows():

        # INSERT new row
          if pd.isna(row["ID"]):

            cursor.execute("""
            INSERT INTO BRANCH_TIN_ADDRESS
            (TIN,BRANCH_CODE,RDO,AREA,BRANCH_NAME,UPDATED_ADDRESS,STATUS,DATE_OPEN,DATE_OF_CLOSURE)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                row["TIN"],
                row["BRANCH_CODE"],
                row["RDO"],
                row["AREA"],
                row["BRANCH_NAME"],
                row["UPDATED_ADDRESS"],
                row["STATUS"],
                row["DATE_OPEN"],
                row["DATE_OF_CLOSURE"]
            ))

        # UPDATE existing row
        else:

            cursor.execute("""
            UPDATE BRANCH_TIN_ADDRESS
            SET
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
            (
                row["TIN"],
                row["BRANCH_CODE"],
                row["RDO"],
                row["AREA"],
                row["BRANCH_NAME"],
                row["UPDATED_ADDRESS"],
                row["STATUS"],
                row["DATE_OPEN"],
                row["DATE_OF_CLOSURE"],
                row["ID"]
            ))

    conn.commit()
    st.success("Data saved successfully")

def business_permits():

    st.title("🏢 Business Permits Report")

    tab1, tab2, tab3, tab4 = st.tabs([
        "Overview",
        "Business Permit",
        "Brgy Permit",
        "Other Fees for Renew"
    ])

    # -------------------------
    # OVERVIEW
    # -------------------------

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

    # -------------------------
    # BUSINESS PERMIT
    # -------------------------

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

         for index, row in edited_df.iterrows():

          cursor.execute("""
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
        ))

    conn.commit()
    st.success("Business Permit Updated")

    # -------------------------
    # BRGY PERMIT
    # -------------------------

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

         for index, row in edited_df.iterrows():

          cursor.execute("""
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
        ))

    conn.commit()
    st.success("Barangay Permit Updated")

    # -------------------------
    # OTHER FEES
    # -------------------------

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

         for index, row in edited_df.iterrows():

          cursor.execute("""
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
        ))

    conn.commit()
    st.success("Other Fees Updated")

def tax_mapped():

    st.title("🏷 TAX MAPPED Report")
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

    df = pd.DataFrame(data, columns=columns)

    edited_df = st.data_editor(
        df,
        num_rows="dynamic",
        use_container_width=True,
        key="tax_mapped_editor"
    )

    if st.button("💾 Save Table"):

     for index, row in edited_df.iterrows():

        # INSERT new row
        if pd.isna(row["ID"]):

            cursor.execute("""
            INSERT INTO TAX_MAPPED
            (AREA,BRANCH,DATE_TAX_MAPPED,BIR_REMARKS)
            VALUES (%s,%s,%s,%s)
            """,
            (
                row["AREA"],
                row["BRANCH"],
                row["DATE_TAX_MAPPED"],
                row["BIR_REMARKS"]
            ))

        # UPDATE existing row
        else:

            cursor.execute("""
            UPDATE TAX_MAPPED
            SET
                AREA=%s,
                BRANCH=%s,
                DATE_TAX_MAPPED=%s,
                BIR_REMARKS=%s
            WHERE ID=%s
            """,
            (
                row["AREA"],
                row["BRANCH"],
                row["DATE_TAX_MAPPED"],
                row["BIR_REMARKS"],
                row["ID"]
            ))

    conn.commit()
    st.success("Table Updated")

    # -----------------------------
    # IMAGE UPLOAD (OPTIONAL)
    # -----------------------------

    st.subheader("Upload Tax Mapped Sticker (Optional)")

    row_id = st.number_input("Enter Row ID", step=1)

    uploaded_file = st.file_uploader("Upload Sticker Image")

    if uploaded_file is not None:

        st.image(uploaded_file, width=250)

        file_path = f"images/{uploaded_file.name}"

        with open(file_path, "wb") as f:
            f.write(uploaded_file.getbuffer())

        cursor.execute("""
        UPDATE TAX_MAPPED
        SET STICKER_IMAGE = %s
        WHERE ID = %s
        """,(file_path,row_id))

        conn.commit()

        st.success("Sticker uploaded successfully")
# ---------------------------------------------------
# Secretary Certificates Page
# ---------------------------------------------------

def secretary_certificates():

    st.title("📄 Secretary Certificate Compliance")

    cursor.execute("SELECT * FROM Secretary_Certificate")

    data = cursor.fetchall()

    columns = [
        "ID",
        "AREA",
        "MONTH_YEAR",
        "COMPANY",
        "BRANCH",
        "REMARKS",
        "DATE_FORWARDED",
        "STATUS",
        "DATE_RECEIVED",
        "FINAL_STATUS"
    ]

    df = pd.DataFrame(data, columns=columns)

    edited_df = st.data_editor(
        df,
        num_rows="dynamic",
        use_container_width=True
    )

    if st.button("💾 Save Changes"):

     for index, row in edited_df.iterrows():

        # INSERT new row
        if pd.isna(row["ID"]):

            cursor.execute("""
            INSERT INTO SECRETARY_CERTIFICATES
            (AREA,MONTH_YEAR,COMPANY,BRANCH,REMARKS,DATE_FORWARDED,STATUS,DATE_RECEIVED,FINAL_STATUS)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                row["AREA"],
                row["MONTH_YEAR"],
                row["COMPANY"],
                row["BRANCH"],
                row["REMARKS"],
                row["DATE_FORWARDED"],
                row["STATUS"],
                row["DATE_RECEIVED"],
                row["FINAL_STATUS"]
            ))

        # UPDATE existing row
        else:

            cursor.execute("""
            UPDATE SECRETARY_CERTIFICATES
            SET
                AREA=%s,
                MONTH_YEAR=%s,
                COMPANY=%s,
                BRANCH=%s,
                REMARKS=%s,
                DATE_FORWARDED=%s,
                STATUS=%s,
                DATE_RECEIVED=%s,
                FINAL_STATUS=%s
            WHERE ID=%s
            """,
            (
                row["AREA"],
                row["MONTH_YEAR"],
                row["COMPANY"],
                row["BRANCH"],
                row["REMARKS"],
                row["DATE_FORWARDED"],
                row["STATUS"],
                row["DATE_RECEIVED"],
                row["FINAL_STATUS"],
                row["ID"]
            ))

    conn.commit()
    st.success("Data saved successfully")

# ---------------------------------------------------
# DASHBOARD
# ---------------------------------------------------
def dashboard_analytics():

    st.title("📊 Compliance Dashboard")

    # Power BI Embed Link
    powerbi_url = "https://app.powerbi.com/view?r=eyJrIjoiNmZhOGI5NjAtN2FjMC00NGUyLWFjOGUtMmFhYjg4NGY0ZThkIiwidCI6ImRmODY3OWNkLWE4MGUtNDVkOC05OWFjLWM4M2VkN2ZmOTVhMCJ9"

    st.components.v1.iframe(
        powerbi_url,
        height=900,
        width=1600,
        scrolling=True
    )

    # -------------------------
    # BUSINESS PERMIT STATUS CHART
    # -------------------------

    cursor.execute("""
    SELECT STATUS, COUNT(*)
    FROM BUSINESS_PERMIT
    GROUP BY STATUS
    """)

    data = cursor.fetchall()

    df = pd.DataFrame(data, columns=["STATUS","COUNT"])

    fig = px.pie(
        df,
        values="COUNT",
        names="STATUS",
        title="Business Permit Status"
    )

    st.plotly_chart(fig, use_container_width=True)

    st.divider()

    # -------------------------
    # COMPLIANCE BY AREA
    # -------------------------

    cursor.execute("""
    SELECT AREA, COUNT(*)
    FROM BRANCH_TIN_ADDRESS
    GROUP BY AREA
    """)

    data = cursor.fetchall()

    df = pd.DataFrame(data, columns=["AREA","COUNT"])

    fig = px.bar(
        df,
        x="AREA",
        y="COUNT",
        title="Branches by Area"
    )

    st.plotly_chart(fig, use_container_width=True)

    st.divider()

    # -------------------------
    # PENDING COMPLIANCE TABLE
    # -------------------------

    st.subheader("Pending Business Permits")

    cursor.execute("""
    SELECT COMPANY, AREA, BRANCH, STATUS
    FROM BUSINESS_PERMIT
    WHERE STATUS!='DONE'
    """)

    data = cursor.fetchall()

    df = pd.DataFrame(data, columns=["COMPANY","AREA","BRANCH","STATUS"])

    st.dataframe(df, use_container_width=True)

def dashboard():

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