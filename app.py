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
# 1. 基础配置 & 数据库
# ==========================================
st.set_page_config(page_title="AI Omni-Tutor V4", page_icon="🦄", layout="wide")

def get_db_connection():
    # 强制使用新数据库，避免结构冲突
    conn = sqlite3.connect("web_language_brain_v4.db") 
    return conn

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
# 2. 核心工具函数
# ==========================================

# 2.1 Edge TTS
VOICE_MAP = {
    "German": "de-DE-KatjaNeural",    
    "Spanish": "es-ES-AlvaroNeural",  
    "English": "en-US-AriaNeural",    
    "French": "fr-FR-DeniseNeural"    
}

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
        print(f"TTS Error: {e}")
        return None

# 2.2 文本清洗
def clean_text_for_tts(text):
    # 去除括号里的纠错内容，只读正文
    text = re.sub(r'\(.*?\)', '', text) 
    text = text.replace('**', '').replace('*', '').replace('`', '')
    return text.strip()

# 2.3 提取单词 (后台静默运行)
def extract_and_save_vocab(text, lang):
    try:
        # 使用简单的正则或 prompt 提取，这里为了速度不阻塞对话，建议只提取关键词
        # 实际生产中可以异步调用，这里为了演示保留同步但放在 status 框里
        model = genai.GenerativeModel("models/gemini-2.5-flash")
        prompt = f"""
        Extract 3 difficult vocabulary words from this {lang} text.
        Output JSON ONLY: [{{"word": "word1", "trans": "english_meaning"}}]
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
# 3. 侧边栏设置
# ==========================================
with st.sidebar:
    st.header("⚙️ Settings")
    api_key = st.secrets.get("GOOGLE_API_KEY") or st.text_input("Google API Key", type="password")
    
    if api_key:
        os.environ["GOOGLE_API_KEY"] = api_key
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("models/gemini-1.5-flash")
    else:
        st.warning("⚠️ Need API Key")
        st.stop()

    language = st.selectbox("Target Language", ["German", "Spanish", "English", "French"])
    
    # 数据库统计
    conn = get_db_connection()
    level_row = conn.cursor().execute("SELECT level FROM user_levels WHERE language=?", (language,)).fetchone()
    current_level = level_row[0] if level_row else "A1"
    
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
    
    st.divider()
    
    # === 情景选择器 ===
    st.subheader("🎭 Choose Scenario")
    scenarios = {
        "☕ Cafe Ordering": "You are a barista in a busy cafe. You are impatient but polite. The user wants to order coffee.",
        "🛃 Customs Officer": "You are a strict customs officer at the airport. Ask about the user's visa, duration of stay, and purpose of visit.",
        "🤝 New Friend": "You are a friendly student at a university party. You want to know about the user's hobbies.",
        "🛒 Grocery Store": "You are a cashier at a supermarket. Ask if the user has a loyalty card and if they need a bag.",
        "🤖 Free Chat": "You are a helpful language tutor. Chat about anything."
    }
    
    selected_scenario = st.radio("Roleplay Context:", list(scenarios.keys()))
    
    # 如果切换了场景，清空历史
    if selected_scenario != st.session_state.current_scenario:
        st.session_state.messages = []
        st.session_state.current_scenario = selected_scenario
        st.rerun()
        
    if st.button("🗑️ Clear Chat History"):
        st.session_state.messages = []
        st.rerun()

# ==========================================
# 4. 主界面
# ==========================================
st.title("🦄 AI Omni-Tutor: Roleplay Mode")
tab1, tab2, tab3 = st.tabs(["💬 Roleplay Chat", "📸 Photo Learning", "🧠 Flashcards"])

# --- TAB 1: 角色扮演对话 (核心升级) ---
with tab1:
    st.caption(f"Scenario: **{selected_scenario}** | Level: **{current_level}**")
    
    # 1. 显示历史消息
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
    
    # 2. 处理用户输入
    if user_input := st.chat_input(f"Say something in {language}..."):
        # 显示用户消息
        st.session_state.messages.append({"role": "user", "content": user_input})
        with st.chat_message("user"):
            st.markdown(user_input)
            
        # 生成 AI 回复
        with st.chat_message("assistant"):
            placeholder = st.empty()
            full_response = ""
            
            # 构造 Prompt：核心魔法
            system_instruction = f"""
            Act as a character in this scenario: {scenarios[selected_scenario]}.
            Language: {language}.
            User Level: {current_level}.
            
            Rules:
            1. Keep responses concise (1-3 sentences) to keep the conversation flowing.
            2. If the user makes a grammar mistake, correct it gently inside parentheses at the end. Example: "Ja, das ist gut. (Correction: 'das' should be 'der')"
            3. Stay in character!
            """
            
            # 将历史记录转换为 Gemini 格式
            history_gemini = [{"role": "user", "parts": [system_instruction]}] 
            for m in st.session_state.messages[:-1]: # 不包含最新的 user_input，因为下面单独发
                role = "user" if m["role"] == "user" else "model"
                history_gemini.append({"role": role, "parts": [m["content"]]})
            history_gemini.append({"role": "user", "parts": [user_input]})
            
            # 流式生成
            try:
                chat = model.start_chat(history=history_gemini[:-1])
                response = chat.send_message(user_input, stream=True)
                
                for chunk in response:
                    if chunk.text:
                        full_response += chunk.text
                        placeholder.markdown(full_response + "▌")
                placeholder.markdown(full_response)
                
                # 保存 AI 消息
                st.session_state.messages.append({"role": "assistant", "content": full_response})
                
                # --- 语音播放 (只播放最新的一句) ---
                clean_txt = clean_text_for_tts(full_response)
                audio_fp = asyncio.run(generate_audio_edge(clean_txt, language))
                if audio_fp:
                    st.audio(audio_fp, format='audio/mp3', autoplay=True)
                
                # --- 自动提取单词 ---
                with st.status("🧠 Analyzing vocabulary...", expanded=False):
                    new_words = extract_and_save_vocab(full_response, language)
                    if new_words:
                        st.write(f"Added to review: **{', '.join(new_words)}**")
                    else:
                        st.write("No difficult words found.")
                        
            except Exception as e:
                st.error(f"Error: {e}")

# --- TAB 2: 拍照学习 (保持不变) ---
with tab2:
    uploaded_file = st.file_uploader("Upload photo", type=["jpg", "png", "jpeg"])
    if uploaded_file:
        image = Image.open(uploaded_file)
        st.image(image, width=300)
        if st.button("🔍 Analyze"):
            with st.spinner("👀 Looking..."):
                try:
                    prompt = f"Describe in {language} (Level {current_level}) and list 3 words."
                    response = model.generate_content([prompt, image])
                    st.markdown(response.text)
                    audio_fp = asyncio.run(generate_audio_edge(clean_text_for_tts(response.text), language))
                    if audio_fp: st.audio(audio_fp, format='audio/mp3')
                    extract_and_save_vocab(response.text, language)
                except Exception as e: st.error(str(e))

# --- TAB 3: 复习模式 (保持不变) ---
with tab3:
    if st.button("🔄 Next Batch"):
        st.session_state.review_queue = []
        st.rerun()

    if not st.session_state.review_queue:
        conn = get_db_connection()
        today = datetime.now().strftime("%Y-%m-%d")
        try:
            rows = conn.cursor().execute(
                "SELECT word, translation, proficiency FROM vocab WHERE language=? AND (next_review_date <= ? OR next_review_date IS NULL) ORDER BY random() LIMIT 10", 
                (language, today)).fetchall()
            st.session_state.review_queue = rows
        except: pass
        conn.close()

    if st.session_state.review_queue:
        word, translation, prof = st.session_state.review_queue[0]
        st.progress(prof/5, text=f"Mastery: {prof}/5")
        st.markdown(f"## {word}")
        
        if st.button("🔊 Pronounce"):
             audio_fp = asyncio.run(generate_audio_edge(word, language))
             if audio_fp: st.audio(audio_fp, format='audio/mp3', autoplay=True)

        if st.button("👀 Reveal"): st.session_state.show_answer = True
            
        if st.session_state.show_answer:
            st.success(f"**{translation}**")
            c1, c2, c3 = st.columns(3)
            def process(res):
                conn = get_db_connection()
                today_dt = datetime.now()
                new_prof = max(0, prof-1) if res == "forget" else (prof if res == "ok" else min(5, prof+1))
                days = 1 if res == "forget" else (2 if res == "ok" else 3 + new_prof * 2)
                next_date = (today_dt + timedelta(days=days)).strftime("%Y-%m-%d")
                conn.cursor().execute("UPDATE vocab SET proficiency=?, last_reviewed=?, next_review_date=? WHERE word=? AND language=?",
                                      (new_prof, today_dt.strftime("%Y-%m-%d"), next_date, word, language))
                conn.commit()
                conn.close()
                st.session_state.review_queue.pop(0)
                st.session_state.show_answer = False
                st.rerun()
            if c1.button("😭 Forgot"): process("forget")
            if c2.button("😐 Hard"): process("ok")
            if c3.button("😎 Easy"): process("easy")
    else:
        st.balloons()
        st.success("No reviews due!")
