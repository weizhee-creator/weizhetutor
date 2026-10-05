# -*- coding: utf-8 -*-
import streamlit as st
import pandas as pd
from datetime import datetime, date, timedelta
import gspread
from google.oauth2.service_account import Credentials
import hashlib
import os

# ---------- 設定 ----------
st.set_page_config(page_title="WeiZhe 家教學習平台", page_icon="📚", layout="wide")

# ---------- 連接 Google Sheets ----------
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

@st.cache_data(ttl=5)
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

# ---------- 密碼雜湊 ----------
def hash_password(password, salt):
    return hashlib.sha256((password + salt).encode()).hexdigest()

def verify_password(password, salt, password_hash):
    return hash_password(password, salt) == password_hash

# ---------- Session 初始化 ----------
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
if "user" not in st.session_state:
    st.session_state.user = None
if "must_change_pw" not in st.session_state:
    st.session_state.must_change_pw = False

# ---------- 常數 ----------
REQ_COLS = ["id", "student_id", "type", "original_lesson_id", "requested_date",
            "requested_start", "requested_end", "reason", "status", "created_at", "teacher_note"]

# ---------- 載入資料 ----------
accounts = load_data("accounts")
students = load_data("students")
lessons = load_data("lessons")
requests_df = load_data("requests", REQ_COLS)

# ---------- 登入頁 ----------
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
                    st.error("帳號系統尚未設定，請聯繫老師")
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

# ---------- 強制改密碼頁 ----------
def change_password_page():
    st.title("🔑 首次登入請更改密碼")
    st.markdown("為了安全，請設定自己的密碼。")
    st.markdown("---")

    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        with st.form("change_pw_form"):
            new_pw = st.text_input("新密碼", type="password", help="至少 6 個字元")
            confirm_pw = st.text_input("確認新密碼", type="password")
            submitted = st.form_submit_button("確認更改", use_container_width=True)

            if submitted:
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
            my_lessons["date"] = pd.to_datetime(my_lessons["date"], errors="coerce").dt.date
            today = date.today()
            upcoming = my_lessons[my_lessons["date"] >= today].sort_values(["date", "start"])
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

# ---------- 請假/補課頁面 ----------
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

    my_lessons["date"] = pd.to_datetime(my_lessons["date"], errors="coerce").dt.date

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
                    st.success("✅ 請假申請已送出，等待老師審核")
                    st.rerun()

    st.markdown("---")

    st.subheader("📋 我的申請紀錄")
    my_requests = requests_df[requests_df["student_id"].astype(str) == str(student_id)] if not requests_df.empty else pd.DataFrame()

    if my_requests.empty:
        st.info("目前沒有申請紀錄")
    else:
        for idx, row in my_requests.iterrows():
            with st.container():
                col1, col2, col3 = st.columns([3, 2, 2])
                with col1:
                    st.markdown(f"**{row['type']}** — {row.get('requested_date', '')} {row.get('requested_start', '')}")
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
                    else:
                        st.info(status)
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

    my_lessons["date"] = pd.to_datetime(my_lessons["date"], errors="coerce")
    my_lessons = my_lessons.dropna(subset=["date"])
    my_lessons = my_lessons.sort_values(["date", "start"])

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
        if s == "已排定":
            return "✅"
        elif s == "待補課":
            return "🔄"
        elif s == "已補課":
            return "✔️"
        return "❓"

    def status_color(s):
        s = str(s)
        if s == "已排定":
            return "🟢"
        elif s == "待補課":
            return "🟡"
        elif s == "已補課":
            return "⚪"
        return "⚫"

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
                st.markdown(
                    f"**{d.strftime('%m/%d')}（週{wd}）**　"
                    f"`{row['start']} - {row['end']}`　"
                    f"**{row.get('type', '正課')}**"
                )
                if row.get("note"):
                    st.caption(f"📝 {row['note']}")
            with col2:
                st.markdown(f"{status_color(status)} {status_icon(status)} {status}")
            st.markdown("")

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
            "📝 請假/補課",
            "📅 我的課表",
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
    elif menu == "📝 請假/補課":
        request_page()
    elif menu == "📅 我的課表":
        my_schedule_page()
    elif menu == "📁 我的檔案":
        st.title("📁 我的檔案")
        st.info("功能開發中...")
