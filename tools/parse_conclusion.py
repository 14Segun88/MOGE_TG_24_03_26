#!/usr/bin/env python3
"""
parse_conclusion.py — Парсер PDF заключения эксперта Госэкспертизы.

Адаптирован под реальный формат PDF МособлГосЭкспертизы:
- Стр 1-3:  Мета-данные (номер, объект, шифр)
- Стр 4:    ТЭП-таблица (Площадь / Этажность / Объём)
- Стр 5-6:  ИРД (ТУ, ГПЗУ, договоры)
- Стр 7-14: Замечания в 2-3-колоночном формате (текст | раздел | норма)
- Стр 14:   Вывод по смете (Достоверно/Недостоверно)
- Стр 15:   Список экспертов (аттестаты)

Использование:
    python tools/parse_conclusion.py reference/<файл>.pdf
"""
import sys
import json
import re
import logging
from pathlib import Path

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


# ─────────────────────────────────────────────────────────────
#  Извлечение текста
# ─────────────────────────────────────────────────────────────
def extract_pages(pdf_path: Path) -> list[str]:
    try:
        import fitz
    except ImportError:
        log.error("PyMuPDF не установлен: pip install pymupdf")
        sys.exit(1)

    pages = []
    with fitz.open(str(pdf_path)) as doc:
        log.info(f"PDF: {len(doc)} страниц")
        for page in doc:
            # join убирает лишние пробелы, которые дают многоколоночные PDF
            text = page.get_text("text")
            pages.append(text)
    return pages


def clean(text: str) -> str:
    """Убирает лишние переносы и пробелы из 2-колоночного PDF."""
    # Убираем перенос слова посередине строки "строи-  тельства" → "строительства"
    text = re.sub(r'-\s+([а-яa-z])', r'\1', text, flags=re.I)
    # Склеиваем строки оборванного абзаца
    text = re.sub(r'(?<!\n)\n(?![\n\d•\-])', ' ', text)
    # Убираем множественные пробелы
    text = re.sub(r'  +', ' ', text)
    return text.strip()


# ─────────────────────────────────────────────────────────────
#  Парсинг мета-данных (стр 1-2)
# ─────────────────────────────────────────────────────────────
def parse_meta(pages: list[str]) -> dict:
    text = clean("\n".join(pages[:4]))
    meta = {}

    # Номер заключения: "50-1-2-3-005906-2026"
    m = re.search(r'(?:заключени[еяю]|номер)[^\d]*(\d{2}-\d-\d-\d-\d+-\d{4})', text, re.I)
    meta["expertise_number"] = m.group(1) if m else ""

    # Шифр: ищем паттерн "ГК.261-062" или "50-1-..."
    m = re.search(r'шифр[а-я:\s]+([А-ЯA-Z0-9ГК./_\-]{4,20})', text, re.I)
    meta["cipher"] = m.group(1).strip() if m else ""

    # Объект: строка после "Наименование" или "объект экспертизы"
    m = re.search(r'(?:наименование объекта|объект(?:а)? экспертизы)[:\s\n]+(.{15,200}?)(?:\n\n|\.|$)',
                  text, re.I | re.DOTALL)
    meta["object_name"] = m.group(1).strip().replace("\n", " ") if m else ""

    # Адрес
    m = re.search(r'(?:адрес|местоположение)[:\s]+(.{10,150}?)(?:\n|\.|,\s*\n)', text, re.I)
    meta["address"] = m.group(1).strip() if m else ""

    # Дата  dd.mm.yyyy
    m = re.search(r'(\d{2}\.\d{2}\.\d{4})', text)
    meta["date"] = m.group(1) if m else ""

    # Организация-заявитель
    m = re.search(r'(?:заявитель|застройщик)[:\s\n]+(.{5,120}?)(?:\n|$)', text, re.I)
    meta["applicant"] = m.group(1).strip() if m else ""

    return meta


# ─────────────────────────────────────────────────────────────
#  Парсинг ТЭП (стр 4)
# ─────────────────────────────────────────────────────────────
def parse_tep(pages: list[str]) -> dict:
    # Ищем страницу с "технико-экономическими показателями"
    tep_page = ""
    for pg in pages[:7]:
        if "технико-экономическ" in pg.lower():
            tep_page = clean(pg)
            break

    if not tep_page:
        tep_page = clean("\n".join(pages[:6]))

    tep = {"total_area": None, "floors": None, "underground_floors": None,
           "construction_volume": None, "plot_area": None,
           "footprint_area": None, "capacity": None, "energy_class": "", "raw": []}

    # Площадь объекта
    m = re.search(r'Площадь объекта\s+кв\.м\s+([\d,. ]+)', tep_page, re.I)
    if m:
        tep["total_area"] = m.group(1).strip().replace(",", ".").replace(" ", "")

    # Площадь участка
    m = re.search(r'Площадь участка\s+кв\.м\s+([\d,. ]+)', tep_page, re.I)
    if m:
        tep["plot_area"] = m.group(1).strip()

    # Площадь застройки
    m = re.search(r'Площадь застройки\s+кв\.м\s+([\d,. ]+)', tep_page, re.I)
    if m:
        tep["footprint_area"] = m.group(1).strip()

    # Строительный объём
    m = re.search(r'(?:Строительный объем|Объем)\s+куб\.м\s+([\d,. ]+)', tep_page, re.I)
    if m:
        tep["construction_volume"] = m.group(1).strip()

    # Этажность (берём первое число после "этаж")
    m = re.search(r'Этажность[^\d]+([\d]+)', tep_page, re.I)
    if m:
        tep["floors"] = int(m.group(1))

    # Подземных этажей
    m = re.search(r'подземных этажей\s+этаж\s+(\d+)', tep_page, re.I)
    if m:
        tep["underground_floors"] = int(m.group(1))

    # Вместимость / количество мест
    m = re.search(r'(?:Количество обучающихся|Вместимость|мест)\s+человек\s+([\d]+)', tep_page, re.I)
    if m:
        tep["capacity"] = int(m.group(1))

    # Класс энергоэффективности
    m = re.search(r'[Кк]ласс\s+энерго\w+\s+([A-GА-Е][+-]?)', tep_page)
    if m:
        tep["energy_class"] = m.group(1)

    # Сырые ТЭП-строки
    tep["raw"] = [ln.strip() for ln in tep_page.split("\n") if re.search(r'кв\.м|куб\.м|этаж|человек', ln)]

    return tep


# ─────────────────────────────────────────────────────────────
#  Парсинг замечаний (стр 7-14)
# ─────────────────────────────────────────────────────────────
def parse_remarks(pages: list[str]) -> tuple[list, list]:
    """
    Замечания идут в формате 2-3 колоночной таблицы:
    [Текст замечания] [Раздел] [Пункт нормы]

    Стратегия: берём стр 7-14, ищем нумерованные пункты.
    """
    remark_pages = pages[6:14]   # стр 7-14 (0-based: 6-13)
    full_text = "\n".join(remark_pages)
    clean_text = clean(full_text)

    critical = []
    warnings_list = []

    # Паттерн замечания: цифра. Текст... (Раздел...) (Постановление...|СП...|ГОСТ...)
    # В реальном документе замечания нумерованы "1.\n текст"
    blocks = re.split(r'\n(?=\d{1,2}\.\s+[А-ЯЁ])', clean_text)

    for block in blocks:
        block = block.strip()
        if len(block) < 30:
            continue
        # Убираем номер
        text = re.sub(r'^\d{1,2}\.\s+', '', block)
        # Убираем часть с "Раздел N. Название"
        text = re.sub(r'\s*Раздел\s+\d+\..{0,100}', '', text)
        # Убираем ссылку на норму в конце (Постановление Правительства ...)
        norm_m = re.search(r'(?:Постановление|п\.\s*\d|пп\.\s*\d|приказ|СП\s+\d)', text)
        if norm_m:
            remark_text = text[:norm_m.start()].strip()
            norm_ref = text[norm_m.start():].strip()[:200]
        else:
            remark_text = text[:300].strip()
            norm_ref = ""

        if len(remark_text) > 20:
            entry = {"text": remark_text[:300], "norm": norm_ref}
            # Если содержит "недостоверн", "не представлен", "ошибк" → критическое
            if re.search(r'недостоверн|не представлен|ошибк|нарушен|отсутств', remark_text, re.I):
                critical.append(entry)
            else:
                warnings_list.append(entry)

    log.info(f"Замечаний: {len(critical)} критических, {len(warnings_list)} предупреждений")
    return critical, warnings_list


# ─────────────────────────────────────────────────────────────
#  Парсинг нормативных ссылок
# ─────────────────────────────────────────────────────────────
def parse_norm_refs(pages: list[str]) -> list:
    text = "\n".join(pages[6:15])
    pattern = re.compile(
        r'(?:'
        r'(?:СП|СНиП|ГОСТ\s*Р?)\s*[\d.]+[\w\d.]*(?:[-–]\d+)?(?:\s*\d{4})?(?:\.\d+)?'
        r'|'
        r'(?:ПП\s*РФ|Постановление\s+Правительства(?:\s+РФ)?)\s+(?:от\s+)?(?:[\d.]+|[«"]\d+[»"])\s*(?:г\.)?'
        r'(?:\s+[№N°]\s*[\d\-]+)?'
        r'|'
        r'(?:ФЗ|Федеральный закон)\s+(?:от\s+)?[\d.]+\s*[№N°]\s*[\d\-]+-ФЗ'
        r')',
        re.I
    )
    refs = []
    seen = set()
    for m in pattern.finditer(text):
        ref = re.sub(r'\s+', ' ', m.group(0)).strip()
        if ref not in seen and len(ref) > 3:
            seen.add(ref)
            refs.append(ref)
    return sorted(refs)[:60]


# ─────────────────────────────────────────────────────────────
#  Парсинг вердикта
# ─────────────────────────────────────────────────────────────
def parse_verdict(pages: list[str]) -> dict:
    # Вердикт обычно на стр 11-14
    verdict_pages = "\n".join(pages[9:15])
    clean_v = clean(verdict_pages)

    verdict = {"overall": "НЕИЗВЕСТНО", "smeta": "", "sections": []}

    if re.search(r'соответствует\s+требовани', clean_v, re.I):
        verdict["overall"] = "СООТВЕТСТВУЕТ"
    if re.search(r'не\s+соответствует|несоответствует', clean_v, re.I):
        verdict["overall"] = "НЕ СООТВЕТСТВУЕТ"

    # Смета отдельно
    if re.search(r'сметная стоимость определена достоверно', clean_v, re.I):
        verdict["smeta"] = "ДОСТОВЕРНО"
    elif re.search(r'сметная стоимость определена недостоверно', clean_v, re.I):
        verdict["smeta"] = "НЕДОСТОВЕРНО"

    # Разделы с вердиктами
    for m in re.finditer(
        r'((?:Раздел|Подраздел)\s+[\d.]+\.?\s+[\w\s]{5,60}?)\s*[–—-]\s*(соответствует|не соответствует)',
        clean_v, re.I
    ):
        verdict["sections"].append({
            "section": m.group(1).strip(),
            "verdict": m.group(2).strip().upper()
        })

    return verdict


# ─────────────────────────────────────────────────────────────
#  Главная функция
# ─────────────────────────────────────────────────────────────
def parse_conclusion(pdf_path: Path, output_dir: Path) -> dict:
    from datetime import datetime

    pages = extract_pages(pdf_path)
    raw_text = "\n\n".join(pages)

    result = {
        "meta": {
            "source_file": pdf_path.name,
            "parsed_at": datetime.now().isoformat(),
            **parse_meta(pages)
        },
        "verdict":          parse_verdict(pages),
        "tep":              parse_tep(pages),
        "critical_remarks": [],
        "warnings":         [],
        "norm_refs":        parse_norm_refs(pages),
        "raw_text":         raw_text,
    }

    critical, warnings = parse_remarks(pages)
    result["critical_remarks"] = [
        {"text": r["text"], "norm": r.get("norm", "")} if isinstance(r, dict) else {"text": r, "norm": ""}
        for r in critical
    ]
    result["warnings"] = [
        {"text": r["text"], "norm": r.get("norm", "")} if isinstance(r, dict) else {"text": r, "norm": ""}
        for r in warnings
    ]

    # — JSON —
    json_path = output_dir / "expert_conclusion.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    log.info(f"✅ JSON: {json_path}")

    # — TXT (читаемый) —
    txt_path = output_dir / "expert_conclusion.txt"
    with open(txt_path, "w", encoding="utf-8") as f:
        m = result["meta"]
        t = result["tep"]
        v = result["verdict"]
        f.write("=== ЗАКЛЮЧЕНИЕ ЭКСПЕРТА ===\n")
        f.write(f"Файл:       {m['source_file']}\n")
        f.write(f"Номер:      {m.get('expertise_number','')}\n")
        f.write(f"Объект:     {m.get('object_name','')[:100]}\n")
        f.write(f"Адрес:      {m.get('address','')[:100]}\n")
        f.write(f"Дата:       {m.get('date','')}\n")
        f.write(f"Заявитель:  {m.get('applicant','')[:100]}\n")
        f.write(f"\n=== ВЕРДИКТ: {v['overall']} (смета: {v['smeta']}) ===\n")
        for sec in v.get("sections", []):
            f.write(f"  {'✅' if 'СООТВ' in sec['verdict'] else '❌'} {sec['section']}: {sec['verdict']}\n")
        f.write(f"\n=== ТЭП ===\n")
        f.write(f"  Площадь объекта:  {t.get('total_area')} кв.м\n")
        f.write(f"  Площадь участка:  {t.get('plot_area')} кв.м\n")
        f.write(f"  Площадь застройки:{t.get('footprint_area')} кв.м\n")
        f.write(f"  Строит. объём:    {t.get('construction_volume')} куб.м\n")
        f.write(f"  Этажность:        {t.get('floors')}\n")
        f.write(f"  Подземных этажей: {t.get('underground_floors')}\n")
        f.write(f"  Вместимость:      {t.get('capacity')} чел.\n")
        f.write(f"  Класс энергоэфф.: {t.get('energy_class')}\n")
        f.write(f"\n=== КРИТИЧЕСКИЕ ЗАМЕЧАНИЯ ({len(result['critical_remarks'])}) ===\n")
        for i, r in enumerate(result["critical_remarks"], 1):
            txt = r["text"] if isinstance(r, dict) else r
            norm = r.get("norm", "") if isinstance(r, dict) else ""
            f.write(f"  {i}. {txt}\n")
            if norm:
                f.write(f"     → Норма: {norm[:120]}\n")
        f.write(f"\n=== ПРЕДУПРЕЖДЕНИЯ ({len(result['warnings'])}) ===\n")
        for i, r in enumerate(result["warnings"][:15], 1):
            txt = r["text"] if isinstance(r, dict) else r
            f.write(f"  {i}. {txt}\n")
        f.write(f"\n=== НОРМАТИВНЫЕ ССЫЛКИ ({len(result['norm_refs'])}) ===\n")
        for ref in result["norm_refs"][:30]:
            f.write(f"  • {ref}\n")
    log.info(f"✅ TXT: {txt_path}")

    return result


# ─────────────────────────────────────────────────────────────
#  CLI
# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if len(sys.argv) >= 2:
        pdf_path = Path(sys.argv[1])
    else:
        ref_dir = Path(__file__).parent.parent / "reference"
        pdfs = [f for f in ref_dir.glob("*.pdf") if "Zone" not in f.name]
        if not pdfs:
            print("Использование: python tools/parse_conclusion.py reference/<файл>.pdf")
            sys.exit(1)
        pdf_path = pdfs[0]
        log.info(f"Автоопределён PDF: {pdf_path.name}")

    if not pdf_path.exists():
        print(f"❌ Файл не найден: {pdf_path}")
        sys.exit(1)

    result = parse_conclusion(pdf_path, pdf_path.parent)

    print(f"\n{'='*55}")
    print(f"ВЕРДИКТ:            {result['verdict']['overall']}")
    print(f"СМЕТА:              {result['verdict']['smeta']}")
    print(f"Объект:             {result['meta'].get('object_name','')[:60]}")
    print(f"ТЭП площадь:        {result['tep'].get('total_area')} кв.м")
    print(f"ТЭП этажность:      {result['tep'].get('floors')}")
    print(f"Критич. замечаний:  {len(result['critical_remarks'])}")
    print(f"Предупреждений:     {len(result['warnings'])}")
    print(f"Нормат. ссылок:     {len(result['norm_refs'])}")
    print(f"{'='*55}")
    print(f"reference/expert_conclusion.txt  ← читаемый отчёт")
    print(f"reference/expert_conclusion.json ← данные для сравнения")
