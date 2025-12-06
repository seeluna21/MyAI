import streamlit as st
import google.generativeai as genai
import sqlite3
import json
import os
import re
import asyncio
import edge_tts
from datetime import datetime, timedelta
from io import BytesIO
from PIL import Image

# ==========================================
# 1. 基础配置
# ==========================================
st.set_page_config(page_title="AI Omni-Tutor V6", page_icon="🦄", layout="wide")

# ==========================================
# 2. 数据库 (V6 - 自动修复)
# ==========================================
def get_db_connection():
    # 使用 v6.db 强制生成新库，解决旧版本冲突
    return sqlite3.connect("web_language_brain_v6.db")

def init_db():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS user_levels 
                 (language TEXT PRIMARY KEY, level TEXT, last_assessed DATE)''')
    c.execute('''CREATE TABLE IF NOT EXISTS vocab 
                 (word TEXT, language TEXT, translation TEXT, proficiency INTEGER DEFAULT 0, 
                  last_reviewed DATE, next_review_date DATE, PRIMARY KEY (word, language))''')
    conn.commit()
    conn.close()

init_db()

# Session State 初始化
if "messages" not in st.session_state: st.session_state.messages = []
if "review_queue" not in st.session_state: st.session_state.review_queue = []
if "show_answer" not in st.session_state: st.session_state.show_answer = False
if "current_scenario" not in st.session_state: st.session_state.current_scenario = "Free Chat"

# ==========================================
# 3. 核心功能 (语音修复版)
# ==========================================

VOICE_MAP = {
    "German": "de-DE-KatjaNeural",
    "Spanish": "es-ES-AlvaroNeural",
    "English": "en-US-AriaNeural",
    "French": "fr-FR-DeniseNeural"
}

# 🔴 关键修复：专门针对 Streamlit Cloud 的异步处理函数
# 这里的逻辑是：每次生成音频都创建一个全新的事件循环，避免和 Streamlit 自身的循环冲突
async def _generate_audio_coroutine(text, voice):
    communicate = edge_tts.Communicate(text, voice)
    mp3_fp = BytesIO()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            mp3_fp.write(chunk["data"])
    mp3_fp.seek(0)
    return mp3_fp

def generate_audio_stream(text, lang):
    """同步包装异步函数，修复 'Event loop stopped' 错误"""
    try:
        voice = VOICE_MAP.get(lang, "en-US-AriaNeural")
        # 创建一个新的 Event Loop 来运行 TTS
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(_generate_audio_coroutine(text, voice))
        finally:
            loop.close()
    except Exception as e:
        print(f"TTS Error: {e}")
        return None

def clean_text_for_tts(text):
    text = re.sub(r'\(.*?\)', '', text) # 去掉纠错括号
    text = text.replace('**', '').replace('*', '').replace('`', '')
    return text.strip()

# 模型自动回退逻辑
def get_working_model():
    try:
        # 尝试寻找 Flash 模型
        available = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        for m in available: 
            if "flash" in m and "1.5" in m: return m
        # 找不到就找 Pro
        for m in available:
            if "gemini-pro" in m: return m
        return "models/gemini-1.5-flash"
    except:
        return "models/gemini-1.5-flash"

# ==========================================
# 4. 侧边栏设置
# ==========================================
with st.sidebar:
    st.header("⚙️ Settings")
    
    # API Key 读取
    api_key = st.secrets.get("GOOGLE_API_KEY")
    if not api_key:
        api_key = st.text_input("Google API Key", type="password")
    
    if api_key:
        os.environ["GOOGLE_API_KEY"] = api_key
        genai.configure(api_key=api_key)
        try:
            model_name = get_working_model()
            model = genai.GenerativeModel(model_name)
        except:
            st.error("Invalid API Key")
            st.stop()
    else:
        st.warning("Please setup API Key in Streamlit Secrets or enter here.")
        st.stop()

    language = st.selectbox("Target Language", ["German", "Spanish", "English", "French"])
    
    # 读取等级
    conn = get_db_connection()
    level_row = conn.cursor().execute("SELECT level FROM user_levels WHERE language=?", (language,)).fetchone()
    db_level = level_row[0] if level_row else "A1"
    conn.close()

    st.divider()
    
    # === 新增：手动选择难度 ===
    st.write("📊 **Level Override**")
    selected_level = st.selectbox(
        "Adjust Difficulty:", 
        ["A1", "A2", "B1", "B2", "C1", "C2"],
        index=["A1", "A2", "B1", "B2", "C1", "C2"].index(db_level)
    )
    
    # 如果手动改了，保存到数据库
    if selected_level != db_level:
        conn = get_db_connection()
        conn.cursor().execute("INSERT OR REPLACE INTO user_levels (language, level, last_assessed) VALUES (?, ?, ?)", 
                              (language, selected_level, datetime.now().strftime("%Y-%m-%d")))
        conn.commit()
        conn.close()
        # st.toast(f"Level set to {selected_level}")

    st.divider()
    
    # === 情景选择 ===
    st.subheader("🎭 Context")
    scenarios = {
        "☕ Cafe": "Barista. You are impatient but polite.",
        "🛃 Customs": "Strict customs officer.",
        "🤝 Friend": "Friendly student at a party.",
        "🤖 Free Chat": "Helpful language tutor."
    }
    current_scenario = st.radio("Choose:", list(scenarios.keys()))
    
    if current_scenario != st.session_state.current_scenario:
        st.session_state.messages = []
        st.session_state.current_scenario = current_scenario
        st.rerun()

    if st.button("🗑️ Clear History"):
        st.session_state.messages = []
        st.rerun()

# ==========================================
# 5. 主界面
# ==========================================
st.title(f"🦄 AI Tutor: {language} ({selected_level})")

# 显示历史消息
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# === 输入框 (st.chat_input 自动吸底) ===
if user_input := st.chat_input(f"Type in {language}..."):
    
    # 1. 用户消息
    st.session_state.messages.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)
        
    # 2. AI 回复
    with st.chat_message("assistant"):
        placeholder = st.empty()
        full_response = ""
        
        try:
            # 构造 Prompt
            prompt = f"""
            Roleplay Scenario: {scenarios[current_scenario]}.
            Language: {language}.
            User Level: {selected_level}.
            
            Instruction:
            1. Reply to the user naturally (1-3 sentences).
            2. If the user makes a grammar mistake, provide the correction at the very end in (parentheses).
            """
            
            # 构建历史上下文
            history = [{"role": "user", "parts": [prompt]}]
            for m in st.session_state.messages[:-1]:
                role = "model" if m["role"] == "assistant" else "user"
                history.append({"role": role, "parts": [m["content"]]})
            history.append({"role": "user", "parts": [user_input]})
            
            # 生成文字
            chat = model.start_chat(history=history[:-1])
            response = chat.send_message(user_input, stream=True)
            
            for chunk in response:
                if chunk.text:
                    full_response += chunk.text
                    placeholder.markdown(full_response + "▌")
            placeholder.markdown(full_response)
            
            st.session_state.messages.append({"role": "assistant", "content": full_response})
            
            # === 3. 生成语音 (使用修复后的函数) ===
            clean_txt = clean_text_for_tts(full_response)
            audio_data = generate_audio_stream(clean_txt, language)
            
            if audio_data:
                # autoplay=True 只有在部分浏览器生效，Streamlit Cloud 上通常需要手动点一下
                st.audio(audio_data, format='audio/mp3', autoplay=True)
            else:
                st.warning("⚠️ Audio generation failed.")

        except Exception as e:
            st.error(f"Error: {e}")
