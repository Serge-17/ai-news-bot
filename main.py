import os
import feedparser
import sqlite3
import time
import logging
import requests
import html
import re

# ─── КОНФИГУРАЦИЯ ───────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHANNEL_ID     = os.environ.get("CHANNEL_ID")
HF_TOKEN       = os.environ.get("HF_TOKEN")

# Используем максимально стабильную модель
HF_API_URL = "https://api-inference.huggingface.co/models/mistralai/Mistral-7B-Instruct-v0.3"

DB_FILE = "ai_news.db"
CHECK_INTERVAL = 1800 

RSS_FEEDS = [
    "https://openai.com/news/rss.xml",
    "https://deepmind.google/blog/rss.xml",
    "https://www.anthropic.com/newsfeed/rss.xml",
    "https://blogs.nvidia.com/feed/",
    "https://habr.com/ru/rss/hub/artificial_intelligence/all/",
    "https://vc.ru/rss/u/1215160-iskusstvennyy-intellekt",
    # Твиттер через разные зеркала
    "https://nitter.net/sama/rss",
    "https://nitter.cz/OpenAI/rss"
]

# ─── ЛОГИРОВАНИЕ ────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ─── БД ─────────────────────────────────────────────────────────
def init_db():
    con = sqlite3.connect(DB_FILE)
    con.execute("CREATE TABLE IF NOT EXISTS published_news (url TEXT PRIMARY KEY)")
    con.commit()
    con.close()

def is_published(url: str) -> bool:
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

# ─── ИИ ОБРАБОТКА (HUGGING FACE) ────────────────────────────────
def process_with_ai(title, description):
    log.info(f"🤖 Запрос к ИИ для: {title[:50]}...")
    text_to_analyze = f"Новость: {title}. {re.sub(r'<[^>]+>', '', (description or ''))[:500]}"
    
    prompt = f"<s>[INST] Ты — техно-блогер. Кратко перескажи новость на русском. Сделай заголовок с эмодзи, 3 тезиса и вывод. Не используй **. \n{text_to_analyze} [/INST]</s>"
    
    headers = {"Authorization": f"Bearer {HF_TOKEN}"}
    payload = {"inputs": prompt, "parameters": {"max_new_tokens": 500, "temperature": 0.7}}
    
    try:
        response = requests.post(HF_API_URL, headers=headers, json=payload, timeout=25)
        result = response.json()
        
        # Если модель грузится, подождем
        if isinstance(result, dict) and "estimated_time" in result:
            wait_time = result['estimated_time']
            log.info(f"⏳ Модель спит, ждем {wait_time} сек...")
            time.sleep(wait_time)
            return process_with_ai(title, description)

        generated_text = result[0]['generated_text'] if isinstance(result, list) else result.get('generated_text', "")
        # Убираем сам промпт из ответа
        clean_res = generated_text.split("[/INST]</s>")[-1].replace("**", "").strip()
        
        if len(clean_res) < 20:
            log.warning("⚠️ ИИ выдал слишком короткий ответ")
            return None
        return clean_res
    except Exception as e:
        log.error(f"❌ Ошибка ИИ: {e}")
        return None

# ─── ТЕЛЕГРАМ ───────────────────────────────────────────────────
def send_telegram(text, url):
    # Разрезаем текст на заголовок и остальное для красоты
    formatted_text = f"🚀 {text}\n\n🔗 <a href='{url}'>Источник</a>"
    api_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        r = requests.post(api_url, json={
            "chat_id": CHANNEL_ID, 
            "text": formatted_text, 
            "parse_mode": "HTML"
        }, timeout=15)
        return r.status_code == 200
    except: return False

# ─── ОСНОВНОЙ ЦИКЛ ──────────────────────────────────────────────
def check_news():
    headers = {'User-Agent': 'Mozilla/5.0'}
    for feed_url in RSS_FEEDS:
        try:
            resp = requests.get(feed_url, headers=headers, timeout=10)
            feed = feedparser.parse(resp.content)
            log.info(f"📡 Лента: {feed_url}")
            
            for entry in feed.entries[:2]:
                url = entry.get("link", "")
                if not url or is_published(url): continue
                
                log.info(f"🆕 Найдено: {url}")
                ai_text = process_with_ai(entry.get("title", ""), entry.get("summary", ""))
                
                if ai_text:
                    if send_telegram(ai_text, url):
                        mark_published(url)
                        log.info("✅ Опубликовано!")
                        time.sleep(5)
                else:
                    log.warning("⚠️ Пропуск новости (ИИ не ответил)")
        except Exception as e:
            log.error(f"📡 Ошибка ленты: {feed_url}")

def main():
    log.info("🚀 Бот запущен!")
    init_db()
    while True:
        check_news()
        log.info(f"⏳ Сон 30 мин...")
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()