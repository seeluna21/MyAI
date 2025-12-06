import streamlit as st
import google.generativeai as genai
import sqlite3
import json
import os
import re
import asyncio
import edge_tts
import nest_asyncio  # 核心救星库
from datetime import datetime, timedelta
from io import BytesIO
from PIL import Image

# ==========================================
# 0. 核心补丁 (解决 Streamlit Event Loop 报错)
# ==========================================
nest_asyncio.apply()

st.set_page_config(page_title="AI Omni-Tutor V7.1", page_icon="🦄", layout="wide")

# ==========================================
# 1. 数据库
# ==========================================
def get_db_connection():
    # 使用 v6.db 确保数据库结构最新
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
# 2. 语音生成 (增强稳定性版)
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

# 同步包装器
def generate_audio_stream(text, lang):
    try:
        voice = VOICE_MAP.get(lang, "en-US-AriaNeural")
        
        # 获取或创建事件循环
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
        # 使用 nest_asyncio 允许的方式运行
        return loop.run_until_complete(_gen_audio(text, voice))
            
    except Exception as e:
        return f"ERROR_DETAILS: {str(e)}"

# ==========================================
# 3. 其他工具函数 (文本清洗、模型选择、单词提取)
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

def extract_and_save_vocab(text, lang, model):
    try:
        prompt = f"""
        Extract 3-5 key vocabulary words from this {lang} text.
        Format JSON: [{{"word": "word1", "trans": "english_meaning"}}, ...]
        Text: {text}
        """
        resp = model.generate_content(prompt)
        text_resp = resp.text
        if "```json" in text_resp:
            clean = text_resp.split("```json")[1].split("```")[0].strip()
        elif "```" in text_resp:
            clean = text_resp.split("```")[1].split("```")[0].strip()
        else:
            clean = text_resp.strip()
        
        data = json.loads(clean)
        conn = get_db_connection()
        today_dt = datetime.now()
        next_review = (today_dt + timedelta(days=1)).strftime("%Y-%m-%d")
        
        saved_words = []
        for item in data:
            conn.cursor().execute(
                '''INSERT OR IGNORE INTO vocab (word, language, translation, last_reviewed, next_review_date, proficiency) 
                   VALUES (?, ?, ?, ?, ?, 0)''', 
                (item['word'], lang, item['trans'], today_dt.strftime("%Y-%m-%d"), next_review)
            )
            saved_words.append(item['word'])
        conn.commit()
        conn.close()
        return saved_words
    except:
        return []

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
    
    # 获取复习数量
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        review_count = conn.cursor().execute(
            "SELECT count(*) FROM vocab WHERE language=? AND (next_review_date <= ? OR next_review_date IS NULL)", 
            (language, today)).fetchone()[0]
    except:
        review_count = 0
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

    st.metric("Review Due", f"{review_count} words")
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
# 5. 主界面 (Tab 布局回归)
# ==========================================
st.title(f"🦄 AI Tutor: {language} ({selected_level})")
tab1, tab2, tab3 = st.tabs(["💬 Chat & Learn", "📸 Photo Learning", "🧠 Review"])

# --- TAB 1: 聊天 ---
with tab1:
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
                
                # 音频处理
                with st.spinner("🔊 Generating audio..."):
                    clean_txt = clean_text_for_tts(full_response)
                    result = generate_audio_stream(clean_txt, language)
                    if isinstance(result, str) and result.startswith("ERROR"):
                        st.error(f"⚠️ 语音失败: {result}")
                    elif result:
                        st.audio(result, format='audio/mp3', autoplay=True)
                
                # 自动存词
                with st.status("🧠 Analyzing vocabulary...", expanded=False):
                    new_words = extract_and_save_vocab(full_response, language, model)
                    if new_words: st.write(f"Saved: {', '.join(new_words)}")

            except Exception as e:
                st.error(f"AI Error: {e}")

# --- TAB 2: 拍照 ---
with tab2:
    uploaded_file = st.file_uploader("Upload photo", type=["jpg", "png", "jpeg"])
    if uploaded_file:
        image = Image.open(uploaded_file)
        st.image(image, width=300)
        
        if st.button("🔍 Analyze"):
            with st.spinner("🤖 Analyzing..."):
                try:
                    prompt = f"Describe in {language} (Level {selected_level}) and list 3 words."
                    response = model.generate_content([prompt, image])
                    st.markdown(response.text)
                    
                    clean_txt = clean_text_for_tts(response.text)
                    result = generate_audio_stream(clean_txt, language)
                    if isinstance(result, BytesIO): st.audio(result, format='audio/mp3')
                    
                    extract_and_save_vocab(response.text, language, model)
                except Exception as e:
                    st.error(f"Vision Error: {e}")

# --- TAB 3: 复习 ---
with tab3:
    if st.button("🔄 Refresh Queue"):
        st.session_state.review_queue = []
        st.rerun()

    if not st.session_state.review_queue:
        conn = get_db_connection()
        today_str = datetime.now().strftime("%Y-%m-%d")
        try:
            rows = conn.cursor().execute(
                "SELECT word, translation, proficiency FROM vocab WHERE language=? AND (next_review_date <= ? OR next_review_date IS NULL) ORDER BY random() LIMIT 10", 
                (language, today_str)).fetchall()
        except: rows = []
        conn.close()
        st.session_state.review_queue = rows
    
    if st.session_state.review_queue:
        word, translation, prof = st.session_state.review_queue[0]
        st.progress(prof/5, text=f"Proficiency: {prof}/5")
        st.markdown(f"# {word}")
        
        if st.button("🔊 Play"):
            result = generate_audio_stream(word, language)
            if isinstance(result, BytesIO): st.audio(result, format='audio/mp3', autoplay=True)

        if st.button("👀 Show Meaning"):
            st.session_state.show_answer = True
            
        if st.session_state.show_answer:
            st.success(f"**Meaning:** {translation}")
            c1, c2, c3 = st.columns(3)
            def handle_review(res):
                conn = get_db_connection()
                today_dt = datetime.now()
                if res == "forget": new_prof, days = max(0, prof - 1), 1 
                elif res == "ok": new_prof, days = prof, 2
                elif res == "easy": new_prof, days = min(5, prof + 1), 3 + prof * 2
                
                next_date = (today_dt + timedelta(days=days)).strftime("%Y-%m-%d")
                conn.cursor().execute(
                    "UPDATE vocab SET proficiency=?, last_reviewed=?, next_review_date=? WHERE word=? AND language=?",
                    (new_prof, today_dt.strftime("%Y-%m-%d"), next_date, word, language)
                )
                conn.commit()
                conn.close()
                st.session_state.review_queue.pop(0)
                st.session_state.show_answer = False
                st.rerun()

            if c1.button("😭 Forgot"): handle_review("forget")
            if c2.button("😐 Hard"): handle_review("ok")
            if c3.button("😎 Easy"): handle_review("easy")
    else:
        st.success("🎉 No words to review!")
