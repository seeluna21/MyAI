# 🦄 AI Omni-Tutor | Your Personal Language Coach

[![Streamlit App](https://static.streamlit.io/badges/streamlit_badge_black_white.svg)](https://你的streamlit应用链接.streamlit.app)
![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)
![License](https://img.shields.io/badge/License-MIT-green.svg)

**AI Omni-Tutor** is a multimodal AI language learning assistant powered by **Google Gemini 1.5** and **Microsoft Edge TTS**. It adapts to your proficiency level, speaks with natural human voices, and can even teach you vocabulary from photos.



## ✨ Key Features

- **🎙️ Natural Voice Engine**: Uses Microsoft Edge Neural TTS for lifelike pronunciation (German, Spanish, English, French).
- **📸 Vision Learning**: Upload any photo, and the AI will analyze it and teach you relevant vocabulary.
- **🧠 Spaced Repetition (SRS)**: Built-in flashcard system based on the Ebbinghaus Forgetting Curve to help you memorize words.
- **📈 Adaptive Difficulty**: The AI adjusts the lesson difficulty (A1-C2) based on your feedback.

## 🛠️ Tech Stack

- **Frontend**: Streamlit
- **LLM**: Google Gemini 1.5 Flash
- **TTS**: Edge-TTS (Python)
- **Database**: SQLite
- **Vision**: Pillow

## 🚀 Quick Start

1. **Clone the repository**
   ```bash
   git clone [https://github.com/your-username/ai-language-tutor.git](https://github.com/your-username/ai-language-tutor.git)
   cd ai-language-tutor
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Set up API Key**
   Create a `.streamlit/secrets.toml` file:
   ```toml
   GOOGLE_API_KEY = "your_api_key_here"
   ```

4. **Run the app**
   ```bash
   streamlit run app.py
   ```

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
