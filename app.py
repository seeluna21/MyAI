import streamlit as st
import google.generativeai as genai
import sqlite3
import json
import os
import pandas as pd
from datetime import datetime

# ==========================================
# 1. 基础配置 & 数据库
# ==========================================
st.set_page_config(page_title="AI Language Tutor", page_icon="🚀", layout="wide")

def get_db_connection():
    # 注意：在 Streamlit Cloud 上，SQLite 数据库是临时的（重启会重置）
    # 如果需要永久保存，建议后续升级为 Google Sheets 或 Supabase
    conn = sqlite3.connect("web_language_brain.db")
    return conn

def init_db():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS user_levels 
                 (language TEXT PRIMARY KEY, level TEXT, last_assessed DATE)''')
    c.execute('''CREATE TABLE IF NOT EXISTS vocab 
                 (word TEXT, language TEXT, proficiency INTEGER DEFAULT 0, last_reviewed DATE, PRIMARY KEY (word, language))''')
    conn.commit()
    conn.close()

# 初始化数据库
init_db()

# 初始化 Session State
if "messages" not in st.session_state:
    st.session_state.messages = []

# ==========================================
# 2. 侧边栏 & API Key 配置 (关键修改)
# ==========================================
with st.sidebar:
    st.header("⚙️ Settings")
    
    # 优先尝试从 Streamlit Secrets 读取 Key
    api_key = None
    if "GOOGLE_API_KEY" in st.secrets:
        api_key = st.secrets["GOOGLE_API_KEY"]
        st.success("✅ API Key loaded from Cloud Secrets")
    else:
        # 如果本地运行且没有配置 secrets.toml，允许手动输入
        api_key = st.text_input("Google API Key", type="password", help="Enter your key here for local testing")

    # 配置 Google Gemini
    if api_key:
        os.environ["GOOGLE_API_KEY"] = api_key
        genai.configure(api_key=api_key)
    else:
        st.warning("⚠️ Please configure your API Key in Streamlit Secrets or enter it above.")

    st.divider()
    
    # 语言选择
    language = st.selectbox("Target Language", ["German", "Spanish", "English", "French"])
    
    # 读取用户等级
    conn = get_db_connection()
    level_row = conn.cursor().execute("SELECT level FROM user_levels WHERE language=?", (language,)).fetchone()
    current_level = level_row[0] if level_row else "A1"
    
    # 读取词汇量
    vocab_count = conn.cursor().execute("SELECT count(*) FROM vocab WHERE language=?", (language,)).fetchone()[0]
    conn.close()
    
    st.metric(f"{language} Level", current_level)
    st.caption(f"📚 Vocab stored: {vocab_count}")

# ==========================================
# 3. 核心功能函数
# ==========================================
model = genai.GenerativeModel('gemini-1.5-flash')

def extract_vocab_in_background(text, lang):
    """从生成的文本中提取单词"""
    prompt = f"""
    Extract 5 key vocabulary words (lemmatized) from the following {lang} text.
    Output JSON ONLY: ["word1", "word2", "word3", "word4", "word5"]
    Text: {text}
    """
    try:
        response = model.generate_content(prompt)
        clean = response.text.replace('```json', '').replace('```', '').strip()
        words = json.loads(clean)
        
        conn = get_db_connection()
        today = datetime.now().strftime("%Y-%m-%d")
        for w in words:
            conn.cursor().execute("INSERT OR IGNORE INTO vocab (word, language, last_reviewed) VALUES (?, ?, ?)", 
                                  (w, lang, today))
        conn.commit()
        conn.close()
        return words
    except:
        return []

def update_level(lang, direction):
    """调整等级"""
    levels = ["A1", "A2", "B1", "B2", "C1", "C2"]
    try:
        curr_idx = levels.index(current_level)
    except:
        curr_idx = 0
    
    new_idx = curr_idx
    if direction == "up" and curr_idx < 5: new_idx += 1
    if direction == "down" and curr_idx > 0: new_idx -= 1
    new_lvl = levels[new_idx]
    
    conn = get_db_connection()
    conn.cursor().execute("INSERT OR REPLACE INTO user_levels (language, level, last_assessed) VALUES (?, ?, ?)", 
                          (lang, new_lvl, datetime.now().strftime("%Y-%m-%d")))
    conn.commit()
    conn.close()
    return new_lvl

# ==========================================
# 4. 主聊天界面
# ==========================================
st.title("🚀 Cloud AI Language Tutor")
st.caption(f"Status: Learning {language} at {current_level} level")

# 如果没有 Key，停止运行并提示
if not api_key:
    st.info("👋 Please add your Google API Key to start.")
    st.stop()

# 聊天输入框
topic = st.chat_input(f"What do you want to learn in {language}? (e.g. Coffee, Coding)")

if topic:
    # 1. 显示用户输入
    with st.chat_message("user"):
        st.write(topic)
    
    # 2. AI 生成回答 (手动流式处理，兼容性最强)
    with st.chat_message("assistant"):
        response_placeholder = st.empty()
        full_response = ""
        
        try:
            prompt = f"""
            Write a short, engaging lesson about '{topic}' in {language} for a {current_level} level student.
            Include the English translation at the end.
            DO NOT use JSON. Just write natural text.
            """
            
            response_stream = model.generate_content(prompt, stream=True)
            
            # 手动循环读取，确保有内容就显示
            for chunk in response_stream:
                if chunk.text:
                    full_response += chunk.text
                    response_placeholder.markdown(full_response + "▌")
            
            # 完成后显示最终文本
            response_placeholder.markdown(full_response)
            
            # 3. 后台提取单词
            if full_response:
                with st.status("🧠 Processing vocabulary...", expanded=False) as status:
                    new_words = extract_vocab_in_background(full_response, language)
                    status.update(label=f"Saved {len(new_words)} new words!", state="complete", expanded=False)
                    if new_words:
                        st.write(f"Added: `{'`, `'.join(new_words)}`")
                        
        except Exception as e:
            response_placeholder.error(f"❌ Error: {e}")

    # 4. 难度反馈按钮
    st.write("---")
    c1, c2, c3 = st.columns(3)
    if c1.button("Too Easy (⬆️ Level Up)"):
        nl = update_level(language, "up")
        st.toast(f"Level up! Now {nl}")
        import time; time.sleep(0.5); st.rerun()
        
    if c2.button("Just Right (✅ Keep)"):
        st.toast("Level maintained")
        
    if c3.button("Too Hard (⬇️ Level Down)"):
        nl = update_level(language, "down")
        st.toast(f"Level down! Now {nl}")
        import time; time.sleep(0.5); st.rerun()

# 历史生词展示
if st.checkbox("Show Vocabulary Bank"):
    conn = get_db_connection()
    df = pd.read_sql_query(f"SELECT word, proficiency FROM vocab WHERE language='{language}' ORDER BY last_reviewed DESC LIMIT 20", conn)
    conn.close()
    st.dataframe(df, use_container_width=True)
