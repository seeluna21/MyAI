import streamlit as st
import google.generativeai as genai
import sqlite3
import json
import os
import re
import asyncio
import edge_tts
import nest_asyncio  # <--- 新增救星库
from datetime import datetime, timedelta
from io import BytesIO
from PIL import Image

# ==========================================
# 0. 核心补丁 (解决 Event Loop 报错)
# ==========================================
# 这行代码至关重要，它允许在 Streamlit 的循环中嵌套运行 Edge-TTS
nest_asyncio.apply()

st.set_page_config(page_title="AI Omni-Tutor V7", page_icon="🦄", layout="wide")

# ==========================================
# 1. 数据库
# ==========================================
def get_db_connection():
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

if "messages" not in st.session_state: st.session_state.messages = []
if "review_queue" not in st.session_state: st.session_state.review_queue = []
if "show_answer" not in st.session_state: st.session_state.show_answer = False
if "current_scenario" not in st.session_state: st.session_state.current_scenario = "Free Chat"

# ==========================================
# 2. 语音生成 (Nest_Asyncio 修复版)
# ==========================================
VOICE_MAP = {
    "German": "de-DE-KatjaNeural",
    "Spanish": "es-ES-AlvaroNeural",
    "English": "en-US-AriaNeural",
    "French": "fr-FR-DeniseNeural"
}

# 纯异步生成函数
async def _gen_audio(text, voice):
    communicate = edge_tts.Communicate(text, voice)
    mp3_fp = BytesIO()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            mp3_fp.write(chunk["data"])
    mp3_fp.seek(0)
    return mp3_fp

# 同步包装器 (带详细 Debug 信息)
def generate_audio_stream(text, lang):
    try:
        voice = VOICE_MAP.get(lang, "en-US-AriaNeural")
        
        # 获取或创建事件循环
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
        # 使用 nest_asyncio 允许的 run_until_complete
        if loop.is_running():
            # 如果循环已经在运行（Streamlit Cloud 常见情况），直接调度
            future = asyncio.ensure_future(_gen_audio(text, voice))
            # 这里稍微有点 hack，但在 nest_asyncio 下通常有效
            # 更稳妥的是直接 run_until_complete，nest_asyncio 会处理重入
            return loop.run_until_complete(_gen_audio(text, voice))
        else:
            return loop.run_until_complete(_gen_audio(text, voice))
            
    except Exception as e:
        # 返回具体的错误信息，而不是 None
        return f"ERROR_DETAILS: {str(e)}"

# ==========================================
# 3. 其他工具函数
# ==========================================
def clean_text_for_tts(text):
    text = re.sub(r'\(.*?\)', '', text)
    text = text.replace('**', '').replace('*', '').replace('`', '')
    return text.strip()

def get_working_model():
    try:
        available = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        for m in available: 
            if "flash" in m and "1.5" in m: return m
        for m in available:
            if "gemini-pro" in m: return m
        return "models/gemini-1.5-flash"
    except:
        return "models/gemini-1.5-flash"

# ==========================================
# 4. 侧边栏
# ==========================================
with st.sidebar:
    st.header("⚙️ Settings")
    
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
        st.warning("Please setup API Key")
        st.stop()

    language = st.selectbox("Language", ["German", "Spanish", "English", "French"])
    
    conn = get_db_connection()
    level_row = conn.cursor().execute("SELECT level FROM user_levels WHERE language=?", (language,)).fetchone()
    db_level = level_row[0] if level_row else "A1"
    conn.close()

    st.divider()
    
    st.write("📊 **Level Override**")
    selected_level = st.selectbox(
        "Adjust Difficulty:", 
        ["A1", "A2", "B1", "B2", "C1", "C2"],
        index=["A1", "A2", "B1", "B2", "C1", "C2"].index(db_level)
    )
    
    if selected_level != db_level:
        conn = get_db_connection()
        conn.cursor().execute("INSERT OR REPLACE INTO user_levels (language, level, last_assessed) VALUES (?, ?, ?)", 
                              (language, selected_level, datetime.now().strftime("%Y-%m-%d")))
        conn.commit()
        conn.close()

    st.divider()
    
    st.subheader("🎭 Context")
    scenarios = {
        "☕ Cafe": "Barista.",
        "🛃 Customs": "Customs officer.",
        "🤝 Friend": "Friendly student.",
        "🤖 Free Chat": "Tutor."
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

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

if user_input := st.chat_input(f"Type in {language}..."):
    
    st.session_state.messages.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)
        
    with st.chat_message("assistant"):
        placeholder = st.empty()
        full_response = ""
        
        try:
            prompt = f"""
            Roleplay: {scenarios[current_scenario]}. Lang: {language}. Level: {selected_level}.
            Reply to user (1-3 sentences). Correct mistakes at end in (parentheses).
            """
            
            history = [{"role": "user", "parts": [prompt]}]
            for m in st.session_state.messages[:-1]:
                role = "model" if m["role"] == "assistant" else "user"
                history.append({"role": role, "parts": [m["content"]]})
            history.append({"role": "user", "parts": [user_input]})
            
            chat = model.start_chat(history=history[:-1])
            response = chat.send_message(user_input, stream=True)
            
            for chunk in response:
                if chunk.text:
                    full_response += chunk.text
                    placeholder.markdown(full_response + "▌")
            placeholder.markdown(full_response)
            
            st.session_state.messages.append({"role": "assistant", "content": full_response})
            
            # === 音频处理 (显示详细错误) ===
            with st.spinner("🔊 Generating audio..."):
                clean_txt = clean_text_for_tts(full_response)
                # 调用音频生成
                result = generate_audio_stream(clean_txt, language)
                
                # 判断结果是 音频流 还是 错误信息
                if isinstance(result, str) and result.startswith("ERROR"):
                    st.error(f"⚠️ 语音生成失败: {result}")
                    st.caption("提示: 如果是 Connection Error，说明 Streamlit Cloud 无法连接微软服务器。如果是 Event Loop Error，说明 nest_asyncio 没生效。")
                elif result:
                    st.audio(result, format='audio/mp3', autoplay=True)
                else:
                    st.warning("⚠️ 未知音频错误")

        except Exception as e:
            st.error(f"AI Error: {e}")
