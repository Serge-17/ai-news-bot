import os
import feedparser
import sqlite3
import time
import logging
import requests
import html
import re
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from huggingface_hub import InferenceClient

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

RSS_FEEDS = [
    # --- ГИГАНТЫ ИИ (ОФИЦИАЛЬНЫЕ БЛОГИ) ---
    "https://openai.com/news/rss.xml",                # OpenAI
    "https://deepmind.google/blog/rss.xml",           # Google DeepMind
    "https://www.anthropic.com/newsfeed/rss.xml",     # Anthropic (Claude)
    "https://blogs.nvidia.com/feed/",                 # NVIDIA
    "https://ai.meta.com/blog/rss/",                  # Meta AI (Llama)
    "https://blogs.microsoft.com/ai/feed/",           # Microsoft AI
    "https://machinelearning.apple.com/rss.xml",      # Apple Machine Learning
    "https://mistral.ai/news/index.xml",              # Mistral AI (Франция)
    
    # --- ТВИТТЕР (ЧЕРЕЗ NITTER - ГЛАВНЫЕ ЛИЦА) ---
    # Мы используем зеркало nitter.no-logs.com (оно сейчас стабильнее)
    "https://nitter.no-logs.com/sama/rss",            # Сэм Альтман (OpenAI)
    "https://nitter.no-logs.com/karpathy/rss",        # Андрей Карпатый
    "https://nitter.no-logs.com/ylecun/rss",          # Ян Лекун (Meta)
    "https://nitter.no-logs.com/demishassabis/rss",   # Демис Хассабис (DeepMind)
    "https://nitter.no-logs.com/gdb/rss",             # Грег Брокман (OpenAI)
    "https://nitter.no-logs.com/ilyasut/rss",         # Илья Суцкевер
    
    # --- ТЕХНО-НОВОСТИ И АГРЕГАТОРЫ ---
    "https://huggingface.co/blog/feed.xml",           # Hugging Face Blog
    "https://techcrunch.com/category/artificial-intelligence/feed/", # TechCrunch AI
    "https://www.theverge.com/ai-artificial-intelligence/rss/index.xml", # The Verge AI
    "https://export.arxiv.org/rss/cs.AI",             # Новые научные статьи (Arxiv)
    
    # --- РУССКОЯЗЫЧНЫЕ ИСТОЧНИКИ ---
    "https://habr.com/ru/rss/hub/artificial_intelligence/all/", # Хабр ИИ
    "https://vc.ru/rss/u/1215160-iskusstvennyy-intellekt",      # VC.ru ИИ
    "https://trends.rbc.ru/trends/rss/5d6910609a7947677846540e" # РБК Тренды ИИ
]

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)
client = InferenceClient(api_key=HF_TOKEN)

def init_db():
    con = sqlite3.connect(DB_FILE)
    con.execute("CREATE TABLE IF NOT EXISTS published_news (url TEXT PRIMARY KEY)")
    con.commit()
    con.close()

def is_published(url: str):
    con = sqlite3.connect(DB_FILE)
    cur = con.execute("SELECT 1 FROM published_news WHERE url=?", (url,))
    res = cur.fetchone()
    con.close()
    return res is not None

def mark_published(url: str):
    con = sqlite3.connect(DB_FILE)
    con.execute("INSERT OR IGNORE INTO published_news VALUES (?)", (url,))
    con.commit()
    con.close()

def process_with_ai(title, description):
    clean_desc = re.sub(r'<[^>]+>', '', (description or ""))[:800]
    messages = [
        {"role": "system", "content": "Ты техно-блогер. Кратко перескажи новость на русском. Сделай заголовок с эмодзи, 3 тезиса и вывод. Не используй **."},
        {"role": "user", "content": f"Заголовок: {title}\nТекст: {clean_desc}"}
    ]
    try:
        response = client.chat_completion(model=MODEL_ID, messages=messages, max_tokens=600, temperature=0.7)
        final_text = response.choices[0].message.content
        return final_text.replace("**", "").strip()
    except Exception as e:
        log.error(f"Ошибка ИИ: {e}")
        return None

def send_telegram(text, url):
    formatted_text = f"{text}\n\n🔗 <a href='{url}'>Источник</a>"
    api_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(api_url, json={"chat_id": CHANNEL_ID, "text": formatted_text, "parse_mode": "HTML"}, timeout=15)
        return True
    except: return False

def check_news():
    headers = {'User-Agent': 'Mozilla/5.0'}
    for feed_url in RSS_FEEDS:
        try:
            resp = requests.get(feed_url, headers=headers, timeout=10)
            feed = feedparser.parse(resp.content)
            for entry in feed.entries[:1]: # Берем только 1 самую свежую новость
                url = entry.get("link", "")
                if not url or is_published(url): continue
                ai_text = process_with_ai(entry.get("title", ""), entry.get("summary", ""))
                if ai_text and send_telegram(ai_text, url):
                    mark_published(url)
                    log.info(f"✅ Опубликовано: {url}")
                    time.sleep(10)
        except: pass

def main():
    init_db()
    # Запускаем фейковый сервер в отдельном потоке
    threading.Thread(target=run_health_server, daemon=True).start()
    log.info("🚀 Бот и Health-сервер запущены!")
    
    while True:
        check_news()
        log.info("⏳ Сон 30 мин...")
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()