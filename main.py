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
from difflib import SequenceMatcher

# --- ФЕЙКОВЫЙ СЕРВЕР ДЛЯ HEALTH CHECK ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is alive!")

def run_health_server():
    server = HTTPServer(('0.0.0.0', 7860), HealthCheckHandler)
    server.serve_forever()

# --- КОНФИГУРАЦИЯ ---
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHANNEL_ID     = os.environ.get("CHANNEL_ID")
HF_TOKEN       = os.environ.get("HF_TOKEN")
MODEL_ID       = "Qwen/Qwen2.5-72B-Instruct"
DB_FILE        = "ai_news.db"
CHECK_INTERVAL = 1800 # 30 минут

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)
client = InferenceClient(api_key=HF_TOKEN)

# --- СПИСОК ИСТОЧНИКОВ ---
RSS_FEED_TAGS_WITH_KEYWORDS = [
    {"url": "https://blogs.nvidia.com/feed/", "tag": "#ГолосовыеТехнологииИИ", "keywords": ["voice", "speech", "audio", "gpu", "cuda"]},
    {"url": "https://www.theverge.com/ai-artificial-intelligence/rss/index.xml", "tag": "#GoClawTech", "keywords": ["agent", "llm", "automation"]},
    {"url": "https://techcrunch.com/category/artificial-intelligence/feed/", "tag": "#ОбщиеНовостиИИ", "keywords": ["ai", "model", "startup"]},
    {"url": "https://huggingface.co/blog/feed.xml", "tag": "#GoClawTech", "keywords": ["model", "dataset", "agent"]},
    {"url": "https://openai.com/news/rss.xml", "tag": "#ОбщиеНовостиИИ", "keywords": ["gpt", "openai", "sam altman"]},
    {"url": "https://habr.com/ru/rss/hub/artificial_intelligence/all/", "tag": "#ОбщиеНовостиИИ", "keywords": ["ии", "нейросети", "ml"]},
    {"url": "https://vc.ru/rss/u/1215160-iskusstvennyy-intellekt", "tag": "#ОбщиеНовостиИИ", "keywords": ["ии", "нейросети"]},
    {"url": "https://nextjs.org/feed.xml", "tag": "#ФронтендРазработка", "keywords": ["next.js", "react", "frontend"]},
    # Добавьте остальные свои ссылки сюда в том же формате
]

# --- РАБОТА С БД ---
def init_db():
    con = sqlite3.connect(DB_FILE)
    con.execute("""
        CREATE TABLE IF NOT EXISTS published_news (
            url TEXT PRIMARY KEY, 
            title TEXT, 
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    con.commit()
    con.close()

def is_duplicate(url, title):
    con = sqlite3.connect(DB_FILE)
    cur = con.cursor()
    
    # 1. Проверка по URL
    cur.execute("SELECT 1 FROM published_news WHERE url=?", (url,))
    if cur.fetchone():
        con.close()
        return True
    
    # 2. Проверка по схожести заголовка (с последними 40 записями)
    cur.execute("SELECT title FROM published_news ORDER BY created_at DESC LIMIT 40")
    recent_titles = cur.fetchall()
    con.close()
    
    for (old_title,) in recent_titles:
        similarity = SequenceMatcher(None, title.lower(), old_title.lower()).ratio()
        if similarity > 0.72: # Если заголовки похожи на 72%+, это дубль
            log.info(f"Дубликат по заголовку: '{title}' похож на '{old_title}' ({int(similarity*100)}%)")
            return True
    return False

def mark_published(url, title):
    try:
        con = sqlite3.connect(DB_FILE)
        con.execute("INSERT OR IGNORE INTO published_news (url, title) VALUES (?, ?)", (url, title))
        con.commit()
        con.close()
    except Exception as e:
        log.error(f"Ошибка записи в БД: {e}")

# --- ЛОГИКА ---
def process_with_ai(title, description, tag):
    clean_desc = re.sub(r'<[^>]+>', '', (description or ""))[:1000]
    prompt = f"Ты техно-блогер. Кратко перескажи новость на русском. Сделай заголовок с эмодзи, 3 тезиса и вывод. Не используй жирный шрифт **. Будь лаконичным.\n\nЗаголовок: {title}\nТекст: {clean_desc}"
    
    try:
        response = client.chat_completion(
            model=MODEL_ID, 
            messages=[{"role": "user", "content": prompt}], 
            max_tokens=600
        )
        res_text = response.choices[0].message.content
        return f"{tag}\n{res_text.replace('**', '').strip()}"
    except Exception as e:
        log.error(f"AI Error: {e}")
        return None

def send_telegram(text, url):
    api_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHANNEL_ID,
        "text": f"{text}\n\n🔗 <a href='{url}'>Источник</a>",
        "parse_mode": "HTML",
        "disable_web_page_preview": False
    }
    try:
        r = requests.post(api_url, json=payload, timeout=20)
        return r.status_code == 200
    except Exception as e:
        log.error(f"TG Error: {e}")
        return False

def check_news():
    log.info(f"--- НАЧАЛО ПРОВЕРКИ ({len(RSS_FEED_TAGS_WITH_KEYWORDS)} лент) ---")
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    
    for feed_item in RSS_FEED_TAGS_WITH_KEYWORDS:
        url_feed = feed_item["url"]
        tag = feed_item["tag"]
        keywords = feed_item.get("keywords", [])
        
        log.info(f"Обработка ленты: {url_feed}")
        
        try:
            resp = requests.get(url_feed, headers=headers, timeout=20)
            feed = feedparser.parse(resp.content)
            
            # Проверяем первые 3 записи в каждой ленте
            for entry in feed.entries[:3]:
                link = entry.get("link")
                title = entry.get("title", "")
                summary = entry.get("summary", "") or entry.get("description", "")

                if not link or not title:
                    continue

                # 1. Проверка на дубликат (URL или Заголовок)
                if is_duplicate(link, title):
                    continue

                # 2. Проверка на релевантность ключевым словам
                full_text = f"{title} {summary}".lower()
                if keywords and not any(kw.lower() in full_text for kw in keywords):
                    continue

                log.info(f"Новая новость найдена: {title}")
                
                # 3. Обработка через AI
                ai_text = process_with_ai(title, summary, tag)
                if ai_text:
                    # 4. Отправка
                    if send_telegram(ai_text, link):
                        mark_published(link, title)
                        log.info(f"✅ Опубликовано: {title}")
                        time.sleep(15) # Пауза чтобы избежать спам-фильтра TG
                    else:
                        log.warning(f"❌ Сбой отправки в TG: {title}")
                
        except Exception as e:
            log.error(f"Ошибка при обработке ленты {url_feed}: {e}")
            continue # Переходим к следующей ленте, если эта упала
            
    log.info("--- ПРОВЕРКА ВСЕХ ЛЕНТ ЗАВЕРШЕНА ---")

def main():
    init_db()
    # Запуск Health-сервера для Hugging Face / Render / Railway
    threading.Thread(target=run_health_server, daemon=True).start()
    
    log.info("🚀 Бот запущен и готов к работе!")
    
    while True:
        try:
            check_news()
        except Exception as e:
            log.critical(f"Критическая ошибка в основном цикле: {e}")
        
        log.info(f"⏳ Сон {CHECK_INTERVAL//60} минут до следующей итерации...")
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()