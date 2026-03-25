"""
Web-интерфейс (чат) для анализа проектной документации.
МособлГосЭкспертиза — DocumentAnalyzer v2.0

Заменяет Telegram-бота. Вся бизнес-логика взята из bot.py.

Запуск:
    python3 web_app.py
    # → Открыть http://localhost:8000
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import shutil
import sys
import tempfile
import textwrap
import zipfile
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, UploadFile, File, BackgroundTasks
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

# ── Загружаем .env ────────────────────────────
load_dotenv()

XSD_VERSION = os.getenv("XSD_VERSION", "01.06")
XSD_MINIMUM_VERSION = "01.05"  # Приказ Минстроя №421/пр от 28.03.2025
WEB_PORT = int(os.getenv("WEB_PORT", "8000"))

# ── Логирование ───────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("bot.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("moexp_web")

# ── Пользовательские сессии ───────────────────
user_sessions: dict[str, dict[str, bytes]] = defaultdict(dict)
debug_enabled: bool = True  # debug по умолчанию включен

# ── Реестр WebSocket-соединений (session_id → ws) ──
# Нужен, чтобы POST /upload мог отправлять результаты через WS
ws_connections: dict[str, WebSocket] = {}

# ── RAG ───────────────────────────────────────
_rag_search = None

def _get_rag_search():
    global _rag_search
    if _rag_search is None:
        try:
            from rag_search import NormSearch
            _rag_search = NormSearch()
            log.info("✅ RAG NormSearch инициализирован")
        except Exception as exc:
            log.warning(f"⚠️ RAG недоступен: {exc}")
    return _rag_search


def _h(text: str) -> str:
    """Экранирование HTML."""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _version_gte(version: str, minimum: str) -> bool:
    try:
        v = [int(x) for x in version.split(".")]
        m = [int(x) for x in minimum.split(".")]
        return v >= m
    except (ValueError, AttributeError):
        return False


# ═══════════════════════════════════════════════
#  Функции форматирования (из bot.py)
# ═══════════════════════════════════════════════

def _format_per_file(result) -> str:
    lines = [f"📂 Анализ состава пакета ({result.total_files} файла/файлов):", ""]

    file_type_icons = {
        "xml_pz":    "📄 XML ПЗ",
        "xml_other": "📄 XML (ТЗ/Иное)",
        "pdf_text":  "📃 PDF (текст)",
        "pdf_scan":  "🖼️ PDF (скан)",
        "estimate":  "💰 Смета",
        "drawing":   "📐 Чертёж",
        "archive":   "📦 Архив",
        "sig":       "🔐 ЭЦП (.sig)",
        "unknown":   "❓ Неизвестный",
    }

    display_files = result.files[:30]
    for i, f in enumerate(display_files, 1):
        ft = f.file_type.lower() if f.file_type else "unknown"
        icon = file_type_icons.get(ft, "❓")
        size = f.size_kb
        scan_note = " (скан)" if f.is_scan else ""
        section = f" → Раздел: {f.suspected_section}" if f.suspected_section else ""
        lines.append(f"{i}. {icon} {f.name}  [{size:.0f} КБ{scan_note}]{section}")

    if len(result.files) > 30:
        lines.append(f"    ...и ещё {len(result.files) - 30} файлов скрыто")

    lines.append("")

    # XML ПЗ карточка
    if result.xml_summary:
        x = result.xml_summary
        version_ok = _version_gte(x.schema_version, XSD_MINIMUM_VERSION)
        version_icon = "✅" if version_ok else "❌"
        validity_icon = "✅" if x.is_valid else "⚠️"
        validity_text = "пройдена" if x.is_valid else f"ошибок: {len(x.validation_errors)}"

        lines += [
            "📄 Пояснительная записка (XML):",
            f"  Шифр: {x.cipher or '—'}",
            f"  Год: {x.year or '—'}",
            f"  {version_icon} Версия XSD: v{x.schema_version}"
            + ("" if version_ok else f" — ❗ требуется ≥ v{XSD_MINIMUM_VERSION}"),
            f"  {validity_icon} XSD-валидация: {validity_text}",
            f"  🏗 Объект: {(x.object_name or '—')[:120]}",
        ]

        if not x.is_valid and x.validation_errors:
            lines.append("  Первые ошибки валидации:")
            for err in x.validation_errors[:3]:
                lines.append(f"    — {str(err)[:120]}")

        ce = x.chief_engineer
        if ce and (ce.full_name or ce.snils or ce.nopriz_id):
            snils_txt = ("✅ " + ce.snils) if ce.snils_present else "❌ не найден"
            nopriz_txt = ("✅ " + ce.nopriz_id) if ce.nopriz_id_present else "❌ не найден"
            lines += [
                "",
                "👷 ГИП (Главный Инженер Проекта):",
                f"  ФИО: {ce.full_name or '—'}",
                f"  СНИЛС: {snils_txt}",
                f"  НОПРИЗ: {nopriz_txt}",
            ]

    return "\n".join(lines)


def _format_summary(result, package_name: str, elapsed: float) -> str:
    verdict_emoji = {
        "APPROVED":       "✅",
        "RETURNED":       "❌",
        "PENDING_EXPERT": "🚴",
        "FAILED":         "🔴",
    }.get(result.verdict, "❓")

    verdict_ru = {
        "APPROVED":       "Одобрено — замечаний не выявлено",
        "RETURNED":       "Возвращено на доработку",
        "PENDING_EXPERT": "На экспертной проверке",
        "FAILED":         "Ошибка обработки",
    }.get(result.verdict, result.verdict)

    lines = [
        "═══════════════════════",
        "📋 ИТОГОВОЕ ЗАКЛЮЧЕНИЕ",
        f"📁 {package_name}",
        f"⏱ Время обработки: {elapsed:.1f} сек.",
        "",
        f"{verdict_emoji} Статус: {verdict_ru}",
        f"{result.verdict_reason}",
        "",
        "─────────────────────",
        "🔍 Формальные проверки (FC):",
    ]

    if result.formal_check:
        fc = result.formal_check
        checks = [
            ("FC-001", fc.xml_found,                    "XML Пояснительной записки"),
            ("FC-002", fc.xml_version_ok,               f"Версия XSD ≥ {XSD_MINIMUM_VERSION}"),
            ("FC-003", fc.iul_present,                  "ИУЛ в документах"),
            ("FC-005", len(fc.missing_sections) == 0,   "Комплектность разделов ПД"),
        ]
        for code, ok, label in checks:
            icon = "✅" if ok else ("⚠️" if code == "FC-003" else "❌")
            lines.append(f"  {icon} {code}: {label}")

        for issue in fc.issues:
            if issue.code == "FC-UIN":
                icon = "✅" if issue.severity == "info" else "⚠️"
                lines.append(f"  {icon} FC-UIN: {issue.message[:120]}")
            elif issue.code == "FC-CRC":
                icon = "✅" if issue.severity == "info" else "⚠️"
                lines.append(f"  {icon} FC-CRC: {issue.message[:120]}")

        lines.append("")
        if fc.critical_count > 0:
            lines.append(f"🔴 Критических замечаний: {fc.critical_count}")
            for issue in fc.issues:
                if issue.severity == "critical":
                    lines.append(f"  • [{issue.code}] {issue.message}")

        if fc.warning_count > 0:
            lines.append(f"🟡 Предупреждений: {fc.warning_count}")
            for issue in fc.issues:
                if issue.severity == "warning":
                    lines.append(f"  • [{issue.code}] {issue.message}")

        if fc.missing_sections:
            section_names = {
                "01.01": "Пояснительная записка",
                "02.01": "Схема планировочной организации (СПОЗУ)",
                "03.01": "Архитектурные решения (АР)",
                "04.01": "Конструктивные решения (КР)",
                "05.01": "Инженерное оборудование (ИС)",
                "10.01": "Пожарная безопасность (ПБ)",
                "11.01": "Смета на строительство (ССР)",
            }
            lines += ["", "📂 Отсутствующие обязательные разделы:"]
            for sec in fc.missing_sections[:7]:
                name = section_names.get(sec, "")
                lines.append(f"  — {sec} {name}")

    # ── PP963 ───────────────────────────────
    if result.pp963_report:
        pp = result.pp963_report

        has_api_error = False
        if pp.tep_discrepancies:
            for d in pp.tep_discrepancies:
                if "Error" in d or "403" in d or "Forbidden" in d:
                    has_api_error = True
                    break

        if has_api_error:
            tep_icon = "⚠️"
            status_text = "ошибка LLM API!"
        else:
            tep_icon = "✅" if pp.tep_compliant else "❌"
            status_text = "совпадает" if pp.tep_compliant else "расхождения!"

        lines += ["", "─────────────────────",
                   "🕵️ Проверка ПП №963 (ТЭП):",
                   f"  {tep_icon} Кросс-валидация ТЭП: {status_text}"]

        if pp.tep_discrepancies:
            tep_only = [d for d in pp.tep_discrepancies if not d.startswith("[ГПЗУ") and not d.startswith("[ТУ")]
            gpzu_items = [d for d in pp.tep_discrepancies if d.startswith("[ГПЗУ")]
            tu_items = [d for d in pp.tep_discrepancies if d.startswith("[ТУ")]

            for d in tep_only[:3]:
                lines.append(f"  ⚠️ {d[:150]}")

            if gpzu_items:
                lines.append("")
                lines.append("  📐 Кросс-проверка ГПЗУ↔ПЗ:")
                for d in gpzu_items[:3]:
                    lines.append(f"    ⚠️ {d[:150]}")

            if tu_items:
                lines.append("")
                lines.append("  🔧 Кросс-проверка ТУ↔ИОС:")
                for d in tu_items[:3]:
                    lines.append(f"    ⚠️ {d[:150]}")

        if pp.sections_checked > 0:
            lines.append(f"  📋 Разделов проверено: {pp.sections_passed}/{pp.sections_checked}")

        if pp.sections:
            for sec in pp.sections:
                s_icon = "✅" if sec.passed else "❌"
                conf_str = f" ({sec.confidence:.0%})" if sec.confidence > 0 else ""
                lines.append(f"    {s_icon} {sec.section_code}. {sec.section_name[:60]}{conf_str}")
                if not sec.passed and sec.remarks:
                    for r in sec.remarks[:2]:
                        lines.append(f"      💬 {r[:120]}")
                if sec.norm_refs:
                    norms_str = ", ".join(sec.norm_refs[:3])
                    if len(sec.norm_refs) > 3:
                        norms_str += f" (+{len(sec.norm_refs)-3})"
                    lines.append(f"      📎 {norms_str[:120]}")

        if pp.llm_model:
            lines.append(f"  🤖 Модель: {pp.llm_model}")

    # ── Сверка ТЗ/ПЗ ────────────────────────
    if getattr(result, "sverka_check", None):
        sv = result.sverka_check
        if sv.error:
            lines += ["", "─────────────────────",
                       "📊 Сверка ТЗ/ПЗ (Таблица Владимира):",
                       f"  ⚠️ Сверка не выполнена: {sv.error[:100]}"]
        else:
            sv_icon = "✅" if sv.is_compliant else ("⚠️" if sv.compliance_rate >= 0.5 else "❌")
            lines += ["", "─────────────────────",
                       "📊 Сверка ТЗ/ПЗ (Таблица Владимира):",
                       f"  {sv_icon} Соответствует: {sv.compliant_count}/{sv.total_items} требований ({sv.compliance_rate:.0%})",
                       f"  ❌ Нарушений: {sv.non_compliant_count}  ⚠️ Пропущено: {sv.skipped_count}"]

            def is_valid_req(req: str) -> bool:
                r = req.strip()
                return len(r) > 3 and r != "-" and r.lower() != "нет"

            violations = [i for i in sv.items if i.compliant is False and is_valid_req(i.requirement)]
            skipped = [i for i in sv.items if i.compliant is None and is_valid_req(i.requirement)]

            if violations:
                lines.append("  Список нарушений:")
                for v in violations:
                    lines.append(f"  ❌ {v.requirement[:150].strip()}")

            if skipped:
                lines.append("  Пропущено (нет данных):")
                for s in skipped:
                    lines.append(f"  ⚠️ {s.requirement[:150].strip()}")

    # ── Смета ────────────────────────────────
    if getattr(result, "estimate_report", None):
        est = result.estimate_report
        lines += ["", "─────────────────────",
                   "💰 Сметная документация (Раздел 11/12):"]
        if not est.found:
            lines.append("  ❌ Сметная документация не обнаружена (ССР, ЛСР)")
        else:
            files_short = ", ".join(est.estimate_files[:3])
            if len(est.estimate_files) > 3:
                files_short += f" (+{len(est.estimate_files)-3})"
            lines.append(f"  ✅ Найдено файлов: {len(est.estimate_files)} — {files_short}")
            if est.ssr_approved is True:
                lines.append("  ✅ ССР утверждён застройщиком")
            elif est.ssr_approved is False:
                lines.append("  ❌ ССР НЕ утверждён застройщиком (нет грифа «Утверждаю»)")
            else:
                lines.append("  ⚠️ ССР не найден или текст недоступен")
            for issue in est.issues[:3]:
                lines.append(f"  ⚠️ {issue}")

    # ── НОПРИЗ ───────────────────────────────
    if result.nopriz_check:
        nr = result.nopriz_check
        if nr.found is True:
            is_active = nr.status == "active"
            nr_icon = "✅" if is_active else "⚠️"
            status_str = "Действует" if is_active else "Не действует"
            nr_text = f"ГИП найден в реестре НОПРИЗ ({nr.reg_number}) — {status_str}"
        elif nr.found is False:
            nr_icon = "❌"
            nr_text = "ГИП НЕ найден в реестре НОПРИЗ"
        else:
            nr_icon = "⚠️"
            nr_text = "Проверка НОПРИЗ не завершена"
        lines += ["", "─────────────────────",
                   "🌐 Проверка НОПРИЗ (пп. 66-67):",
                   f"  {nr_icon} {nr_text}"]
        if nr.fio:
            lines.append(f"  👷 {nr.fio}")

    lines += ["", "─────────────────────",
              "МособлГосЭкспертиза | DocumentAnalyzer v2.0"]

    return "\n".join(lines)


def _format_debug_report(result, elapsed: float) -> str:
    from src.agents.groq_client import MODEL_USAGE_COUNTERS

    lines = ["🔬 DEBUG — Архитектура Агентов (Маршрутизация)", ""]

    # Orchestrator
    lines.append("🧠 [АГЕНТ 1] Orchestrator (Маршрутизатор)")
    lines.append("  [ВХОД] Имена и размеры файлов пакета, а также извлечённый текст из PDF (до 50000 символов со всех PDF)")
    run_154 = "Да" if getattr(result, "pp154_report", None) else "Нет (не профильный объект)"
    lines.append(f"  [ВЫХОД] План выполнения: PP963=Да, PP154={run_154}")
    lines.append("")

    # Document Analyzer
    lines.append("📁 [АГЕНТ 2] Document Analyzer (FileClassifier + XmlParser + FormalCheck)")
    lines.append("  [ВХОД] Сырой ZIP-архив с ПД")
    lines.append(f"  [ВЫХОД] Файлов классифицировано: {result.total_files} (XML: {result.xml_files_count}, PDF: {result.pdf_files_count}, Сканы: {result.scan_files_count})")

    if getattr(result, "files", None):
        lines.append("  [ВЫХОД] Список принятых документов:")
        for f in result.files[:30]:
            lines.append(f"    • {f.name}")
        if len(result.files) > 30:
            lines.append(f"    ...и ещё {len(result.files) - 30} файлов скрыто")

    if result.xml_summary:
        xs = result.xml_summary
        lines.append(f"  [ВЫХОД] Метаданные: Шифр={xs.cipher}, XSD=v{xs.schema_version}")

    if result.formal_check:
        fc = result.formal_check
        lines.append(f"  [ВЫХОД] Формальные проверки: {getattr(fc, 'rules_checked', '?')} правил. Ошибок: {fc.critical_count} шт.")
    lines.append("")

    # PP963 + RAG
    lines.append("🕵️ [АГЕНТ 3 + 5] PP963 Compliance & Knowledge Base (RAG)")
    lines.append("  [ВХОД] ТЭП из XML, текст PDF-файлов")
    if result.pp963_report:
        pp = result.pp963_report
        lines.append(f"  [ВЫХОД] PP963: ТЭП консистентны? {'✅ Да' if pp.tep_compliant else '❌ Нет'}")
        rag_queried = sum(1 for s in pp.sections if s.norm_refs) if pp.sections else 0
        lines.append(f"  [ВЫХОД] База Знаний: Сделано {rag_queried}/{pp.sections_checked} RAG-запросов к Weaviate.")
        lines.append(f"  [ВЫХОД] База Знаний: Найдено норм: {pp.rag_chunks_used} шт.")
        if pp.sections:
            passed = sum(1 for s in pp.sections if s.passed)
            lines.append(f"  [ВЫХОД] Итог по разделам: {passed}/{len(pp.sections)} прошли проверку")
    else:
        lines.append("  ⚠️ Не запускался или упал")
    lines.append("")

    # PP154
    lines.append("🏭 [АГЕНТ 4] PP154 Compliance (Теплоснабжение)")
    lines.append("  [ВХОД] Текст ПД, распознанный OCR (pdf/doc)")
    if getattr(result, "pp154_report", None):
        p154 = result.pp154_report
        eb_str = "Нет данных"
        if getattr(p154, "energy_balance", None):
            eb = p154.energy_balance
            eb_icon = "✅" if eb.is_compliant else ("⚠" if not eb.math_done else "❌")
            eb_str = f"Ист: {eb.source_mw}МВт -> Потребитель: {eb.load_mw}МВт + Потери {eb.loss_mw}МВт (Невязка: {eb.imbalance_pct:.1f}%) {eb_icon}"
        lines.append(f"  [ВЫХОД] Энергобаланс (математика): {eb_str}")
        lines.append(f"  [ВЫХОД] Горизонт: {p154.horizon_years} лет {'✅' if p154.horizon_ok else '❌'}")
        lines.append(f"  [ВЫХОД] Разделы: {len(p154.sections_found)}/13 найдено")
    else:
        lines.append("  ⏭️ [ВЫХОД] Пропущен (Оркестратор решил, что это не промышленный объект/теплосеть)")
    lines.append("")

    # Estimate
    lines.append("")
    lines.append("💰 [АГЕНТ 4.5] Estimate Checker (Сметная документация)")
    lines.append("  [ВХОД] Имена и размеры файлов пакета, извлечённый текст из PDF (для поиска по ключевым словам и подписям)")
    if getattr(result, "estimate_report", None):
        est = result.estimate_report
        lines.append(f"  [ВЫХОД] Раздел найден: {'✅ Да' if est.found else '❌ Нет'}")
        if est.found:
            lines.append(f"  [ВЫХОД] Найдено файлов: {len(est.estimate_files)} шт.")
            if est.ssr_approved is True:
                lines.append("  [ВЫХОД] Утверждение ССР: ✅ Подтверждено")
            elif est.ssr_approved is False:
                lines.append("  [ВЫХОД] Утверждение ССР: ❌ ССР НЕ утвержден застройщиком")
            else:
                lines.append("  [ВЫХОД] Утверждение ССР: ⚠️ ССР не найден или нет текста")
            if est.issues:
                lines.append(f"  [ВЫХОД] Замечаний: {len(est.issues)} шт.")
    else:
        lines.append("  ⏭️ [ВЫХОД] Не запускался или упал")
    lines.append("")

    # НОПРИЗ
    lines.append("🌐 [АГЕНТ 7] Human-in-the-Loop & Внешние интеграции (НОПРИЗ)")
    if result.xml_summary and result.xml_summary.chief_engineer:
        lines.append(f"  [ВХОД] СНИЛС ГИПа: {result.xml_summary.chief_engineer.snils}")
    else:
        lines.append("  [ВХОД] СНИЛС ГИПа не извлечен")

    if result.nopriz_check:
        nc = result.nopriz_check
        if nc.status == "skipped":
            lines.append("  [ВЫХОД] ⚠️ Данные ГИП отсутствуют в XML — проверка НОПРИЗ пропущена")
        elif nc.found == True:
            lines.append(f"  [ВЫХОД] ✅ ГИП найден в реестре НОПРИЗ ({nc.reg_number or 'Без номера'})")
            lines.append(f"  ФИО: {nc.fio} | Статус: {nc.status}")
        elif nc.found == False:
            lines.append(f"  [ВЫХОД] ❌ ГИП НЕ найден (ФИО: {nc.fio}, Рег.№: {nc.reg_number})")
        else:
            lines.append(f"  [ВЫХОД] ⚠️ {nc.message or nc.status}")
    else:
        lines.append("  [ВЫХОД] ⚠️ Ошибка проверки или данные не найдены")
    lines.append("")

    # Report Generator
    lines.append("📄 [АГЕНТ 6] Report Generator")
    lines.append("  [ВХОД] Выборки ВСЕХ предыдущих агентов")
    has_pdf = "✅ Сгенерирован (PDF)" if result.pdf_report else "❌ Ошибка"
    lines.append(f"  [ВЫХОД] Отчёт: {has_pdf}")
    lines.append("")

    # Вердикт
    verdict = getattr(result, "verdict", "Н/Д")
    reason = getattr(result, "verdict_reason", "Причина не указана")
    verdict_emoji = {
        "POSITIVE": "✅", "NEGATIVE": "❌",
        "PENDING_EXPERT": "🚴", "RETURNED_FOR_REVISION": "⚠️"
    }.get(verdict, "❓")

    lines.append("⚖️ [РЕЗУЛЬТАТ] Итоговое заключение")
    lines.append(f"  Статус: {verdict_emoji} {verdict}")
    lines.append(f"  Причина: {reason}")
    lines.append("")

    # Мониторинг
    lines.append("📊 [МОНИТОРИНГ] Использование LLM")
    for model_id, count in MODEL_USAGE_COUNTERS.items():
        if count > 0:
            lines.append(f"  {model_id}: {count} вызовов")
    if not any(c > 0 for c in MODEL_USAGE_COUNTERS.values()):
        lines.append("  Счетчики по нулям")
    lines.append("")

    lines.append(f"⏱ Общее время: {elapsed:.1f}с")
    lines.append(f"📦 Файлов в пакете: {result.total_files}")
    lines.append("")
    lines.append("Выключить: /debug")

    return "\n".join(lines)


# ═══════════════════════════════════════════════
#  FastAPI приложение
# ═══════════════════════════════════════════════
app = FastAPI(title="МособлГосЭкспертиза — DocumentAnalyzer v2.0")


@app.get("/", response_class=HTMLResponse)
async def index():
    """Отдаёт главную страницу чата."""
    html_path = Path(__file__).parent / "web" / "index.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


# ── WebSocket чат ─────────────────────────────
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    session_id = str(uuid4())[:8]
    log.info(f"[WS] Новое подключение: {session_id}")

    # Регистрируем WS в глобальном реестре
    ws_connections[session_id] = ws

    # Сообщаем клиенту его session_id (для /upload)
    await ws.send_text(f"__SESSION__{session_id}")

    # Отправляем приветствие
    await ws.send_text(_welcome_message())
    timestamp = datetime.now().strftime("%H:%M:%S")
    await ws.send_text(f"[{timestamp}] 👤 Подключён к DocumentAnalyzer v2.0")

    try:
        while True:
            data = await ws.receive_text()
            data = data.strip()
            if not data:
                continue

            log.info(f"[WS:{session_id}] → {data[:100]}")

            # Роутинг команд
            if data.startswith("/"):
                await _handle_command(ws, session_id, data)
            else:
                await ws.send_text("💡 Введите команду, например /help или /status\nДля анализа: /local_zip <путь к ZIP> или прикрепите файл 📎")

    except WebSocketDisconnect:
        log.info(f"[WS] Отключён: {session_id}")
        ws_connections.pop(session_id, None)
    except Exception as e:
        log.error(f"[WS:{session_id}] Ошибка: {e}", exc_info=True)
        ws_connections.pop(session_id, None)


def _welcome_message() -> str:
    return textwrap.dedent(f"""\
    👋 Добро пожаловать!

    🏛 МособлГосЭкспертиза — Анализ проектной документации
    Регламент: XML-схема ≥ v{XSD_MINIMUM_VERSION} (Приказ Минстроя №421/пр от 28.03.2025)

    📦 Как пользоваться:
    Способ 1 — ZIP-архив (предпочтительный):
    Упакуйте весь пакет ПД в один ZIP и отправьте.

    Способ 2 — Отдельные файлы (Корзина):
    Отправляйте файлы по одному или группой.
    Когда все готово — нажмите [🚀 Запустить проверку].

    🔎 Поиск по нормативной базе:
    /search — найти нормы (СП, ФЗ, ГОСТ) по запросу
    Пример: /search ширина пути эвакуации

    🔍 Что проверяю:
    • FC-001 — наличие XML Пояснительной записки
    • FC-002 — версия XSD-схемы ≥ {XSD_MINIMUM_VERSION}
    • FC-003 — наличие ИУЛ
    • FC-004 — признак ЭЦП/XMLDsig
    • FC-005 — комплектность разделов ПД (пп. 72, 84)
    • FC-006 — имена файлов (Приказ №783/пр)

    /basket — посмотреть что в корзине
    /help — справка
    /status — статус системы""")


async def _handle_command(ws: WebSocket, session_id: str, data: str):
    """Маршрутизация команд."""
    global debug_enabled

    parts = data.split(maxsplit=1)
    cmd = parts[0].lower()
    args = parts[1] if len(parts) > 1 else ""

    if cmd == "/start":
        await ws.send_text(_welcome_message())

    elif cmd == "/help":
        await ws.send_text(textwrap.dedent(f"""\
        📖 Справка

        Нормативное требование к версии XML ПЗ:
        — Минимально допустимая версия XSD: {XSD_MINIMUM_VERSION} (с 28.03.2025)
        — Версия 01.04 и ниже: ❌ ВОЗВРАТ (устарела)
        — Версия 01.05: ✅ принята (действующий стандарт)
        — Версия 01.06: ✅ принята (новая версия)

        Вердикты:
        ✅ APPROVED — формальный контроль пройден
        🔄 PENDING_EXPERT — требуется проверка эксперта
        ❌ RETURNED — пакет возвращён на доработку

        Нормативная база:
        — ПП РФ №963 (от 01.09.2022): комплектность ПД
        — Приказ Минстроя №783/пр: именование файлов
        — Приказ Минстроя №421/пр: XSD v01.05 (с 28.03.2025)"""))

    elif cmd == "/status":
        rag_status = "🟢 доступен" if _get_rag_search() else "🔴 Weaviate не запущен"
        await ws.send_text(textwrap.dedent(f"""\
        ⚙️ Статус системы

        🟢 Сервер: работает
        📐 Мин. версия XSD: {XSD_MINIMUM_VERSION} (по Приказу №421/пр)
        📐 Целевая версия XSD: {XSD_VERSION}
        🕐 Время: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}
        📚 RAG: {rag_status}
        🔬 Debug-режим: {'🟢 ВКЛ' if debug_enabled else '🔴 ВЫКЛ'}"""))

    elif cmd == "/basket":
        files = user_sessions.get(session_id, {})
        if not files:
            await ws.send_text("🧺 Ваша корзина пуста.\nОтправьте файлы (XML, PDF и т.д.) чтобы добавить их в пакет.")
        else:
            file_list = "\n".join(
                f"  {i+1}. {name} ({len(data) // 1024} КБ)"
                for i, (name, data) in enumerate(files.items())
            )
            await ws.send_text(f"🧺 Ваш пакет документов ({len(files)} файлов):\n\n{file_list}\n\nОтправьте /run чтобы запустить анализ или /clear чтобы очистить.")

    elif cmd == "/clear":
        user_sessions.pop(session_id, None)
        await ws.send_text("🗑 Корзина очищена. Можете добавлять файлы заново.")

    elif cmd == "/debug":
        debug_enabled = not debug_enabled
        if debug_enabled:
            await ws.send_text("🟢 Debug-режим ВКЛЮЧЁН\n\nПосле каждого анализа будет подробный отчёт по агентам.\nВыключить: /debug")
        else:
            await ws.send_text("🔴 Debug-режим ВЫКЛЮЧЕН\nПосле анализа — только стандартный вывод.")

    elif cmd == "/search":
        await _cmd_search(ws, args)

    elif cmd == "/local_zip":
        await _cmd_local_zip(ws, session_id, args)

    elif cmd == "/run":
        await _cmd_run(ws, session_id)

    elif cmd == "/hitl":
        await _cmd_hitl(ws)

    else:
        await ws.send_text(f"❓ Неизвестная команда: {cmd}\nВведите /help для списка команд.")


async def _cmd_search(ws: WebSocket, query: str):
    if not query.strip():
        await ws.send_text(
            "🔎 Укажите запрос после команды.\n"
            "Примеры:\n"
            "  /search ширина пути эвакуации школа\n"
            "  /search СП 42 таблица расстояния от застройки\n"
            "  /search состав проектной документации раздел 5"
        )
        return

    s = _get_rag_search()
    if s is None:
        await ws.send_text("⚠️ База нормативных документов (Weaviate) недоступна.\nУбедитесь, что Docker-контейнер moexp_weaviate запущен.")
        return

    log.info(f"RAG /search: '{query}'")

    try:
        results = s.hybrid(query, top_k=3, alpha=0.5)
    except Exception as exc:
        log.error(f"RAG поиск упал: {exc}", exc_info=True)
        await ws.send_text(f"❌ Ошибка поиска: {str(exc)[:200]}")
        return

    if not results:
        await ws.send_text(f"🔍 По запросу «{query}» ничего не найдено.\nПопробуйте переформулировать запрос.")
        return

    lines = [f"🔎 Поиск по нормативной базе: «{query}»\n"]
    for i, r in enumerate(results, 1):
        table_badge = " 📊" if r.is_table else ""
        section_path = f"\n  {r.breadcrumb[:100]}" if r.breadcrumb and r.breadcrumb != r.doc_title else ""
        preview = r.raw_text[:350].replace("\n", " ")
        lines.append(
            f"{i}. {r.doc_title[:70]}{table_badge}{section_path}\n"
            f"  {preview}{'...' if len(r.raw_text) > 350 else ''}\n"
            f"  🔗 {r.source_url}\n"
        )

    await ws.send_text("\n".join(lines))


async def _cmd_local_zip(ws: WebSocket, session_id: str, zip_path: str):
    zip_path = zip_path.strip()
    if not zip_path:
        await ws.send_text(
            "🔎 Укажите абсолютный путь к ZIP-архиву.\n"
            "Пример: /local_zip /home/segun/Практика в машинном обучении/Test/распаковка отдельная.zip"
        )
        return

    path_obj = Path(zip_path)
    if not path_obj.exists() or not path_obj.is_file():
        await ws.send_text(f"❌ Файл не найден: {zip_path}")
        return
    if path_obj.suffix.lower() != ".zip":
        await ws.send_text("❌ Указанный файл не является ZIP-архивом.")
        return

    await ws.send_text(f"📦 Читаю локальный архив {path_obj.name}...\n📂 Путь: {zip_path}\n⏳ Извлекаю файлы в память...")

    try:
        added_count = 0
        total_size = 0
        extract_dir = tempfile.mkdtemp()

        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                for zinfo in zf.infolist():
                    if zinfo.is_dir() or "__MACOSX" in zinfo.filename or "Zone.Identifier" in zinfo.filename:
                        continue

                    # Фикс кодировки CP437 -> CP866 (кириллица Windows)
                    try:
                        raw_bytes = zinfo.filename.encode("cp437")
                        try:
                            name = raw_bytes.decode("utf-8")
                        except UnicodeDecodeError:
                            name = raw_bytes.decode("cp866")
                    except Exception:
                        name = zinfo.filename

                    fname = name.replace("\\", "/")
                    if not Path(name).name:
                        continue

                    safe_extracted_path = os.path.join(extract_dir, f"tmp_{added_count}.bin")
                    with zf.open(zinfo) as src, open(safe_extracted_path, "wb") as dst:
                        shutil.copyfileobj(src, dst)

                    with open(safe_extracted_path, "rb") as f:
                        file_data = f.read()
                    os.remove(safe_extracted_path)

                    user_sessions[session_id][fname] = file_data
                    added_count += 1
                    total_size += len(file_data)
        finally:
            shutil.rmtree(extract_dir, ignore_errors=True)

        size_mb = total_size / (1024 * 1024)
        timestamp = datetime.now().strftime("%H:%M:%S")

        await ws.send_text(
            f"[{timestamp}] 📥 Админ локально загрузил архив {path_obj.name} ({size_mb:.1f} МБ, {added_count} файлов)"
        )

        # Автоматически запускаем анализ (как в Telegram при загрузке ZIP)
        await ws.send_text("⏳ Собираю пакет и запускаю анализ...")
        await _run_analysis(ws, session_id, f"Пакет ({added_count} файлов)")

    except zipfile.BadZipFile:
        await ws.send_text("❌ Это повреждённый или многотомный ZIP-архив.")
    except Exception as e:
        log.error(f"Ошибка при чтении {zip_path}: {e}", exc_info=True)
        await ws.send_text(f"❌ Ошибка: {str(e)[:300]}")


async def _cmd_run(ws: WebSocket, session_id: str):
    """Запуск анализа из корзины."""
    files = user_sessions.get(session_id, {})
    if not files:
        await ws.send_text("⚠️ Корзина пуста! Сначала загрузите файлы через /local_zip")
        return

    await ws.send_text("⏳ Собираю пакет и запускаю анализ...")
    await _run_analysis(ws, session_id, f"Пакет ({len(files)} файлов)")


async def _run_analysis(ws: WebSocket, session_id: str, package_name: str):
    """Единый метод запуска пайплайна (аналог _run_analysis_and_report из bot.py)."""
    global debug_enabled

    files = user_sessions.get(session_id, {})
    if not files:
        await ws.send_text("⚠️ Нет файлов для анализа.")
        return

    # Собираем ZIP
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "a", zipfile.ZIP_DEFLATED, False) as zf:
        for fname, fdata in files.items():
            zf.writestr(fname, fdata)
    zip_bytes = zip_buf.getvalue()

    # Очищаем корзину
    user_sessions.pop(session_id, None)

    from src.api.pipeline import _run_pipeline
    start = datetime.now()

    try:
        result = await _run_pipeline(uuid4(), zip_bytes)
    except Exception as exc:
        log.error(f"Пайплайн упал с ошибкой: {exc}", exc_info=True)
        await ws.send_text(f"❌ Ошибка при анализе пакета:\n{str(exc)[:300]}")
        return

    elapsed = (datetime.now() - start).total_seconds()

    try:
        # Часть 1: анализ файлов
        per_file_msg = _format_per_file(result)
        if per_file_msg:
            await ws.send_text(per_file_msg)

        # Часть 2: итоговое заключение
        summary_msg = _format_summary(result, package_name, elapsed)
        await ws.send_text(summary_msg)

        # Часть 2б: PDF-заключение
        if getattr(result, "pdf_report", None):
            # Сохраняем PDF на диск для скачивания
            pdf_dir = Path(__file__).parent / "web" / "reports"
            pdf_dir.mkdir(exist_ok=True)
            pdf_name = f"Expertise_Report_{session_id}.pdf"
            pdf_path = pdf_dir / pdf_name
            with open(pdf_path, "wb") as f:
                f.write(result.pdf_report)
            await ws.send_text(f"📄 Официальное заключение по ГОСТ Р 7.0.97-2016\nМособлГосЭкспертиза — DocumentAnalyzer v2.0\n📥 Скачать: /reports/{pdf_name}")

        timestamp = datetime.now().strftime("%H:%M:%S")
        verdict = getattr(result, "verdict", "?")
        await ws.send_text(f"[{timestamp}] ✅ Готово: {package_name} | Вердикт: {verdict} | Время: {elapsed:.1f}с")

    except Exception as send_err:
        log.error(f"Ошибка при отправке результатов: {send_err}", exc_info=True)
        await ws.send_text("⚠️ Ошибка при формировании результатов. Проверьте логи.")

    # Часть 3: Debug
    if debug_enabled:
        await ws.send_text("🔬 DEBUG MODE — начало отчёта по агентам")
        debug_msg = _format_debug_report(result, elapsed)
        await ws.send_text(debug_msg)

    # Часть 4: Сверка титульных листов (test_first_page.py)
    try:
        await ws.send_text("📋 Запускаю сверку титульных листов...")
        await _run_title_page_check(ws, zip_bytes)
    except Exception as tp_err:
        log.error(f"Ошибка сверки титульных: {tp_err}", exc_info=True)
        await ws.send_text(f"⚠️ Сверка титульных не выполнена: {str(tp_err)[:200]}")

    timestamp = datetime.now().strftime("%H:%M:%S")
    await ws.send_text(f"[{timestamp}] 📦 🚀 Анализ завершён: {package_name}")


async def _run_title_page_check(ws: WebSocket, zip_bytes: bytes):
    """Запускает test_first_page.py для сверки имён файлов с титульными листами."""
    import subprocess

    # Сохраняем ZIP во временный файл
    tmp_zip = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    try:
        tmp_zip.write(zip_bytes)
        tmp_zip.close()

        script_path = Path(__file__).parent / "test_first_page.py"
        if not script_path.exists():
            await ws.send_text("⚠️ Скрипт test_first_page.py не найден")
            return

        # Запускаем скрипт
        proc = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(
                [sys.executable, str(script_path), tmp_zip.name],
                capture_output=True, text=True, timeout=120,
                cwd=str(Path(__file__).parent)
            )
        )

        # Выводим stdout скрипта
        if proc.stdout:
            log.info(f"test_first_page stdout:\n{proc.stdout}")

        if proc.returncode != 0:
            await ws.send_text(f"⚠️ test_first_page.py вернул ошибку:\n{proc.stderr[:300]}")
            return

        # Ищем последний созданный отчёт
        results_dir = Path(__file__).parent / "ResyltatTesta"
        if results_dir.exists():
            reports = sorted(results_dir.glob("*.md"), key=lambda f: f.stat().st_mtime, reverse=True)
            if reports:
                latest = reports[0]
                report_text = latest.read_text(encoding="utf-8")

                # Отправляем краткую сводку (таблицу)
                lines = report_text.split("\n")
                header_and_table = []
                in_table = False
                for line in lines:
                    if line.startswith("# "):
                        header_and_table.append(line)
                    elif line.startswith("| "):
                        in_table = True
                        header_and_table.append(line)
                    elif line.startswith("## Итого"):
                        header_and_table.append("")
                        in_table = False
                    elif in_table and not line.startswith("|"):
                        in_table = False
                    # Собираем итог
                    if line.startswith("- "):
                        header_and_table.append(line)

                await ws.send_text("\n".join(header_and_table))
                await ws.send_text(f"📝 Полный отчёт сохранён: ResyltatTesta/{latest.name}")

    finally:
        try:
            os.unlink(tmp_zip.name)
        except Exception:
            pass


async def _cmd_hitl(ws: WebSocket):
    """Показать HITL записи."""
    try:
        from src.db.database import SessionLocal
        from src.db.models import DisagreementLog

        db = SessionLocal()
        unreviewed = (
            db.query(DisagreementLog)
            .filter(DisagreementLog.is_reviewed == False)
            .order_by(DisagreementLog.created_at.desc())
            .limit(5)
            .all()
        )
        db.close()

        if not unreviewed:
            await ws.send_text("✅ Нет неотвеченных записей в Disagreement Log.\nВсе решения агентов подтверждены.")
            return

        lines = [f"⚠️ Записи HITL, ожидающие проверки ({len(unreviewed)}):\n"]
        for i, record in enumerate(unreviewed, 1):
            trigger = record.agent_name.split("/")[-1] if "/" in record.agent_name else "confidence"
            trigger_icon = {
                "confidence": "🔵", "critical_error": "🔴",
                "agent_disagreement": "🟡", "is_edge_case": "🟠",
            }.get(trigger, "⚪")

            lines.append(
                f"{trigger_icon} {i}. [{record.agent_name}]\n"
                f"  📄 Документ: {record.document_id[:30]}\n"
                f"  🎯 Уверенность: {record.confidence:.0%}\n"
                f"  💬 {record.ai_decision[:200]}{'...' if len(record.ai_decision) > 200 else ''}\n"
                f"  🕐 {record.created_at.strftime('%d.%m.%Y %H:%M') if record.created_at else '—'}\n"
            )

        await ws.send_text("\n".join(lines))

    except Exception as e:
        await ws.send_text(f"❌ Ошибка при запросе HITL: {str(e)[:200]}")


# ═══════════════════════════════════════════════
#  POST /upload — загрузка файлов через браузер
# ═══════════════════════════════════════════════
@app.post("/upload")
async def upload_files(
    files: list[UploadFile] = File(...),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    session_id: str = "",
):
    """
    Принимает файлы из браузера.
    - Если один ZIP → распаковать + запустить анализ
    - Иначе → добавить в корзину
    """
    messages = []
    action = "basket"  # или "analyze" если ZIP

    # session_id передаётся как query param из фронтенда
    # Если не передан — берём последнюю WS-сессию
    if not session_id and ws_connections:
        session_id = list(ws_connections.keys())[-1]
    elif not session_id:
        session_id = str(uuid4())[:8]

    # Читаем все файлы
    uploaded: dict[str, bytes] = {}
    for f in files:
        data = await f.read()
        uploaded[f.filename or "unknown"] = data

    # Проверяем: один ZIP?
    is_single_zip = (
        len(uploaded) == 1
        and list(uploaded.keys())[0].lower().endswith(".zip")
    )

    if is_single_zip:
        action = "analyze"
        zip_name = list(uploaded.keys())[0]
        zip_data = list(uploaded.values())[0]
        size_mb = len(zip_data) / (1024 * 1024)

        # Распаковываем ZIP в сессию
        try:
            added_count = 0
            total_size = 0

            with zipfile.ZipFile(io.BytesIO(zip_data), "r") as zf:
                for zinfo in zf.infolist():
                    if zinfo.is_dir() or "__MACOSX" in zinfo.filename or "Zone.Identifier" in zinfo.filename:
                        continue

                    try:
                        raw_bytes = zinfo.filename.encode("cp437")
                        try:
                            name = raw_bytes.decode("utf-8")
                        except UnicodeDecodeError:
                            name = raw_bytes.decode("cp866")
                    except Exception:
                        name = zinfo.filename

                    fname = name.replace("\\", "/")
                    if not Path(fname).name:
                        continue

                    file_data = zf.read(zinfo)
                    user_sessions[session_id][fname] = file_data
                    added_count += 1
                    total_size += len(file_data)

            timestamp = datetime.now().strftime("%H:%M:%S")
            messages.append(
                f"[{timestamp}] 📥 Загружен архив {zip_name} ({size_mb:.1f} МБ, {added_count} файлов)"
            )
            messages.append("⏳ Собираю пакет и запускаю анализ...")

            # Запускаем анализ в фоне через WS
            ws = ws_connections.get(session_id)
            if ws:
                background_tasks.add_task(
                    _run_analysis, ws, session_id, f"Пакет ({added_count} файлов)"
                )

        except zipfile.BadZipFile:
            messages.append("❌ Повреждённый ZIP-архив.")
            action = "error"
        except Exception as e:
            messages.append(f"❌ Ошибка: {str(e)[:200]}")
            action = "error"

    else:
        # Отдельные файлы → в корзину
        for fname, fdata in uploaded.items():
            user_sessions[session_id][fname] = fdata

        basket = user_sessions.get(session_id, {})
        file_list = "\n".join(
            f"  {i+1}. {name} ({len(data) // 1024} КБ)"
            for i, (name, data) in enumerate(basket.items())
        )
        messages.append(
            f"📥 Добавлено файлов: {len(uploaded)}\n"
            f"🧺 В корзине ({len(basket)} файлов):\n{file_list}\n\n"
            f"Нажмите 🚀 Запустить проверку или добавьте ещё файлы."
        )

    return JSONResponse({"action": action, "messages": messages})


# ── Статические файлы (PDF-отчёты для скачивания) ──
reports_dir = Path(__file__).parent / "web" / "reports"
reports_dir.mkdir(parents=True, exist_ok=True)

app.mount("/reports", StaticFiles(directory=str(reports_dir)), name="reports")


# ── Точка входа ───────────────────────────────
if __name__ == "__main__":
    log.info("🚀 Запуск DocumentAnalyzer Web v2.0...")
    log.info(f"   XSD целевая:   {XSD_VERSION}")
    log.info(f"   XSD минимум:   {XSD_MINIMUM_VERSION}")
    log.info(f"   Порт:          {WEB_PORT}")
    log.info(f"   URL:           http://localhost:{WEB_PORT}")

    uvicorn.run(app, host="0.0.0.0", port=WEB_PORT, log_level="info")
