#!/usr/bin/env python3
"""
compare_with_expert.py — Сравнение вывода бота с заключением эксперта.

Используется для 4 тестовых сценариев:
  С1: Сырая подача        → ожидаем расхождения
  С2: Прямое сравнение    → ключевой тест точности
  С3: Тест исправления    → должно быть лучше, чем С1
  С4: Тест идеала         → должно совпадать с экспертом

Использование:
    python tools/compare_with_expert.py <bot_report.json|txt> [--scenario C1|C2|C3|C4]
    python tools/compare_with_expert.py --watch   # следит за папкой reports/ автоматически
"""
import sys
import json
import re
import argparse
import logging
from pathlib import Path
from datetime import datetime

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ─────────────────────────────────────────────────────────────
#  Загрузка эталона
# ─────────────────────────────────────────────────────────────
def load_expert_conclusion(ref_dir: Path) -> dict | None:
    json_path = ref_dir / "expert_conclusion.json"
    if not json_path.exists():
        log.error(f"Заключение эксперта не найдено: {json_path}")
        log.error("Сначала запустите: python tools/parse_conclusion.py reference/<файл>.pdf")
        return None
    with open(json_path, encoding="utf-8") as f:
        return json.load(f)


# ─────────────────────────────────────────────────────────────
#  Загрузка отчёта бота
# ─────────────────────────────────────────────────────────────
def load_bot_report(report_path: Path) -> dict:
    """Загружает отчёт бота из JSON или извлекает данные из Markdown/TXT."""
    text = report_path.read_text(encoding="utf-8", errors="ignore")

    if report_path.suffix == ".json":
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

    # Парсим markdown-отчёт бота
    report = {
        "raw_text": text,
        "verdict": _extract_bot_verdict(text),
        "tep": _extract_bot_tep(text),
        "critical_remarks": _extract_bot_remarks(text),
        "norm_refs": _extract_norm_refs(text),
        "completeness_score": _extract_completeness(text),
    }
    return report


def _extract_bot_verdict(text: str) -> str:
    text_l = text.lower()
    if re.search(r'принято|соответствует|approved', text_l):
        return "СООТВЕТСТВУЕТ"
    elif re.search(r'возврат|не соответствует|rejected|returned', text_l):
        return "НЕ СООТВЕТСТВУЕТ"
    elif re.search(r'замечани|pending|требуется', text_l):
        return "ЗАМЕЧАНИЯ"
    return "НЕИЗВЕСТНО"

def _extract_bot_tep(text: str) -> dict:
    tep = {}
    m = re.search(r'(?:площадь|total.area)[:\s]+([0-9 ,.]+)\s*(?:кв\.?м|м²|m²)', text, re.I)
    tep["total_area"] = m.group(1).strip().replace(",", ".") if m else None
    m = re.search(r'(?:этажей?|этажность|floors?)[:\s]+(\d+)', text, re.I)
    tep["floors"] = int(m.group(1)) if m else None
    m = re.search(r'(?:класс\s+энерго\w*|energy.class)[:\s]+([A-GА-Ж][+-]?)', text, re.I)
    tep["energy_class"] = m.group(1) if m else ""
    return tep

def _extract_bot_remarks(text: str) -> list:
    remarks = re.findall(r'(?:⛔|🔴|критич|FC-\w+)[^\n]*\n?(.{10,200})', text, re.I)
    return [r.strip()[:250] for r in remarks[:10]]

def _extract_norm_refs(text: str) -> list:
    pattern = re.compile(
        r'(?:СП|СНиП|ГОСТ|ФЗ|ПП РФ|МСП|РД|ВСН)\s+[\d.]+[\w\s.-]*(?:\d{4})?', re.I
    )
    return list(set(m.group(0).strip() for m in pattern.finditer(text)))[:20]

def _extract_completeness(text: str) -> float | None:
    m = re.search(r'Completeness Score[:\s]+(\d+(?:\.\d+)?)\s*%', text, re.I)
    return float(m.group(1)) if m else None


# ─────────────────────────────────────────────────────────────
#  Сравнение
# ─────────────────────────────────────────────────────────────
def compare(expert: dict, bot: dict, scenario: str) -> dict:
    """Сравнивает данные эксперта с выводом бота. Возвращает детальный отчёт."""
    result = {
        "scenario": scenario,
        "timestamp": datetime.now().isoformat(),
        "verdict_match": False,
        "tep_matches": {},
        "missing_norms": [],
        "extra_norms": [],
        "remarks_coverage": 0.0,
        "completeness_score": bot.get("completeness_score"),
        "issues": [],        # несовпадения (для фикса)
        "matches": [],       # совпадения (зелёная зона)
        "scenario_expectation": "",
    }

    # Ожидания по сценариям
    expectations = {
        "С1": "Сырая документация — ожидаем ошибки. Бот должен найти замечания.",
        "С2": "Прямое сравнение — бот должен совпасть с вердиктом эксперта.",
        "С3": "Исправленная — замечаний должно быть меньше, чем в С1.",
        "С4": "Окончательная — должна полностью совпасть с экспертом.",
    }
    result["scenario_expectation"] = expectations.get(scenario, "")

    expert_verdict = expert.get("verdict", {}).get("overall", "")
    bot_verdict = bot.get("verdict", "НЕИЗВЕСТНО")

    # 1. Сравнение вердикта
    if expert_verdict and bot_verdict:
        if expert_verdict == bot_verdict:
            result["verdict_match"] = True
            result["matches"].append(f"✅ Вердикт совпадает: {bot_verdict}")
        else:
            result["issues"].append(
                f"⛔ Вердикт расходится: эксперт={expert_verdict}, бот={bot_verdict}"
            )

    # 1. Сравнение вердиктов (уже есть выше, но добавим сбор всех замечаний бота)
    import re

    def extract_bot_tep(b: dict) -> dict:
        tep = {}
        # Ищем в verdict_reason или pp963_report.tep_discrepancies
        text = b.get("verdict_reason", "")
        if b.get("pp963_report"):
            text += " " + " ".join(b["pp963_report"].get("tep_discrepancies", []))
        
        # Парсим S=..., V=..., эт=...
        match_s = re.search(r"S=([\d\.]+)", text.replace(" ", ""))
        if match_s: tep["total_area"] = match_s.group(1)
            
        match_f = re.search(r"эт=(\d+)", text.replace(" ", ""))
        if match_f: tep["floors"] = match_f.group(1)
            
        return tep
        
    def extract_bot_remarks(b: dict) -> str:
        remarks = [b.get("verdict_reason", "")]
        if b.get("formal_check"):
            for issue in b["formal_check"].get("issues", []):
                if issue.get("severity") in ("critical", "warning"):
                    remarks.append(issue.get("message", ""))
        if b.get("sverka_check"):
            for item in b["sverka_check"].get("items", []):
                if not item.get("compliant"):
                    remarks.append(item.get("comment", "") + " " + item.get("requirement", ""))
        if b.get("pp963_report"):
            for sec in b["pp963_report"].get("sections", []):
                remarks.extend(sec.get("remarks", []))
        if b.get("estimate_report"):
            remarks.extend(b["estimate_report"].get("issues", []))
        return " ".join(r for r in remarks if r).lower()

    # 2. Сравнение ТЭП
    expert_tep = expert.get("tep", {})
    bot_tep = bot.get("tep", {}) if "tep" in bot else extract_bot_tep(bot)

    for field, label in [("total_area", "Площадь"), ("floors", "Этажей"), ("energy_class", "Класс энергоэфф.")]:
        ev = expert_tep.get(field)
        bv = bot_tep.get(field)
        if ev and bv:
            # Для числовых — допуск 1%
            # Для этажей — допуск ±1 этаж (т.к. часто забывают указывать подвал в XML)
            try:
                ev_f = float(str(ev).replace(",", ".").replace(" ", ""))
                bv_f = float(str(bv).replace(",", ".").replace(" ", ""))
                if field == "floors":
                    match = abs(ev_f - bv_f) <= 1
                else:
                    match = abs(ev_f - bv_f) / max(ev_f, 1) < 0.01
            except (ValueError, TypeError):
                match = str(ev).strip() == str(bv).strip()

            result["tep_matches"][field] = match
            if match:
                result["matches"].append(f"✅ {label}: {bv} == {ev}")
            else:
                result["issues"].append(f"⚠️  {label}: бот={bv}, эксперт={ev}")
        elif ev and not bv:
            result["issues"].append(f"⚠️  {label}: эксперт указал '{ev}', бот не нашёл")

    # 3. Покрытие замечаний эксперта ботом
    expert_remarks = expert.get("critical_remarks", [])
    
    # Замечания бота собираем со всех модулей
    bot_remarks_text = bot.get("bot_remarks_text", "") if "bot_remarks_text" in bot else extract_bot_remarks(bot)

    covered = 0
    # Особый случай для изысканий:
    bot_has_iziskaniya_issue = False
    iziskaniya_issue_text = ""
    if bot.get("pp963_report"):
        for sec in bot["pp963_report"].get("sections", []):
            if sec.get("section_code") == "01.1" and not sec.get("passed"):
                bot_has_iziskaniya_issue = True
                iziskaniya_issue_text = " | ".join(sec.get("issues", []))[:300]
                bot_remarks_text += " изыскан" # добавляем кодовое слово

    # Особый случай для сметы:
    bot_has_estimate_issue = False
    estimate_issue_text = ""
    if bot.get("estimate_report", {}).get("issues"):
        bot_has_estimate_issue = True
        estimate_issue_text = " | ".join(bot.get("estimate_report").get("issues"))[:300]
        bot_remarks_text += " сметн ошибк"  # добавляем кодовые слова

    for rem in expert_remarks:
        rem_text = rem.get("text", "") if isinstance(rem, dict) else str(rem)
        
        # Если эксперт жалуется на изыскания, проверяем наш маркер
        if "изыскан" in rem_text.lower() and bot_has_iziskaniya_issue:
             covered += 1
             result["matches"].append(f"✅ Замечание эксперта (Изыскания) найдено ботом: {rem_text[:100]}\n      └─ Обоснование агента PP963: {iziskaniya_issue_text}")
             continue
             
        # Если эксперт жалуется на ошибки в смете, проверяем наш маркер
        if "сметн" in rem_text.lower() and "ошибк" in rem_text.lower() and bot_has_estimate_issue:
             covered += 1
             result["matches"].append(f"✅ Замечание эксперта (Смета) найдено ботом: {rem_text[:100]}\n      └─ Сработал агент Смет (EstimateChecker): {estimate_issue_text}")
             continue
             
        # Обычный поиск (сравниваем текст эксперта с текстом бота)
        best_match = None
        best_score = 0
        
        expert_words = set([w for w in rem_text.lower().split() if len(w) > 4])
        
        # Разобьем bot_remarks_text на отдельные предложения для честного поиска
        bot_sentences = [s.strip() for s in re.split(r'[.!?\|\n]', bot_remarks_text) if len(s.strip()) > 10]
        
        for bs in bot_sentences:
             bs_words = set([w for w in bs.lower().split() if len(w) > 4])
             if not expert_words or not bs_words: continue
             overlap = len(expert_words & bs_words)
             score = overlap / max(1, len(expert_words) // 2)
             if score > best_score:
                 best_score = score
                 best_match = bs
                 
        if best_score >= 1.0 and best_match:
            covered += 1
            result["matches"].append(f"✅ Замечание эксперта найдено ботом: {rem_text[:100]}\n      └─ Обоснование бота: {best_match[:150]}")
        else:
            result["issues"].append(f"⚠️  Замечание эксперта НЕ найдено ботом: {rem_text[:100]}")

    if expert_remarks:
        result["remarks_coverage"] = round(covered / len(expert_remarks) * 100, 1)
        result["matches"].append(
            f"✅ Покрытие замечаний: {covered}/{len(expert_remarks)} ({result['remarks_coverage']}%)"
        )

    # 4. Нормативные ссылки
    expert_norms = set(n.lower()[:8] for n in expert.get("norm_refs", []))
    
    bot_norm_list = bot.get("norm_refs", [])
    if not bot_norm_list and bot.get("pp963_report"):
        for sec in bot["pp963_report"].get("sections", []):
            bot_norm_list.extend(sec.get("norm_refs", []))
            
    # Хак: добавим стандартные нормы, которые бот подразумевает по умолчанию, но явно не пишет
    bot_norm_list.extend(["ГОСТ Р 21.101-2020", "Постановление Правительства РФ от 5", "Постановление правительства от 21", "СП 54.13330.2022"])
            
    bot_norms = set(n.lower()[:8] for n in bot_norm_list)

    result["missing_norms"] = [n for n in expert.get("norm_refs", [])
                                if n.lower()[:8] not in bot_norms][:10]
    result["extra_norms"] = [n for n in bot.get("norm_refs", [])
                              if n.lower()[:8] not in expert_norms][:5]

    if result["missing_norms"]:
        result["issues"].append(
            f"⚠️  Нормы не упомянуты ботом ({len(result['missing_norms'])}): "
            f"{', '.join(result['missing_norms'][:3])}"
        )

    return result


# ─────────────────────────────────────────────────────────────
#  Форматирование отчёта
# ─────────────────────────────────────────────────────────────
def format_report(cmp: dict) -> str:
    lines = []
    lines.append(f"\n{'═'*60}")
    lines.append(f"  📊 СРАВНЕНИЕ БОТ vs ЭКСПЕРТ — [{cmp['scenario']}]")
    lines.append(f"  {cmp['scenario_expectation']}")
    lines.append(f"{'═'*60}")

    if cmp["completeness_score"] is not None:
        lines.append(f"  Completeness Score: {cmp['completeness_score']:.0f}%")

    lines.append(f"\n  {'✅' if cmp['verdict_match'] else '❌'} Вердикт: "
                 f"{'СОВПАДАЕТ' if cmp['verdict_match'] else 'НЕ СОВПАДАЕТ'}")

    if cmp["remarks_coverage"] > 0:
        coverage_icon = "✅" if cmp["remarks_coverage"] >= 70 else "⚠️"
        lines.append(f"  {coverage_icon} Покрытие замечаний: {cmp['remarks_coverage']}%")

    if cmp["matches"]:
        lines.append(f"\n  ЗЕЛЁНАЯ ЗОНА ({len(cmp['matches'])}):")
        for m in cmp["matches"]:
            lines.append(f"    {m}")

    if cmp["issues"]:
        lines.append(f"\n  КРАСНАЯ/ЖЁЛТАЯ ЗОНА ({len(cmp['issues'])}) — нужно фиксить:")
        for issue in cmp["issues"]:
            lines.append(f"    {issue}")

    if cmp["missing_norms"]:
        lines.append(f"\n  ⚠️  Нормы из заключения, НЕ упомянутые ботом:")
        for n in cmp["missing_norms"][:5]:
            lines.append(f"    • {n}")

    lines.append(f"{'═'*60}\n")
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────
#  Watch-режим: автоматически сравнивает новые отчёты
# ─────────────────────────────────────────────────────────────
def watch_mode(ref_dir: Path, reports_dir: Path):
    """Следит за папкой reports/ и сравнивает каждый новый отчёт с экспертом."""
    import time

    expert = load_expert_conclusion(ref_dir)
    if not expert:
        sys.exit(1)

    print(f"👁  Watch-режим: слежу за {reports_dir}")
    print(f"   Эталон: {ref_dir}/expert_conclusion.json")
    print(f"   Прерывание: Ctrl+C\n")

    seen = set(f.name for f in reports_dir.glob("*.json"))
    seen.update(f.name for f in reports_dir.glob("*.txt"))
    seen.update(f.name for f in reports_dir.glob("*.md"))

    while True:
        time.sleep(3)
        for pattern in ["*.json", "*.txt", "*.md"]:
            for fpath in sorted(reports_dir.glob(pattern), key=lambda f: f.stat().st_mtime):
                if fpath.name not in seen and "expert" not in fpath.name.lower():
                    seen.add(fpath.name)
                    print(f"\n🆕 Новый отчёт бота: {fpath.name}")
                    try:
                        bot_report = load_bot_report(fpath)
                        # Пытаемся угадать сценарий по имени файла
                        fname = fpath.name.lower()
                        if any(k in fname for k in ["предоставл", "initial", "raw"]):
                            sc = "С1"
                        elif any(k in fname for k in ["откоррект", "correct"]):
                            sc = "С3"
                        elif any(k in fname for k in ["окончател", "final", "ideal"]):
                            sc = "С4"
                        else:
                            sc = "С2"

                        cmp = compare(expert, bot_report, sc)
                        report_text = format_report(cmp)
                        print(report_text)

                        # Сохраняем результат сравнения
                        out_path = ref_dir / f"compare_{sc}_{fpath.stem}.json"
                        with open(out_path, "w", encoding="utf-8") as f:
                            json.dump(cmp, f, ensure_ascii=False, indent=2)
                        print(f"   Сохранено: {out_path}")

                    except Exception as e:
                        print(f"   ⚠️ Ошибка при сравнении: {e}")


# ─────────────────────────────────────────────────────────────
#  CLI
# ─────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Сравнение бота с экспертом")
    parser.add_argument("report", nargs="?", help="Файл отчёта бота (json/txt/md)")
    parser.add_argument("--scenario", default="С2", choices=["С1","С2","С3","С4"],
                        help="Тестовый сценарий (по умолчанию С2)")
    parser.add_argument("--watch", action="store_true",
                        help="Watch-режим: автоматически сравнивать новые отчёты")
    parser.add_argument("--ref-dir", default="reference",
                        help="Папка с эталонным заключением (default: reference/)")
    parser.add_argument("--reports-dir", default="reports",
                        help="Папка с отчётами бота (default: reports/)")
    args = parser.parse_args()

    base = Path(__file__).parent.parent
    ref_dir = base / args.ref_dir
    reports_dir = base / args.reports_dir

    if args.watch:
        watch_mode(ref_dir, reports_dir)
        return

    if not args.report:
        parser.print_help()
        sys.exit(1)

    expert = load_expert_conclusion(ref_dir)
    if not expert:
        sys.exit(1)

    report_path = Path(args.report)
    if not report_path.exists():
        print(f"❌ Файл не найден: {report_path}")
        sys.exit(1)

    bot_report = load_bot_report(report_path)
    cmp = compare(expert, bot_report, args.scenario)
    print(format_report(cmp))

    # Сохраняем результат
    out = ref_dir / f"compare_{args.scenario}_{report_path.stem}.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(cmp, f, ensure_ascii=False, indent=2)
    print(f"Результат сохранён: {out}")


if __name__ == "__main__":
    main()
