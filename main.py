import os
import feedparser
import sqlite3
import time
import logging
import requests
import re
import threading
import socket
from http.server import BaseHTTPRequestHandler, HTTPServer
from deep_translator import GoogleTranslator
from google import genai
from difflib import SequenceMatcher
from html import escape
import urllib3.util.connection as urllib3_cn

# --- HEALTH CHECK СЕРВЕР ---
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is alive!")

    def log_message(self, format, *args):
        pass

def run_health_server():
    # ✅ ИСПРАВЛЕНИЕ: берём PORT из Render (не хардкодим 7860)
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    log.info(f"Health-сервер запущен на порту {port}")
    server.serve_forever()

# --- КОНФИГУРАЦИЯ ---
TELEGRAM_TOKEN  = os.environ.get("TELEGRAM_TOKEN")
CHANNEL_ID      = os.environ.get("CHANNEL_ID")
GEMINI_TOKEN    = os.environ.get("GEMINI_TOKEN")
MODEL_ID        = "gemini-2.0-flash"
DB_FILE         = os.environ.get("DB_PATH", "/tmp/ai_news.db")
CHECK_INTERVAL  = int(os.environ.get("CHECK_INTERVAL", "1800"))
MAX_PER_FEED    = int(os.environ.get("MAX_PER_FEED", "3"))
SIMILARITY_DB   = 0.72
SIMILARITY_CYCLE = 0.60

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# Принудительно используем IPv4 (в контейнерах IPv6 к Telegram может зависать)
urllib3_cn.allowed_gai_family = lambda: socket.AF_INET

http = requests.Session()
gemini_retry_after = 0.0
translator = GoogleTranslator(source="auto", target="ru")
client = genai.Client(api_key=GEMINI_TOKEN) if GEMINI_TOKEN else None

# ---------------------------------------------------------------------------
# ИСТОЧНИКИ НОВОСТЕЙ
# ---------------------------------------------------------------------------
RSS_FEEDS = [
    # ── ОБЩИЕ НОВОСТИ ИИ ──────────────────────────────────────────────────
    {
        "url": "https://techcrunch.com/category/artificial-intelligence/feed/",
        "tag": "#НовостиИИ",
        "keywords": ["ai", "model", "startup", "llm", "openai", "anthropic", "gemini"]
    },
    {
        "url": "https://www.theverge.com/ai-artificial-intelligence/rss/index.xml",
        "tag": "#НовостиИИ",
        "keywords": ["ai", "agent", "llm", "automation", "openai", "google", "meta"]
    },
    {
        "url": "https://venturebeat.com/category/ai/feed/",
        "tag": "#НовостиИИ",
        "keywords": ["ai", "model", "agent", "inference", "enterprise"]
    },
    {
        "url": "https://www.artificialintelligence-news.com/feed/",
        "tag": "#НовостиИИ",
        "keywords": ["ai", "machine learning", "neural", "model"]
    },
    {
        "url": "https://openai.com/news/rss.xml",
        "tag": "#НовостиИИ",
        "keywords": ["gpt", "openai", "sora", "agents", "o1", "o3"]
    },
    {
        "url": "https://www.anthropic.com/rss.xml",
        "tag": "#НовостиИИ",
        "keywords": ["claude", "anthropic", "safety", "agent"]
    },
    {
        "url": "https://ai.googleblog.com/feeds/posts/default",
        "tag": "#НовостиИИ",
        "keywords": ["ai", "gemini", "model", "research", "agent"]
    },
    {
        "url": "https://huggingface.co/blog/feed.xml",
        "tag": "#OpenSourceAI",
        "keywords": ["model", "dataset", "agent", "fine-tuning", "open-source", "lora"]
    },
    {
        "url": "https://habr.com/ru/rss/hub/artificial_intelligence/all/",
        "tag": "#НовостиИИ",
        "keywords": ["ии", "нейросети", "ml", "llm", "агент", "модель"]
    },
    {
        "url": "https://vc.ru/rss/u/1215160-iskusstvennyy-intellekt",
        "tag": "#НовостиИИ",
        "keywords": ["ии", "нейросети", "gpt", "claude", "модель"]
    },
    # ── ГОЛОС / TTS ────────────────────────────────────────────────────────
    {
        "url": "https://blogs.nvidia.com/feed/",
        "tag": "#ГолосовойАИ",
        "keywords": ["voice", "speech", "tts", "audio", "cloning", "synthesis", "deepfake voice"]
    },
    {
        "url": "https://techcrunch.com/tag/voice/feed/",
        "tag": "#ГолосовойАИ",
        "keywords": ["voice", "speech", "audio", "clone", "synthesis", "eleven labs", "elevenlabs"]
    },
    {
        "url": "https://elevenlabs.io/blog/rss.xml",
        "tag": "#ГолосовойАИ",
        "keywords": ["voice", "speech", "audio", "clone", "tts"]
    },
    {
        "url": "https://www.deepmind.com/blog/rss.xml",
        "tag": "#ГолосовойАИ",
        "keywords": ["voice", "speech", "audio", "language", "model", "gemini"]
    },
    # ── CREATOR ECONOMY ────────────────────────────────────────────────────
    {
        "url": "https://socialmediatoday.com/rss.xml",
        "tag": "#КреаторЭкономика",
        "keywords": ["creator", "reels", "shorts", "tiktok", "instagram", "ai content", "ugc", "influencer"]
    },
    {
        "url": "https://www.socialmediaexaminer.com/feed/",
        "tag": "#КреаторЭкономика",
        "keywords": ["creator", "shorts", "reels", "ai", "automation", "content", "brand"]
    },
    {
        "url": "https://later.com/blog/feed/",
        "tag": "#КреаторЭкономика",
        "keywords": ["creator", "social media", "reels", "shorts", "tiktok", "instagram", "brand deal"]
    },
    {
        "url": "https://www.tubefilter.com/feed/",
        "tag": "#КреаторЭкономика",
        "keywords": ["creator", "youtube", "shorts", "monetization", "sponsor", "brand", "influencer"]
    },
    # ── AD TECH ────────────────────────────────────────────────────────────
    {
        "url": "https://adexchanger.com/feed/",
        "tag": "#AdTech",
        "keywords": ["programmatic", "cpa", "cpc", "brand", "ad", "affiliate", "performance", "ai"]
    },
    {
        "url": "https://martech.org/feed/",
        "tag": "#AdTech",
        "keywords": ["ai", "brand", "marketing", "automation", "cpa", "conversion", "affiliate"]
    },
    {
        "url": "https://digiday.com/feed/",
        "tag": "#AdTech",
        "keywords": ["brand", "ad", "creator", "influencer", "cpa", "ai", "monetization", "social"]
    },
    {
        "url": "https://www.marketingweek.com/feed/",
        "tag": "#AdTech",
        "keywords": ["brand", "ad", "creator", "influencer", "ai", "affiliate", "performance marketing"]
    },
    # ── AI-АГЕНТЫ ──────────────────────────────────────────────────────────
    {
        "url": "https://agentsnews.io/rss.xml",
        "tag": "#AIАгенты",
        "keywords": ["agent", "automation", "workflow", "autonomous", "agentic", "multi-agent"]
    },
    {
        "url": "https://www.llmsecurity.net/rss.xml",
        "tag": "#AIАгенты",
        "keywords": ["agent", "llm", "autonomous", "tool use", "multi-agent"]
    },
    {
        "url": "https://techcrunch.com/tag/automation/feed/",
        "tag": "#AIАгенты",
        "keywords": ["automation", "agent", "ai", "workflow", "autonomous"]
    },
    # ── СТАРТАПЫ ───────────────────────────────────────────────────────────
    {
        "url": "https://techcrunch.com/category/startups/feed/",
        "tag": "#AIСтартапы",
        "keywords": ["ai", "funding", "series", "seed", "startup", "venture", "raise"]
    },
    {
        "url": "https://sifted.eu/rss",
        "tag": "#AIСтартапы",
        "keywords": ["ai", "startup", "funding", "series", "venture", "raise", "scaleup"]
    },
    # ── БЕСПЛАТНЫЙ AI ──────────────────────────────────────────────────────
    {
        "url": "https://techcrunch.com/tag/free/feed/",
        "tag": "#БесплатныйAI",
        "keywords": ["free", "freemium", "open-source", "no-code", "affordable", "subscription", "access"]
    },
]

# ---------------------------------------------------------------------------
# БАЗА ДАННЫХ
# ---------------------------------------------------------------------------
def init_db():
    con = sqlite3.connect(DB_FILE)
    con.execute("""
        CREATE TABLE IF NOT EXISTS published_news (
            url        TEXT PRIMARY KEY,
            title      TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    con.commit()
    con.close()
    log.info(f"БД инициализирована: {DB_FILE}")

def titles_are_similar(a: str, b: str, threshold: float) -> bool:
    return SequenceMatcher(None, a.lower(), b.lower()).ratio() >= threshold

def is_duplicate(url: str, title: str, cycle_titles: list) -> bool:
    try:
        con = sqlite3.connect(DB_FILE)
        cur = con.cursor()
        cur.execute("SELECT 1 FROM published_news WHERE url=?", (url,))
        if cur.fetchone():
            con.close()
            return True
        cur.execute("SELECT title FROM published_news ORDER BY created_at DESC LIMIT 80")
        recent_db_titles = [row[0] for row in cur.fetchall()]
        con.close()
    except Exception as e:
        log.error(f"Ошибка чтения БД: {e}")
        return False

    for old_title in recent_db_titles:
        if titles_are_similar(title, old_title, SIMILARITY_DB):
            return True
    for cycle_title in cycle_titles:
        if titles_are_similar(title, cycle_title, SIMILARITY_CYCLE):
            return True
    return False

def mark_published(url: str, title: str):
    try:
        con = sqlite3.connect(DB_FILE)
        con.execute(
            "INSERT OR IGNORE INTO published_news (url, title) VALUES (?, ?)",
            (url, title)
        )
        con.commit()
        con.close()
    except Exception as e:
        log.error(f"Ошибка записи в БД: {e}")

# ---------------------------------------------------------------------------
# AI-ОБРАБОТКА
# ---------------------------------------------------------------------------
def process_with_ai(title: str, description: str, tag: str):
    global gemini_retry_after
    if not client:
        return None
    if time.time() < gemini_retry_after:
        log.warning("Gemini на паузе из-за квоты, пропускаем AI.")
        return None

    clean_desc = re.sub(r'<[^>]+>', '', (description or ""))[:2000]
    prompt = (
        "Ты техно-блогер для Telegram-канала об AI и GoClaw. "
        "Перескажи новость КРАТКО на русском языке:\n"
        "— Заголовок с одним релевантным эмодзи\n"
        "— 3 коротких тезиса (каждый с эмодзи)\n"
        "— 1 строка вывода: почему это важно\n"
        "Не используй жирный шрифт **. Не пиши 'Заголовок:' или 'Вывод:'.\n\n"
        f"Оригинал: {title}\nТекст: {clean_desc}"
    )
    try:
        response = client.models.generate_content(model=MODEL_ID, contents=prompt)
        if not response.text:
            return None
        return f"{tag}\n\n{response.text.replace('**', '').strip()}"
    except Exception as e:
        log.error(f"Gemini Error: {e}")
        if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
            gemini_retry_after = time.time() + 3600
            log.warning("Gemini отключён на 1 час из-за превышения квоты.")
        return None

# ---------------------------------------------------------------------------
# ТЕЛЕГРАМ
# ---------------------------------------------------------------------------
def send_telegram(text: str, url: str) -> bool:
    api_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    safe_text = text[:3500].strip()
    safe_url  = escape(url, quote=True)

    html_payload = {
        "chat_id": CHANNEL_ID,
        "text": f"{safe_text}\n\n🔗 <a href='{safe_url}'>Источник</a>",
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
    plain_payload = {
        "chat_id": CHANNEL_ID,
        "text": f"{safe_text}\n\nИсточник: {url}",
        "disable_web_page_preview": False,
    }

    for idx, payload in enumerate((html_payload, plain_payload), start=1):
        try:
            r = http.post(api_url, json=payload, timeout=(10, 45))
            if r.status_code == 200:
                return True
            log.error(f"Telegram Error {r.status_code} (попытка {idx}): {r.text[:300]}")
        except requests.exceptions.Timeout as e:
            log.error(f"Telegram timeout (попытка {idx}): {e}")
        except Exception as e:
            log.error(f"Telegram request error (попытка {idx}): {e}")
    return False

# ---------------------------------------------------------------------------
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ---------------------------------------------------------------------------
def _clean_text(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", text).strip()

def _looks_russian(text: str) -> bool:
    if not text:
        return True
    cyr = len(re.findall(r"[А-Яа-яЁё]", text))
    lat = len(re.findall(r"[A-Za-z]", text))
    return cyr >= lat

def _translate_to_ru(text: str) -> str:
    clean = _clean_text(text)
    if not clean or _looks_russian(clean):
        return clean
    try:
        translated = translator.translate(clean)
        return _clean_text(translated or clean)
    except Exception as e:
        log.warning(f"Ошибка перевода: {e}")
        return clean

def _shorten(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit - 3].rstrip(" ,.;:-") + "..."

def _extract_points(description: str) -> list:
    clean_desc = _clean_text(description)
    if not clean_desc:
        return []
    parts = re.split(r"(?<=[.!?])\s+", clean_desc)
    points = []
    for part in parts:
        part = part.strip(" -•")
        if len(part) < 35:
            continue
        points.append(_shorten(part, 160))
        if len(points) == 2:
            break
    if not points:
        points.append(_shorten(clean_desc, 160))
    return points

def build_fallback_post(title: str, description: str, tag: str) -> str:
    ru_title = _shorten(_translate_to_ru(title), 180)
    points   = [_translate_to_ru(p) for p in _extract_points(description)]
    lines = [
        escape(tag), "",
        f"<b>{escape(ru_title)}</b>", "",
    ]
    for point in points:
        lines.append(f"• {escape(point)}")
    lines.extend([
        "",
        "<i>Почему важно:</i> тема уже в актуальной AI-повестке и может быстро разойтись по рынку.",
    ])
    return "\n".join(lines)

# ---------------------------------------------------------------------------
# ОСНОВНОЙ ЦИКЛ
# ---------------------------------------------------------------------------
def check_news():
    log.info("--- ЗАПУСК ПРОВЕРКИ ---")
    cycle_titles = []
    headers = {'User-Agent': 'Mozilla/5.0'}
    sent_count = 0

    for feed_item in RSS_FEEDS:
        try:
            resp = requests.get(feed_item["url"], headers=headers, timeout=20)
            resp.raise_for_status()
            feed = feedparser.parse(resp.content)

            for entry in feed.entries[:MAX_PER_FEED]:
                link  = entry.get("link", "").strip()
                title = entry.get("title", "").strip()

                if not link or not title:
                    continue
                if is_duplicate(link, title, cycle_titles):
                    log.debug(f"Дубликат пропущен: {title[:60]}")
                    continue

                full_content = (title + " " + entry.get("summary", "")).lower()
                if not any(kw.lower() in full_content for kw in feed_item["keywords"]):
                    continue

                ai_text    = process_with_ai(title, entry.get("summary", ""), feed_item["tag"])
                final_text = ai_text or build_fallback_post(title, entry.get("summary", ""), feed_item["tag"])

                if send_telegram(final_text, link):
                    mark_published(link, title)
                    cycle_titles.append(title)
                    sent_count += 1
                    log.info(f"✅ Опубликовано ({sent_count}): {title[:60]}...")
                    time.sleep(5)
                else:
                    log.error(f"❌ Не удалось отправить: {title[:80]}")

        except requests.exceptions.RequestException as e:
            log.error(f"Ошибка загрузки фида {feed_item['url']}: {e}")
        except Exception as e:
            log.error(f"Неожиданная ошибка фида {feed_item['url']}: {e}")

    log.info(f"--- ПРОВЕРКА ЗАВЕРШЕНА: отправлено {sent_count} новостей ---")

def main():
    if not TELEGRAM_TOKEN or not CHANNEL_ID:
        raise ValueError("ОШИБКА: TELEGRAM_TOKEN или CHANNEL_ID не заданы.")
    if not GEMINI_TOKEN:
        log.warning("GEMINI_TOKEN не задан — AI-пересказ отключён.")

    init_db()

    # Health-сервер в фоне (нужен для type: web в render.yaml)
    threading.Thread(target=run_health_server, daemon=True).start()

    log.info("🚀 Бот запущен!")
    cycle_num = 0

    while True:
        cycle_num += 1
        log.info(f"========== ЦИКЛ #{cycle_num} НАЧАТ ==========")
        try:
            check_news()
        except Exception as e:
            log.error(f"Критическая ошибка в check_news(): {e}", exc_info=True)

        log.info(f"========== ЦИКЛ #{cycle_num} ЗАВЕРШЁН, следующий через {CHECK_INTERVAL}с ==========")
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
