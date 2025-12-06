import streamlit as st
import google.generativeai as genai
import sqlite3
import json
import os
import re
import uuid
from datetime import datetime, timedelta
from io import BytesIO
from PIL import Image
from gtts import gTTS  # 核心：换成 Google 的 TTS 库

# ==========================================
# 0. 基础配置
# ==========================================
st.set_page_config(page_title="AI Omni-Tutor V7.3 (Google Edition)", page_icon="🦄", layout="wide")

# ==========================================
# 1. 数据库逻辑
# ==========================================
def get_db_connection():
    return sqlite3.connect("web_language_brain_v6.db", check_same_thread=False)

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
# 2. 语音生成 (切换为 Google gTTS)
# ==========================================
# gTTS 使用的是简写的语言代码，我们需要映射一下
LANG_CODE_MAP = {
    "German": "de",
    "Spanish": "es",
    "English": "en",
    "French": "fr"
}

def generate_audio_stream(text, lang_name):
    """
    使用 gTTS (Google Translate TTS) 生成语音。
    优点：不需要 asyncio，不需要 key，连接稳定。
    """
    try:
        # 1. 获取对应的简写代码 (如 German -> de)
        lang_code = LANG_CODE_MAP.get(lang_name, "en")
        
        # 2. 如果是空文本，直接返回
        if not text.strip():
            return None
            
        # 3. 调用 Google 接口
        tts = gTTS(text=text, lang=lang_code, slow=False)
        
        # 4. 写入内存
        mp3_fp = BytesIO()
        tts.write_to_fp(mp3_fp)
        mp3_fp.seek(0)
        
        return mp3_fp
        
    except Exception as e:
        return f"TTS Error: {str(e)}"

# ==========================================
# 3. 工具函数
# ==========================================
def clean_text_for_tts(text):
    # 去除括号内容、Markdown符号
    text = re.sub(r'\(.*?\)', '', text)
    text = text.replace('**', '').replace('*', '').replace('`', '').replace('#', '')
    return text.strip()

def get_model():
    # 这里设置你想要的模型
    # 注意：目前没有 2.5-flash，只有 1.5-flash 或 2.0-flash-exp
    return "models/gemini-2.5-flash" 

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
        
        # 尝试加载模型
        try:
            model_name = get_model()
            model = genai.GenerativeModel(model_name)
            # 简单的测试调用，确保模型可用
            # model.generate_content("test") 
            st.caption(f"🚀 Running on: {model_name}")
        except Exception as e:
            st.error(f"Model Error: {e}")
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
    selected_level = st.selectbox("Adjust Difficulty:", ["A1", "A2", "B1", "B2", "C1", "C2"], index=["A1", "A2", "B1", "B2", "C1", "C2"].index(db_level))
    
    if selected_level != db_level:
        conn = get_db_connection()
        conn.cursor().execute("INSERT OR REPLACE INTO user_levels (language, level, last_assessed) VALUES (?, ?, ?)", 
                              (language, selected_level, datetime.now().strftime("%Y-%m-%d")))
        conn.commit()
        conn.close()

    st.metric("Review Due", f"{review_count} words")
    st.divider()
    
    scenarios = {"☕ Cafe": "Barista", "🛃 Customs": "Officer", "🤝 Friend": "Student", "🤖 Free Chat": "Tutor"}
    current_scenario = st.radio("Context:", list(scenarios.keys()))
    
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
tab1, tab2, tab3 = st.tabs(["💬 Chat & Learn", "📸 Photo Learning", "🧠 Review"])

# --- TAB 1: 聊天 ---
with tab1:
    chat_container = st.container()
    
    with chat_container:
        for msg in st.session_state.messages:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])
        st.empty() 

    if user_input := st.chat_input(f"Type in {language}..."):
        st.session_state.messages.append({"role": "user", "content": user_input})
        with chat_container:
            with st.chat_message("user"):
                st.markdown(user_input)

        with chat_container:
            with st.chat_message("assistant"):
                placeholder = st.empty()
                full_response = ""
                
                try:
                    # 构建 Prompt
                    prompt = f"""
                    Act as a {scenarios[current_scenario]}. Language: {language} ({selected_level}).
                    User says: "{user_input}".
                    Reply naturally (2-3 sentences). 
                    Then, if the user made grammar mistakes, list them briefly at the very end inside (parentheses).
                    """
                    
                    response = model.generate_content(prompt, stream=True)
                    
                    for chunk in response:
                        if chunk.text:
                            full_response += chunk.text
                            placeholder.markdown(full_response + "▌")
                    placeholder.markdown(full_response)
                    
                    st.session_state.messages.append({"role": "assistant", "content": full_response})

                    # === 语音生成 (gTTS) ===
                    clean_txt = clean_text_for_tts(full_response)
                    # gTTS 是同步的，直接调用即可
                    audio_data = generate_audio_stream(clean_txt, language)
                    
                    if isinstance(audio_data, str):
                        st.error(audio_data)
                    elif audio_data:
                        # 依然使用 key 来强制刷新播放器
                        st.audio(audio_data, format='audio/mp3', autoplay=True, key=f"tts_{uuid.uuid4()}")

                    # 存词
                    new_words = extract_and_save_vocab(full_response, language, model)
                    if new_words:
                        st.toast(f"💾 Saved: {', '.join(new_words)}", icon="🧠")

                except Exception as e:
                    st.error(f"Error: {e}")

# --- TAB 2: 拍照 ---
with tab2:
    uploaded_file = st.file_uploader("Upload photo", type=["jpg", "png", "jpeg"])
    if uploaded_file:
        image = Image.open(uploaded_file)
        st.image(image, width=300)
        
        if st.button("🔍 Analyze Photo"):
            with st.spinner("🤖 Analyzing..."):
                try:
                    prompt = f"Describe this image in {language} (Level {selected_level}) and list 3 key vocabulary words."
                    response = model.generate_content([prompt, image])
                    st.markdown(response.text)
                    
                    clean_txt = clean_text_for_tts(response.text)
                    audio_data = generate_audio_stream(clean_txt, language)
                    if isinstance(audio_data, BytesIO):
                        st.audio(audio_data, format='audio/mp3', key=f"photo_{uuid.uuid4()}")
                    
                    extract_and_save_vocab(response.text, language, model)
                except Exception as e:
                    st.error(f"Vision Error: {e}")

# --- TAB 3: 复习 ---
with tab3:
    col_a, col_b = st.columns([4, 1])
    with col_a: st.subheader("Flashcards")
    with col_b: 
        if st.button("🔄 Reload"): 
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
        
        st.markdown(f"""
        <div style="padding: 20px; border-radius: 10px; background-color: #f0f2f6; text-align: center; margin-bottom: 20px;">
            <h1 style="color: #333; margin:0;">{word}</h1>
            <p style="color: #666;">Proficiency: {'⭐' * prof}</p>
        </div>
        """, unsafe_allow_html=True)
        
        c1, c2, c3 = st.columns([1, 1, 2])
        with c1:
            if st.button("🔊 Pronounce", key=f"btn_{word}"):
                res = generate_audio_stream(word, language)
                if isinstance(res, BytesIO): 
                    st.audio(res, format='audio/mp3', autoplay=True, key=f"rev_{word}_{uuid.uuid4()}")

        with c2:
            if st.button("👀 Reveal"):
                st.session_state.show_answer = True
        
        if st.session_state.show_answer:
            st.info(f"**Meaning:** {translation}")
            st.write("How hard was this?")
            b1, b2, b3 = st.columns(3)
            
            def update_word(quality):
                conn = get_db_connection()
                today_dt = datetime.now()
                if quality == 0: new_prof, days = max(0, prof - 1), 0
                elif quality == 1: new_prof, days = prof, 2
                else: new_prof, days = min(5, prof + 1), 3 + prof * 2
                
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

            if b1.button("😭 Forgot", use_container_width=True): update_word(0)
            if b2.button("😐 Hard", use_container_width=True): update_word(1)
            if b3.button("😎 Easy", use_container_width=True): update_word(2)
    else:
        st.success("🎉 You are all caught up!")
