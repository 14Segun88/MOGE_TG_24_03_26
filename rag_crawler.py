"""
RAG Crawler v2  — нормативные документы для Knowledge Base
МособлГосЭкспертиза | Шаг 3

Запуск:
  python3 rag_crawler.py --priority 1          # Приоритет 1 (kap, ito, gochs)
  python3 rag_crawler.py --priority all        # Все 41 документ
  python3 rag_crawler.py --dept gochs          # Только пожарная безопасность
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from pathlib import Path
from urllib.parse import urlparse

import aiofiles
import aiohttp
from bs4 import BeautifulSoup
from markdownify import markdownify as md
from tqdm.asyncio import tqdm

# ─────────────────────────────────────────────
#  Пути (Диск D)
# ─────────────────────────────────────────────
RAW_DIR       = Path("/mnt/d/rag_data/raw")
META_DIR      = Path("/mnt/d/rag_data/meta")
MANIFEST_PATH = Path("/mnt/d/rag_data/manifest.json")

RAW_DIR.mkdir(parents=True, exist_ok=True)
META_DIR.mkdir(parents=True, exist_ok=True)

# ─────────────────────────────────────────────
#  Настройки
# ─────────────────────────────────────────────
CONCURRENCY     = 3      # параллельных запросов
DELAY_BETWEEN   = 3.0    # секунд между запросами на один домен
MAX_RETRIES     = 3      # попыток при 403/429/таймауте
REQUEST_TIMEOUT = 45

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept":                  "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language":         "ru-RU,ru;q=0.9,en;q=0.8",
    "Accept-Encoding":         "gzip, deflate, br",
    "Connection":              "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest":          "document",
    "Sec-Fetch-Mode":          "navigate",
    "Sec-Fetch-Site":          "none",
    "Sec-Fetch-User":          "?1",
    "Cache-Control":           "max-age=0",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("/mnt/d/rag_data/crawler.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("rag_crawler")


# ─────────────────────────────────────────────
#  HTML → Markdown (с сохранением таблиц)
# ─────────────────────────────────────────────
def html_to_markdown(html: str, url: str) -> dict:
    """
    Конвертирует HTML документа в структурированный Markdown.
    - Заголовки → # ## ### (иерархия)
    - Таблицы   → Markdown | col1 | col2 |
    - Пункты    → - строки
    """
    soup = BeautifulSoup(html, "lxml")

    # Удаляем мусор
    for tag in soup.find_all(["nav", "footer", "header", "aside", "script", "style", "iframe", "noscript"]):
        tag.decompose()
    for sel in [".navbar", ".breadcrumb", ".sidebar", ".advertisement", "#navigation", "#footer"]:
        for el in soup.select(sel):
            el.decompose()

    # Основной контент
    content_el = (
        soup.find("div", class_=re.compile(r"document[-_]?(content|text|body)", re.I))
        or soup.find("div", id=re.compile(r"document[-_]?(content|text|body)", re.I))
        or soup.find("article")
        or soup.find("main")
        or soup.find("body")
    )
    if not content_el:
        return {"title": "", "markdown": "", "has_tables": False, "status": "active"}

    # Заголовок
    title_el = soup.find("h1") or soup.find("title")
    title = title_el.get_text(strip=True) if title_el else ""

    has_tables = bool(content_el.find("table"))

    # HTML → Markdown (без strip + convert одновременно — они конфликтуют)
    markdown_text = md(
        str(content_el),
        heading_style="ATX",
        bullets="-",
        newline_style="backslash",
    )

    # Убираем base64-блоки из готового Markdown
    markdown_text = re.sub(r"!\[.*?\]\(data:[^)]+\)", "", markdown_text)
    # Убираем ненужные экранированные символы
    markdown_text = re.sub(r"\\([.\-_()\[\]])", r"\1", markdown_text)
    markdown_text = re.sub(r"\n{4,}", "\n\n\n", markdown_text)
    markdown_text = re.sub(r"^ +$", "", markdown_text, flags=re.MULTILINE)

    if title and not markdown_text.startswith("# "):
        markdown_text = f"# {title}\n\n> Источник: {url}\n\n" + markdown_text

    # Статус
    tl = markdown_text.lower()
    status = "cancelled" if any(w in tl for w in ["утратил силу", "отменён", "не действует"]) else "active"

    return {"title": title, "markdown": markdown_text, "has_tables": has_tables, "status": status}


# ─────────────────────────────────────────────
#  aiohttp загрузка с retry
# ─────────────────────────────────────────────
async def fetch_url(url: str, session: aiohttp.ClientSession) -> str | None:
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            async with session.get(
                url,
                headers=HEADERS,
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
                allow_redirects=True,
                ssl=False,
            ) as resp:
                if resp.status == 200:
                    return await resp.text(encoding="utf-8", errors="replace")
                elif resp.status in (403, 429):
                    wait = attempt * 7
                    log.warning(f"HTTP {resp.status} → ждём {wait}s (попытка {attempt}/{MAX_RETRIES}): {url}")
                    await asyncio.sleep(wait)
                else:
                    log.warning(f"HTTP {resp.status}: {url}")
                    return None
        except asyncio.TimeoutError:
            log.warning(f"⏰ Таймаут (попытка {attempt}/{MAX_RETRIES}): {url}")
            await asyncio.sleep(attempt * 3)
        except Exception as exc:
            log.warning(f"Ошибка {url}: {exc}")
            return None
    log.error(f"❌ Исчерпаны попытки: {url}")
    return None


# ─────────────────────────────────────────────
#  Краулер
# ─────────────────────────────────────────────
class RagCrawler:
    def __init__(self):
        self._sem = asyncio.Semaphore(CONCURRENCY)
        self._domain_ts: dict[str, float] = {}

    async def _rate_limit(self, url: str) -> None:
        domain = urlparse(url).netloc
        wait = DELAY_BETWEEN - (time.monotonic() - self._domain_ts.get(domain, 0))
        if wait > 0:
            await asyncio.sleep(wait)
        self._domain_ts[domain] = time.monotonic()

    async def process(self, doc: dict, session: aiohttp.ClientSession) -> dict | None:
        doc_id = doc["id"]
        url    = doc["url"]

        md_path   = RAW_DIR  / f"{doc_id}.md"
        meta_path = META_DIR / f"{doc_id}.json"

        if md_path.exists() and meta_path.exists():
            log.info(f"⏭ {doc_id} — уже скачан")
            return None

        async with self._sem:
            await self._rate_limit(url)
            html = await fetch_url(url, session)

        if not html:
            return None

        parsed = html_to_markdown(html, url)

        if len(parsed["markdown"]) < 300:
            log.warning(f"⚠ Мало текста ({len(parsed['markdown'])} симв.): {doc_id}")

        async with aiofiles.open(md_path, "w", encoding="utf-8") as f:
            await f.write(parsed["markdown"])

        meta = {
            "id":         doc_id,
            "title":      parsed["title"] or doc["title"],
            "dept":       doc["dept"],
            "section":    doc["section"],
            "status":     parsed["status"],
            "url":        url,
            "has_tables": parsed["has_tables"],
            "chars":      len(parsed["markdown"]),
            "file":       str(md_path),
            "url_hash":   hashlib.md5(url.encode()).hexdigest(),
        }

        async with aiofiles.open(meta_path, "w", encoding="utf-8") as f:
            await f.write(json.dumps(meta, ensure_ascii=False, indent=2))

        tbl = " 📊" if parsed["has_tables"] else ""
        log.info(f"✅ {doc_id}: {len(parsed['markdown'])} симв.{tbl}")
        return meta

    async def run(self, docs: list[dict]) -> None:
        results: list[dict] = []
        connector = aiohttp.TCPConnector(limit=CONCURRENCY * 2, ssl=False)

        async with aiohttp.ClientSession(connector=connector) as session:
            tasks = [self.process(doc, session) for doc in docs]
            for coro in tqdm(asyncio.as_completed(tasks), total=len(tasks), desc="RAG Crawler"):
                result = await coro
                if result:
                    results.append(result)

        # Манифест
        existing = {}
        if MANIFEST_PATH.exists():
            existing = {m["id"]: m for m in json.loads(MANIFEST_PATH.read_text("utf-8"))}
        existing.update({m["id"]: m for m in results})
        MANIFEST_PATH.write_text(json.dumps(list(existing.values()), ensure_ascii=False, indent=2), "utf-8")

        log.info("\n" + "═" * 50)
        log.info(f"📊 Загружено:    {len(results)}/{len(docs)}")
        log.info(f"📋 С таблицами: {sum(1 for r in results if r.get('has_tables'))}")
        log.info(f"⚠  Отменённых:  {sum(1 for r in results if r.get('status') == 'cancelled')}")
        log.info(f"📁 D:\\rag_data\\raw\\ и D:\\rag_data\\meta\\")
        log.info("═" * 50)


# ─────────────────────────────────────────────
#  Каталог документов
# ─────────────────────────────────────────────
DOCUMENTS: list[dict] = [
    # 🔴 Объекты кап. строительства (приоритет 1)
    {"id": "GRK-RF",    "title": "Градостроительный кодекс РФ",                   "dept": "kap",   "section": "01",   "status": "active", "url": "https://docs.cntd.ru/document/901919338"},
    {"id": "FZ-384",    "title": "ФЗ №384 Техрегламент о безопасности зданий",     "dept": "kap",   "section": "02",   "status": "active", "url": "https://docs.cntd.ru/document/1200026446"},
    {"id": "FZ-181",    "title": "ФЗ №181 О социальной защите инвалидов",          "dept": "kap",   "section": "11",   "status": "active", "url": "https://docs.cntd.ru/document/9046803"},
    {"id": "PP-87",     "title": "ПП РФ №87 Состав проектной документации",        "dept": "kap",   "section": "all",  "status": "active", "url": "https://docs.cntd.ru/document/902059849"},
    # URL исправлены (предыдущие возвращали 404)
    # PP-963 — consultant.ru (pravo.gov.ru требует JS-браузер)
    {"id": "PP-963",    "title": "ПП РФ №963 Государственная экспертиза",      "dept": "kap", "section": "all", "status": "active", "url": "https://www.consultant.ru/document/cons_doc_LAW_418024/"},
    {"id": "PP-154",    "title": "ПП РФ №154 Электронная экспертиза",          "dept": "kap", "section": "all", "status": "active", "url": "https://docs.cntd.ru/document/902086396"},
    {"id": "SP-42",     "title": "СП 42.13330.2016 Градостроительство",            "dept": "kap",   "section": "02",   "status": "active", "url": "https://docs.cntd.ru/document/456054209"},
    {"id": "SP-54",     "title": "СП 54.13330.2022 Жилые здания",                  "dept": "kap",   "section": "03",   "status": "active", "url": "https://docs.cntd.ru/document/727378283"},
    {"id": "SP-59",     "title": "СП 59.13330.2020 Доступность для ОВЗ",           "dept": "kap",   "section": "11",   "status": "active", "url": "https://docs.cntd.ru/document/565372705"},
    {"id": "SP-118",    "title": "СП 118.13330.2022 Общественные здания",          "dept": "kap",   "section": "03",   "status": "active", "url": "https://docs.cntd.ru/document/351102147"},
    {"id": "SP-20",     "title": "СП 20.13330.2017 Нагрузки и воздействия",       "dept": "kap",   "section": "04",   "status": "active", "url": "https://docs.cntd.ru/document/456069843"},
    {"id": "SP-22",     "title": "СП 22.13330.2016 Основания зданий",              "dept": "kap",   "section": "04",   "status": "active", "url": "https://docs.cntd.ru/document/456069011"},
    {"id": "SP-63",     "title": "СП 63.13330.2018 ЖБ конструкции",            "dept": "kap", "section": "04", "status": "active", "url": "https://docs.cntd.ru/document/564376473"},
    {"id": "SP-48",     "title": "СП 48.13330.2019 Организация строительства", "dept": "kap", "section": "07", "status": "active", "url": "https://docs.cntd.ru/document/564931958"},
    # Исправленные URL для ИТО
    {"id": "SP-256",    "title": "СП 256 Электроустановки жилых зданий",           "dept": "ito",   "section": "05.1", "status": "active", "url": "https://docs.cntd.ru/document/1200162802"},
    {"id": "PUE-7",     "title": "ПУЭ 7-е издание",                               "dept": "ito",   "section": "05.1", "status": "active", "url": "https://docs.cntd.ru/document/1200003114"},
    {"id": "SP-31",     "title": "СП 31.13330.2021 Водоснабжение",             "dept": "ito", "section": "05.2", "status": "active", "url": "https://docs.cntd.ru/document/573039603"},
    {"id": "SP-30",     "title": "СП 30.13330.2020 Внутренний водопровод",        "dept": "ito",   "section": "05.2", "status": "active", "url": "https://docs.cntd.ru/document/565318015"},
    {"id": "SP-32",     "title": "СП 32.13330.2018 Канализация",                  "dept": "ito",   "section": "05.3", "status": "active", "url": "https://docs.cntd.ru/document/554402297"},
    # ФЗ-416 перемещён на официальный pravo.gov.ru (CNTD периодически переносит ФЗ)
    # ФЗ-416 — consultant.ru (pravo.gov.ru требует JS-браузер)
    {"id": "FZ-416",    "title": "ФЗ №416 О водоснабжении и водоотведении",    "dept": "ito", "section": "05.3", "status": "active", "url": "https://www.consultant.ru/document/cons_doc_LAW_122855/"},
    {"id": "SP-60",     "title": "СП 60.13330.2020 Отопление, вентиляция",        "dept": "ito",   "section": "05.4", "status": "active", "url": "https://docs.cntd.ru/document/565370316"},
    {"id": "SP-50",     "title": "СП 50.13330.2012 Тепловая защита",              "dept": "ito",   "section": "05.4", "status": "active", "url": "https://docs.cntd.ru/document/1200095525"},
    {"id": "SP-131",    "title": "СП 131.13330.2020 Строительная климатология",   "dept": "ito",   "section": "05.4", "status": "active", "url": "https://docs.cntd.ru/document/565373906"},
    {"id": "SP-62",     "title": "СП 62.13330.2011 Газораспределительные сети",   "dept": "ito",   "section": "05.6", "status": "active", "url": "https://docs.cntd.ru/document/1200084035"},
    # ФЗ-69 — consultant.ru (CNTD сменил ID)
    {"id": "FZ-69",     "title": "ФЗ №69 О газоснабжении в РФ",                  "dept": "ito",   "section": "05.6", "status": "active", "url": "https://www.consultant.ru/document/cons_doc_LAW_22548/"},
    # 🔴 Пожарная безопасность и ГО ЧС (приоритет 1)
    {"id": "FZ-123",    "title": "ФЗ №123 Техрегламент о ПБ",                    "dept": "gochs", "section": "09",   "status": "active", "url": "https://docs.cntd.ru/document/902111644"},
    {"id": "FZ-116",    "title": "ФЗ №116 Промышленная безопасность ОПО",        "dept": "gochs", "section": "10",   "status": "active", "url": "https://docs.cntd.ru/document/9013762"},
    {"id": "SP-1-PB",   "title": "СП 1.13130.2020 Пути эвакуации",               "dept": "gochs", "section": "09",   "status": "active", "url": "https://docs.cntd.ru/document/565394611"},
    # URL исправлен (CNTD перенумеровал)
    {"id": "SP-2-PB",   "title": "СП 2.13130.2020 Огнестойкость",                "dept": "gochs", "section": "09",   "status": "active", "url": "https://docs.cntd.ru/document/565399384"},
    {"id": "SP-4-PB",   "title": "СП 4.13130.2013 Ограничение огня",             "dept": "gochs", "section": "09",   "status": "active", "url": "https://docs.cntd.ru/document/1200101593"},
    {"id": "SP-6-PB",   "title": "СП 6.13130.2021 Электросети и ПБ",             "dept": "gochs", "section": "09",   "status": "active", "url": "https://docs.cntd.ru/document/603668016"},
    {"id": "SP-8-PB",   "title": "СП 8.13130.2020 Водоснабжение и ПБ",          "dept": "gochs", "section": "09",   "status": "active", "url": "https://docs.cntd.ru/document/565387996"},
    {"id": "SP-10-PB",  "title": "СП 10.13130.2020 Внутр. пожарный водопровод",  "dept": "gochs", "section": "09",   "status": "active", "url": "https://docs.cntd.ru/document/565383715"},
    # 🟡 Инженерные изыскания (приоритет 2)
    {"id": "SP-11-105", "title": "СП 11-105-97 ИГИ",                              "dept": "iig",   "section": "2",    "status": "active", "url": "https://docs.cntd.ru/document/1200003424"},
    {"id": "SP-47",     "title": "СП 47.13330.2016 Инженерные изыскания",         "dept": "iig",   "section": "2",    "status": "active", "url": "https://docs.cntd.ru/document/456054197"},
    {"id": "SP-28",     "title": "СП 28.13330.2017 Защита от коррозии",           "dept": "iig",   "section": "2",    "status": "active", "url": "https://docs.cntd.ru/document/456069977"},
    {"id": "SP-14",     "title": "СП 14.13330.2018 Сейсмостойкое строительство",  "dept": "iig",   "section": "2",    "status": "active", "url": "https://docs.cntd.ru/document/554390337"},
    # 🟡 ООС / СЭС (приоритет 2)
    {"id": "FZ-7",      "title": "ФЗ №7 Об охране окружающей среды",              "dept": "oos",   "section": "08",   "status": "active", "url": "https://docs.cntd.ru/document/901808297"},
    {"id": "FZ-89",     "title": "ФЗ №89 Об отходах производства",               "dept": "oos",   "section": "08",   "status": "active", "url": "https://docs.cntd.ru/document/9037401"},
    {"id": "SANPIN-1",  "title": "СанПиН 1.2.3685-21",                            "dept": "oos",   "section": "08",   "status": "active", "url": "https://docs.cntd.ru/document/573650197"},
    # СанПиН-Z — consultant.ru (CNTD сменил ID)
    {"id": "SANPIN-Z",  "title": "СанПиН 2.2.1/2.1.1 Санзоны",                  "dept": "oos",   "section": "08",   "status": "active", "url": "https://www.consultant.ru/document/cons_doc_LAW_86796/"},
    # 🟢 ТИМ / BIM (приоритет 3) — Отдел информационных моделей
    {"id": "PP-331",    "title": "ПП РФ №331 ТИМ при строительстве",              "dept": "tim",   "section": "14",   "status": "active", "url": "https://docs.cntd.ru/document/350385892"},
    # ГОСТ Р ЕСИМ 10.00.00.00-2023 — опубликованный замен несуществующих 10.0.016 и 10.0.012
    {"id": "GOST-TIM",  "title": "ГОСТ Р 10.00.00.00-2023 ЕСИМ. Основные положения", "dept": "tim", "section": "14", "status": "active", "url": "https://docs.cntd.ru/document/1200196127"},
    # ПП РФ №614 — ТИМ при госзакупках (замена несуществующего ГОСТ-BIM-2)
    {"id": "GOST-BIM-2","title": "ПП РФ №614 ТИМ при государственных закупках",    "dept": "tim", "section": "14", "status": "active", "url": "https://www.consultant.ru/document/cons_doc_LAW_417553/"},
    {"id": "PP-1431",   "title": "ПП РФ №1431 Цифровые материалы изысканий",     "dept": "tim",   "section": "14",   "status": "active", "url": "https://docs.cntd.ru/document/350424044"},

    # 🟡 Нормоконтроль оформления ПД (источник: "Сценарии ИИ в экспертизе.docx")
    # ГОСТ Р 21.101-2020 прямо упомянут в DOCX-источнике проекта
    {"id": "GOST-SPDS", "title": "ГОСТ Р 21.101-2020 СПДС. Требования к оформлению ПД", "dept": "norm", "section": "all", "status": "active", "url": "https://www.consultant.ru/document/cons_doc_LAW_385208/"},
    {"id": "GOST-ESKD", "title": "ГОСТ 2.104-2006 ЕСКД. Основные надписи",             "dept": "norm", "section": "all", "status": "active", "url": "https://docs.cntd.ru/document/1200045443"},
    {"id": "PRIKAZ-783","title": "Приказ Минстроя №783/пр Именование файлов ПД",        "dept": "norm", "section": "all", "status": "active", "url": "https://www.consultant.ru/document/cons_doc_LAW_367291/"},
    {"id": "PRIKAZ-421","title": "Приказ Минстроя №421/пр XSD-схема ПЗ v01.05",         "dept": "norm", "section": "01",  "status": "active", "url": "https://www.consultant.ru/document/cons_doc_LAW_393555/"},

    # 🟡 Управление проверки сметной документации
    {"id": "MDS-81",    "title": "МДС 81-35.2004 Методика сметного нормирования",      "dept": "smeta", "section": "11", "status": "active", "url": "https://docs.cntd.ru/document/1200035051"},
    {"id": "PP-1315",   "title": "ПП РФ №1315 Государственные сметные нормативы",      "dept": "smeta", "section": "11", "status": "active", "url": "https://www.consultant.ru/document/cons_doc_LAW_393208/"},
    {"id": "PR-421-SM", "title": "Приказ Минстроя №421 ФСНБ-2022 Сметные нормативы",   "dept": "smeta", "section": "11", "status": "active", "url": "https://www.consultant.ru/document/cons_doc_LAW_398942/"},

    # 🟡 Управление комплексной экспертизы — Линейные объекты
    {"id": "SP-35",     "title": "СП 35.13330.2011 Мосты и трубы",                     "dept": "linear", "section": "06", "status": "active", "url": "https://docs.cntd.ru/document/1200084422"},
    {"id": "SP-36",     "title": "СП 36.13330.2012 Магистральные трубопроводы",        "dept": "linear", "section": "06", "status": "active", "url": "https://docs.cntd.ru/document/1200084073"},
    {"id": "SP-119",    "title": "СП 119.13330.2017 Дороги железные",                  "dept": "linear", "section": "06", "status": "active", "url": "https://docs.cntd.ru/document/456073040"},
    {"id": "SP-34",     "title": "СП 34.13330.2021 Автомобильные дороги",              "dept": "linear", "section": "06", "status": "active", "url": "https://docs.cntd.ru/document/1200095546"},
    {"id": "FZ-257",    "title": "ФЗ №257 Об автомобильных дорогах",                   "dept": "linear", "section": "06", "status": "active", "url": "https://docs.cntd.ru/document/902078688"},
]

PRIORITY_MAP = {
    "1":   {"kap", "ito", "gochs"},
    "2":   {"iig", "oos", "smeta", "norm"},
    "3":   {"tim", "linear"},
    "all": {"kap", "ito", "gochs", "iig", "oos", "tim", "smeta", "norm", "linear"},
}


async def main() -> None:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--dept",     default="all")
    parser.add_argument("--priority", choices=["1", "2", "3", "all"], default="1")
    args = parser.parse_args()

    allowed = PRIORITY_MAP[args.priority]
    if args.dept != "all":
        allowed = {args.dept}

    docs = [d for d in DOCUMENTS if d["dept"] in allowed]
    log.info(f"🚀 RAG Crawler | {len(docs)} документов | отделы: {allowed}")

    await RagCrawler().run(docs)


if __name__ == "__main__":
    asyncio.run(main())
