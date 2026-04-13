import os
import feedparser
import sqlite3
import time
import logging
import requests
import html # Не используется напрямую в текущей версии, но может быть полезен
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
CHANNEL_ID     = os.environ.get("CHANNEL_ID") # Единый канал для всех новостей

HF_TOKEN       = os.environ.get("HF_TOKEN")
MODEL_ID       = "Qwen/Qwen2.5-72B-Instruct"
DB_FILE        = "ai_news.db"
CHECK_INTERVAL = 1800 # 30 минут

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)
client = InferenceClient(api_key=HF_TOKEN)

# Группировка RSS-лент по тематикам с присвоением тегов и ключевых слов
# Важно: если новость может подходить под несколько категорий,
# порядок в этом списке может иметь значение (первое совпадение будет использовано)
RSS_FEED_TAGS_WITH_KEYWORDS = [
    # 1. Voice Conversion Pipeline (RVC v2, FFmpeg, GPU)
    {"url": "https://blogs.nvidia.com/feed/", "tag": "#ГолосовыеТехнологииИИ", "keywords": ["voice", "speech", "audio", "ffmpeg", "rvc", "gpu", " cuda", "synthes", "clone"]},
    {"url": "https://www.theverge.com/ai-artificial-intelligence/rss/index.xml", "tag": "#ГолосовыеТехнологииИИ", "keywords": ["voice", "speech", "audio", "synthes", "clone"]},
    {"url": "https://techcrunch.com/category/artificial-intelligence/feed/", "tag": "#ГолосовыеТехнологииИИ", "keywords": ["voice", "speech", "audio", "synthes", "clone", "gpu"]},
    {"url": "https://export.arxiv.org/rss/cs.SD", "tag": "#ГолосовыеТехнологииИИ", "keywords": ["speech", "audio", "voice", "synthesis", "cloning"]}, # Speech and Audio Processing Arxiv
    {"url": "https://habr.com/ru/rss/hub/sound/all/", "tag": "#ГолосовыеТехнологииИИ", "keywords": ["голос", "речь", "аудио", "синтез", "клонирование"]}, # Хабр Звук

    # 2. GoClaw (Мультиагентные системы, Go, Browser Automation, whatsmeow, LLM, Канбан, Scheduling, Memory, Observability, Безопасность)
    {"url": "https://huggingface.co/blog/feed.xml", "tag": "#GoClawTech", "keywords": ["agent", "llm", "automation", "go", "golang", "multi-agent", "kanban", "observability", "security", "whatsmeow"]},
    {"url": "https://deepmind.google/blog/rss.xml", "tag": "#GoClawTech", "keywords": ["agent", "llm", "multi-agent", "ai system"]},
    {"url": "https://openai.com/news/rss.xml", "tag": "#GoClawTech", "keywords": ["agent", "llm", "automation", "multi-agent"]},
    {"url": "https://habr.com/ru/rss/hub/go/all/", "tag": "#GoClawTech", "keywords": ["go", "golang", "concurrency", "performance"]}, # Хабр Go
    {"url": "https://habr.com/ru/rss/hub/chatbots/all/", "tag": "#GoClawTech", "keywords": ["бот", "агент", "автоматизация", "llm"]}, # Хабр Чат-боты
    {"url": "https://www.theverge.com/ai-artificial-intelligence/rss/index.xml", "tag": "#GoClawTech", "keywords": ["agent", "llm", "automation", "multi-agent"]}, # The Verge AI
    {"url": "https://techcrunch.com/category/artificial-intelligence/feed/", "tag": "#GoClawTech", "keywords": ["agent", "llm", "automation", "multi-agent"]}, # TechCrunch AI

    # 3. CPA-трекинг и Платежи (Deep Links, Webhooks, Escrow, ЮKassa)
    {"url": "https://vc.ru/rss/u/100587-finansy", "tag": "#ФинансыИПлатежи", "keywords": ["cpa", "affiliate", "tracking", "webhook", "escrow", "payment", "юkassa", "финтех", "платеж", "комиссия"]}, # VC.ru Финансы
    {"url": "https://trends.rbc.ru/trends/rss/5d6910609a7947677846540e", "tag": "#ФинансыИПлатежи", "keywords": ["cpa", "affiliate", "tracking", "payment", "финтех", "платеж", "комиссия"]}, # РБК Тренды ИИ (перекрытие возможно)
    {"url": "https://www.paymentssource.com/rss", "tag": "#ФинансыИПлатежи", "keywords": ["payment", "fintech", "escrow", "transactions", "commerce", "cpa"]}, # PaymentsSource
    {"url": "https://www.fintechfutures.com/feed/", "tag": "#ФинансыИПлатежи", "keywords": ["payment", "fintech", "escrow", "transactions", "commerce", "cpa"]}, # FinTech Futures

    # 4. Фронтенд (Next.js, React, UI/UX)
    {"url": "https://nextjs.org/feed.xml", "tag": "#ФронтендРазработка", "keywords": ["next.js", "react", "frontend", "ui", "ux", "web development", "javascript", "tsx"]}, # Next.js Blog
    {"url": "https://react.dev/feed.xml", "tag": "#ФронтендРазработка", "keywords": ["react", "frontend", "ui", "ux", "web development", "javascript", "tsx"]}, # React Blog
    {"url": "https://habr.com/ru/rss/hub/react/all/", "tag": "#ФронтендРазработка", "keywords": ["react", "frontend", "ui", "ux", "web development", "javascript", "tsx"]}, # Хабр React
    {"url": "https://www.smashingmagazine.com/feed/", "tag": "#ФронтендРазработка", "keywords": ["ui", "ux", "frontend", "web design", "design system"]}, # Smashing Magazine

    # 5. Соцсети и Маркетинг (Stories, SMM, Контент-маркетинг)
    {"url": "https://techcrunch.com/category/social/feed/", "tag": "#СоцсетиМаркетинг", "keywords": ["social media", "marketing", "smm", "stories", "content", "influencer", "brand"]},
    {"url": "https://blog.hootsuite.com/feed/", "tag": "#СоцсетиМаркетинг", "keywords": ["social media", "marketing", "smm", "stories", "content", "brand", "engagement"]}, # Hootsuite Blog
    {"url": "https://vc.ru/rss/new", "tag": "#СоцсетиМаркетинг", "keywords": ["соцсети", "маркетинг", "smm", "сторис", "контент", "бренд", "продвижение"]}, # VC.ru
    {"url": "https://www.socialmediaexaminer.com/feed/", "tag": "#СоцсетиМаркетинг", "keywords": ["social media", "marketing", "smm", "stories", "content", "influencer", "brand"]}, # Social Media Examiner

    # 6. Общие новости ИИ (если новость не подошла под более специфичные категории)
    {"url": "https://openai.com/news/rss.xml", "tag": "#ОбщиеНовостиИИ", "keywords": ["ai", "artificial intelligence", "machine learning", "deep learning", "model"]},
    {"url": "https://deepmind.google/blog/rss.xml", "tag": "#ОбщиеНовостиИИ", "keywords": ["ai", "artificial intelligence", "machine learning", "deep learning", "model"]},
    {"url": "https://www.anthropic.com/newsfeed/rss.xml", "tag": "#ОбщиеНовостиИИ", "keywords": ["ai", "artificial intelligence", "machine learning", "deep learning", "model"]},
    {"url": "https://ai.meta.com/blog/rss/", "tag": "#ОбщиеНовостиИИ", "keywords": ["ai", "artificial intelligence", "machine learning", "deep learning", "model"]},
    {"url": "https://blogs.microsoft.com/ai/feed/", "tag": "#ОбщиеНовостиИИ", "keywords": ["ai", "artificial intelligence", "machine learning", "deep learning", "model"]},
    {"url": "https://machinelearning.apple.com/rss.xml", "tag": "#ОбщиеНовостиИИ", "keywords": ["ai", "artificial intelligence", "machine learning", "deep learning", "model"]},
    {"url": "https://mistral.ai/news/index.xml", "tag": "#ОбщиеНовостиИИ", "keywords": ["ai", "artificial intelligence", "machine learning", "deep learning", "model"]},
    {"url": "https://export.arxiv.org/rss/cs.AI", "tag": "#ОбщиеНовостиИИ", "keywords": ["ai", "artificial intelligence", "machine learning", "deep learning", "model"]},
    {"url": "https://habr.com/ru/rss/hub/artificial_intelligence/all/", "tag": "#ОбщиеНовостиИИ", "keywords": ["ии", "искусственный интеллект", "машинное обучение", "нейросети"]},
    {"url": "https://vc.ru/rss/u/1215160-iskusstvennyy-intellekt", "tag": "#ОбщиеНовостиИИ", "keywords": ["ии", "искусственный интеллект", "машинное обучение", "нейросети"]},
    {"url": "https://trends.rbc.ru/trends/rss/5d6910609a7947677846540e", "tag": "#ОбщиеНовостиИИ", "keywords": ["ии", "искусственный интеллект", "машинное обучение", "нейросети"]},
    # Твиттер (для главных лиц, их новости могут быть общими)
    {"url": "https://nitter.no-logs.com/sama/rss", "tag": "#ЛичностиИИ", "keywords": ["ai", "openai", "gpt", "agи", "ии", "альман"]},
    {"url": "https://nitter.no-logs.com/karpathy/rss", "tag": "#ЛичностиИИ", "keywords": ["ai", "tesla", "ml", "neural network", "ии", "карпатый"]},
    {"url": "https://nitter.no-logs.com/ylecun/rss", "tag": "#ЛичностиИИ", "keywords": ["ai", "meta", "dl", "yann lecun", "ии", "лекан"]},
    {"url": "https://nitter.no-logs.com/demishassabis/rss", "tag": "#ЛичностиИИ", "keywords": ["deepmind", "google", "agи", "ai", "демис хассабис"]},
    {"url": "https://nitter.no-logs.com/gdb/rss", "tag": "#ЛичностиИИ", "keywords": ["openai", "gpt", "ai", "броман"]},
    {"url": "https://nitter.no-logs.com/ilyasut/rss", "tag": "#ЛичностиИИ", "keywords": ["openai", "gpt", "ai", "илья суцкевер"]},
]


def init_db():
    con = sqlite3.connect(DB_FILE)
    con.execute("CREATE TABLE IF NOT EXISTS published_news (url TEXT PRIMARY KEY, tag TEXT)")
    con.commit()
    con.close()

def is_published(url: str, tag: str):
    con = sqlite3.connect(DB_FILE)
    # Теперь проверяем не только URL, но и тег, чтобы одна и та же новость из разных фидов
    # с разными тегами могла быть опубликована, если это необходимо.
    # Если вы хотите строгую уникальность по URL, уберите `AND tag=?`
    cur = con.execute("SELECT 1 FROM published_news WHERE url=? AND tag=?", (url, tag,))
    res = cur.fetchone()
    con.close()
    return res is not None

def mark_published(url: str, tag: str):
    con = sqlite3.connect(DB_FILE)
    con.execute("INSERT OR IGNORE INTO published_news (url, tag) VALUES (?, ?)", (url, tag,))
    con.commit()
    con.close()

def is_relevant(entry_text: str, keywords: list) -> bool:
    """
    Проверяет, содержит ли текст новости хотя бы одно из ключевых слов.
    """
    if not keywords:
        return True # Если нет ключевых слов, считаем релевантным
    text_lower = entry_text.lower()
    for keyword in keywords:
        if keyword.lower() in text_lower:
            return True
    return False

def process_with_ai(title: str, description: str, tag: str):
    clean_desc = re.sub(r'<[^>]+>', '', (description or ""))[:800]
    messages = [
        {"role": "system", "content": "Ты техно-блогер. Кратко перескажи новость на русском. Сделай заголовок с эмодзи, 3 тезиса и вывод. Не используй **. Будь лаконичным и информативным."},
        {"role": "user", "content": f"Заголовок: {title}\nТекст: {clean_desc}"}
    ]
    try:
        response = client.chat_completion(model=MODEL_ID, messages=messages, max_tokens=600, temperature=0.7)
        final_text = response.choices[0].message.content
        # Добавляем тег в начало текста
        return f"{tag}\n{final_text.replace('**', '').strip()}"
    except Exception as e:
        log.error(f"Ошибка ИИ при обработке '{title}': {e}")
        return None

def send_telegram(text: str, url: str):
    formatted_text = f"{text}\n\n🔗 <a href='{url}'>Источник</a>"
    api_url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        response = requests.post(api_url, json={"chat_id": CHANNEL_ID, "text": formatted_text, "parse_mode": "HTML"}, timeout=15)
        response.raise_for_status() # Вызовет исключение для ошибок HTTP
        return True
    except requests.exceptions.RequestException as e:
        log.error(f"Ошибка отправки в Telegram: {e}. Ответ: {e.response.text if e.response else 'N/A'}")
        return False

def check_news():
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
    
    processed_urls_in_this_run = set() # Чтобы избежать дублирования из разных фидов в рамках одного запуска
    
    for feed_config in RSS_FEED_TAGS_WITH_KEYWORDS:
        feed_url = feed_config["url"]
        tag = feed_config["tag"]
        keywords = feed_config["keywords"]

        try:
            log.info(f"Проверка RSS-ленты: {feed_url} для тега {tag}")
            resp = requests.get(feed_url, headers=headers, timeout=15)
            resp.raise_for_status() # Вызовет исключение для ошибок HTTP 4xx/5xx
            feed = feedparser.parse(resp.content)
            
            if feed.bozo:
                log.warning(f"Ошибка парсинга RSS-ленты {feed_url}: {feed.bozo_exception}")

            for entry in feed.entries[:3]: # Берем несколько самых свежих новостей на случай, если первая нерелевантна
                url = entry.get("link", "")
                title = entry.get("title", "")
                summary = entry.get("summary", "")

                if not url:
                    log.warning(f"Пропущена запись без ссылки в ленте {feed_url}")
                    continue
                
                # Проверяем, была ли эта новость уже опубликована с этим тегом
                if is_published(url, tag) or url in processed_urls_in_this_run:
                    continue
                
                entry_text = f"{title} {summary}"
                
                # Фильтруем по ключевым словам, если они заданы для этой категории
                if not is_relevant(entry_text, keywords):
                    log.debug(f"Новость '{title}' из {feed_url} нерелевантна для тега {tag} по ключевым словам.")
                    continue
                
                ai_text = process_with_ai(title, summary, tag)
                if ai_text:
                    if send_telegram(ai_text, url):
                        mark_published(url, tag)
                        processed_urls_in_this_run.add(url)
                        log.info(f"✅ Опубликовано: '{title}' в канале {CHANNEL_ID} с тегом {tag}. Источник: {url}")
                        time.sleep(15) # Увеличиваем паузу после успешной публикации
                    else:
                        log.error(f"Не удалось отправить новость '{title}' в Telegram.")
                else:
                    log.warning(f"AI не сгенерировал текст для новости '{title}'. Пропускаем.")
        
        except requests.exceptions.Timeout:
            log.error(f"Таймаут при запросе RSS-ленты: {feed_url}")
        except requests.exceptions.HTTPError as e:
            log.error(f"HTTP ошибка при доступе к RSS-ленте {feed_url}: {e.response.status_code} - {e.response.text}")
        except requests.exceptions.RequestException as e:
            log.error(f"Ошибка соединения при доступе к RSS-ленте {feed_url}: {e}")
        except Exception as e:
            log.error(f"Непредвиденная ошибка при обработке ленты {feed_url}: {e}", exc_info=True)
        
        time.sleep(5) # Небольшая пауза между обработкой разных RSS-лент

def main():
    init_db()
    # Запускаем фейковый сервер в отдельном потоке
    threading.Thread(target=run_health_server, daemon=True).start()
    log.info("🚀 Бот и Health-сервер запущены!")

    while True:
        log.info("Начинаем проверку новостей...")
        check_news()
        log.info(f"⏳ Сон {CHECK_INTERVAL / 60} мин...")
        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()