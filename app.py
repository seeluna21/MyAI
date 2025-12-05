import streamlit as st
import google.generativeai as genai
import sqlite3
import json
import os
import pandas as pd
from datetime import datetime
from gtts import gTTS  # <--- 新增：文本转语音库
from io import BytesIO # <--- 新增：内存文件处理

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
# 2. 智能模型选择 & TTS 工具
# ==========================================
def get_best_available_model():
    """自动寻找最佳模型"""
    try:
        model_list = []
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                model_list.append(m.name)
        
        # 优先级：Flash -> Pro -> Basic
        for m in model_list:
            if "flash" in m and "1.5" in m: return m
        for m in model_list:
            if "pro" in m and "1.5" in m: return m
        for m in model_list:
            if "gemini" in m: return m
        return "models/gemini-1.5-flash"
    except Exception as e:
        return "models/gemini-pro"

# 语言代码映射 (用于语音合成)
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
    vocab_count = conn.cursor().execute("SELECT count(*) FROM vocab WHERE language=?", (language,)).fetchone()[0]
    conn.close()
    
    st.metric(f"{language} Level", current_level)

# ==========================================
# 4. 功能函数
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
                # 提示词微调：让它把外语放前面，翻译放后面，这样听起来比较连贯
                prompt = f"""
                Write a short, engaging lesson about '{topic}' in {language} for a {current_level} level student.
                IMPORTANT: Write the full {language} text FIRST. Then add the English translation at the very bottom.
                DO NOT use JSON. Just write natural text.
                """
                
                response_stream = model.generate_content(prompt, stream=True)
                
                for chunk in response_stream:
                    if chunk.text:
                        full_response += chunk.text
                        response_placeholder.markdown(full_response + "▌")
                
                response_placeholder.markdown(full_response)
                
                # === 新增：生成语音 (TTS) ===
                if full_response:
                    with st.spinner("🔊 Generating audio..."):
                        # 获取对应的语言代码 (例如 German -> de)
                        lang_code = LANG_CODES.get(language, 'en')
                        
                        # 创建语音对象
                        tts = gTTS(text=full_response, lang=lang_code, slow=False)
                        
                        # 写入内存 (不存硬盘，速度快)
                        sound_file = BytesIO()
                        tts.write_to_fp(sound_file)
                        
                        # 显示播放器
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
