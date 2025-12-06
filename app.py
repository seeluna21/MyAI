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
st.set_page_config(page_title="AI Omni-Tutor", page_icon="🦄", layout="wide")

# 🟢 修复 1: 强制使用新数据库文件名，解决 "no such column" 报错
def get_db_connection():
    # 只要改这个名字，系统就会自动创建一个新的数据库文件，彻底解决旧数据冲突
    return sqlite3.connect("web_language_brain_fixed.db")

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

# ==========================================
# 2. 核心功能 (带容错保护)
# ==========================================

VOICE_MAP = {
    "German": "de-DE-KatjaNeural",
    "Spanish": "es-ES-AlvaroNeural",
    "English": "en-US-AriaNeural",
    "French": "fr-FR-DeniseNeural"
}

# 🟢 修复 2: 增加 try-except 保护，防止网络问题导致程序闪退
async def generate_audio_edge(text, lang):
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
        # 如果出错了（比如没开梯子），只在后台打印，不让前台崩溃
        print(f"⚠️ TTS Error (网络问题): {e}")
        return None

def clean_text_for_tts(text):
    text = text.replace('**', '').replace('*', '').replace('##', '').replace('#', '').replace('`', '')
    text = re.sub(r'^\s*-\s+', '', text, flags=re.MULTILINE)
    return text.strip()

# 🟢 修复 3: 自动寻找可用的模型，解决 404 问题
def get_best_model():
    try:
        # 尝试列出所有模型
        models = [m.name for m in genai.list_models() if 'generateContent' in m.supported_generation_methods]
        
        # 优先策略：Flash -> Pro -> 任意Gemini
        for m in models: 
            if "flash" in m and "1.5" in m: return m
        for m in models: 
            if "pro" in m and "1.5" in m: return m
        
        return models[0] if models else "models/gemini-pro"
    except:
        # 如果列出失败，盲猜一个最常用的
        return "models/gemini-1.5-flash"

# ==========================================
# 3. 侧边栏设置
# ==========================================
with st.sidebar:
    st.header("⚙️ Settings")
    
    # 优先从 Secrets 读取 Key
    api_key = st.secrets.get("GOOGLE_API_KEY")
    if not api_key:
        api_key = st.text_input("Google API Key", type="password")
    
    if api_key:
        os.environ["GOOGLE_API_KEY"] = api_key
        genai.configure(api_key=api_key)
        
        # 自动加载模型
        try:
            model_name = get_best_model()
            model = genai.GenerativeModel(model_name)
        except Exception as e:
            st.error(f"API Key Error: {e}")
            st.stop()
    else:
        st.warning("⚠️ Please enter API Key")
        st.stop()

    language = st.selectbox("Target Language", ["German", "Spanish", "English", "French"])
    
    conn = get_db_connection()
    level_row = conn.cursor().execute("SELECT level FROM user_levels WHERE language=?", (language,)).fetchone()
    current_level = level_row[0] if level_row else "A1"
    
    # 修复查询
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        review_count = conn.cursor().execute(
            "SELECT count(*) FROM vocab WHERE language=? AND (next_review_date <= ? OR next_review_date IS NULL)", 
            (language, today)).fetchone()[0]
    except:
        review_count = 0
    conn.close()
    
    st.metric(f"Current Level", current_level)
    st.metric(f"Due for Review", f"{review_count} words")

# ==========================================
# 4. 辅助函数
# ==========================================
def extract_and_save_vocab(text, lang):
    prompt = f"""
    Extract 3-5 key vocabulary words from this {lang} text.
    Format JSON: [{{"word": "word1", "trans": "english_meaning"}}, ...]
    Text: {text}
    """
    try:
        resp = model.generate_content(prompt)
        text_resp = resp.text
        # 增强的 JSON 清洗逻辑
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

def update_level(lang, direction):
    levels = ["A1", "A2", "B1", "B2", "C1", "C2"]
    idx = levels.index(current_level) if current_level in levels else 0
    if direction == "up" and idx < 5: idx += 1
    if direction == "down" and idx > 0: idx -= 1
    
    conn = get_db_connection()
    conn.cursor().execute("INSERT OR REPLACE INTO user_levels (language, level, last_assessed) VALUES (?, ?, ?)", 
                          (lang, levels[idx], datetime.now().strftime("%Y-%m-%d")))
    conn.commit()
    conn.close()
    st.toast(f"Level adjusted to {levels[idx]}")
    return levels[idx]

# ==========================================
# 5. 主界面
# ==========================================
st.title("🦄 AI Omni-Tutor")
tab1, tab2, tab3 = st.tabs(["💬 Chat & Learn", "📸 Photo Learning", "🧠 Flashcard Review"])

# --- TAB 1 ---
with tab1:
    st.caption("Learn by conversation. AI will generate audio automatically.")
    topic = st.chat_input(f"Topic in {language}...")
    
    if topic:
        with st.chat_message("user"): st.write(topic)
        with st.chat_message("assistant"):
            placeholder = st.empty()
            full_text = ""
            
            try:
                prompt = f"Write a lesson about '{topic}' in {language} (Level {current_level}). Include English translation at bottom."
                stream = model.generate_content(prompt, stream=True)
                
                for chunk in stream:
                    if chunk.text:
                        full_text += chunk.text
                        placeholder.markdown(full_text + "▌")
                placeholder.markdown(full_text)
                
                if full_text:
                    col_a, col_b = st.columns([1, 1])
                    with col_a:
                        with st.spinner("🔊 Synthesizing speech..."):
                            clean_txt = clean_text_for_tts(full_text)
                            # 运行异步 TTS
                            try:
                                audio_fp = asyncio.run(generate_audio_edge(clean_txt, language))
                                if audio_fp:
                                    st.audio(audio_fp, format='audio/mp3')
                                else:
                                    st.warning("⚠️ 语音生成失败 (请检查网络/代理)")
                            except Exception as e:
                                st.warning(f"⚠️ 语音组件错误: {e}")
                    
                    with col_b:
                        with st.status("📥 Saving vocabulary...", expanded=False) as status:
                            new_words = extract_and_save_vocab(full_text, language)
                            if new_words:
                                status.update(label=f"Saved: {', '.join(new_words)}", state="complete")
                            else:
                                status.update(label="No new words found", state="complete")
            except Exception as e:
                st.error(f"AI Generation Error: {e}")

            st.write("---")
            b1, b2, b3 = st.columns(3)
            if b1.button("Too Easy ⬆️", key="t1_easy"): update_level(language, "up"); st.rerun()
            if b2.button("Just Right ✅", key="t1_ok"): st.toast("Kept")
            if b3.button("Too Hard ⬇️", key="t1_hard"): update_level(language, "down"); st.rerun()

# --- TAB 2 ---
with tab2:
    st.caption("Upload a photo to learn related vocabulary.")
    uploaded_file = st.file_uploader("Choose an image...", type=["jpg", "png", "jpeg"])
    
    if uploaded_file:
        image = Image.open(uploaded_file)
        st.image(image, caption="Uploaded Image", width=300)
        
        if st.button("🔍 Analyze & Teach Me"):
            with st.spinner("🤖 Vision AI is looking at your photo..."):
                try:
                    prompt = f"""
                    Look at this image. 
                    1. Describe what you see in {language} (Level {current_level}).
                    2. List 5 key vocabulary words from the image with English translations.
                    """
                    response = model.generate_content([prompt, image])
                    st.markdown(response.text)
                    
                    clean_txt = clean_text_for_tts(response.text)
                    try:
                        audio_fp = asyncio.run(generate_audio_edge(clean_txt, language))
                        if audio_fp: st.audio(audio_fp, format='audio/mp3')
                    except: pass
                    
                    extract_and_save_vocab(response.text, language)
                except Exception as e:
                    st.error(f"Vision Error: {e}")

# --- TAB 3 ---
with tab3:
    st.subheader("🧠 Spaced Repetition Review")
    
    if st.button("🔄 Refresh Queue"):
        st.session_state.review_queue = []
        st.rerun()

    if not st.session_state.review_queue:
        conn = get_db_connection()
        today = datetime.now().strftime("%Y-%m-%d")
        try:
            rows = conn.cursor().execute(
                "SELECT word, translation, proficiency FROM vocab WHERE language=? AND (next_review_date <= ? OR next_review_date IS NULL) ORDER BY random() LIMIT 10", 
                (language, today)).fetchall()
        except:
            rows = []
        conn.close()
        st.session_state.review_queue = rows
    
    if st.session_state.review_queue:
        word, translation, prof = st.session_state.review_queue[0]
        st.progress(prof/5, text=f"Proficiency: {prof}/5")
        st.markdown(f"# {word}")
        
        # 翻转卡片
        if st.button("👀 Show Meaning"):
            st.session_state.show_answer = True
            
        if st.session_state.show_answer:
            st.success(f"**Meaning:** {translation}")
            
            c1, c2, c3 = st.columns(3)
            
            def handle_review(result):
                conn = get_db_connection()
                today_dt = datetime.now()
                
                if result == "forget":
                    new_prof = max(0, prof - 1)
                    days = 1 
                elif result == "ok":
                    new_prof = prof
                    days = 2
                elif result == "easy":
                    new_prof = min(5, prof + 1)
                    days = 3 + new_prof * 2
                
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
        st.balloons()
        st.success("🎉 All caught up! No words to review for today.")
        if st.button("Load Random Words"):
            conn = get_db_connection()
            rows = conn.cursor().execute(
                "SELECT word, translation, proficiency FROM vocab WHERE language=? ORDER BY random() LIMIT 5", 
                (language,)).fetchall()
            conn.close()
            st.session_state.review_queue = rows
            st.rerun()
