# -*- coding: utf-8 -*-
import streamlit as st
import pandas as pd
from datetime import datetime, date, timedelta
import gspread
from google.oauth2.service_account import Credentials
from google.oauth2.credentials import Credentials as OAuthCredentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
import hashlib
import os
import io

st.set_page_config(page_title="WeiZhe 家教學習平台", page_icon="📚", layout="wide")

# ---------- Google Sheets ----------
@st.cache_resource
def get_gsheet_client():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds_dict = dict(st.secrets["gcp_service_account"])
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    return gspread.authorize(creds)

def get_sheet(name):
    client = get_gsheet_client()
    sheet_id = st.secrets["gcp"]["sheet_id"]
    return client.open_by_key(sheet_id).worksheet(name)

@st.cache_data(ttl=100)
def load_data(sheet_name, columns=None):
    try:
        ws = get_sheet(sheet_name)
        data = ws.get_all_records()
        if not data:
            return pd.DataFrame(columns=columns) if columns else pd.DataFrame()
        return pd.DataFrame(data)
    except Exception as e:
        st.error(f"讀取 {sheet_name} 失敗：{e}")
        return pd.DataFrame(columns=columns) if columns else pd.DataFrame()

def save_data(df, sheet_name):
    try:
        ws = get_sheet(sheet_name)
        ws.clear()
        ws.update([df.columns.tolist()] + df.values.tolist())
        st.cache_data.clear()
    except Exception as e:
        st.error(f"寫入 {sheet_name} 失敗：{e}")

# ---------- Google Drive（OAuth） ----------
@st.cache_resource
def get_drive_service():
    client_id = st.secrets["oauth"]["client_id"]
    client_secret = st.secrets["oauth"]["client_secret"]
    refresh_token = st.secrets["gcp"]["refresh_token"]

    creds = OAuthCredentials(
        None,
        refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=["https://www.googleapis.com/auth/drive"],
    )
    return build("drive", "v3", credentials=creds)

def find_folder(service, name, parent_id=None):
    query = f"name='{name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
    if parent_id:
        query += f" and '{parent_id}' in parents"
    results = service.files().list(q=query, fields="files(id, name)").execute()
    files = results.get("files", [])
    return files[0]["id"] if files else None

def list_files(service, folder_id):
    query = f"'{folder_id}' in parents and trashed=false"
    results = service.files().list(
        q=query,
        fields="files(id, name, mimeType, size, createdTime, webViewLink)",
        orderBy="createdTime desc"
    ).execute()
    return results.get("files", [])

def upload_file(service, folder_id, filename, file_bytes, mime_type):
    file_metadata = {
        "name": filename,
        "parents": [folder_id],
    }
    media = MediaIoBaseUpload(io.BytesIO(file_bytes), mimetype=mime_type, resumable=False)
    file = service.files().create(body=file_metadata, media_body=media, fields="id").execute()
    return file["id"]

def download_file(service, file_id):
    request = service.files().get_media(fileId=file_id)
    return request.execute()

# ---------- 工具 ----------
def parse_date_safe(series):
    result = pd.to_datetime(series, errors="coerce", format="%Y-%m-%d")
    mask = result.isna()
    if mask.any():
        result2 = pd.to_datetime(series[mask], errors="coerce", format="%Y/%m/%d")
        result.loc[mask] = result2
    return result

def hash_password(password, salt):
    return hashlib.sha256((password + salt).encode()).hexdigest()

def verify_password(password, salt, password_hash):
    return hash_password(password, salt) == password_hash

# ---------- Session ----------
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
if "user" not in st.session_state:
    st.session_state.user = None
if "must_change_pw" not in st.session_state:
    st.session_state.must_change_pw = False

REQ_COLS = ["id", "student_id", "type", "original_lesson_id", "requested_date",
            "requested_start", "requested_end", "reason", "status", "created_at", "teacher_note"]
PROG_COLS = ["id", "student_id", "lesson_id", "date", "content", "homework", "note", "created_at"]

accounts = load_data("accounts")
students = load_data("students")
lessons = load_data("lessons")
requests_df = load_data("requests", REQ_COLS)
progress_df = load_data("progress", PROG_COLS)

# ---------- 登入 ----------
def login_page():
    st.title("📚 WeiZhe 家教學習平台")
    st.markdown("---")

    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.subheader("🔐 登入")
        with st.form("login_form"):
            username = st.text_input("學號", placeholder="例如：S001")
            password = st.text_input("密碼", type="password")
            submitted = st.form_submit_button("登入", use_container_width=True)

            if submitted:
                if accounts.empty:
                    st.error("帳號系統尚未設定")
                else:
                    user_row = accounts[accounts["username"].astype(str) == username.strip()]
                    if user_row.empty:
                        st.error("❌ 學號或密碼錯誤")
                    else:
                        user = user_row.iloc[0]
                        salt = str(user.get("salt", ""))
                        pwd_hash = str(user.get("password_hash", ""))
                        if verify_password(password, salt, pwd_hash):
                            st.session_state.logged_in = True
                            st.session_state.user = user.to_dict()
                            must_change = str(user.get("must_change_pw", "")).upper() in ["TRUE", "1", "YES"]
                            st.session_state.must_change_pw = must_change
                            st.rerun()
                        else:
                            st.error("❌ 學號或密碼錯誤")

# ---------- 改密碼 ----------
def change_password_page():
    st.title("🔑 首次登入請更改密碼")
    st.markdown("---")

    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        with st.form("change_pw_form"):
            new_pw = st.text_input("新密碼", type="password")
            confirm_pw = st.text_input("確認新密碼", type="password")
            if st.form_submit_button("確認更改", use_container_width=True):
                if len(new_pw) < 6:
                    st.error("密碼至少 6 個字元")
                elif new_pw != confirm_pw:
                    st.error("兩次密碼不一致")
                else:
                    user = st.session_state.user
                    username = user["username"]
                    new_salt = os.urandom(16).hex()
                    new_hash = hash_password(new_pw, new_salt)
                    accounts.loc[accounts["username"] == username, "salt"] = new_salt
                    accounts.loc[accounts["username"] == username, "password_hash"] = new_hash
                    accounts.loc[accounts["username"] == username, "must_change_pw"] = "FALSE"
                    save_data(accounts, "accounts")
                    st.session_state.must_change_pw = False
                    st.success("✅ 密碼已更新！")
                    st.rerun()

# ---------- 首頁 ----------
def home_page():
    user = st.session_state.user
    student_id = user.get("student_id")
    name = user.get("name", "同學")

    st.title(f"👋 嗨，{name}")

    st.subheader("📅 下次上課")
    if lessons.empty or "student_id" not in lessons.columns:
        st.info("目前沒有排定的課程")
    else:
        my_lessons = lessons[lessons["student_id"].astype(str) == str(student_id)].copy()
        if my_lessons.empty:
            st.info("目前沒有排定的課程")
        else:
            my_lessons["date"] = parse_date_safe(my_lessons["date"]).dt.date
            today = date.today()
            upcoming = my_lessons[
                (my_lessons["date"] >= today) &
                (my_lessons["status"].astype(str) != "已補課")
            ].sort_values(["date", "start"])
            if upcoming.empty:
                st.info("目前沒有即將到來的課程")
            else:
                next_lesson = upcoming.iloc[0]
                wd = ['一', '二', '三', '四', '五', '六', '日'][next_lesson['date'].weekday()]
                st.success(f"**{next_lesson['date']}（{wd}）** {next_lesson['start']} - {next_lesson['end']}")
                st.caption(f"類型：{next_lesson.get('type', '正課')}")

    st.markdown("---")
    st.subheader("📝 我的申請")
    my_requests = requests_df[requests_df["student_id"].astype(str) == str(student_id)] if not requests_df.empty else pd.DataFrame()
    if my_requests.empty:
        st.info("目前沒有申請紀錄")
    else:
        view = my_requests[["type", "requested_date", "requested_start", "status", "teacher_note"]].copy()
        view.columns = ["類型", "日期", "開始時間", "狀態", "老師回覆"]
        st.dataframe(view, use_container_width=True)

# ---------- 請假/補課 ----------
def request_page():
    user = st.session_state.user
    student_id = user.get("student_id")
    st.title("📝 請假 / 補課申請")

    if lessons.empty or "student_id" not in lessons.columns:
        st.warning("目前沒有可申請的課程")
        return

    my_lessons = lessons[lessons["student_id"].astype(str) == str(student_id)].copy()
    if my_lessons.empty:
        st.warning("目前沒有可申請的課程")
        return

    my_lessons["date"] = parse_date_safe(my_lessons["date"]).dt.date

    st.subheader("🛌 申請請假")
    with st.form("leave_form"):
        today = date.today()
        future = my_lessons[
            (my_lessons["date"] >= today) &
            (my_lessons["status"].astype(str) == "已排定")
        ].sort_values("date")

        if future.empty:
            st.info("目前沒有可請假的課程")
        else:
            options = {
                f"{row['date']} {row['start']}-{row['end']}（{row.get('type', '正課')}）": row["id"]
                for _, row in future.iterrows()
            }
            selected = st.selectbox("選擇要請假的課程", list(options.keys()))
            reason = st.text_area("請假原因")
            if st.form_submit_button("送出請假申請"):
                if not reason.strip():
                    st.error("請填寫請假原因")
                else:
                    lesson_id = options[selected]
                    new_id = int(requests_df["id"].max() + 1) if not requests_df.empty and "id" in requests_df.columns else 1
                    new_row = pd.DataFrame([[
                        new_id, student_id, "請假", lesson_id, "", "", "", reason, "待處理",
                        datetime.now().strftime("%Y-%m-%d %H:%M"), ""
                    ]], columns=REQ_COLS)
                    requests_df_new = pd.concat([requests_df, new_row], ignore_index=True)
                    save_data(requests_df_new, "requests")
                    st.success("✅ 請假申請已送出")
                    st.rerun()

    st.markdown("---")
    st.subheader("📋 我的申請紀錄")
    my_requests = requests_df[requests_df["student_id"].astype(str) == str(student_id)] if not requests_df.empty else pd.DataFrame()

    if my_requests.empty:
        st.info("目前沒有申請紀錄")
    else:
        lesson_map = lessons.set_index("id").to_dict("index") if not lessons.empty else {}
        for idx, row in my_requests.iterrows():
            with st.container():
                col1, col2, col3 = st.columns([3, 2, 2])
                with col1:
                    st.markdown(f"**{row['type']}**")
                    if row["type"] == "請假":
                        orig_id = row.get("original_lesson_id")
                        if str(orig_id).isdigit() and int(orig_id) in lesson_map:
                            orig = lesson_map[int(orig_id)]
                            st.caption(f"📅 原課程：{orig.get('date')} {orig.get('start')}-{orig.get('end')}")
                    st.caption(f"原因：{row.get('reason', '')}")
                    st.caption(f"送出時間：{row.get('created_at', '')}")
                with col2:
                    status = str(row.get("status", ""))
                    if status == "待處理":
                        st.warning("⏳ 待處理")
                    elif status == "已同意":
                        st.success("✅ 已同意")
                    elif status == "已拒絕":
                        st.error("❌ 已拒絕")
                    elif status == "已取消":
                        st.info("🚫 已取消")
                with col3:
                    if str(row.get("status", "")) == "待處理":
                        if st.button("取消申請", key=f"cancel_{row['id']}"):
                            requests_df.loc[requests_df["id"] == row["id"], "status"] = "已取消"
                            save_data(requests_df, "requests")
                            st.success("已取消")
                            st.rerun()
                    if row.get("teacher_note"):
                        st.caption(f"老師：{row['teacher_note']}")
                st.markdown("---")

# ---------- 我的課表 ----------
def my_schedule_page():
    user = st.session_state.user
    student_id = user.get("student_id")
    st.title("📅 我的課表")

    if lessons.empty or "student_id" not in lessons.columns:
        st.info("目前沒有課程")
        return

    my_lessons = lessons[lessons["student_id"].astype(str) == str(student_id)].copy()
    if my_lessons.empty:
        st.info("目前沒有課程")
        return

    my_lessons["date"] = parse_date_safe(my_lessons["date"])
    my_lessons = my_lessons.dropna(subset=["date"]).sort_values(["date", "start"])

    today = date.today()
    scope = st.radio("顯示範圍", ["未來課程", "過去課程", "全部"], horizontal=True)
    if scope == "未來課程":
        my_lessons = my_lessons[my_lessons["date"].dt.date >= today]
    elif scope == "過去課程":
        my_lessons = my_lessons[my_lessons["date"].dt.date < today]

    if my_lessons.empty:
        st.info(f"沒有{scope}")
        return

    st.markdown("---")
    def status_icon(s):
        s = str(s)
        if s == "已排定": return "✅"
        elif s == "待補課": return "🔄"
        elif s == "已補課": return "✔️"
        return "❓"

    current_month = None
    for _, row in my_lessons.iterrows():
        d = row["date"].date()
        month_str = d.strftime("%Y年%m月")
        if month_str != current_month:
            st.subheader(f"📅 {month_str}")
            current_month = month_str

        wd = ['一', '二', '三', '四', '五', '六', '日'][d.weekday()]
        status = row.get("status", "")
        with st.container():
            col1, col2 = st.columns([3, 2])
            with col1:
                st.markdown(f"**{d.strftime('%m/%d')}（週{wd}）**　`{row['start']} - {row['end']}`　**{row.get('type', '正課')}**")
                if row.get("note"):
                    st.caption(f"📝 {row['note']}")
            with col2:
                st.markdown(f"{status_icon(status)} {status}")
            st.markdown("")

# ---------- 學習進度 ----------
def my_progress_page():
    user = st.session_state.user
    student_id = user.get("student_id")
    st.title("📊 我的學習進度")

    if progress_df.empty:
        st.info("目前沒有進度紀錄")
        return

    my_progress = progress_df[progress_df["student_id"].astype(str) == str(student_id)].copy()
    if my_progress.empty:
        st.info("目前沒有進度紀錄")
        return

    my_progress["date_parsed"] = parse_date_safe(my_progress["date"])
    my_progress = my_progress.dropna(subset=["date_parsed"]).sort_values("date_parsed", ascending=False)

    st.caption(f"共 {len(my_progress)} 筆紀錄")

    for _, row in my_progress.iterrows():
        d = row["date_parsed"].date()
        wd = ['一', '二', '三', '四', '五', '六', '日'][d.weekday()]
        with st.container():
            st.markdown(f"## 📅 {d.strftime('%Y-%m-%d')}（週{wd}）")
            st.markdown(f"**📖 上課內容：**")
            st.markdown(f"{row.get('content', '')}")
            if row.get("homework"):
                st.markdown(f"**📝 作業：**")
                st.markdown(f"{row['homework']}")
            if row.get("note"):
                st.caption(f"💬 老師備註：{row['note']}")
            st.markdown("---")

# ---------- 我的檔案 ----------
def my_files_page():
    user = st.session_state.user
    student_id = user.get("student_id")
    name = user.get("name", "")

    st.title("📁 我的檔案")
    st.caption("教材、講義、作業都在這裡")

    try:
        service = get_drive_service()
        root_id = st.secrets["gcp"]["drive_folder_id"]
        student_folder_name = f"S{int(student_id):03d}_{name}"

        # 找學生資料夾
        folder_id = find_folder(service, student_folder_name, root_id)

        if not folder_id:
            st.info(f"📁 尚未建立資料夾（{student_folder_name}）")
            st.caption("等老師上傳第一個檔案後，資料夾就會自動出現")
            return

        # 列出所有子資料夾（分類）
        subfolders = service.files().list(
            q=f"'{folder_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false",
            fields="files(id, name)"
        ).execute().get("files", [])

        st.markdown("---")

        # 上傳作業區
        st.subheader("📤 上傳作業")
        with st.form("upload_homework"):
            uploaded_file = st.file_uploader("選擇檔案", key="student_upload")
            if st.form_submit_button("上傳作業"):
                if uploaded_file is None:
                    st.error("請先選擇檔案")
                else:
                    try:
                        # 找或建立「作業」資料夾
                        hw_folder = None
                        for sf in subfolders:
                            if sf["name"] == "作業":
                                hw_folder = sf["id"]
                                break
                        if not hw_folder:
                            metadata = {
                                "name": "作業",
                                "mimeType": "application/vnd.google-apps.folder",
                                "parents": [folder_id]
                            }
                            hw_folder = service.files().create(body=metadata, fields="id").execute()["id"]

                        upload_file(
                            service,
                            hw_folder,
                            uploaded_file.name,
                            uploaded_file.getvalue(),
                            uploaded_file.type or "application/octet-stream"
                        )
                        st.success(f"✅ 已上傳：{uploaded_file.name}")
                        st.rerun()
                    except Exception as e:
                        st.error(f"上傳失敗：{e}")

        st.markdown("---")
        st.subheader("📂 我的檔案")

        if not subfolders:
            st.info("目前沒有檔案")
            return

        for sf in subfolders:
            with st.expander(f"📁 {sf['name']}", expanded=True):
                files = list_files(service, sf["id"])
                if not files:
                    st.caption("（空）")
                else:
                    for f in files:
                        c1, c2, c3 = st.columns([4, 1, 1])
                        with c1:
                            size_kb = int(f.get("size", 0)) / 1024 if f.get("size") else 0
                            st.markdown(f"📄 **{f['name']}** （{size_kb:.1f} KB）")
                        with c2:
                            st.markdown(f"[🔗 開啟]({f.get('webViewLink', '#')})")
                        with c3:
                            try:
                                file_bytes = download_file(service, f["id"])
                                st.download_button(
                                    "⬇️",
                                    data=file_bytes,
                                    file_name=f["name"],
                                    key=f"dl_{f['id']}"
                                )
                            except:
                                st.caption("—")

    except Exception as e:
        st.error(f"Drive 連線失敗：{e}")

# ---------- 主程式 ----------
if not st.session_state.logged_in:
    login_page()
elif st.session_state.must_change_pw:
    change_password_page()
else:
    with st.sidebar:
        st.markdown(f"### 👤 {st.session_state.user.get('name', '')}")
        st.caption(f"學號：{st.session_state.user.get('username', '')}")
        st.markdown("---")

        menu = st.radio("功能選單", [
            "🏠 首頁",
            "📅 我的課表",
            "📊 學習進度",
            "📝 請假/補課",
            "📁 我的檔案",
        ])

        st.markdown("---")
        if st.button("🚪 登出", use_container_width=True):
            st.session_state.logged_in = False
            st.session_state.user = None
            st.session_state.must_change_pw = False
            st.rerun()

    if menu == "🏠 首頁":
        home_page()
    elif menu == "📅 我的課表":
        my_schedule_page()
    elif menu == "📊 學習進度":
        my_progress_page()
    elif menu == "📝 請假/補課":
        request_page()
    elif menu == "📁 我的檔案":
        my_files_page()
