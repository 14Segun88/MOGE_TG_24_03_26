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
from pathlib import Path
import fitz  # PyMuPDF


# ─── СТОП-СЛОВА (не несут смыслового содержания в имени файла) ───────────
STOPWORDS = {
    "pdf", "sig", "xml", "v", "v1", "v2", "v3", "v4", "v5",
    "раздел", "пд", "nr", "no", "книга", "подраздел", "часть",
    "1", "2", "3", "4", "5", "6", "7", "8", "9", "0",
}

# Сокращения-синонимы: токен из файла → возможные вариации в тексте
SYNONYMS: dict[str, list[str]] = {
    "пз": ["пояснительная", "записка"],
    "ар": ["архитектурные", "решения"],
    "кр": ["конструктивные", "решения"],
    "пзу": ["планировочной", "организации"],
    "ул": ["удостоверяющий", "лист", "информационно"],
    "сп": ["состав", "проекта"],
    "ппб": ["пожарной", "безопасности"],
    "иос": ["инженерного", "оборудования", "инженерных"],
    "пос": ["организации", "строительства"],
    "ос": ["охрана", "среды"],
    "оди": ["доступности", "инвалидов"],
    "ссп": ["сетей", "связи"],
    "бим": ["информационной", "модели"],
}


def normalize(text: str) -> str:
    """Нижний регистр + оставляем только буквы, цифры и пробелы."""
    return re.sub(r"[^а-яёa-z0-9 ]", " ", text.lower())


def tokenize(text: str) -> list[str]:
    """Разбиваем нормализованный текст на токены длиной >= 2."""
    return [t for t in normalize(text).split() if len(t) >= 2]


def tokens_from_filename(rel_path: str) -> list[str]:
    """
    Извлекаем значимые токены из полного пути файла.
    Убираем номера версий (v.1, v2 …) и расширение.
    Убираем стоп-слова. Убираем цифровые маркеры папок (4-1-03 → [03]).
    """
    # Берём только имя файла (не путь целиком — путь анализируем отдельно)
    stem = Path(rel_path).stem   # например: "Раздел ПД №3 АР-УЛ"
    folder = str(Path(rel_path).parent)  # например: "4-1-03 Архитектурные решения/v.1"

    combined = f"{folder} {stem}"
    tokens = tokenize(combined)
    result = []
    for t in tokens:
        if t in STOPWORDS:
            continue
        # Убираем "чистые" числа
        if re.fullmatch(r"\d+", t):
            continue
        result.append(t)
    return result


def tokens_from_page(text: str) -> set[str]:
    """Множество токенов из текста страницы."""
    return set(tokenize(text))


def compare(filename: str, page_text: str) -> dict:
    """
    Детерминированное сравнение.
    Возвращает словарь с полями:
      score        — доля найденных токенов (0.0–1.0)
      found        — список токенов, найденных в тексте
      missing      — список токенов, НЕ найденных в тексте
      verdict      — строка-вердикт
    """
    if not page_text.strip():
        return {"score": 0.0, "found": [], "missing": [], "verdict": "⚠️ Текст не извлечён (вероятно, скан)"}

    file_tokens = tokens_from_filename(filename)
    page_tokens  = tokens_from_page(page_text)

    if not file_tokens:
        return {"score": 1.0, "found": [], "missing": [], "verdict": "ℹ️ Токены не извлечены из имени файла"}

    found   = []
    missing = []

    for token in file_tokens:
        # Прямое попадание
        if token in page_tokens:
            found.append(token)
            continue
        # Проверяем синонимы
        synonyms = SYNONYMS.get(token, [])
        if any(syn in page_tokens for syn in synonyms):
            found.append(f"{token}~")   # ~ = найдено через синоним
            continue
        # Частичное совпадение: токен является подстрокой какого-то слова в тексте
        partial_hit = any(token in pt for pt in page_tokens)
        if partial_hit:
            found.append(f"({token})")  # () = частичное
        else:
            missing.append(token)

    total   = len(file_tokens)
    # Вес: длинные токены важнее
    def weight(t: str) -> float:
        base = t.rstrip("~").strip("()")
        return max(1.0, len(base) / 3.0)

    w_found   = sum(weight(t) for t in found)
    w_total   = sum(weight(t) for t in file_tokens)
    score     = round(w_found / w_total, 2) if w_total else 0.0

    if score >= 0.70:
        verdict = f"✅ СОВПАДАЕТ ({int(score*100)}%)"
    elif score >= 0.40:
        verdict = f"⚠️ ЧАСТИЧНО ({int(score*100)}%) — проверьте вручную"
    else:
        verdict = f"❌ НЕ СОВПАДАЕТ ({int(score*100)}%) — возможна ошибка маркировки"

    return {
        "score":   score,
        "found":   found,
        "missing": missing,
        "verdict": verdict,
    }


# ──────────────────────────── PDF-парсер ────────────────────────────────────

def extract_first_page_text(pdf_path: Path) -> str:
    """Извлекает текст первой (и, при нехватке, второй) страницы PDF."""
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
        if doc and len(doc) > 0:
            text = doc[0].get_text("text").strip()
        if doc and len(text) < 80 and len(doc) > 1:
            text += "\n" + doc[1].get_text("text").strip()
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


# ──────────────────────────── Распаковка ZIP ────────────────────────────────

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

def main():
    if len(sys.argv) < 2:
        print("Использование: python test_first_page.py <путь_к_папке_или_zip>")
        sys.exit(1)

    target_path = Path(sys.argv[1])
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
        rf.write("| Файл | Вердикт | % | Найдено | Пропущено |\n")
        rf.write("|------|---------|---|---------|----------|\n")

    # Распаковка ZIP (если нужно)
    temp_dir_obj = None
    search_dir   = target_path

    if target_path.is_file() and target_path.suffix.lower() == ".zip":
        temp_dir_obj = tempfile.TemporaryDirectory()
        search_dir   = Path(temp_dir_obj.name)
        print("📦 Распаковка...")
        added = safe_unzip(target_path, search_dir)
        print(f"📦 Распаковано: {added} файлов")

    pdf_files = sorted(search_dir.rglob("*.pdf"))
    print(f"📄 Найдено PDF: {len(pdf_files)}")

    stats = {"ok": 0, "warn": 0, "fail": 0, "scan": 0}

    for i, pdf_path in enumerate(pdf_files, 1):
        rel = str(pdf_path.relative_to(search_dir))
        print(f"[{i}/{len(pdf_files)}] {rel}")

        page_text    = extract_first_page_text(pdf_path)
        result       = compare(rel, page_text)

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

        # Строка в таблицу
        short_name = Path(rel).name[:60]
        with open(report_file, "a", encoding="utf-8") as rf:
            rf.write(f"| `{short_name}` | {result['verdict']} | "
                     f"{int(result['score']*100)}% | {found_str} | {missing_str} |\n")

    # Детальные блоки — каждый файл отдельно (после таблицы)
    with open(report_file, "a", encoding="utf-8") as rf:
        rf.write("\n---\n\n## Подробные данные\n\n")

    for i, pdf_path in enumerate(pdf_files, 1):
        rel       = str(pdf_path.relative_to(search_dir))
        page_text = extract_first_page_text(pdf_path)
        result    = compare(rel, page_text)
        display   = page_text[:1500] + ("..." if len(page_text) > 1500 else "")

        with open(report_file, "a", encoding="utf-8") as rf:
            rf.write(f"\n### {i}. `{rel}`\n\n")
            rf.write(f"**Вердикт:** {result['verdict']}\n\n")
            if result["missing"]:
                rf.write(f"**❌ Токены НЕ найдены на титуле:** `{', '.join(result['missing'])}`\n\n")
            if result["found"]:
                rf.write(f"**✅ Токены найдены:** `{', '.join(result['found'])}`\n\n")
            rf.write("**Текст первой страницы:**\n")
            rf.write("```text\n")
            rf.write(display + "\n")
            rf.write("```\n\n")
            rf.write("---\n")

    # Итог
    total = len(pdf_files)
    with open(report_file, "a", encoding="utf-8") as rf:
        rf.write(f"\n## Итого\n\n")
        rf.write(f"- Всего PDF: **{total}**\n")
        rf.write(f"- ✅ Совпадают: **{stats['ok']}**\n")
        rf.write(f"- ⚠️ Частично: **{stats['warn']}**\n")
        rf.write(f"- ❌ Не совпадают: **{stats['fail']}**\n")
        rf.write(f"- 🖼 Сканы/пустые: **{stats['scan']}**\n")

    if temp_dir_obj:
        temp_dir_obj.cleanup()

    print(f"\n✅ Готово! Отчет: {report_file}")
    print(f"   ✅ {stats['ok']} | ⚠️ {stats['warn']} | ❌ {stats['fail']} | 🖼 {stats['scan']}")


if __name__ == "__main__":
    main()
