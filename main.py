import os
import feedparser
import sqlite3
import time
import logging
import requests
from groq import Groq
import html
import re

# ─── КОНФИГУРАЦИЯ ───────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHANNEL_ID     = os.environ.get("CHANNEL_ID")
GROQ_API_KEY   = os.environ.get("GROQ_API_KEY")

if not all([TELEGRAM_TOKEN, CHANNEL_ID, GROQ_API_KEY]):
    print("ОШИБКА: Проверьте Secrets (TELEGRAM_TOKEN, CHANNEL_ID, GROQ_API_KEY)!")

DB_FILE        = "ai_news.db"
CHECK_INTERVAL = 1800 

RSS_FEEDS = [
    "https://openai.com/news/rss.xml",
    "https://deepmind.google/blog/rss.xml",
    "https://www.anthropic.com/newsfeed/rss.xml",
    "https://blogs.nvidia.com/feed/",
    "https://aws.amazon.com/blogs/machine-learning/feed/",
    "https://habr.com/ru/rss/hub/artificial_intelligence/all/",
    "https://vc.ru/rss/u/1215160-iskusstvennyy-intellekt",
    "https://trends.rbc.ru/trends/rss/5d6910609a7947677846540e",
    # Twitter аккаунты (через зеркало Nitter)
    "https://nitter.privacydev.net/sama/rss",
    "https://nitter.privacydev.net/OpenAI/rss",
    "https://nitter.privacydev.net/karpathy/rss",
    "https://nitter.privacydev.net/ylecun/rss"
]

PROMPT_STYLE = """Ты — профессиональный техно-журналист. Проанализируй текст.
Если он на английском — переведи на русский.
Напиши пост для Telegram:
1) Короткий заголовок с эмодзи.
2) Суть в 3-5 тезисах.
3) Вывод (1 предложение).

ВАЖНО: Не используй символы **. Пиши обычным текстом.
Формат СТРОГО:
ЗАГОЛОВОК: <текст>
ТЕЗИСЫ:
• <тезис 1>
• <тезис 2>
ВЫВОД: <текст>"""

# ─── ИНИЦИАЛИЗАЦИЯ ──────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)
client = Groq(api_key=GROQ_API_KEY)

def init_db():
    con = sqlite3.connect(DB_FILE)
    con.execute("CREATE TABLE IF NOT EXISTS published_news (url TEXT PRIMARY KEY)")
    con.commit()
    con.close()

def is_published(url: str) -> bool:
    try:
        con = sqlite3.connect(DB_FILE)
        cur = con.execute("SELECT 1 FROM published_news WHERE url=?", (url,))
        res = cur.fetchone()
        con.close()
        return res is not None
    except: return False

def mark_published(url: str):
    try:
        con = sqlite3.connect(DB_FILE)
        con.execute("INSERT OR IGNORE INTO published_news VALUES (?)", (url,))
        con.commit()
        con.close()
    except: pass

# ─── ИИ ОБРАБОТКА (GROQ + LLAMA 3) ──────────────────────────────
def process_with_ai(title, description):
    clean_text = re.sub(r'<[^>]+>', '', (description or ""))[:1500]
    content = f"Заголовок: {title}\nТекст: {clean_text}"
    
    try:
        completion = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": PROMPT_STYLE},
                {"role": "user", "content": content}
            ],
            temperature=0.5,
            max_tokens=1000
        )
        
        text = completion.choices[0].message.content.replace("**", "")
        header, bullets, conclusion = "Новость", [], ""
        
        for line in text.splitlines():
            line = line.strip()
            if line.upper().startswith("ЗАГОЛОВОК:"):
                header = line.split(":", 1)[1].strip()
            elif line.startswith("•") or line.startswith("-"):
                bullets.append("• " + line.lstrip("•- "))
            elif line.upper().startswith("ВЫВОД:"):
                conclusion = line.split(":", 1)[1].strip()
        
        if bullets:
            return {"header": header, "bullets": bullets[:5], "conclusion": conclusion}
    except Exception as e:
        log.error(f"Ошибка Groq: {e}")
    return None

# ─── TELEGRAM ───────────────────────────────────────────────────
def send_telegram(res, url):
    msg = f"🚀 <b>{html.escape(res['header'])}</b>\n\n"
    msg += "\n".join([html.escape(b) for b in res['bullets']])
    if res['conclusion']:
        msg += f"\n\n💡 <i>Почему это важно:</i> {html.escape(res['conclusion'])}"
    msg += f"\n\n🔗 <a href='{url}'>Источник</a>"

    api_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        r = requests.post(api_url, json={
            "chat_id": CHANNEL_ID, "text": msg, "parse_mode": "HTML"
        }, timeout=20)
        return r.status_code == 200
    except: return False

# ─── ГЛАВНЫЙ ЦИКЛ ───────────────────────────────────────────────
def check_news():
    headers = {'User-Agent': 'Mozilla/5.0'}
    for feed_url in RSS_FEEDS:
        try:
            resp = requests.get(feed_url, headers=headers, timeout=15)
            feed = feedparser.parse(resp.content)
            log.info(f"📡 Проверка: {feed_url}")
            
            for entry in feed.entries[:2]:
                url = entry.get("link", "")
                if not url or is_published(url): continue
                
                log.info(f"🆕 Найдено: {url}")
                res = process_with_ai(entry.get("title", ""), entry.get("summary", ""))
                
                if res:
                    if send_telegram(res, url):
                        mark_published(url)
                        log.info(f"✅ Опубликовано!")
                        time.sleep(5)
        except Exception as e:
            log.error(f"Ошибка ленты {feed_url}: {e}")

def main():
    log.info("🚀 Бот запущен на Groq!")
    init_db()
    while True:
        check_news()
        log.info(f"⏳ Сон {CHECK_INTERVAL//60} мин...")
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()