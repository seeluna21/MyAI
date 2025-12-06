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
st.set_page_config(page_title="AI Omni-Tutor V5", page_icon="🦄", layout="wide")

# ==========================================
# 2. 数据库 (自动修复冲突)
# ==========================================
def get_db_connection():
    # 强制使用 v5 新数据库，解决 'no such column' 报错
    return sqlite3.connect("web_language_brain_v5.db")

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
# 3. 核心功能 (语音 & 模型)
# ==========================================

# 3.1 语音生成 (带防崩溃保护)
VOICE_MAP = {
    "German": "de-DE-KatjaNeural",
    "Spanish": "es-ES-AlvaroNeural",
    "English": "en-US-AriaNeural",
    "French": "fr-FR-DeniseNeural"
}

async def generate_audio_edge(text, lang):
    """使用 Edge TTS 生成语音流 (带错误捕获)"""
    try:
        voice = VOICE_MAP.get(lang, "en-US-AriaNeural")
        communicate = edge_tts.Communicate(text, voice)
        mp3_fp = BytesIO()
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                mp3_fp.write(chunk["data"])
        mp3_fp.seek(0)
        return mp3_fp
    except Exception as e:
        # 如果报错（比如没开VPN），返回 None，不让程序崩溃
        print(f"TTS Error: {e}")
        return None

# 3.2 文本清洗
def clean_text_for_tts(text):
    text = re.sub(r'\(.*?\)', '', text) # 去掉括号里的纠错
    text = text.replace('**', '').replace('*', '').replace('`', '')
    return text.strip()

# 3.3 自动寻找可用模型 (解决 404 问题)
def get_working_model():
    try:
        # 尝试列出模型，如果 Key 没权限，会报错进入 except
        available = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        # 优先找 Flash，没有就找 Pro
        for m in available: 
            if "flash" in m and "1.5" in m: return m
        for m in available:
            if "gemini-pro" in m: return m
        return "models/gemini-1.5-flash" # 默认备选
    except:
        return "models/gemini-1.5-flash" # 盲猜一个

# ==========================================
# 4. 侧边栏设置
# ==========================================
with st.sidebar:
    st.header("⚙️ Settings")
    
    # API Key
    api_key = st.secrets.get("GOOGLE_API_KEY") or st.text_input("Google API Key", type="password")
    
    if api_key:
        os.environ["GOOGLE_API_KEY"] = api_key
        genai.configure(api_key=api_key)
        # 自动选择模型
        model_name = get_working_model()
        model = genai.GenerativeModel(model_name)
    else:
        st.warning("⚠️ Need API Key")
        st.stop()

    language = st.selectbox("Target Language", ["German", "Spanish", "English", "French"])
    
    # 数据库读取当前等级
    conn = get_db_connection()
    level_row = conn.cursor().execute("SELECT level FROM user_levels WHERE language=?", (language,)).fetchone()
    db_level = level_row[0] if level_row else "A1"
    conn.close()

    # === 新增功能：手动选择难度 ===
    st.divider()
    st.write("📊 **Difficulty Level**")
    # 默认选中数据库里的等级，但用户可以手动改
    selected_level = st.selectbox(
        "Current Level (You can change this):", 
        ["A1", "A2", "B1", "B2", "C1", "C2"],
        index=["A1", "A2", "B1", "B2", "C1", "C2"].index(db_level)
    )
    
    # 如果用户改了，更新数据库
    if selected_level != db_level:
        conn = get_db_connection()
        conn.cursor().execute("INSERT OR REPLACE INTO user_levels (language, level, last_assessed) VALUES (?, ?, ?)", 
                              (language, selected_level, datetime.now().strftime("%Y-%m-%d")))
        conn.commit()
        conn.close()
        st.toast(f"Level updated to {selected_level}!")

    st.divider()
    
    # === 情景选择器 ===
    st.subheader("🎭 Scenario")
    scenarios = {
        "☕ Cafe": "Barista. Impatient but polite.",
        "🛃 Customs": "Strict customs officer.",
        "🤝 Friend": "Friendly student.",
        "🤖 Free Chat": "Helpful tutor."
    }
    current_scenario = st.radio("Context:", list(scenarios.keys()))
    
    # 切换场景清空历史
    if current_scenario != st.session_state.current_scenario:
        st.session_state.messages = []
        st.session_state.current_scenario = current_scenario
        st.rerun()

    if st.button("🗑️ Clear Chat"):
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

# === 核心修改：输入框永远在底部 ===
# st.chat_input 是 Streamlit 专门设计的底部吸附组件
if user_input := st.chat_input(f"Say something in {language}..."):
    
    # 1. 显示用户输入
    st.session_state.messages.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)
        
    # 2. AI 生成回复
    with st.chat_message("assistant"):
        placeholder = st.empty()
        full_response = ""
        
        try:
            # 构造 Prompt
            prompt = f"""
            Act as a character in this scenario: {scenarios[current_scenario]}.
            Language: {language}.
            User Level: {selected_level}.
            
            Task: Reply to the user.
            1. Keep it concise (1-3 sentences).
            2. If user makes a mistake, correct it at the end in (parentheses).
            """
            
            # 历史记录上下文
            history = [{"role": "user", "parts": [prompt]}]
            for m in st.session_state.messages[:-1]:
                role = "model" if m["role"] == "assistant" else "user"
                history.append({"role": role, "parts": [m["content"]]})
            history.append({"role": "user", "parts": [user_input]})
            
            # 流式生成
            chat = model.start_chat(history=history[:-1])
            response = chat.send_message(user_input, stream=True)
            
            for chunk in response:
                if chunk.text:
                    full_response += chunk.text
                    placeholder.markdown(full_response + "▌")
            placeholder.markdown(full_response)
            
            st.session_state.messages.append({"role": "assistant", "content": full_response})
            
            # === 语音播放 (带错误处理) ===
            with st.spinner("🔊 Generating audio..."):
                clean_txt = clean_text_for_tts(full_response)
                # 这里的 asyncio.run 可能会在某些特定环境报错，如果报错请告诉我
                try:
                    audio_fp = asyncio.run(generate_audio_edge(clean_txt, language))
                    if audio_fp:
                        st.audio(audio_fp, format='audio/mp3', autoplay=True)
                    else:
                        st.warning("⚠️ 语音生成失败 (请检查网络/代理)")
                except Exception as tts_err:
                    st.warning(f"⚠️ 语音组件错误: {tts_err}")

        except Exception as e:
            st.error(f"AI Error: {e}")
