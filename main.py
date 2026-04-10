import feedparser
import sqlite3
import time
import logging
import requests
import google.generativeai as genai
import html
import re

# ─── КОНФИГУРАЦИЯ ───────────────────────────────────────────────
DB_FILE        = "ai_news.db"
CHECK_INTERVAL = 1800  # 30 минут

RSS_FEEDS = [
    "https://openai.com/news/rss.xml",
    "https://deepmind.google/blog/rss.xml",
    "https://www.anthropic.com/newsfeed/rss.xml",
    "https://blogs.nvidia.com/feed/",
    "https://aws.amazon.com/blogs/machine-learning/feed/",
    "https://habr.com/ru/rss/hub/artificial_intelligence/all/",
    "https://vc.ru/rss/u/1215160-iskusstvennyy-intellekt",
    "https://trends.rbc.ru/trends/rss/5d6910609a7947677846540e",
]

GEMINI_PROMPT = """Ты — профессиональный техно-журналист. Проанализируй эту новость.
Если она на английском — переведи на русский.
Напиши пост для Telegram:
1) Короткий заголовок с одним подходящим эмодзи в начале.
2) Суть новости в 3-5 маркированных пунктах (буллитах).
3) В конце добавь краткий вывод (1 предложение), почему это важно.

ВАЖНО: Не используй символы ** для жирности. Пиши обычным текстом.
Формат ответа СТРОГО:
ЗАГОЛОВОК: <текст>
ТЕЗИСЫ:
• <тезис 1>
• <тезис 2>
ВЫВОД: <текст>

Новость:
Заголовок: {title}
Описание: {description}"""

# ─── ЛОГИРОВАНИЕ ────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(), logging.FileHandler("bot.log", encoding="utf-8")]
)
log = logging.getLogger(__name__)

# ─── БАЗА ДАННЫХ ────────────────────────────────────────────────
def init_db():
    con = sqlite3.connect(DB_FILE)
    con.execute("CREATE TABLE IF NOT EXISTS published_news (url TEXT PRIMARY KEY)")
    con.commit()
    con.close()

def is_published(url: str) -> bool:
    con = sqlite3.connect(DB_FILE)
    cur = con.execute("SELECT 1 FROM published_news WHERE url=?", (url,))
    found = cur.fetchone() is not None
    con.close()
    return found

def mark_published(url: str):
    con = sqlite3.connect(DB_FILE)
    con.execute("INSERT OR IGNORE INTO published_news VALUES (?)", (url,))
    con.commit()
    con.close()

# ─── ОБРАБОТКА ТЕКСТА ───────────────────────────────────────────
def clean_html(raw_html):
    """Очистка текста от лишних HTML тегов из RSS"""
    cleanr = re.compile('<.*?>')
    cleantext = re.sub(cleanr, '', raw_html)
    return html.unescape(cleantext)

# ─── GEMINI AI ──────────────────────────────────────────────────
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel("gemini-1.5-flash")

def process_with_gemini(title: str, description: str) -> dict | None:
    # Очищаем входные данные
    desc_clean = clean_html(description or "")[:1000]
    prompt = GEMINI_PROMPT.format(title=title, description=desc_clean)
    
    for attempt in range(3):
        try:
            response = model.generate_content(prompt)
            text = response.text.replace("**", "") # Убираем маркдаун, который ломает HTML в ТГ
            
            header, bullets, conclusion = "", [], ""
            for line in text.splitlines():
                line = line.strip()
                if line.upper().startswith("ЗАГОЛОВОК:"):
                    header = line.split(":", 1)[1].strip()
                elif line.startswith("•") or line.startswith("-"):
                    bullets.append("• " + line.lstrip("•- "))
                elif line.upper().startswith("ВЫВОД:"):
                    conclusion = line.split(":", 1)[1].strip()
            
            if header and bullets:
                return {"header": header, "bullets": bullets[:5], "conclusion": conclusion}
        except Exception as e:
            log.warning(f"Ошибка Gemini (попытка {attempt+1}): {e}")
            time.sleep(10)
    return None

# ─── TELEGRAM ───────────────────────────────────────────────────
def send_telegram(res: dict, url: str):
    # Формируем HTML-сообщение
    msg = f"<b>{html.escape(res['header'])}</b>\n\n"
    for b in res['bullets']:
        msg += f"{html.escape(b)}\n"
    if res['conclusion']:
        msg += f"\n💡 <i>Почему это важно:</i> {html.escape(res['conclusion'])}"
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

# ─── ОСНОВНОЙ ПРОЦЕСС ───────────────────────────────────────────
def process_feeds():
    # Настройка заголовков, чтобы сайты не блокировали бота
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AI News Bot'}
    
    for feed_url in RSS_FEEDS:
        try:
            # Загружаем содержимое через requests для обхода блокировок
            resp = requests.get(feed_url, headers=headers, timeout=15)
            feed = feedparser.parse(resp.content)
            log.info(f"📡 Проверка: {feed_url} ({len(feed.entries)} записей)")
        except Exception as e:
            log.error(f"Ошибка парсинга {feed_url}: {e}")
            continue

        for entry in feed.entries[:3]: # Проверяем 3 последних новости из каждой ленты
            url = entry.get("link", "")
            if not url or is_published(url):
                continue

            title = entry.get("title", "Без заголовка")
            summary = entry.get("summary", "") or entry.get("description", "")

            log.info(f"🆕 Найдена новость: {title[:50]}...")
            
            ai_result = process_with_gemini(title, summary)
            if ai_result:
                if send_telegram(ai_result, url):
                    mark_published(url)
                    log.info(f"✅ Опубликовано в канал!")
                    time.sleep(10) # Защита от лимитов Gemini
            else:
                log.warning("❌ Не удалось обработать через ИИ")

def main():
    log.info("🚀 Бот запущен и мониторит новости...")
    init_db()
    while True:
        try:
            process_feeds()
        except Exception as e:
            log.error(f"Ошибка в основном цикле: {e}")
        
        log.info(f"⏳ Ожидание {CHECK_INTERVAL//60} минут...")
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()