import os
import feedparser
import sqlite3
import time
import logging
import requests
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from huggingface_hub import InferenceClient
from difflib import SequenceMatcher # Для сравнения схожести текстов

# --- ФЕЙКОВЫЙ СЕРВЕР ДЛЯ HUGGING FACE ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running!")

def run_health_server():
    server = HTTPServer(('0.0.0.0', 7860), HealthCheckHandler)
    server.serve_forever()

# --- КОНФИГУРАЦИЯ ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHANNEL_ID     = os.environ.get("CHANNEL_ID")
HF_TOKEN       = os.environ.get("HF_TOKEN")
MODEL_ID       = "Qwen/Qwen2.5-72B-Instruct"
DB_FILE        = "ai_news.db"
CHECK_INTERVAL = 1800 

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)
client = InferenceClient(api_key=HF_TOKEN)

# (Ваш список RSS_FEED_TAGS_WITH_KEYWORDS остается прежним, я его сократил для краткости в примере)
RSS_FEED_TAGS_WITH_KEYWORDS = [
    # ... (весь ваш список из исходного кода здесь без изменений) ...
    {"url": "https://blogs.nvidia.com/feed/", "tag": "#ГолосовыеТехнологииИИ", "keywords": ["voice", "speech", "audio", "ffmpeg", "rvc", "gpu"]},
    # и так далее...
]

# --- РАБОТА С БАЗОЙ ДАННЫХ ---

def init_db():
    con = sqlite3.connect(DB_FILE)
    # Добавляем колонку title и created_at для отслеживания похожих новостей
    con.execute("""
        CREATE TABLE IF NOT EXISTS published_news (
            url TEXT PRIMARY KEY, 
            tag TEXT, 
            title TEXT, 
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    con.commit()
    con.close()

def is_similar(title1, title2):
    """Проверяет степень сходства двух строк (от 0 до 1)"""
    if not title1 or not title2:
        return 0
    return SequenceMatcher(None, title1.lower(), title2.lower()).ratio()

def is_already_published(url, title):
    """Проверяет, публиковалась ли новость по ссылке ИЛИ по похожему заголовку"""
    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()
    
    # 1. Сначала проверяем по точному URL (самый быстрый способ)
    cur.execute("SELECT 1 FROM published_news WHERE url=?", (url,))
    if cur.fetchone():
        con.close()
        return True
    
    # 2. Проверяем по заголовку среди последних 50 записей
    # (берем последние, так как новости устаревают и сравнивать со старыми нет смысла)
    cur.execute("SELECT title FROM published_news ORDER BY created_at DESC LIMIT 50")
    recent_titles = cur.fetchall()
    con.close()
    
    for (old_title,) in recent_titles:
        if is_similar(title, old_title) > 0.75: # Порог 75% сходства
            log.info(f"Дубликат обнаружен: '{title}' похоже на '{old_title}'")
            return True
            
    return False

def mark_published(url, tag, title):
    con = sqlite3.connect(DB_FILE)
    con.execute("INSERT OR IGNORE INTO published_news (url, tag, title) VALUES (?, ?, ?)", (url, tag, title))
    con.commit()
    con.close()

# --- ЛОГИКА ОБРАБОТКИ ---

def is_relevant(entry_text: str, keywords: list) -> bool:
    if not keywords: return True
    text_lower = entry_text.lower()
    return any(kw.lower() in text_lower for kw in keywords)

def process_with_ai(title: str, description: str, tag: str):
    clean_desc = re.sub(r'<[^>]+>', '', (description or ""))[:800]
    messages = [
        {"role": "system", "content": "Ты техно-блогер. Кратко перескажи новость на русском. Сделай заголовок с эмодзи, 3 тезиса и вывод. Не используй **. Будь лаконичным."},
        {"role": "user", "content": f"Заголовок: {title}\nТекст: {clean_desc}"}
    ]
    try:
        response = client.chat_completion(model=MODEL_ID, messages=messages, max_tokens=600, temperature=0.7)
        final_text = response.choices[0].message.content
        return f"{tag}\n{final_text.replace('**', '').strip()}"
    except Exception as e:
        log.error(f"Ошибка ИИ: {e}")
        return None

def send_telegram(text: str, url: str):
    formatted_text = f"{text}\n\n🔗 <a href='{url}'>Источник</a>"
    api_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        response = requests.post(api_url, json={"chat_id": CHANNEL_ID, "text": formatted_text, "parse_mode": "HTML"}, timeout=15)
        return response.status_code == 200
    except Exception as e:
        log.error(f"Ошибка Telegram: {e}")
        return False

def check_news():
    headers = {'User-Agent': 'Mozilla/5.0'}
    # Локальный список заголовков, обработанных в текущем цикле, чтобы не дублировать внутри одного запуска
    processed_titles_this_run = []

    for feed_config in RSS_FEED_TAGS_WITH_KEYWORDS:
        feed_url = feed_config["url"]
        tag = feed_config["tag"]
        keywords = feed_config["keywords"]

        try:
            log.info(f"Проверка: {feed_url}")
            resp = requests.get(feed_url, headers=headers, timeout=15)
            feed = feedparser.parse(resp.content)

            for entry in feed.entries[:5]: 
                url = entry.get("link", "")
                title = entry.get("title", "")
                summary = entry.get("summary", "")

                if not url or not title:
                    continue
                
                # ПРОВЕРКА НА ДУБЛИКАТЫ (URL + Похожий заголовок)
                if is_already_published(url, title):
                    continue
                
                # Проверка схожести с тем, что мы уже нашли в ЭТОМ запуске (но еще не записали в БД)
                if any(is_similar(title, pt) > 0.75 for pt in processed_titles_this_run):
                    continue

                if not is_relevant(f"{title} {summary}", keywords):
                    continue
                
                ai_text = process_with_ai(title, summary, tag)
                if ai_text:
                    if send_telegram(ai_text, url):
                        mark_published(url, tag, title)
                        processed_titles_this_run.append(title)
                        log.info(f"✅ Опубликовано: {title}")
                        time.sleep(10) 
        except Exception as e:
            log.error(f"Ошибка в ленте {feed_url}: {e}")
        
        time.sleep(2)

def main():
    init_db()
    threading.Thread(target=run_health_server, daemon=True).start()
    log.info("🚀 Бот запущен!")

    while True:
        check_news()
        log.info(f"⏳ Ожидание {CHECK_INTERVAL} сек...")
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()