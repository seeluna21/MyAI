import streamlit as st
import google.generativeai as genai
import json
import os
import re
import base64
import random
from datetime import datetime, timedelta
from io import BytesIO
from PIL import Image
from gtts import gTTS

# Firebase 库
import firebase_admin
from firebase_admin import credentials, firestore

# ==========================================
# 0. 基础配置 & Firebase 初始化
# ==========================================
st.set_page_config(page_title="AI Omni-Tutor V8.0 (Firebase Cloud)", page_icon="🔥", layout="wide")

# 初始化 Firebase (防止重复初始化报错)
if not firebase_admin._apps:
    # 从 Streamlit Secrets 读取配置
    key_dict = st.secrets["firebase"]
    cred = credentials.Certificate(key_dict)
    firebase_admin.initialize_app(cred)

# 获取数据库客户端
db = firestore.client()

# ==========================================
# 1. 数据库逻辑 (Firestore 版)
# ==========================================
# 我们不需要 init_db 了，Firestore 会自动创建集合

def get_user_level(lang):
    """从 Firestore 读取用户等级"""
    # 结构: collection "users" -> doc "config" -> field "German_level"
    # 这里简化：直接存一个 user_levels 集合
    docs = db.collection("user_levels").where("language", "==", lang).stream()
    for doc in docs:
        return doc.to_dict().get("level", "A1")
    return "A1"

def save_user_level(lang, level):
    """保存用户等级"""
    # 使用 set(..., merge=True) 来更新或创建
    doc_ref = db.collection("user_levels").document(lang) # 使用语言作为ID
    doc_ref.set({
        "language": lang,
        "level": level,
        "last_assessed": datetime.now().strftime("%Y-%m-%d")
    }, merge=True)

def save_vocab_to_db(word_data, lang):
    """保存单词到 Firestore"""
    # 结构: collection "vocab" -> document (word_lang)
    today_str = datetime.now().strftime("%Y-%m-%d")
    
    for item in word_data:
        word = item['word']
        doc_id = f"{word}_{lang}" # 唯一ID
        
        doc_ref = db.collection("vocab").document(doc_id)
        doc = doc_ref.get()
        
        # 如果单词不存在，才创建。如果存在，就不覆盖（保留复习进度）
        if not doc.exists:
            doc_ref.set({
                "word": word,
                "language": lang,
                "translation": item['trans'],
                "proficiency": 0,
                "last_reviewed": today_str,
                "next_review_date": today_str # 立即复习
            })

def get_review_words(lang, limit=10):
    """获取需要复习的单词"""
    today_str = datetime.now().strftime("%Y-%m-%d")
    vocab_ref = db.collection("vocab")
    
    # 查询条件：语言匹配 AND (日期<=今天)
    # Firestore 的复合查询需要索引，如果报错，去控制台点一下链接即可
    # 简单起见，我们先拉取该语言的所有词，在内存里过滤 (数据量不大时没问题)
    docs = vocab_ref.where("language", "==", lang).stream()
    
    due_words = []
    for doc in docs:
        data = doc.to_dict()
        data['id'] = doc.id # 存一下ID方便更新
        # 兼容旧数据或空日期
        next_date = data.get("next_review_date", "2000-01-01") 
        if next_date <= today_str:
            due_words.append(data)
            
    # 随机取 limit 个
    if len(due_words) > limit:
        return random.sample(due_words, limit)
    return due_words

def update_word_progress(doc_id, new_prof, next_date):
    """更新单词复习进度"""
    db.collection("vocab").document(doc_id).update({
        "proficiency": new_prof,
        "last_reviewed": datetime.now().strftime("%Y-%m-%d"),
        "next_review_date": next_date
    })

def get_total_review_count(lang):
    """统计待复习总数"""
    # 同样，为了简化索引，简单粗暴统计
    words = get_review_words(lang, limit=9999)
    return len(words)

# Session State 初始化
if "messages" not in st.session_state: st.session_state.messages = []
if "review_queue" not in st.session_state: st.session_state.review_queue = []
if "show_answer" not in st.session_state: st.session_state.show_answer = False
if "current_scenario" not in st.session_state: st.session_state.current_scenario = "Free Chat"

# ==========================================
# 2. 语音生成工具
# ==========================================
LANG_CODE_MAP = {"German": "de", "Spanish": "es", "English": "en", "French": "fr"}

def generate_audio_bytes(text, lang_name):
    try:
        speak_text = text.split("🇺🇸")[0].strip().replace("#", "").replace("*", "")
        if not speak_text: speak_text = text
        lang_code = LANG_CODE_MAP.get(lang_name, "en")
        if not speak_text.strip(): return None
        tts = gTTS(text=speak_text, lang=lang_code, slow=False)
        mp3_fp = BytesIO()
        tts.write_to_fp(mp3_fp)
        mp3_fp.seek(0)
        return mp3_fp
    except: return None

def make_audio_html(audio_fp, autoplay=False):
    if not audio_fp: return ""
    b64 = base64.b64encode(audio_fp.getvalue()).decode()
    autoplay_attr = "autoplay" if autoplay else ""
    return f"""<audio controls {autoplay_attr} style="width: 100%; margin-top: 5px;"><source src="data:audio/mp3;base64,{b64}" type="audio/mp3"></audio>"""

# ==========================================
# 3. AI 核心逻辑
# ==========================================
def get_model(): return "models/gemini-2.5-flash"

def extract_and_save_vocab(text, lang, model):
    try:
        prompt = f"""
        Analyze this text. Identify 3-5 key vocabulary words specifically for the {lang} language.
        Ignore the English explanations. Output ONLY a raw JSON list. 
        Format: [{{"word": "ForeignWord", "trans": "EnglishTranslation"}}, ...]
        Text: {text}
        """
        resp = model.generate_content(prompt)
        text_resp = resp.text
        if "```json" in text_resp: clean = text_resp.split("```json")[1].split("```")[0].strip()
        elif "```" in text_resp: clean = text_resp.split("```")[1].split("```")[0].strip()
        else: clean = text_resp.strip()
        
        data = json.loads(clean)
        # === 存入 Firebase ===
        save_vocab_to_db(data, lang)
        return [item['word'] for item in data]
    except: return []

# ==========================================
# 4. 侧边栏
# ==========================================
with st.sidebar:
    st.header("⚙️ Settings")
    api_key = st.secrets.get("GOOGLE_API_KEY")
    if not api_key: api_key = st.text_input("Google API Key", type="password")
    
    if api_key:
        os.environ["GOOGLE_API_KEY"] = api_key
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(get_model())
    else:
        st.warning("Please setup API Key")
        st.stop()

    language = st.selectbox("Language", ["German", "Spanish", "English", "French"])
    
    # 从 Firebase 读取 Level
    db_level = get_user_level(language)
    
    # 从 Firebase 读取待复习数
    review_count = get_total_review_count(language)

    st.divider()
    st.write("📊 **Level Override**")
    selected_level = st.selectbox("Adjust Difficulty:", ["A1", "A2", "B1", "B2", "C1", "C2"], index=["A1", "A2", "B1", "B2", "C1", "C2"].index(db_level))
    
    if selected_level != db_level:
        save_user_level(language, selected_level)
        st.rerun()

    st.metric("Review Due", f"{review_count} words")
    st.divider()
    
    scenarios = {"🔤 Translator": "Translator", "☕ Cafe": "Barista", "🛃 Customs": "Officer", "🤝 Friend": "Student", "🤖 Free Chat": "Tutor"}
    current_scenario = st.radio("Context:", list(scenarios.keys()))
    
    if current_scenario != st.session_state.current_scenario:
        st.session_state.messages = []
        st.session_state.current_scenario = current_scenario
        st.rerun()

    if st.button("🗑️ Clear Chat History"):
        st.session_state.messages = []
        st.rerun()
    
    # 移除了下载按钮，因为现在数据在云端，不需要手动备份了

# ==========================================
# 5. 主界面
# ==========================================
st.title(f"🔥 AI Tutor: {language} ({selected_level})")
tab1, tab2, tab3 = st.tabs(["💬 Chat & Learn", "📸 Photo Learning", "🧠 Review"])

# --- TAB 1: 聊天 ---
with tab1:
    chat_container = st.container()
    with chat_container:
        for msg in st.session_state.messages:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])
                if "audio_html" in msg and msg["audio_html"]: st.markdown(msg["audio_html"], unsafe_allow_html=True)
        st.empty() 

    input_placeholder = f"Type in {language}..."
    if scenarios[current_scenario] == "Translator": input_placeholder = "Enter text to translate & explain..."

    if user_input := st.chat_input(input_placeholder):
        st.session_state.messages.append({"role": "user", "content": user_input})
        with chat_container:
            with st.chat_message("user"): st.markdown(user_input)

        with chat_container:
            with st.chat_message("assistant"):
                placeholder = st.empty()
                full_response = ""
                try:
                    if scenarios[current_scenario] == "Translator":
                        prompt = f"""
                        Act as an expert language coach. Target: {language} ({selected_level}). Input: "{user_input}"
                        Task: Translate to natural {language}, explain why, give alternatives.
                        Output:
                        ### [Translation]
                        🇺🇸 Literal: [English]
                        **💡 Analysis:** ...
                        **🔄 Alternatives:** ...
                        **📝 Example:** ...
                        """
                    else:
                        prompt = f"""
                        Act as {scenarios[current_scenario]}. Lang: {language} ({selected_level}). User: "{user_input}"
                        Reply naturally. Structure:
                        1. Reply in {language}.
                        2. "🇺🇸 Translation: " English meaning.
                        3. Corrections in (parentheses).
                        """
                    
                    response = model.generate_content(prompt, stream=True)
                    for chunk in response:
                        if chunk.text:
                            full_response += chunk.text
                            placeholder.markdown(full_response + "▌")
                    placeholder.markdown(full_response)
                    
                    audio_bytes = generate_audio_bytes(full_response, language)
                    audio_html_autoplay = make_audio_html(audio_bytes, autoplay=True)
                    audio_html_store = make_audio_html(audio_bytes, autoplay=False)
                    
                    if audio_html_autoplay: st.markdown(audio_html_autoplay, unsafe_allow_html=True)
                    st.session_state.messages.append({"role": "assistant", "content": full_response, "audio_html": audio_html_store})

                    new_words = extract_and_save_vocab(full_response, language, model)
                    if new_words: st.toast(f"☁️ Saved to Cloud: {', '.join(new_words)}", icon="🔥")
                except Exception as e: st.error(f"Error: {e}")

# --- TAB 2: 拍照 ---
with tab2:
    uploaded_file = st.file_uploader("Upload photo", type=["jpg", "png", "jpeg"])
    if uploaded_file:
        image = Image.open(uploaded_file)
        st.image(image, width=300)
        if st.button("🔍 Analyze Photo"):
            with st.spinner("🤖 Analyzing..."):
                try:
                    prompt = f"""Describe in {language} (Level {selected_level}). Then "🇺🇸 Translation:". List 3 words."""
                    response = model.generate_content([prompt, image])
                    st.markdown(response.text)
                    audio_bytes = generate_audio_bytes(response.text, language)
                    if audio_bytes: st.markdown(make_audio_html(audio_bytes), unsafe_allow_html=True)
                    extract_and_save_vocab(response.text, language, model)
                except Exception as e: st.error(f"Vision Error: {e}")

# --- TAB 3: 复习 (Firebase 版) ---
with tab3:
    c_a, c_b = st.columns([4, 1])
    with c_a: st.subheader("Flashcards (Cloud Synced)")
    with c_b: 
        if st.button("🔄 Reload"): 
            st.session_state.review_queue = []
            st.rerun()

    if not st.session_state.review_queue:
        # 从 Firebase 获取数据
        try:
            rows = get_review_words(language)
            st.session_state.review_queue = rows
        except Exception as e:
            st.error(f"DB Error: {e}")
            rows = []
    
    if st.session_state.review_queue:
        # 这里的 current_card 是一个字典
        current_card = st.session_state.review_queue[0]
        word = current_card['word']
        translation = current_card['translation']
        prof = current_card['proficiency']
        doc_id = current_card['id']
        
        st.markdown(f"""
        <div style="padding: 20px; border-radius: 10px; background-color: #f0f2f6; text-align: center; margin-bottom: 20px;">
            <h1 style="color: #333; margin:0;">{word}</h1>
            <p style="color: #666;">Proficiency: {'⭐' * prof}</p>
        </div>
        """, unsafe_allow_html=True)
        
        c1, c2, c3 = st.columns([1, 1, 2])
        with c1:
            if st.button("🔊 Pronounce", key=f"btn_{word}"):
                res = generate_audio_bytes(word, language)
                if res: st.markdown(make_audio_html(res, autoplay=True), unsafe_allow_html=True)
        with c2:
            if st.button("👀 Reveal"): st.session_state.show_answer = True
        
        if st.session_state.show_answer:
            st.info(f"**Meaning:** {translation}")
            st.write("How hard was this?")
            b1, b2, b3 = st.columns(3)
            
            def handle_update(quality):
                if quality == 0: new_prof, days = max(0, prof - 1), 0
                elif quality == 1: new_prof, days = prof, 2
                else: new_prof, days = min(5, prof + 1), 3 + prof * 2
                
                next_date = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
                
                # 更新 Firebase
                update_word_progress(doc_id, new_prof, next_date)
                
                st.session_state.review_queue.pop(0)
                st.session_state.show_answer = False
                st.rerun()

            if b1.button("😭 Forgot", use_container_width=True): handle_update(0)
            if b2.button("😐 Hard", use_container_width=True): handle_update(1)
            if b3.button("😎 Easy", use_container_width=True): handle_update(2)
    else:
        st.success("🎉 You are all caught up! (Data saved to Firebase)")
