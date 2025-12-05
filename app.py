import streamlit as st
import google.generativeai as genai
import sqlite3
import json
import os
import pandas as pd
import re
import asyncio
import edge_tts
from datetime import datetime, timedelta
from io import BytesIO
from PIL import Image

# ==========================================
# 1. 基础配置 & 数据库
# ==========================================
st.set_page_config(page_title="AI Omni-Tutor", page_icon="🦄", layout="wide")

def get_db_connection():
    conn = sqlite3.connect("web_language_brain_v3.db")
    # conn = sqlite3.connect("web_language_brain.db")
    return conn

def init_db():
    conn = get_db_connection()
    c = conn.cursor()
    # 用户等级表
    c.execute('''CREATE TABLE IF NOT EXISTS user_levels 
                 (language TEXT PRIMARY KEY, level TEXT, last_assessed DATE)''')
    # 词汇表 (增加了 next_review_date 用于复习算法)
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

# ==========================================
# 2. 核心工具函数 (TTS, Vision, Clean)
# ==========================================

# 2.1 微软 Edge TTS (超逼真语音)
# 语音角色映射表
VOICE_MAP = {
    "German": "de-DE-KatjaNeural",    # 德国-卡佳 (女，超自然)
    "Spanish": "es-ES-AlvaroNeural",  # 西班牙-阿尔瓦罗 (男)
    "English": "en-US-AriaNeural",    # 美国-Aria
    "French": "fr-FR-DeniseNeural"    # 法国-丹尼斯
}

async def generate_audio_edge(text, lang):
    """使用 Edge TTS 生成语音流"""
    voice = VOICE_MAP.get(lang, "en-US-AriaNeural")
    communicate = edge_tts.Communicate(text, voice)
    
    # 写入内存流
    mp3_fp = BytesIO()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            mp3_fp.write(chunk["data"])
    mp3_fp.seek(0)
    return mp3_fp

# 2.2 文本清洗
def clean_text_for_tts(text):
    text = text.replace('**', '').replace('*', '').replace('##', '').replace('#', '').replace('`', '')
    text = re.sub(r'^\s*-\s+', '', text, flags=re.MULTILINE)
    return text.strip()

# 2.3 智能模型选择
def get_best_model():
    # 简单粗暴：直接用 Flash，它现在支持 Vision 且速度快
    return "models/gemini-1.5-flash"

# ==========================================
# 3. 侧边栏设置
# ==========================================
with st.sidebar:
    st.header("⚙️ Settings")
    
    # API Key
    api_key = st.secrets.get("GOOGLE_API_KEY") or st.text_input("Google API Key", type="password")
    
    if api_key:
        os.environ["GOOGLE_API_KEY"] = api_key
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(get_best_model())
    else:
        st.warning("⚠️ Need API Key")
        st.stop()

    language = st.selectbox("Target Language", ["German", "Spanish", "English", "French"])
    
    # 获取数据统计
    conn = get_db_connection()
    level_row = conn.cursor().execute("SELECT level FROM user_levels WHERE language=?", (language,)).fetchone()
    current_level = level_row[0] if level_row else "A1"
    
    # 获取待复习单词数 (今天之前的)
    today = datetime.now().strftime("%Y-%m-%d")
    review_count = conn.cursor().execute(
        "SELECT count(*) FROM vocab WHERE language=? AND (next_review_date <= ? OR next_review_date IS NULL)", 
        (language, today)).fetchone()[0]
    conn.close()
    
    st.metric(f"Current Level", current_level)
    st.metric(f"Due for Review", f"{review_count} words", delta_color="off")

# ==========================================
# 4. 核心功能逻辑
# ==========================================

# 4.1 提取并保存单词 (后台处理)
def extract_and_save_vocab(text, lang):
    prompt = f"""
    Extract 3-5 key vocabulary words from this {lang} text.
    Format JSON: [{{"word": "word1", "trans": "english_meaning"}}, ...]
    Text: {text}
    """
    try:
        resp = model.generate_content(prompt)
        clean = resp.text.replace('```json', '').replace('```', '').strip()
        data = json.loads(clean)
        
        conn = get_db_connection()
        today_dt = datetime.now()
        next_review = (today_dt + timedelta(days=1)).strftime("%Y-%m-%d") # 默认明天复习
        
        for item in data:
            # 插入或忽略
            conn.cursor().execute(
                '''INSERT OR IGNORE INTO vocab (word, language, translation, last_reviewed, next_review_date, proficiency) 
                   VALUES (?, ?, ?, ?, ?, 0)''', 
                (item['word'], lang, item['trans'], today_dt.strftime("%Y-%m-%d"), next_review)
            )
        conn.commit()
        conn.close()
        return [d['word'] for d in data]
    except:
        return []

# 4.2 更新等级
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
# 5. 主界面 (Tab 布局)
# ==========================================
st.title("🦄 AI Omni-Tutor")
tab1, tab2, tab3 = st.tabs(["💬 Chat & Learn", "📸 Photo Learning", "🧠 Flashcard Review"])

# --- TAB 1: 文本对话 & 语音 ---
with tab1:
    st.caption("Learn by conversation. AI will generate audio automatically.")
    topic = st.chat_input(f"Topic in {language}...")
    
    if topic:
        with st.chat_message("user"): st.write(topic)
        with st.chat_message("assistant"):
            placeholder = st.empty()
            full_text = ""
            
            # 生成文本
            prompt = f"Write a lesson about '{topic}' in {language} (Level {current_level}). Include English translation at bottom."
            stream = model.generate_content(prompt, stream=True)
            
            for chunk in stream:
                if chunk.text:
                    full_text += chunk.text
                    placeholder.markdown(full_text + "▌")
            placeholder.markdown(full_text)
            
            # 生成语音 (Edge TTS) & 提取单词
            if full_text:
                col_a, col_b = st.columns([1, 1])
                with col_a:
                    with st.spinner("🔊 Synthesizing natural speech..."):
                        clean_txt = clean_text_for_tts(full_text)
                        # 运行异步 TTS
                        audio_fp = asyncio.run(generate_audio_edge(clean_txt, language))
                        st.audio(audio_fp, format='audio/mp3')
                
                with col_b:
                    with st.status("📥 Saving vocabulary...", expanded=False) as status:
                        new_words = extract_and_save_vocab(full_text, language)
                        status.update(label=f"Saved: {', '.join(new_words)}", state="complete")

            # 难度反馈
            st.write("---")
            b1, b2, b3 = st.columns(3)
            if b1.button("Too Easy ⬆️", key="t1_easy"): update_level(language, "up"); st.rerun()
            if b2.button("Just Right ✅", key="t1_ok"): st.toast("Kept")
            if b3.button("Too Hard ⬇️", key="t1_hard"): update_level(language, "down"); st.rerun()

# --- TAB 2: 拍照学习 (Vision) ---
with tab2:
    st.caption("Upload a photo to learn related vocabulary.")
    uploaded_file = st.file_uploader("Choose an image...", type=["jpg", "png", "jpeg"])
    
    if uploaded_file:
        image = Image.open(uploaded_file)
        st.image(image, caption="Uploaded Image", width=300)
        
        if st.button("🔍 Analyze & Teach Me"):
            with st.spinner("🤖 Vision AI is looking at your photo..."):
                prompt = f"""
                Look at this image. 
                1. Describe what you see in {language} (Level {current_level}).
                2. List 5 key vocabulary words from the image with English translations.
                """
                # Gemini 接收 [文本, 图片]
                response = model.generate_content([prompt, image])
                st.markdown(response.text)
                
                # 自动生成语音
                clean_txt = clean_text_for_tts(response.text)
                audio_fp = asyncio.run(generate_audio_edge(clean_txt, language))
                st.audio(audio_fp, format='audio/mp3')
                
                # 存词
                extract_and_save_vocab(response.text, language)

# --- TAB 3: 复习模式 (Review) ---
with tab3:
    st.subheader("🧠 Spaced Repetition Review")
    
    # 如果队列为空，从数据库加载
    if not st.session_state.review_queue:
        conn = get_db_connection()
        today = datetime.now().strftime("%Y-%m-%d")
        # 选取复习时间到了的词，或者 proficiency 低的词
        rows = conn.cursor().execute(
            "SELECT word, translation, proficiency FROM vocab WHERE language=? AND (next_review_date <= ? OR next_review_date IS NULL) ORDER BY random() LIMIT 10", 
            (language, today)).fetchall()
        conn.close()
        st.session_state.review_queue = rows
    
    # 还有词没复习完
    if st.session_state.review_queue:
        # 获取当前词
        word, translation, prof = st.session_state.review_queue[0]
        
        # 卡片 UI
        st.info(f"🔥 Proficiency: {prof}/5")
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
                    days = 1 # 忘了就明天再复习
                elif result == "ok":
                    new_prof = prof # 保持
                    days = 2
                elif result == "easy":
                    new_prof = min(5, prof + 1)
                    days = 3 + new_prof * 2 # 越熟练，间隔越久
                
                next_date = (today_dt + timedelta(days=days)).strftime("%Y-%m-%d")
                
                conn.cursor().execute(
                    "UPDATE vocab SET proficiency=?, last_reviewed=?, next_review_date=? WHERE word=? AND language=?",
                    (new_prof, today_dt.strftime("%Y-%m-%d"), next_date, word, language)
                )
                conn.commit()
                conn.close()
                
                # 移除当前词，进入下一个
                st.session_state.review_queue.pop(0)
                st.session_state.show_answer = False
                st.rerun()

            if c1.button("😭 Forgot"): handle_review("forget")
            if c2.button("😐 Hard"): handle_review("ok")
            if c3.button("😎 Easy"): handle_review("easy")
            
    else:
        st.balloons()
        st.success("🎉 All caught up! No words to review for today.")
        if st.button("Load Random Words (Extra Practice)"):
             # 强制加载随机词用于练习
            conn = get_db_connection()
            rows = conn.cursor().execute(
                "SELECT word, translation, proficiency FROM vocab WHERE language=? ORDER BY random() LIMIT 5", 
                (language,)).fetchall()
            conn.close()
            st.session_state.review_queue = rows
            st.rerun()
