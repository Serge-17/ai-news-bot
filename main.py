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

# Модель Qwen 2.5 — она сейчас самая стабильная на HF
HF_API_URL = "https://api-inference.huggingface.co/models/Qwen/Qwen2.5-72B-Instruct"

DB_FILE = "ai_news.db"
CHECK_INTERVAL = 1800 

RSS_FEEDS = [
    "https://openai.com/news/rss.xml",
    "https://deepmind.google/blog/rss.xml",
    "https://www.anthropic.com/newsfeed/rss.xml",
    "https://blogs.nvidia.com/feed/",
    "https://habr.com/ru/rss/hub/artificial_intelligence/all/",
    "https://vc.ru/rss/u/1215160-iskusstvennyy-intellekt"
]

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

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

# ─── ИИ ОБРАБОТКА (С ДЕТАЛЬНЫМ ЛОГОМ ОШИБОК) ───────────────────
def process_with_ai(title, description):
    log.info(f"🤖 Анализ новости: {title[:50]}...")
    
    clean_desc = re.sub(r'<[^>]+>', '', (description or ""))[:800]
    prompt = f"<|im_start|>system\nТы техно-блогер. Кратко перескажи новость на русском. Сделай заголовок с эмодзи, 3 тезиса и вывод. Не используй **.<|im_end|>\n<|im_start|>user\n{title}\n{clean_desc}<|im_end|>\n<|im_start|>assistant"
    
    headers = {"Authorization": f"Bearer {HF_TOKEN}"}
    payload = {"inputs": prompt, "parameters": {"max_new_tokens": 600, "temperature": 0.7}}
    
    try:
        response = requests.post(HF_API_URL, headers=headers, json=payload, timeout=30)
        result = response.json()
        
        # Если пришла ошибка от сервера - мы её увидим!
        if isinstance(result, dict) and "error" in result:
            log.error(f"❌ Ошибка от Hugging Face: {result['error']}")
            if "estimated_time" in result:
                log.info(f"⏳ Модель загружается, подождем {int(result['estimated_time'])} сек...")
                time.sleep(result['estimated_time'])
            return None

        # Парсим текст
        raw_text = result[0]['generated_text'] if isinstance(result, list) else result.get('generated_text', "")
        # Очищаем от промпта (Qwen возвращает весь текст целиком)
        if "assistant" in raw_text:
            final_text = raw_text.split("assistant")[-1].strip()
        else:
            final_text = raw_text.replace(prompt, "").strip()
            
        final_text = final_text.replace("**", "")
        
        if len(final_text) < 30:
            log.warning(f"⚠️ Слишком короткий ответ от ИИ: {final_text}")
            return None
            
        return final_text

    except Exception as e:
        log.error(f"❌ Системная ошибка при запросе к ИИ: {e}")
        return None

def send_telegram(text, url):
    # Добавляем кнопку-ссылку внизу
    formatted_text = f"{text}\n\n🔗 <a href='{url}'>Источник</a>"
    api_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        r = requests.post(api_url, json={
            "chat_id": CHANNEL_ID, 
            "text": formatted_text, 
            "parse_mode": "HTML"
        }, timeout=15)
        return r.status_code == 200
    except Exception as e:
        log.error(f"❌ Ошибка отправки в TG: {e}")
        return False

def check_news():
    headers = {'User-Agent': 'Mozilla/5.0'}
    for feed_url in RSS_FEEDS:
        try:
            resp = requests.get(feed_url, headers=headers, timeout=10)
            feed = feedparser.parse(resp.content)
            log.info(f"📡 Проверка ленты: {feed_url}")
            
            for entry in feed.entries[:2]:
                url = entry.get("link", "")
                if not url or is_published(url): continue
                
                ai_text = process_with_ai(entry.get("title", ""), entry.get("summary", ""))
                
                if ai_text:
                    if send_telegram(ai_text, url):
                        mark_published(url)
                        log.info("✅ Опубликовано!")
                        time.sleep(10)
                else:
                    log.warning(f"⏭ Пропуск: {url}")
        except Exception as e:
            log.error(f"📡 Ошибка доступа к ленте: {feed_url}")

def main():
    log.info("🚀 Запуск бота...")
    init_db()
    while True:
        check_news()
        log.info(f"⏳ Сон 30 минут...")
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()