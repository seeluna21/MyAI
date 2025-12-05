import streamlit as st
import google.generativeai as genai
import sqlite3
import json
import os
import pandas as pd
import re  # <--- 新增：用于正则清洗文本
from datetime import datetime
from gtts import gTTS
from io import BytesIO

# ==========================================
# 1. 基础配置 & 数据库
# ==========================================
st.set_page_config(page_title="AI Language Tutor", page_icon="🗣️", layout="wide")

def get_db_connection():
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

init_db()

if "messages" not in st.session_state:
    st.session_state.messages = []
if "current_model_name" not in st.session_state:
    st.session_state.current_model_name = None

# ==========================================
# 2. 智能模型选择 & 工具函数
# ==========================================
def get_best_available_model():
    """自动寻找最佳模型"""
    try:
        model_list = []
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                model_list.append(m.name)
        
        for m in model_list:
            if "flash" in m and "1.5" in m: return m
        for m in model_list:
            if "pro" in m and "1.5" in m: return m
        for m in model_list:
            if "gemini" in m: return m
        return "models/gemini-1.5-flash"
    except Exception as e:
        return "models/gemini-pro"

# === 新增：TTS 文本清洗函数 ===
def clean_text_for_tts(text):
    """
    去除 Markdown 符号，只保留纯文本供朗读。
    比如把 "**Hello**" 变成 "Hello"。
    """
    # 1. 去除加粗和斜体符号 (* 或 **)
    text = text.replace('**', '').replace('*', '')
    # 2. 去除标题符号 (#)
    text = text.replace('##', '').replace('#', '')
    # 3. 去除代码块符号 (`)
    text = text.replace('`', '')
    # 4. 去除列表开头的横杠 (- )，替换为逗号以增加停顿
    text = re.sub(r'^\s*-\s+', '', text, flags=re.MULTILINE)
    
    return text.strip()

LANG_CODES = {
    "German": "de",
    "Spanish": "es",
    "English": "en",
    "French": "fr"
}

# ==========================================
# 3. 侧边栏 & 设置
# ==========================================
with st.sidebar:
    st.header("⚙️ Settings")
    
    api_key = None
    if "GOOGLE_API_KEY" in st.secrets:
        api_key = st.secrets["GOOGLE_API_KEY"]
        st.success("✅ Cloud Key Loaded")
    else:
        api_key = st.text_input("Google API Key", type="password")

    model = None
    if api_key:
        os.environ["GOOGLE_API_KEY"] = api_key
        try:
            genai.configure(api_key=api_key)
            if not st.session_state.current_model_name:
                with st.spinner("🤖 Finding best model..."):
                    st.session_state.current_model_name = get_best_available_model()
            st.info(f"🧠 Model: `{st.session_state.current_model_name}`")
            model = genai.GenerativeModel(st.session_state.current_model_name)
        except Exception as e:
            st.error(f"Config Error: {e}")
    else:
        st.warning("⚠️ Please enter API Key")

    st.divider()
    
    language = st.selectbox("Target Language", ["German", "Spanish", "English", "French"])
    
    conn = get_db_connection()
    level_row = conn.cursor().execute("SELECT level FROM user_levels WHERE language=?", (language,)).fetchone()
    current_level = level_row[0] if level_row else "A1"
    conn.close()
    
    st.metric(f"{language} Level", current_level)

# ==========================================
# 4. 核心功能
# ==========================================
def extract_vocab_in_background(text, lang):
    if not model: return []
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
# 5. 主界面
# ==========================================
st.title("🗣️ Speak & Learn AI Tutor")

if not api_key: st.stop()

topic = st.chat_input(f"What do you want to learn in {language}?")

if topic:
    with st.chat_message("user"):
        st.write(topic)
    
    with st.chat_message("assistant"):
        response_placeholder = st.empty()
        full_response = ""
        
        if model:
            try:
                # 提示词优化：让 AI 知道它是为了朗读而写的
                prompt = f"""
                Write a short, engaging lesson about '{topic}' in {language} for a {current_level} level student.
                Format: 
                1. The full {language} text.
                2. Then a separator line.
                3. The English translation.
                
                DO NOT use JSON. Write natural text.
                """
                
                response_stream = model.generate_content(prompt, stream=True)
                
                for chunk in response_stream:
                    if chunk.text:
                        full_response += chunk.text
                        response_placeholder.markdown(full_response + "▌")
                
                response_placeholder.markdown(full_response)
                
                # === 修复点：先清洗，再朗读 ===
                if full_response:
                    with st.spinner("🔊 Generating clean audio..."):
                        # 1. 清洗掉 markdown 符号
                        clean_text = clean_text_for_tts(full_response)
                        
                        # 2. 生成语音
                        lang_code = LANG_CODES.get(language, 'en')
                        tts = gTTS(text=clean_text, lang=lang_code, slow=False)
                        
                        sound_file = BytesIO()
                        tts.write_to_fp(sound_file)
                        st.audio(sound_file, format='audio/mp3')

                # 提取单词
                if full_response:
                    with st.status("🧠 Processing vocabulary...", expanded=False) as status:
                        new_words = extract_vocab_in_background(full_response, language)
                        status.update(label=f"Saved {len(new_words)} words!", state="complete", expanded=False)
                        if new_words:
                            st.write(f"Added: `{'`, `'.join(new_words)}`")
                            
            except Exception as e:
                response_placeholder.error(f"❌ Error: {e}")
        else:
            st.error("Model not initialized.")

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
