import asyncio
import json
import logging
import os
import re
import random
import time
from pathlib import Path
from typing import Optional
from tqdm import tqdm
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright, BrowserContext, Page

# ─────────────────────────────────────────────
#  Настройки
# ─────────────────────────────────────────────
RAW_DIR         = Path("/mnt/d/rag_data/raw")
META_DIR        = Path("/mnt/d/rag_data/meta")
MANIFEST_PATH   = Path("/mnt/d/rag_data/manifest.json")

CONCURRENCY     = 1      # Для Playwright лучше 1-2, чтобы не забанили
DELAY_BETWEEN   = 12.0   # Увеличиваем задержку для обхода антифрод-систем
REQUEST_TIMEOUT = 120000 # 120 сек для больших документов
BROWSER_HEADLESS = True  # True для WSL

# API для векторизации (опционально, если нужно сразу считать токены)
LM_STUDIO_URL   = os.getenv("LM_STUDIO_URL", "http://172.31.128.1:1234/v1")
LM_STUDIO_API_KEY = os.getenv("LM_STUDIO_API_KEY", "sk-lm-V6B8mgjk:7DFHluGBuv2U6bmhuoZ5")

RAW_DIR.mkdir(parents=True, exist_ok=True)
META_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
log = logging.getLogger("RAG_Crawler")

# ─────────────────────────────────────────────
#  Логика выгрузки
# ─────────────────────────────────────────────

class RagCrawler:
    def __init__(self):
        self.user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"

    async def fetch_full_content(self, context: BrowserContext, url: str, doc_id: str, timeout: int = 180000) -> Optional[str]:
        """
        Загружает страницу через Playwright, прокручивает до САМОГО конца
        и возвращает полный HTML.
        """
        page = await context.new_page()
        try:
            # 1. Загрузка
            log.info(f"🌐 Загрузка ({doc_id}): {url}")
            await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
            await asyncio.sleep(8) # Даем JS прогрузиться
            
            # 2. Проверка заголовка (анти-SudRF/Junk)
            title = await page.title()
            
            if "docs.cntd.ru" in url:
                bad_keywords = ["определение", "решение", "алфавитный указатель", "судебная коллегия", "суд ", "суда ", "судебный", "судебно", "документ не найден", "заказать демонстрацию"]
                try: page_text = await page.inner_text("body")
                except: page_text = ""
                
                if any(k in title.lower() for k in bad_keywords) or any(k in page_text[:2000].lower() for k in bad_keywords):
                    log.error(f"❌ Пропуск ({doc_id}): Мусорная страница или Paywall — {title}")
                    return None

            if "consultant.ru" in url:
                # На консультанте часто выскакивает окно с просьбой подождать или капча
                await asyncio.sleep(5)
                # Проверка на 'document-page'
                if not await page.query_selector(".document-page"):
                    log.warning(f"⚠ Consultant.ru: Контент не найден. Возможно, требуется ручное подтверждение.")

            # 3. Агрессивное развертывание (JS-инъекция)
            log.info(f"🔘 Раскрытие всех секций ({doc_id})...")
            
            # Нативный клик по общим селекторам
            selectors = [
                ".document-show-full-text", ".show-all", ".expand-all", ".btn-show-full",
                ".js-show-full-text", ".document-page__fullized-link", "#js-expand-all"
            ]
            for sel in selectors:
                try: 
                    if await page.query_selector(sel):
                        await page.click(sel, timeout=3000)
                        log.info(f"✅ Клик по селектору: {sel}")
                except: pass

            # Глубокое раскрытие всех скрытых блоков через JS
            await page.evaluate("""
                async () => {
                    // Разворачиваем все <details>
                    document.querySelectorAll('details').forEach(d => d.open = true);
                    // Прожимаем все кнопки 'Развернуть'
                    const buttons = Array.from(document.querySelectorAll('button, a'));
                    buttons.forEach(b => {
                        const txt = b.innerText.toLowerCase();
                        if (txt.includes('развернуть') || txt.includes('показать полностью') || txt.includes('открыть полностью')) {
                           try { b.click(); } catch(e) {}
                        }
                    });
                }
            """)
            await asyncio.sleep(3)

            # 4. Скроллинг до УПОРА (scrollHeight)
            log.info(f"📜 Динамический скроллинг до конца страницы ({doc_id})...")
            
            last_height = await page.evaluate("document.body.scrollHeight")
            scroll_attempts = 0
            max_scrolls = 150 # Защита от бесконечных страниц
            
            while scroll_attempts < max_scrolls:
                # Скроллим вниз
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await asyncio.sleep(2.0) # Ждем подгрузки
                
                # Дополнительно прожимаем PageDown (триггерит lazy load в некоторых JS-фреймворках)
                for _ in range(5):
                    await page.keyboard.press("PageDown")
                    await asyncio.sleep(0.1)
                
                new_height = await page.evaluate("document.body.scrollHeight")
                if new_height == last_height:
                    # Если высота не изменилась, пробуем еще один раз (бывают задержки сети)
                    await asyncio.sleep(3.0)
                    new_height = await page.evaluate("document.body.scrollHeight")
                    if new_height == last_height:
                        break
                
                last_height = new_height
                scroll_attempts += 1
                if scroll_attempts % 10 == 0:
                    log.info(f"   ...пройдено {scroll_attempts} итераций скролла (высота: {new_height})")

            # Финальный клик на кнопках, если они вылезли в конце
            await page.evaluate("() => { document.querySelectorAll('.document-show-full-text').forEach(el => el.click()); }")
            await asyncio.sleep(2)

            return await page.content()

        except Exception as e:
            log.error(f"❌ Ошибка Playwright на {doc_id} ({url}): {e}")
            return None
        finally:
            await page.close()

    def html_to_markdown(self, html: str, doc_id: str) -> dict:
        """Парсит HTML в чистый Markdown."""
        soup = BeautifulSoup(html, "html.parser")
        
        # Удаляем интерактивный мусор
        for el in soup(["script", "style", "header", "footer", "nav"]):
            el.decompose()

        # Берем все тело документа для гарантии полноты
        content_el = soup.find("body")

        if not content_el:
            return {"markdown": "", "chars": 0}

        # Базовая конвертация заголовков и параграфов
        for h in content_el.find_all(["h1", "h2", "h3", "h4", "h5"]):
            h.replace_with(f"\n\n{'#' * int(h.name[1])} {h.get_text().strip()}\n")
            
        # Для Консультанта: обрабатываем блоки с id
        for div in content_el.find_all("div", id=True):
             # Помечаем пункты для RAG
             if re.match(r"^p\d+$", div.get("id", "")):
                 div.insert_before("\n\n")

        for p in content_el.find_all("p"):
            p.replace_with(f"\n{p.get_text().strip()}\n")
        for li in content_el.find_all("li"):
            li.replace_with(f"\n- {li.get_text().strip()}")
        
        text = content_el.get_text(separator="\n")
        # Очистка пустых строк
        text = re.sub(r'\n{3,}', '\n\n', text).strip()
        
        return {
            "markdown": text,
            "chars": len(text)
        }

    async def process(self, doc: dict, context: BrowserContext) -> Optional[dict]:
        doc_id = doc["id"]
        url = doc["url"]
        
        html = await self.fetch_full_content(context, url, doc_id)
        if not html:
            return None

        parsed = self.html_to_markdown(html, doc_id)
        if not parsed["markdown"] or parsed["chars"] < 500:
            log.warning(f"⚠ Контент для {doc_id} пуст или слишком мал ({parsed['chars']} симв.)")
            return None

        # Сохраняем
        md_file = RAW_DIR / f"{doc_id}.md"
        md_file.write_text(parsed["markdown"], "utf-8")
        
        meta = {**doc, "chars": parsed["chars"], "timestamp": time.time()}
        (META_DIR / f"{doc_id}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), "utf-8")
        
        log.info(f"✅ {doc_id} ОБНОВЛЕН: {parsed['chars']} симв.")
        
        # Рандомная пауза для имитации человека
        await asyncio.sleep(DELAY_BETWEEN + random.uniform(0, 5))
        return meta

    async def run(self, docs: list[dict]):
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=BROWSER_HEADLESS)
            context = await browser.new_context(user_agent=self.user_agent)

            results: list[dict] = []
            for doc in tqdm(docs, desc="RAG Quality Crawl"):
                res = await self.process(doc, context)
                if res:
                    results.append(res)

            await browser.close()

        # Обновляем манифест
        existing = {}
        if MANIFEST_PATH.exists():
            try:
                existing = {m["id"]: m for m in json.loads(MANIFEST_PATH.read_bytes())}
            except: pass
        existing.update({m["id"]: m for m in results})
        MANIFEST_PATH.write_text(json.dumps(list(existing.values()), ensure_ascii=False, indent=2), "utf-8")

        log.info("\n" + "═" * 50)
        log.info(f"📊 Загружено:    {len(results)}/{len(docs)}")
        log.info(f"📁 Файлы в {RAW_DIR}")
        log.info("═" * 50)


# ─────────────────────────────────────────────
#  Каталог документов
# ─────────────────────────────────────────────
DOCUMENTS: list[dict] = [
    # 🔴 Объекты кап. строительства (приоритет 1) - Reverted to CNTD due to Consultant.ru Paywall/Lock
    {"id": "FZ-384",    "title": "ФЗ №384 Техрегламент о безопасности зданий",     "dept": "kap",   "section": "02",   "status": "active", "url": "https://docs.cntd.ru/document/902192610"},
    {"id": "FZ-181",    "title": "ФЗ №181 О социальной защите инвалидов",          "dept": "kap",   "section": "11",   "status": "active", "url": "https://docs.cntd.ru/document/9014511"},
    {"id": "PP-87",     "title": "ПП РФ №87 Состав проектной документации",        "dept": "kap",   "section": "all",  "status": "active", "url": "https://docs.cntd.ru/document/902087949"},
    {"id": "PP-145",    "title": "ПП РФ №145 Порядок госэкспертизы",           "dept": "kap", "section": "all", "status": "active", "url": "https://docs.cntd.ru/document/902031383"},
    {"id": "SP-54",     "title": "СП 54.13330.2022 Жилые здания",              "dept": "kap", "section": "03",  "status": "active", "url": "https://docs.cntd.ru/document/565360986"},
    {"id": "SP-59",     "title": "СП 59.13330.2020 Доступность для ОВЗ",           "dept": "kap",   "section": "11",   "status": "active", "url": "https://docs.cntd.ru/document/565372705"},
    {"id": "SP-118",    "title": "СП 118.13330.2022 Общественные здания",          "dept": "kap",   "section": "03",   "status": "active", "url": "https://docs.cntd.ru/document/351102147"},
    {"id": "SP-42",     "title": "СП 42.13330.2016 Градостроительство",            "dept": "kap",   "section": "02",   "status": "active", "url": "https://docs.cntd.ru/document/456054209"},
    {"id": "SP-20",     "title": "СП 20.13330.2017 Нагрузки и воздействия",       "dept": "kap",   "section": "04",   "status": "active", "url": "https://docs.cntd.ru/document/456069843"},
    {"id": "SP-22",     "title": "СП 22.13330.2016 Основания зданий",              "dept": "kap",   "section": "04",   "status": "active", "url": "https://docs.cntd.ru/document/456069011"},
    {"id": "SP-63",     "title": "СП 63.13330.2018 ЖБ конструкции",            "dept": "kap", "section": "04", "status": "active", "url": "https://docs.cntd.ru/document/564376473"},
    {"id": "SP-48",     "title": "СП 48.13330.2019 Организация строительства", "dept": "kap", "section": "07", "status": "active", "url": "https://docs.cntd.ru/document/564931958"},
    {"id": "GRK-RF",    "title": "Градостроительный кодекс РФ",                   "dept": "kap",   "section": "all",  "status": "active", "url": "https://docs.cntd.ru/document/901919338"},
    
    # ИТО
    {"id": "SP-256",    "title": "СП 256 Электроустановки жилых зданий",           "dept": "ito",   "section": "05.1", "status": "active", "url": "https://docs.cntd.ru/document/1200162802"},
    {"id": "PUE-7",     "title": "ПУЭ 7-е издание",                               "dept": "ito",   "section": "05.1", "status": "active", "url": "https://docs.cntd.ru/document/1200003114"},
    {"id": "SP-31",     "title": "СП 31.13330.2021 Водоснабжение",             "dept": "ito", "section": "05.2", "status": "active", "url": "https://docs.cntd.ru/document/603204781"},
    {"id": "SP-30",     "title": "СП 30.13330.2020 Внутренний водопровод",        "dept": "ito",   "section": "05.2", "status": "active", "url": "https://docs.cntd.ru/document/573136217"},
    {"id": "SP-32",     "title": "СП 32.13330.2018 Канализация",                  "dept": "ito",   "section": "05.3", "status": "active", "url": "https://docs.cntd.ru/document/554395727"},
    {"id": "FZ-416",    "title": "ФЗ №416 О водоснабжении и водоотведении",    "dept": "ito", "section": "05.3", "status": "active", "url": "https://docs.cntd.ru/document/902315354"},
    {"id": "SP-60",     "title": "СП 60.13330.2020 Отопление, вентиляция",        "dept": "ito",   "section": "05.4", "status": "active", "url": "https://docs.cntd.ru/document/573136215"},
    {"id": "SP-50",     "title": "СП 50.13330.2012 Тепловая защита",              "dept": "ito",   "section": "05.4", "status": "active", "url": "https://docs.cntd.ru/document/1200095525"},
    {"id": "SP-62",     "title": "СП 62.13330.2011 Газораспределительные сети",   "dept": "ito",   "section": "05.6", "status": "active", "url": "https://docs.cntd.ru/document/1200084035"},
    {"id": "FZ-69",     "title": "ФЗ №69 О газоснабжении в РФ",                  "dept": "ito",   "section": "05.6", "status": "active", "url": "https://docs.cntd.ru/document/901731661"},
    
    # 🔴 Пожарная безопасность и ГО ЧС (приоритет 1)
    {"id": "FZ-123",    "title": "ФЗ №123 Техрегламент о ПБ",                    "dept": "gochs", "section": "09",   "status": "active", "url": "https://docs.cntd.ru/document/902111644"},
    {"id": "FZ-116",    "title": "ФЗ №116 Промышленная безопасность ОПО",        "dept": "gochs", "section": "10",   "status": "active", "url": "https://docs.cntd.ru/document/9010833"},
    {"id": "SP-1-PB",   "title": "СП 1.13130.2020 Пути эвакуации",               "dept": "gochs", "section": "09",   "status": "active", "url": "https://docs.cntd.ru/document/565372703"},
    {"id": "SP-2-PB",   "title": "СП 2.13130.2020 Огнестойкость",                "dept": "gochs", "section": "09",   "status": "active", "url": "https://docs.cntd.ru/document/1200071152"},
    {"id": "SP-4-PB",   "title": "СП 4.13130.2013 Ограничение огня",             "dept": "gochs", "section": "09",   "status": "active", "url": "https://docs.cntd.ru/document/1200101593"},
    {"id": "SP-6-PB",   "title": "СП 6.13130.2021 Электросети и ПБ",             "dept": "gochs", "section": "09",   "status": "active", "url": "https://docs.cntd.ru/document/603668016"},
    {"id": "SP-8-PB",   "title": "СП 8.13130.2020 Водоснабжение и ПБ",          "dept": "gochs", "section": "09",   "status": "active", "url": "https://docs.cntd.ru/document/565387996"},
    {"id": "SP-10-PB",  "title": "СП 10.13130.2020 Внутр. пожарный водопровод",  "dept": "gochs", "section": "09",   "status": "active", "url": "https://docs.cntd.ru/document/565383715"},
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
    parser.add_argument("--dept", type=str, help="Отфильтровать по отделу (ito, gochs, kap)", default="all")
    parser.add_argument("--priority", type=str, choices=["1", "2", "3", "all"], default="1")
    parser.add_argument("--id", type=str, help="Выгрузить только один документ по его ID")
    args = parser.parse_args()
    
    selected_docs = []
    if args.id:
        selected_docs = [d for d in DOCUMENTS if d["id"] == args.id]
        if not selected_docs:
            print(f"❌ Документ с ID {args.id} не найден в манифесте.")
            return
        log.info(f"🚀 RAG Crawler | 1 документ по ID: {args.id}")
    else:
        allowed_depts = PRIORITY_MAP[args.priority]
        if args.dept != "all":
            # If a specific department is requested, filter further
            allowed_depts = allowed_depts.intersection({args.dept})

        selected_docs = [d for d in DOCUMENTS if d["dept"] in allowed_depts]
        log.info(f"🚀 RAG Crawler | {len(selected_docs)} документов | приоритет: {args.priority}, отделы: {allowed_depts}")

    await RagCrawler().run(selected_docs)


if __name__ == "__main__":
    asyncio.run(main())
