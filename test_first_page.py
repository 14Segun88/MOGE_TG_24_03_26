#!/usr/bin/env python3
"""
test_first_page.py — Отладочный скрипт для проверки соответствия
имени PDF-файла тексту его первого (титульного) листа.

Сравнение ДЕТЕРМИНИРОВАННОЕ (без LLM):
  1. Токенизация имени файла и текста страницы
  2. Нормализация (нижний регистр, убираем спецсимволы)
  3. Подсчёт % совпадения токенов (взвешенный: длинные важнее)
  4. Вердикт: ✅ / ⚠️ / ❌

Использование:
  python test_first_page.py <путь_к_папке_или_zip>
"""
import sys
import os
import re
import zipfile
import tempfile
import shutil
import datetime
import json
import argparse
from pathlib import Path
import fitz  # PyMuPDF


# ─── СТОП-СЛОВА (не несут смыслового содержания в имени файла) ───────────
# НЕ включаем: раздел, пд, подраздел, часть, том — они обрабатываются
# отдельно через extract_structural_tokens()
STOPWORDS = {
    "pdf", "sig", "xml", "ifc", "v", "v1", "v2", "v3", "v4", "v5",
    "nr", "no", "на", "для", "от",
}

# Сокращения-синонимы: токен из файла → возможные вариации в тексте
# Исправлено по ПП-87: ИОС1=электро, ИОС2=водоснабж, ИОС3=водоотв и т.д.
SYNONYMS: dict[str, list[str]] = {
    # Раздел 01 — Пояснительная записка
    "пз": ["пояснительная", "записка"],
    # Раздел 02 — Схема планировочной организации земельного участка
    "пзу": ["планировочной", "организации", "земельного", "схема"],
    "спозу": ["планировочной", "организации", "земельного", "участка"],
    # Раздел 03 — Архитектурные решения
    "ар": ["архитектурные", "решения", "архитектурных"],
    # Раздел 04 — Конструктивные решения
    "кр": ["конструктивные", "решения", "конструктивных"],
    # Раздел 05 — ИОС (по ПП-87)
    "иос": ["инженерного", "оборудования", "инженерных", "инженерное", "инженерно"],
    "иос1": ["электроснабжение", "электроснабжения", "электрического", "электрооборудование"],
    "эс":   ["электроснабжение", "электроснабжения", "электрического"],
    "иос2": ["водоснабжение", "водоснабжения", "водоотведение", "водоотведения"],
    "вс":   ["водоснабжение", "водоснабжения", "внутреннего"],
    "нвс":  ["водоснабжение", "водоснабжения", "внутриплощадочные", "наружные"],
    "иос3": ["водоотведение", "водоотведения", "канализация", "канализации"],
    "во":   ["водоотведение", "водоотведения", "внутреннего"],
    "нво":  ["водоотведение", "водоотведения", "наружные", "канализация"],
    "иос4": ["отопление", "вентиляция", "кондиционирование", "тепловые"],
    "иос5": ["газоснабжение", "газоснабжения", "газораспределение"],
    "иос6": ["связи", "сигнализации", "автоматизации", "телекоммуникации"],
    "иос7": ["технологические", "решения"],
    # Раздел 06 — Проект организации строительства
    "пос": ["организации", "строительства"],
    # Раздел 08 — Охрана окружающей среды
    "ос": ["охрана", "среды", "окружающей"],
    "оос": ["охрана", "окружающей", "среды"],
    # Раздел 09 — ПБ
    "ппб": ["пожарной", "безопасности"],
    "пб": ["пожарной", "безопасности"],
    # Раздел 10 — ОДИ
    "оди": ["доступности", "инвалидов", "маломобильных"],
    # Раздел 11 — Энергоэффективность
    "эп": ["энергетической", "эффективности", "энергоэффективности"],
    "ээ": ["энергетической", "эффективности"],
    # Раздел 12 — Мероприятия ГО и ЧС
    "тбэ": ["безопасности", "эксплуатации"],
    "обин": ["инженерной", "защиты"],
    # Общие аббревиатуры
    "ул": ["удостоверяющий", "лист", "информационно"],
    "иул": ["информационно", "удостоверяющий", "лист"],
    "сп": ["состав", "проекта"],
    "ссп": ["сетей", "связи"],
    "бим": ["информационной", "модели"],
    "см": ["сметной", "документации", "сметы"],
    "тх": ["технологические", "характеристики"],
    # ── Сопроводительные документы (не ПД) ────────────────────
    # ГПЗУ — Градостроительный план земельного участка
    "гпзу": ["градостроительный", "план", "земельного", "участка"],
    # ЕГРН — Единый государственный реестр недвижимости
    "егрн": ["реестра", "недвижимости", "кадастровая", "кадастровый"],
    # ТЗ — Техническое задание
    "тз": ["техническое", "задание"],
    # ТУ — Технические условия
    "ту": ["технические", "условия"],
    # ИГДИ — Инженерно-геодезические изыскания
    "игди": ["инженерно", "геодезических", "геодезические", "геодезической"],
    # ИГИ — Инженерно-геологические изыскания
    "иги": ["инженерно", "геологических", "геологические"],
    # ИЭИ — Инженерно-экологические изыскания
    "иэи": ["инженерно", "экологических", "экологические"],
    # ДЗКС — Дирекция заказчика капитального строительства
    "дзкс": ["дирекция", "заказчика", "капитального"],
    # МРП / Мосрегионпроект
    "мрп": ["мосрегионпроект", "региональный"],
    # УУТЭ — Узел учёта тепловой энергии
    "уутэ": ["учёта", "учета", "тепловой", "энергии", "теплоносителя"],
    # ОСО — Объектовая система оповещения
    "осо": ["объектовой", "системы", "оповещения"],
    # РСОН — уже совпадает через текст
    "рсон": ["региональной", "оповещения", "населения"],
    # Накладная / Акт приема-передачи
    "накладная": ["приема", "передачи", "приёма"],
    # Доверенность / МЧД (машиночитаемая доверенность)
    "доверенность": ["представлять", "интересы", "поручения", "уполномочивает"],
    # Программа (для случаев когда только шифр на стр.1)
    "программа": ["программа", "изысканий"],
    # Диспетчеризация / диспетчерская
    "диспетчеризацию": ["диспетчерской", "диспетчерская", "диспетчеризации"],
    "диспетчеризации": ["диспетчерской", "диспетчерская", "диспетчеризацию"],
    # Обратные синонимы (Полное имя -> Аббревиатура)
    "технические": ["ту", "соглашение", "договор", "контракт"],
    "условия": ["ту", "соглашение", "договор", "контракт"],
    "задание": ["тз", "заказу", "договор"],
    "техническое": ["тз", "заказу", "договор"],
    "инженерно": ["игди", "иги", "иэи"],
    "геодезические": ["игди"],
    "геологические": ["иги"],
    "экологические": ["иэи"],
    # Теплоснабжение
    "теплоснабжения": ["теплоснабжении", "теплоснабжение", "тепловых", "сетей", "теплосети", "теплоснабжающей", "тс"],
    "теплоснабжение": ["теплоснабжении", "теплоснабжения", "тепловых", "сетей", "теплосети", "тс"],
}


def normalize(text: str) -> str:
    """Нижний регистр + оставляем только буквы, цифры и пробелы.
    Также сжимаем разрядку: 'П Р О Г Р А М М А' → 'ПРОГРАММА'."""
    t = text.lower()
    # Сжимаем разрядку: одиночные буквы через пробел (≥3 подряд)
    t = re.sub(r'(?<![а-яёa-z])([а-яёa-z](?:\s[а-яёa-z]){2,})(?![а-яёa-z])',
               lambda m: m.group(0).replace(" ", ""), t)
    return re.sub(r"[^а-яёa-z0-9 ]", " ", t)


def tokenize(text: str) -> list[str]:
    """Разбиваем нормализованный текст на токены длиной >= 2."""
    return [t for t in normalize(text).split() if len(t) >= 2]


def extract_structural_tokens(filename: str) -> list[dict]:
    """
    Извлекает структурные токены из имени файла:
      пд2 / пд №2 / пд 2 → {"label": "раздел 2", "search": ["раздел 2"]}
      подраздел1 / подраздел 1 → ...
      часть1 / часть 1 → ...
      том 5.1 → ...
    Возвращает список словарей с полями label, search_patterns.
    """
    text = normalize(filename)
    results = []

    # Паттерн «пд2», «пд 2», «пд№2», «пд №2»
    for m in re.finditer(r'пд\s*№?\s*(\d+)', text):
        n = m.group(1)
        results.append({
            "label": f"раздел {n}",
            "patterns": [f"раздел {n}", f"раздел пд {n}", f"раздел пд{n}"]
        })

    # Паттерн «подраздел1», «подраздел 1»
    for m in re.finditer(r'подраздел\s*(\d+)', text):
        n = m.group(1)
        results.append({
            "label": f"подраздел {n}",
            "patterns": [f"подраздел {n}"]
        })

    # Паттерн «часть1», «часть 1»
    for m in re.finditer(r'часть\s*(\d+)', text):
        n = m.group(1)
        results.append({
            "label": f"часть {n}",
            "patterns": [f"часть {n}"]
        })

    # Паттерн «том 5.1», «том 2»
    for m in re.finditer(r'том\s*(\d+(?:\.\d+)*)', text):
        n = m.group(1)
        results.append({
            "label": f"том {n}",
            "patterns": [f"том {n}", f"том"]
        })

    return results


def tokens_from_filename(rel_path: str) -> list[str]:
    """
    Извлекаем значимые токены из полного пути файла.
    Убираем номера версий (v.1, v2 …) и расширение.
    Убираем стоп-слова. Убираем цифровые маркеры папок (4-1-03 → [03]).
    """
    stem = Path(rel_path).stem
    folder = str(Path(rel_path).parent)

    combined = f"{folder} {stem}"
    tokens = tokenize(combined)
    result = []
    seen_structural = set()  # Чтобы не дублировать с extract_structural_tokens

    # Собираем структурные паттерны для фильтрации
    struct_tokens = extract_structural_tokens(combined)
    for st in struct_tokens:
        # Слова которые являются частью структурных паттернов
        for pat_word in st["label"].split():
            seen_structural.add(pat_word)

    for t in tokens:
        if t in STOPWORDS:
            continue
        if re.fullmatch(r"\d+", t):
            continue
        # Пропускаем слова, уже обработанные структурным парсером
        if t in {"раздел", "пд", "подраздел", "часть", "том", "книга"}:
            continue
        # Составные токены (пд5, подраздел2, часть1) — уже в структурных
        if re.fullmatch(r"(пд|подраздел|часть|том)\d+", t):
            continue
        result.append(t)
    return result


def tokens_from_page(text: str) -> set[str]:
    """Множество токенов из текста страницы."""
    return set(tokenize(text))


def extract_text_content_from_xml(xml_data: bytes) -> str:
    """Извлекает только текстовое содержимое всех узлов XML (для отображения в отчете)."""
    from lxml import etree
    import io
    try:
        parser = etree.XMLParser(remove_comments=True, recover=True)
        tree = etree.parse(io.BytesIO(xml_data), parser)
        return " ".join(tree.getroot().itertext()).strip()
    except Exception:
        return ""

def extract_xml_metadata(xml_data: bytes) -> dict:
    """Извлекает метаданные из XML (ПЗ или ЗнП) используя lxml и XPaths."""
    from lxml import etree
    import io
    
    meta = {"шифр": "", "объект": "", "стадия": "", "год": "", "type": ""}
    
    try:
        parser = etree.XMLParser(remove_comments=True, recover=True)
        tree = etree.parse(io.BytesIO(xml_data), parser)
        root = tree.getroot()
        tag = etree.QName(root.tag).localname
        ns = root.nsmap
        clean_ns = {k: v for k, v in ns.items() if k is not None}

        # Определяем тип
        if tag in ["Assignment", "DesignAssignment", "Document"]:
            meta["type"] = "ZNP"
        elif tag == "ExplanatoryNote":
            meta["type"] = "PZ"
        else:
            return meta

        def get_text(xpath):
            try:
                res = tree.xpath(xpath, namespaces=clean_ns)
                if res and isinstance(res, list):
                    if isinstance(res[0], etree._Element):
                        return "".join(res[0].itertext()).strip()
                    return str(res[0]).strip()
                return ""
            except Exception:
                return ""

        if meta["type"] == "PZ":
            obj_type = ""
            for t in ["NonIndustrialObject", "IndustrialObject", "LinearObject"]:
                if root.find(f".//{t}") is not None or any(t in child.tag for child in root):
                    obj_type = t
                    break
            meta["шифр"] = get_text("/ExplanatoryNote/ExplanatoryNoteNumber")
            meta["год"] = get_text("/ExplanatoryNote/ExplanatoryNoteYear")
            if obj_type:
                meta["объект"] = get_text(f"/ExplanatoryNote/{obj_type}/Name")
            meta["стадия"] = "Проектная документация"
            
        elif meta["type"] == "ZNP":
            meta["шифр"] = get_text("//Requisites/Number") or get_text("//Assignment/Number")
            meta["объект"] = get_text("//Content/Object/Name") or get_text("//Assignment/ObjectName")
            meta["стадия"] = get_text("//Assignment/Stage") or get_text("//Document/Stage") # Возможные поля
            date_str = get_text("//Requisites/Date")
            if date_str and len(date_str) >= 4:
                meta["год"] = date_str[:4]

    except Exception:
        pass
    
    return meta

def extract_etalon_from_xml(xml_files: list[Path], search_dir: Path) -> dict:
    """
    Первичный проход по XML файлам для поиска ПЗ/ЗнП и извлечения ЭТАЛОННЫХ МЕТАДАННЫХ.
    Использует логику и XPaths из xml_comparator.
    """
    etalon = {
        "объект": "", "стадия": "", "шифр": "", "год": "", "file": "",
        "pz_file": "", "znp_file": "", 
        "pz_meta": {}, "znp_meta": {},
        "warnings": [], "status": "⚠️ НЕ ПРОВЕРЕНО"
    }
    
    pz_meta = {}
    znp_meta = {}

    for xml_path in xml_files:
        try:
            data = xml_path.read_bytes()
            meta = extract_xml_metadata(data)
            if not meta["type"]:
                continue
                
            rel_path = str(xml_path.relative_to(search_dir))
            if meta["type"] == "PZ":
                pz_meta = meta
                etalon["pz_file"] = rel_path
                etalon["pz_meta"] = pz_meta
            elif meta["type"] == "ZNP":
                znp_meta = meta
                etalon["znp_file"] = rel_path
                etalon["znp_meta"] = znp_meta
        except Exception as e:
            etalon["warnings"].append(f"Ошибка парсинга {xml_path.name}: {e}")

    # ── Сливаем данные в Эталон ──
    # Приоритет ЗнП как "задающей" части
    primary = znp_meta if znp_meta.get("шифр") else pz_meta
    if primary:
        etalon.update({
            "шифр": primary.get("шифр", ""),
            "объект": primary.get("объект", ""),
            "стадия": primary.get("стадия", ""),
            "год": primary.get("год", ""),
        })
        etalon["status"] = "⚓ УСТАНОВЛЕН (Источник: XML)"
        etalon["file"] = etalon["znp_file"] if etalon["znp_file"] else etalon["pz_file"]

    # ── Кросс-верификация ── (Warnings уже не нужны, мы сделаем таблицу в отчете)
    if pz_meta and znp_meta:
        if pz_meta.get("шифр") != znp_meta.get("шифр"):
             pass # Таблица в main() покажет это нагляднее

    return etalon


def compare_with_etalon(doc_meta: dict, etalon: dict) -> list[str]:
    """Сверяет метаданные документа с эталоном. Возвращает список ошибок."""
    errors = []
    if not etalon.get("file"):
        return errors

    # Проверка Шифра (допускаем частичное совпадение, т.к. в PDF может быть короче)
    if etalon.get("шифр") and doc_meta.get("шифр"):
        e_cipher = etalon["шифр"].lower()
        d_cipher = doc_meta["шифр"].lower()
        if e_cipher not in d_cipher and d_cipher not in e_cipher:
            errors.append(f"❌ Шифр '{doc_meta['шифр']}' не совпадает с эталоном '{etalon['шифр']}'")

    # Проверка Названия объекта (очень мягко, по ключевым словам)
    if etalon.get("объект") and doc_meta.get("объект"):
        e_words = set(tokenize(etalon["объект"]))
        d_words = set(tokenize(doc_meta["объект"]))
        common = e_words.intersection(d_words)
        if len(common) < 2 and len(e_words) > 3:
            errors.append(f"⚠️ Название объекта в PDF подозрительно отличается от эталона")

    return errors


def compare(filename: str, page_text: str, etalon: dict = None) -> dict:
    """
    Детерминированное сравнение.
    Возвращает словарь с полями:
      score        — доля найденных токенов (0.0–1.0)
      found        — список токенов, найденных в тексте
      missing      — список токенов, НЕ найденных в тексте
      verdict      — строка-вердикт
      metadata     — словарь извлечённых метаданных
    """
    is_xml = filename.lower().endswith(".xml")
    if is_xml:
        token_text = extract_text_content_from_xml(page_text.encode("utf-8")) or page_text
    else:
        token_text = page_text

    page_tokens = tokens_from_page(token_text)
    page_lower  = normalize(token_text)
    file_tokens = tokens_from_filename(filename)

    # ── Структурные токены (раздел N, подраздел N, часть N) ──
    struct_tokens = extract_structural_tokens(filename)
    struct_found   = []
    struct_missing = []
    for st in struct_tokens:
        hit = any(pat in page_lower for pat in st["patterns"])
        if hit:
            struct_found.append(f"📂{st['label']}")
        else:
            struct_missing.append(f"📂{st['label']}")

    # ── Обычные токены ──
    found   = []
    missing = []

    # Добавляем шифр из эталона в список для поиска (для наглядности в отчете)
    extra_tokens = []
    if etalon and etalon.get("шифр"):
        # Берем только значимую часть шифра (первые 2 части)
        e_parts = [p for p in tokenize(etalon["шифр"]) if len(p) >= 3]
        extra_tokens = e_parts

    effective_tokens = list(dict.fromkeys(file_tokens + extra_tokens)) # unique preserving order

    if not effective_tokens and not struct_tokens:
        return {"score": 1.0, "found": [], "missing": [], "verdict": "ℹ️ Токены не извлечены из имени файла", "metadata": {}}

    for token in effective_tokens:
        # Прямое попадание
        if token in page_tokens:
            found.append(token)
            continue
        # Проверяем синонимы
        synonyms = SYNONYMS.get(token, [])
        if any(syn in page_tokens for syn in synonyms):
            found.append(f"{token}~")   # ~ = найдено через синоним
            continue
        # Частичное совпадение: токен является подстрокой какого-то слова
        partial_hit = any(token in pt for pt in page_tokens)
        if partial_hit:
            found.append(f"({token})")  # () = частичное
        else:
            missing.append(token)

    # ── Суммарный скор ──
    all_found   = found + struct_found
    all_missing = missing + struct_missing
    all_tokens  = file_tokens + [st["label"] for st in struct_tokens]

    def weight(t: str) -> float:
        base = t.lstrip("📂").rstrip("~").strip("()")
        return max(1.0, len(base) / 3.0)

    w_found = sum(weight(t) for t in all_found)
    w_total = sum(weight(t) for t in all_tokens)
    score   = round(w_found / w_total, 2) if w_total else 0.0

    if score >= 0.70:
        verdict = f"✅ СОВПАДАЕТ ({int(score*100)}%)"
    elif score >= 0.40:
        verdict = f"⚠️ ЧАСТИЧНО ({int(score*100)}%) — проверьте вручную"
    else:
        verdict = f"❌ НЕ СОВПАДАЕТ ({int(score*100)}%) — возможна ошибка маркировки"

    # ── Извлечение метаданных объекта (Фикс 4) ──
    if filename.lower().endswith(".xml"):
        try:
            metadata = extract_xml_metadata(page_text.encode("utf-8"))
        except Exception:
            metadata = extract_document_metadata(page_text)
    else:
        metadata = extract_document_metadata(page_text)

    # ── Сверка с ЭТАЛОНОМ (Ground Truth) ──
    etalon_errors = []
    is_source = False
    has_etalon = bool(etalon and etalon.get("шифр"))
    no_xml_msg = "не проверенно , отсутствуют или не расознанно xml источник"

    if etalon:
        is_source = (filename == etalon.get("file") or 
                     filename == etalon.get("pz_file") or 
                     filename == etalon.get("znp_file"))
        
        if not is_source:
             etalon_errors = compare_with_etalon(metadata, etalon)

    # ── Формирование 3-циклового результата ──
    cycle1_res = "✅ ЭТАЛОН" if is_source else ("⚓ УСТАНОВЛЕН" if has_etalon else f"❌ {no_xml_msg}")
    
    cycle2_res = "✅ Совпадает"
    if not has_etalon and not is_source:
        cycle2_res = "⚪ Не проверено"
    elif etalon_errors:
        if any("Шифр" in err for err in etalon_errors):
            cycle2_res = "❌ Шифр не совпадает"
        else:
            cycle2_res = "⚠️ Различия в данных"

    # Результирующий вердикт
    if is_source:
        verdict = "✅ ЭТАЛОН (Источник Истины)"
    elif not has_etalon:
        verdict = f"⚠️ {no_xml_msg}"
        score = min(score, 0.5)
    elif cycle2_res.startswith("❌"):
        verdict = f"❌ ОШИБКА ИДЕНТИФИКАЦИИ ({cycle2_res})"
        score = min(score, 0.3)

    return {
        "cycle1": cycle1_res,
        "cycle2": cycle2_res,
        "cycle3": {
            "score": score,
            "found": all_found,
            "missing": all_missing,
        },
        "score": score,
        "found": all_found,
        "missing": all_missing,
        "verdict": verdict,
        "metadata": metadata,
        "etalon_errors": etalon_errors,
    }


# ──────────────────────── Извлечение метаданных объекта (Фикс 4) ────────

def extract_document_metadata(page_text: str) -> dict:
    """Извлекает ключевые метаданные из текста титульного листа."""
    meta = {}
    text = page_text.strip()
    if not text:
        return meta

    # Наименование объекта (МБОУ СОШ..., Многоквартирный...)
    obj_patterns = [
        r'(МБОУ[^\n]{5,80})',
        r'(Многоквартирный[^\n]{5,80})',
        r'(Жилой дом[^\n]{5,80})',
        r'(объект капитального строительства[^\n]{5,80})',
    ]
    for pat in obj_patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            meta["объект"] = m.group(1).strip()
            break

    # Стадия
    if re.search(r'ПРОЕКТНАЯ ДОКУМЕНТАЦИЯ', text, re.IGNORECASE):
        meta["стадия"] = "Проектная документация"
    elif re.search(r'РАБОЧАЯ ДОКУМЕНТАЦИЯ', text, re.IGNORECASE):
        meta["стадия"] = "Рабочая документация"

    # Шифр (157/25-ПЗУ, МКД-0109-2024-ПИР-ПЗ, 157/25)
    m = re.search(r'(\d{2,5}/\d{2,4}(?:-[\wА-Яа-я.]+)?)\b', text)
    if m:
        meta["шифр"] = m.group(1)
    else:
        m = re.search(r'\b([A-ZА-Я]{2,5}-\d{3,5}-\d{4}-[\w-]+)\b', text)
        if m:
            meta["шифр"] = m.group(1)

    # Год
    m = re.search(r'(20[12]\d)\s*г\.?', text)
    if m:
        meta["год"] = m.group(1)

    # Контрольная сумма
    m = re.search(r'(MD5|CRC32|SHA\d*)\s+([A-Fa-f0-9]{6,64})', text)
    if m:
        meta["контрольная_сумма"] = f"{m.group(1)} {m.group(2)}"

    return meta


# ──────────────────────── PDF-парсер ────────────────────────────────────

def extract_first_page_text(pdf_path: Path) -> str:
    """Извлекает текст первой (и, при нехватке, второй) страницы PDF.
    Если текст < 30 символов (скан) → запускает OCR через pytesseract.
    """
    text = ""
    doc = None
    tmp_path = None

    try:
        doc = fitz.open(str(pdf_path))
    except Exception:
        try:
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                tmp_path = tmp.name
            shutil.copy2(str(pdf_path), tmp_path)
            doc = fitz.open(tmp_path)
        except Exception as e:
            return f"[Ошибка чтения PDF: {e}]"

    try:
        page1_text = ""
        if doc and len(doc) > 0:
            page1_text = doc[0].get_text("text").strip()
            text = page1_text

        # ── OCR fallback для сканов (Фикс 5) ──
        # Проверяем именно стр.1, даже если стр.2+ содержат колонтитулы (Threshold 500)
        if doc and len(page1_text) < 500 and len(doc) > 0:
            ocr_text, dpi = _ocr_first_page(doc)
            if dpi < 300:
                text = f"[Скан < 300 DPI ({dpi}). Качество недостаточно для OCR]\n{ocr_text}"
            elif ocr_text.strip():
                text = f"[OCR, DPI≈{dpi}]\n{ocr_text}"

        # Читаем до 4 страниц — стр.1-2 могут быть оглавлением/колонтитулом
        for page_idx in range(1, min(4, len(doc) if doc else 0)):
            page_text = doc[page_idx].get_text("text").strip()
            if page_text:
                text += "\n" + page_text

    except Exception as e:
        text = f"[Ошибка извлечения текста: {e}]"
    finally:
        if doc:
            doc.close()
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass

    return text


def _ocr_first_page(doc) -> tuple:
    """OCR первой страницы через pytesseract. Возвращает (текст, dpi)."""
    try:
        import pytesseract
        from PIL import Image
        import io as _io
    except ImportError:
        return ("[OCR недоступен: pytesseract/PIL не установлены]", 72)

    try:
        page = doc[0]

        # Оценка DPI по встроенным изображениям
        dpi = 72  # default
        images = page.get_images()
        if images:
            try:
                img_info = doc.extract_image(images[0][0])
                img_width = img_info.get("width", 0)
                page_width_pts = page.rect.width
                if page_width_pts > 0 and img_width > 0:
                    dpi = int(img_width / (page_width_pts / 72))
            except Exception:
                pass

        # Рендерим страницу: 2x масштаб ≈ 144 DPI минимум
        zoom = max(2.0, dpi / 72.0) if dpi < 300 else 2.0
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat)

        # Конвертируем в PIL Image
        img_data = pix.tobytes("png")
        pil_img = Image.open(_io.BytesIO(img_data))

        # OCR (русский + английский)
        ocr_text = pytesseract.image_to_string(pil_img, lang="rus+eng")
        return (ocr_text.strip(), dpi)

    except Exception as e:
        return (f"[Ошибка OCR: {e}]", 72)


# ──────────────────────── XML-парсер (Фикс 3) ────────────────────────

def extract_xml_descriptive_text(xml_path: Path) -> str:
    """Извлекает текстовое содержимое из XML для идентификации."""
    import xml.etree.ElementTree as ET
    
    # Маппинг корневых тегов на человекочитаемые разделы
    XML_ROOT_MAP = {
        "ExplanatoryNote": "Раздел 1 Пояснительная записка ПЗ",
        "SchemePlanning": "Раздел 2 ПЗУ Схема планировочной организации земельного участка",
        "ArchitecturalSolutions": "Раздел 3 АР Архитектурные решения",
        "ConstructionSolutions": "Раздел 4 КР Конструктивные решения",
        "PowerSupplySystem": "Раздел 5 Подраздел 1 ИОС1 ЭС Электроснабжение",
        "WaterSupplySystem": "Раздел 5 Подраздел 2 ИОС2 ВС Водоснабжение",
        "WaterDisposalSystem": "Раздел 5 Подраздел 3 ИОС3 ВО Канализация Водоотведение",
        "HeatingNetwork": "Раздел 5 Подраздел 4 ИОС4 ТС Теплоснабжение",
        "CommunicationNetwork": "Раздел 5 Подраздел 5 ИОС5 СС Сети связи",
        "GasSupplySystem": "Раздел 5 Подраздел 6 ИОС6 ГС Газоснабжение",
        "TechnologicalSolutions": "Раздел 5 Подраздел 7 ИОС7 ТХ Технологические решения",
        "ConstructionOrganization": "Раздел 6 ПОС Проект организации строительства",
        "DemolitionWork": "Раздел 7 ПОД Проект организации работ по сносу",
        "EnvironmentalProtection": "Раздел 8 ООС Перечень мероприятий по охране окружающей среды",
        "FireSafety": "Раздел 9 ПБ Мероприятия по обеспечению пожарной безопасности",
        "DisabledAccess": "Раздел 10 ОДИ Мероприятия по обеспечению доступа инвалидов",
        "EnergyEfficiency": "Раздел 11 ЭЭ Мероприятия по обеспечению соблюдения требований энергетической эффективности",
        "Estimates": "Раздел 12 СМ Сметная документация Смета",
    }

    try:
        tree = ET.parse(str(xml_path))
        root = tree.getroot()
        texts = []
        
        # Инъекция описания по корневому тегу
        tag_local_name = root.tag.split('}')[-1] if '}' in root.tag else root.tag
        if tag_local_name in XML_ROOT_MAP:
            texts.append(XML_ROOT_MAP[tag_local_name])
            
        for elem in root.iter():
            # Собираем текст из тегов
            if elem.text and elem.text.strip():
                texts.append(elem.text.strip())
            # И атрибутов
            for attr_val in elem.attrib.values():
                if len(attr_val) > 3:
                    texts.append(attr_val)
                    
        return "\n".join(texts[:300])  # Увеличил лимит
    except Exception as e:
        return f"[Ошибка чтения XML: {e}]"


# ──────────────────────── IFC/BIM-парсер (Фикс 6) ──────────────────

def extract_ifc_metadata(ifc_path: Path) -> str:
    """Извлекает метаданные из IFC (BIM-модели) через ifcopenshell."""
    try:
        import ifcopenshell
    except ImportError:
        return "[Ошибка: ifcopenshell не установлен. pip install ifcopenshell]"
    try:
        ifc = ifcopenshell.open(str(ifc_path))
        lines = []
        for proj in ifc.by_type("IfcProject"):
            lines.append(f"Проект: {proj.Name or '—'}")
            if proj.Description:
                lines.append(f"Описание: {proj.Description}")
        for bld in ifc.by_type("IfcBuilding"):
            lines.append(f"Здание: {bld.Name or '—'}")
            if bld.Description:
                lines.append(f"Тип: {bld.Description}")
        for site in ifc.by_type("IfcSite"):
            lines.append(f"Площадка: {site.Name or '—'}")
        for oh in ifc.by_type("IfcOwnerHistory"):
            if oh.OwningUser and oh.OwningUser.ThePerson:
                p = oh.OwningUser.ThePerson
                name = f"{p.GivenName or ''} {p.FamilyName or ''}".strip()
                if name:
                    lines.append(f"Автор: {name}")
            if oh.OwningApplication:
                lines.append(f"ПО: {oh.OwningApplication.ApplicationFullName}")
            break  # Только первый
        stories = ifc.by_type("IfcBuildingStorey")
        if stories:
            lines.append(f"Этажей: {len(stories)}")
        try:
            hdr = ifc.header.file_name
            if hdr.organization:
                lines.append(f"Организация: {', '.join(hdr.organization)}")
        except Exception:
            pass
        return "\n".join(lines) if lines else "[IFC: метаданные не найдены]"
    except Exception as e:
        return f"[Ошибка чтения IFC: {e}]"


# ──────────────────────── Распаковка ZIP ────────────────────────────────

def safe_unzip(zip_path: Path, dest_dir: Path) -> int:
    """Безопасная распаковка с кириллической кодировкой."""
    added = 0
    with zipfile.ZipFile(zip_path, "r") as zf:
        for zinfo in zf.infolist():
            if zinfo.is_dir() or "__MACOSX" in zinfo.filename or "Zone.Identifier" in zinfo.filename:
                continue
            try:
                raw = zinfo.filename.encode("cp437")
                try:
                    name = raw.decode("utf-8")
                except UnicodeDecodeError:
                    name = raw.decode("cp866")
            except Exception:
                name = zinfo.filename

            name = name.replace("\\", "/")
            name = "/".join([p[:100] for p in name.split("/")])

            out = dest_dir / name
            if not out.name:
                continue
            out.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(zinfo) as src, open(out, "wb") as dst:
                shutil.copyfileobj(src, dst)
            added += 1
    return added


# ──────────────────────────── Главная функция ───────────────────────────────

def extract_text_for_file(file_path: Path) -> tuple[str, str]:
    """Извлекает текст из файла в зависимости от расширения. Возвращает (text, type_label)."""
    ext = file_path.suffix.lower()
    if ext == ".pdf":
        return extract_first_page_text(file_path), "PDF"
    elif ext == ".xml":
        try:
            return file_path.read_text(encoding="utf-8"), "XML"
        except Exception:
            return file_path.read_text(encoding="cp1251", errors="ignore"), "XML"
    elif ext == ".ifc":
        return extract_ifc_metadata(file_path), "IFC/BIM"
    else:
        return "", "???"


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Сверка титульных листов проектной документации.")
    parser.add_argument("path", nargs="?", help="Путь к папке или ZIP-архиву")
    parser.add_argument("--etalon-json", help="Путь к JSON-файлу с готовым эталоном (для цикличной проверки)")
    args = parser.parse_args()

    if not args.path:
        print("Использование: python test_first_page.py <путь_к_папке_или_zip>")
        sys.exit(1)

    target_path = Path(args.path)
    if not target_path.exists():
        print(f"Путь не найден: {target_path}")
        sys.exit(1)

    results_dir = Path(__file__).parent / "ResyltatTesta"
    results_dir.mkdir(exist_ok=True)

    now_str     = datetime.datetime.now().strftime("%H.%M %d.%m.%Y")
    report_file = results_dir / f"{now_str}.md"

    print(f"🚀 Запуск: {target_path}")
    print(f"📝 Отчет: {report_file}")

    # Заголовок отчёта
    with open(report_file, "w", encoding="utf-8") as rf:
        rf.write(f"# Отчет сверки титульных листов ({now_str})\n\n")
        rf.write(f"**Источник:** `{target_path}`\n\n")
        rf.write("| Файл | Тип | Вердикт | % | Найдено | Пропущено |\n")
        rf.write("|------|-----|---------|---|---------|----------|\n")

    # Распаковка ZIP
    temp_dir_obj = None
    search_dir   = target_path

    if target_path.is_file() and target_path.suffix.lower() == ".zip":
        temp_dir_obj = tempfile.TemporaryDirectory()
        search_dir   = Path(temp_dir_obj.name)
        print("📦 Распаковка...")
        added = safe_unzip(target_path, search_dir)
        print(f"📦 Распаковано: {added} файлов")

    # Собираем все поддерживаемые файлы
    xml_files = sorted(search_dir.rglob("*.xml"))
    pdf_files = sorted(search_dir.rglob("*.pdf"))
    ifc_files = sorted(search_dir.rglob("*.ifc"))

    # ── ФАЗА 1: Извлечение ЭТАЛОНА (Ground Truth) - Цикл 1 ──
    etalon = None
    no_xml_status = "не проверенно, отсутствуют или не распознано xml источник"
    
    # 1. Если передан готовый JSON
    if args.etalon_json and Path(args.etalon_json).exists():
        try:
            with open(args.etalon_json, "r", encoding="utf-8") as f:
                etalon = json.load(f)
            print(f"⚓ Загружен ЭТАЛОН из внешнего источника: {args.etalon_json}")
        except Exception as e:
            print(f"⚠️ Ошибка загрузки эталона из JSON: {e}")

    # 2. Иначе извлекаем из текущих XML
    if not etalon:
        etalon = extract_etalon_from_xml(xml_files, search_dir)
        
    has_etalon = bool(etalon and etalon.get("шифр"))

    if has_etalon:
        print(f"⚓ Цикл 1 ЗАВЕРШЕН (Источник: {etalon['file']})")
        print(f"   Объект: {etalon['объект']}")
        print(f"   Шифр:   {etalon['шифр']}")
    else:
        print(f"⚠️ Цикл 1: {no_xml_status}")

    all_files = xml_files + pdf_files + ifc_files
    print(f"📄 Найдено файлов: {len(all_files)} (PDF + XML + IFC)")

    stats = {"ok": 0, "warn": 0, "fail": 0, "scan": 0}

    # Заголовок отчёта (3-цикловая структура)
    with open(report_file, "w", encoding="utf-8") as rf:
        rf.write(f"# Отчет сверки титульных листов ({now_str})\n\n")
        rf.write(f"**Источник:** `{target_path}`\n\n")
        
        if has_etalon:
            rf.write("## ⚓ ЦИКЛ 1: ЭТАЛОННЫЕ ДАННЫЕ (Ground Truth)\n")
            rf.write(f"- **Файл-источник:** `{etalon['file']}`\n")
            rf.write(f"- **Объект:** `{etalon['объект']}`\n")
            rf.write(f"- **Шифр:** `{etalon['шифр']}`\n")
            rf.write(f"- **Стадия:** `{etalon['стадия']}`\n")
            rf.write(f"- **Год:** `{etalon['год']}`\n")
            
            if etalon.get("pz_file") and etalon.get("znp_file"):
                rf.write("\n### ⚖️ Сверка ПЗ vs ЗнП\n")
                rf.write("| Реквизит | Значение из ПЗ | Значение из ЗнП | Результат |\n")
                rf.write("| :--- | :--- | :--- | :--- |\n")
                
                def compare_vals(v1, v2, soft=False):
                    v1, v2 = (v1 or "").strip(), (v2 or "").strip()
                    if not v1 and not v2: return "⚪ Не найдено"
                    if not v1 or not v2: return "🟡 Частично"
                    if v1.lower() == v2.lower(): return "✅ Совпадает"
                    if soft:
                        w1, w2 = set(tokenize(v1)), set(tokenize(v2))
                        if len(w1.intersection(w2)) >= 2: return "✅ Совпадает (мягко)"
                    return "❌ Расхождение"

                p, z = etalon.get("pz_meta",{}), etalon.get("znp_meta",{})
                rf.write(f"| **Шифр объекта** | {p.get('шифр','-')} | {z.get('шифр','-')} | {compare_vals(p.get('шифр'), z.get('шифр'))} |\n")
                rf.write(f"| **Наименование** | {p.get('объект','-')[:40]}... | {z.get('объект','-')[:40]}... | {compare_vals(p.get('объект'), z.get('объект'), True)} |\n")
                rf.write(f"| **Стадия** | {p.get('стадия','-')} | {z.get('стадия','-')} | {compare_vals(p.get('стадия'), z.get('стадия'))} |\n")
        else:
            rf.write(f"## ❌ ЦИКЛ 1: ОШИБКА\n> **{no_xml_status}**\n\n")

        rf.write("\n## 📋 ЦИКЛ 2 и 3: СВОДНЫЙ РЕЗУЛЬТАТ\n")
        rf.write("| Файл | Тип | Вердикт (Цикл 2) | Найдено (Цикл 3) | Пропущено |\n")
        rf.write("|------|-----|---------|---------|----------|\n")

    for i, file_path in enumerate(all_files, 1):
        rel = str(file_path.relative_to(search_dir))
        print(f"[{i}/{len(all_files)}] {rel}")

        page_text, type_label = extract_text_for_file(file_path)
        result = compare(rel, page_text, etalon=etalon)

        display_text = page_text[:1500] + ("..." if len(page_text) > 1500 else "")
        if not display_text.strip():
            display_text = "*(Скан или пустой документ)*"
            stats["scan"] += 1
        elif result["score"] >= 0.70:
            stats["ok"] += 1
        elif result["score"] >= 0.40:
            stats["warn"] += 1
        else:
            stats["fail"] += 1

        found_str   = ", ".join(result["found"][:10])
        missing_str = ", ".join(result["missing"][:10])
        short_name  = Path(rel).name[:60]

        with open(report_file, "a", encoding="utf-8") as rf:
            # Цикл 2 (Мета) и Цикл 3 (Токены / %)
            rf.write(f"| `{short_name}` | {type_label} | {result['cycle2']} | "
                     f"{int(result['cycle3']['score']*100)}% | {found_str} | {missing_str} |\n")

    # Детальные блоки
    with open(report_file, "a", encoding="utf-8") as rf:
        rf.write("\n---\n\n## Подробные данные\n\n")

    for i, file_path in enumerate(all_files, 1):
        rel = str(file_path.relative_to(search_dir))
        page_text, type_label = extract_text_for_file(file_path)
        result  = compare(rel, page_text, etalon=etalon)
        
        # ── Для XML показываем чистый текст, а не сырые теги ──
        if type_label == "XML":
            display_source = extract_text_content_from_xml(page_text.encode("utf-8"))
        else:
            display_source = page_text

        display = display_source[:1500] + ("..." if len(display_source) > 1500 else "")

        with open(report_file, "a", encoding="utf-8") as rf:
            rf.write(f"\n### {i}. `{rel}` [{type_label}]\n\n")
            rf.write(f"**Вердикт:** {result['verdict']}\n\n")
            if result["missing"]:
                rf.write(f"**❌ Токены НЕ найдены:** `{', '.join(result['missing'])}`\n\n")
            if result["found"]:
                rf.write(f"**✅ Токены найдены:** `{', '.join(result['found'])}`\n\n")

            # Ошибки сверки с эталоном
            if result.get("etalon_errors"):
                rf.write("**❌ Расхождения с ЭТАЛОНОМ:**\n")
                for err in result["etalon_errors"]:
                    rf.write(f"- {err}\n")
                rf.write("\n")

            # Метаданные объекта
            meta = result.get("metadata", {})
            if any(meta.values()):
                rf.write("**📋 Метаданные:**\n")
                for key, val in meta.items():
                    if val:
                        rf.write(f"- {key}: {val}\n")
                rf.write("\n")

            rf.write(f"**Текст/содержимое:**\n")
            rf.write("```text\n")
            rf.write(display + "\n")
            rf.write("```\n\n")
            rf.write("---\n")

    # Итог
    total = len(all_files)
    with open(report_file, "a", encoding="utf-8") as rf:
        rf.write(f"\n## Итого\n\n")
        rf.write(f"- Всего файлов: **{total}** (PDF + XML + IFC)\n")
        rf.write(f"- ✅ Совпадают: **{stats['ok']}**\n")
        rf.write(f"- ⚠️ Частично: **{stats['warn']}**\n")
        rf.write(f"- ❌ Не совпадают: **{stats['fail']}**\n")
        rf.write(f"- 🖼 Сканы/пустые: **{stats['scan']}**\n")

    if temp_dir_obj is not None:
        temp_dir_obj.cleanup()

    print(f"\n✅ Готово! Отчет: {report_file}")
    print(f"   ✅ {stats['ok']} | ⚠️ {stats['warn']} | ❌ {stats['fail']} | 🖼 {stats['scan']}")


if __name__ == "__main__":
    main()

