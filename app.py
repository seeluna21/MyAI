import streamlit as st
import google.generativeai as genai
import sqlite3
import json
import os
import pandas as pd
from datetime import datetime

# ==========================================
# 1. 基础配置 & 数据库
# ==========================================
st.set_page_config(page_title="AI Language Tutor", page_icon="🚀", layout="wide")

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

# 初始化数据库
init_db()

# 初始化 Session State
if "messages" not in st.session_state:
    st.session_state.messages = []
if "current_model_name" not in st.session_state:
    st.session_state.current_model_name = None

# ==========================================
# 2. 智能模型选择函数 (核心更新)
# ==========================================
def get_best_available_model():
    """
    自动寻找当前 API Key 可用的最佳模型。
    优先级: 1.5-Flash -> 1.5-Pro -> 1.0-Pro
    """
    try:
        # 获取所有支持 generateContent 的模型
        model_list = []
        for m in genai.list_models():
            if 'generateContent' in m.supported_generation_methods:
                model_list.append(m.name)
        
        # 打印出来方便调试（在终端可以看到）
        print(f"Available models: {model_list}")

        # 优先级逻辑
        # 1. 优先尝试 Flash (速度最快)
        for m in model_list:
            if "flash" in m and "1.5" in m: return m
        
        # 2. 其次尝试 1.5 Pro (效果最好)
        for m in model_list:
            if "pro" in m and "1.5" in m: return m
            
        # 3. 保底尝试任何带 gemini 的模型
        for m in model_list:
            if "gemini" in m: return m
            
        # 4. 如果还没找到，返回默认值碰碰运气
        return "models/gemini-1.5-flash"
        
    except Exception as e:
        # 如果列出模型失败（比如 Key 只有特定权限），返回一个保守的默认值
        print(f"Error listing models: {e}")
        return "models/gemini-pro"

# ==========================================
# 3. 侧边栏 & 设置
# ==========================================
with st.sidebar:
    st.header("⚙️ Settings")
    
    # 获取 API Key
    api_key = None
    if "GOOGLE_API_KEY" in st.secrets:
        api_key = st.secrets["GOOGLE_API_KEY"]
        st.success("✅ Cloud Key Loaded")
    else:
        api_key = st.text_input("Google API Key", type="password")

    # 配置 Google Gemini 并自动选模型
    model = None
    if api_key:
        os.environ["GOOGLE_API_KEY"] = api_key
        try:
            genai.configure(api_key=api_key)
            
            # === 自动选择模型 ===
            if not st.session_state.current_model_name:
                with st.spinner("🤖 Finding the best model for you..."):
                    best_model = get_best_available_model()
                    st.session_state.current_model_name = best_model
            
            # 显示当前使用的模型
            st.info(f"🧠 Model: `{st.session_state.current_model_name}`")
            
            # 实例化模型
            model = genai.GenerativeModel(st.session_state.current_model_name)
            
        except Exception as e:
            st.error(f"Config Error: {e}")
    else:
        st.warning("⚠️ Please enter API Key")

    st.divider()
    
    # 语言选择
    language = st.selectbox("Target Language", ["German", "Spanish", "English", "French"])
    
    # 读取数据
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
    """提取单词"""
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
    """调整等级"""
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
st.title("🚀 Auto-Model AI Tutor")

if not api_key:
    st.stop()

topic = st.chat_input(f"What do you want to learn in {language}?")

if topic:
    # 1. 显示用户输入
    with st.chat_message("user"):
        st.write(topic)
    
    # 2. AI 生成 (手动流式)
    with st.chat_message("assistant"):
        response_placeholder = st.empty()
        full_response = ""
        
        if model:
            try:
                prompt = f"""
                Write a short, engaging lesson about '{topic}' in {language} for a {current_level} level student.
                Include the English translation at the end.
                DO NOT use JSON. Just write natural text.
                """
                
                # 尝试生成
                response_stream = model.generate_content(prompt, stream=True)
                
                for chunk in response_stream:
                    if chunk.text:
                        full_response += chunk.text
                        response_placeholder.markdown(full_response + "▌")
                
                response_placeholder.markdown(full_response)
                
                # 3. 提取单词
                if full_response:
                    with st.status("🧠 Processing vocabulary...", expanded=False) as status:
                        new_words = extract_vocab_in_background(full_response, language)
                        status.update(label=f"Saved {len(new_words)} words!", state="complete", expanded=False)
                        if new_words:
                            st.write(f"Added: `{'`, `'.join(new_words)}`")
                            
            except Exception as e:
                # 即使模型选择失败，这里也能捕获到
                response_placeholder.error(f"❌ Error: {e}")
                st.error("Tip: Check if your API Key has access to the selected model.")
        else:
            st.error("Model not initialized.")

    # 4. 反馈按钮
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
