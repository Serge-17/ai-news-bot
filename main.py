import os
import feedparser
import sqlite3
import time
import logging
import requests
import google.generativeai as genai
import html
import re

# ─── КОНФИГУРАЦИЯ (Берем из Secrets Hugging Face) ────────────────
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHANNEL_ID     = os.environ.get("CHANNEL_ID")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# Проверка наличия ключей
if not all([TELEGRAM_TOKEN, CHANNEL_ID, GEMINI_API_KEY]):
    print("ОШИБКА: Проверьте Secrets в настройках Space!")

DB_FILE        = "ai_news.db"
CHECK_INTERVAL = 1800  # 30 минут

# ─── ИСТОЧНИКИ (AI Блоги + Twitter через Nitter) ──────────────────
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

GEMINI_PROMPT = """Ты — профессиональный техно-журналист. Проанализируй эту новость.
Если она на английском — переведи на русский.
Напиши пост для Telegram:
1) Короткий заголовок с эмодзи в начале.
2) Суть новости в 3-5 тезисах.
3) Краткий вывод, почему это важно.

ВАЖНО: Не используй символы ** для жирности. Пиши обычным текстом.
Формат ответа СТРОГО:
ЗАГОЛОВОК: <заголовок>
ТЕЗИСЫ:
• <тезис 1>
• <тезис 2>
ВЫВОД: <вывод>

Новость:
{title}
{description}"""

# ─── ЛОГИРОВАНИЕ ────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
log = logging.getLogger(__name__)

# ─── БАЗА ДАННЫХ ────────────────────────────────────────────────
def init_db():
    con = sqlite3.connect(DB_FILE)
    con.execute("CREATE TABLE IF NOT EXISTS published_news (url TEXT PRIMARY KEY)")
    con.commit()
    con.close()

def is_published(url: str) -> bool:
    try:
        con = sqlite3.connect(DB_FILE)
        cur = con.execute("SELECT 1 FROM published_news WHERE url=?", (url,))
        found = cur.fetchone() is not None
        con.close()
        return found
    except: return False

def mark_published(url: str):
    try:
        con = sqlite3.connect(DB_FILE)
        con.execute("INSERT OR IGNORE INTO published_news VALUES (?)", (url,))
        con.commit()
        con.close()
    except: pass

# ─── ГЕНЕРАЦИЯ КОНТЕНТА (GEMINI) ────────────────────────────────
genai.configure(api_key=GEMINI_API_KEY)
# Используем стабильное имя модели
model = genai.GenerativeModel(model_name="gemini-1.5-flash")

def process_with_gemini(title, description):
    # Очистка от HTML-тегов
    clean_desc = re.sub(r'<[^>]+>', '', (description or ""))[:1000]
    prompt = GEMINI_PROMPT.format(title=title, description=clean_desc)
    
    for attempt in range(3):
        try:
            response = model.generate_content(prompt)
            if not response or not response.text:
                continue
            
            text = response.text.replace("**", "")
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
            log.warning(f"Ошибка Gemini: {e}")
            time.sleep(10)
    return None

# ─── ОТПРАВКА В TELEGRAM ────────────────────────────────────────
def send_telegram(res, url):
    msg = f"🚀 <b>{html.escape(res['header'])}</b>\n\n"
    msg += "\n".join([html.escape(b) for b in res['bullets']])
    if res['conclusion']:
        msg += f"\n\n💡 <i>Почему это важно:</i> {html.escape(res['conclusion'])}"
    msg += f"\n\n🔗 <a href='{url}'>Источник</a>"

    api_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        r = requests.post(api_url, json={
            "chat_id": CHANNEL_ID,
            "text": msg,
            "parse_mode": "HTML",
            "disable_web_page_preview": False
        }, timeout=20)
        r.raise_for_status()
        return True
    except Exception as e:
        log.error(f"Ошибка Telegram: {e}")
        return False

# ─── ОСНОВНОЙ ЦИКЛ ──────────────────────────────────────────────
def check_news():
    headers = {'User-Agent': 'Mozilla/5.0'}
    for feed_url in RSS_FEEDS:
        try:
            resp = requests.get(feed_url, headers=headers, timeout=15)
            feed = feedparser.parse(resp.content)
            log.info(f"📡 Проверка: {feed_url}")
            
            # Берем только 2 последние новости, чтобы не спамить
            for entry in feed.entries[:2]:
                url = entry.get("link", "")
                if not url or is_published(url):
                    continue
                
                log.info(f"🆕 Найдено: {url}")
                res = process_with_gemini(entry.get("title", ""), entry.get("summary", ""))
                
                if res:
                    if send_telegram(res, url):
                        mark_published(url)
                        log.info(f"✅ Опубликовано!")
                        time.sleep(10) # Защита от лимитов
                else:
                    log.warning("❌ Не удалось обработать")
        except Exception as e:
            log.error(f"Ошибка ленты {feed_url}: {e}")

def main():
    log.info("🚀 Бот запущен!")
    init_db()
    while True:
        check_news()
        log.info(f"⏳ Сон {CHECK_INTERVAL//60} мин...")
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()