"""
ReportGeneratorAgent — форматирование итогового отчёта.

Форматы:
  - Telegram Markdown (быстрый превью в боте)
  - PDF по ГОСТ Р 7.0.97-2016 (официальный документ с reportlab)

Структура PDF:
  1. Заголовок и реквизиты (организация, дата, номер)
  2. Сводная таблица проверок (FC, PP963, PP154, НОПРИЗ)
  3. Критические замечания → Предупреждения → Инфо
  4. Подпись
"""
from __future__ import annotations

import io
import logging
from datetime import datetime
from dataclasses import dataclass, field
from typing import Optional

from src.agents.groq_client import call_llm, MODEL_REPORT_GENERATOR

log = logging.getLogger("report_agent")

# ─────────────────────────────────────────────
#  Данные отчёта (входная структура)
# ─────────────────────────────────────────────

@dataclass
class ReportSection:
    code: str          # FC-001, PP963-01 ...
    severity: str      # critical / warning / info
    message: str
    norm_ref: str = ""
    section_name: str = ""  # Пояснительная записка, АР, КР ...


@dataclass
class PP963SectionDetail:
    """Детальная информация по одному разделу PP963."""
    code: str           # "01", "03", "09"...
    name: str
    passed: bool
    confidence: float = 0.0
    remarks: list[str] = field(default_factory=list)
    norm_refs: list[str] = field(default_factory=list)


@dataclass
class SverkaItem:
    """Один пункт из сверки ТЗ/ПЗ."""
    requirement: str
    compliant: bool | None = None
    comment: str = ""


@dataclass
class ReportInput:
    document_id: str
    verdict: str        # APPROVED / RETURNED / PENDING_EXPERT
    verdict_reason: str
    object_name: str = ""
    cipher: str = ""
    gip_name: str = ""
    sections: list[ReportSection] = field(default_factory=list)
    nopriz_status: str = ""       # active / not_found / manual_check_required
    pp154_errors: list[str] = field(default_factory=list)
    pp154_warnings: list[str] = field(default_factory=list)
    estimate_found: bool = False
    estimate_ssr_approved: bool | None = None
    estimate_issues: list[str] = field(default_factory=list)
    low_dpi_files: list[tuple[str, int]] = field(default_factory=list)  # (имя файла, dpi)

    # --- Новые поля (7 недостающих секций) ---
    # ТЭП (из XML)
    tep_area: str = ""          # Площадь (S), кв.м
    tep_volume: str = ""        # Объём (V), куб.м
    tep_floors: str = ""        # Этажность
    tep_build_area: str = ""    # Площадь застройки
    tep_compliant: bool | None = None  # Совпадение XML↔ПЗ

    # Кросс-проверки ГПЗУ и ТУ
    gpzu_findings: list[str] = field(default_factory=list)
    tu_findings: list[str] = field(default_factory=list)

    # Сверка ТЗ/ПЗ (Таблица Владимира)
    sverka_items: list[SverkaItem] = field(default_factory=list)
    sverka_total: int = 0
    sverka_compliant: int = 0
    sverka_rate: float = 0.0

    # Все 13 разделов PP963 (не только failed)
    pp963_sections: list[PP963SectionDetail] = field(default_factory=list)
    pp963_sections_checked: int = 0
    pp963_sections_passed: int = 0

    # Completeness Score
    completeness_score: float = 0.0


# ─────────────────────────────────────────────
#  Агент
# ─────────────────────────────────────────────

class ReportGeneratorAgent:
    """
    Формирует итоговый отчёт в двух форматах:
      - Telegram Markdown (метод generate_markdown)
      - PDF по ГОСТ Р 7.0.97-2016 (метод generate_pdf_report)
    """

    def __init__(self):
        self.model = MODEL_REPORT_GENERATOR
        log.info(f"ReportGeneratorAgent init. Модель: {self.model}")

    # ──────────────────────────────────────────
    #  Telegram-формат (LLM)
    # ──────────────────────────────────────────

    def generate_markdown(self, raw_results: dict, document_id: str) -> str:
        """
        LLM-генерация краткого Telegram-отчёта с рекомендациями и ссылками на нормы.
        raw_results — dict с результатами всех агентов.
        """
        # Вычисляем Completeness Score до обращения к LLM
        completeness = self._calculate_completeness_score(raw_results)

        system_prompt = (
            "Ты — эксперт Государственной экспертизы. "
            "Преобразуй результаты автоматических проверок в официальное заключение. "
            "Используй ЧЁТКУЮ структуру на русском языке:\n\n"
            "📊 ИТОГ — Completeness Score: X%\n"
            "Краткий вердикт\n\n"
            "🔴 КРИТИЧЕСКИЕ ЗАМЕЧАНИЯ\n"
            "Для каждого критического замечания указывай:\n"
            "  • Нарушение: [описание]\n"
            "  • Норм. ссылка: [точный пункт закона]\n"
            "  • Рекомендация: [конкретное действие для устранения]\n\n"
            "⚠️ ПРЕДУПРЕЖДЕНИЯ\n"
            "Та же структура: Нарушение / Норм. ссылка / Рекомендация\n\n"
            "✅ ЗАКЛЮЧЕНИЕ — одной строкой вердикт."
        )
        user_prompt = (
            f"Документ ID: {document_id}\n"
            f"Completeness Score (уже рассчитан): {completeness:.0f}%\n"
            f"Результаты проверок:\n{str(raw_results)[:4000]}\n\n"
            "Сформируй итоговое заключение по указанной структуре. "
            "Для КАЖДОГО замечания укажи конкретную рекомендацию по устранению и ссылку на норму."
        )
        try:
            report_text = call_llm(
                model=self.model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=0.1
            )
            # Добавляем score в начало если LLM его пропустил
            if "Completeness Score" not in report_text:
                report_text = f"📊 **Completeness Score: {completeness:.0f}%**\n\n" + report_text
            return report_text
        except Exception as e:
            log.error(f"ReportAgent LLM ошибка: {e}")
            return f"❌ Ошибка генерации отчёта: {e}"

    def _calculate_completeness_score(self, raw_results: dict) -> float:
        """
        Рассчитывает Completeness Score как процент успешно пройденных проверок.

        Логика:
          - FC без критических ошибок = +20 очков
          - PP963 с количеством пройденных разделов = до +40 очков
          - НОПРИЗ = +20 очков (если найден)
          - Сверка ТЗ = пропорционально найденным требованиям
        """
        total = 0.0
        max_score = 0.0

        # FC (формальные проверки): 0-20 баллов  
        fc = raw_results.get("formal_check") or {}
        max_score += 20
        fc_critical = fc.get("critical_count", 0) if isinstance(fc, dict) else getattr(fc, "critical_count", 0)
        fc_warning = fc.get("warning_count", 0) if isinstance(fc, dict) else getattr(fc, "warning_count", 0)
        fc_score = 20 - min(fc_critical * 5, 20) - min(fc_warning * 2, 10)
        total += max(0, fc_score)

        # PP963 (разделы): 0-30 баллов
        pp963 = raw_results.get("pp963") or {}
        max_score += 30
        if pp963:
            checked = pp963.get("sections_checked", 0) if isinstance(pp963, dict) else getattr(pp963, "sections_checked", 0)
            passed = pp963.get("sections_passed", 0) if isinstance(pp963, dict) else getattr(pp963, "sections_passed", 0)
            tep_ok = pp963.get("tep_compliant", None) if isinstance(pp963, dict) else getattr(pp963, "tep_compliant", None)
            if checked > 0:
                section_score = (passed / checked) * 20
            else:
                section_score = 0
            tep_score = 10 if tep_ok else (5 if tep_ok is None else 0)
            total += section_score + tep_score

        # НОПРИЗ: 0-20 баллов
        nopriz = raw_results.get("nopriz") or {}
        max_score += 20
        nopriz_status = nopriz.get("status", "") if isinstance(nopriz, dict) else getattr(nopriz, "status", "")
        if nopriz_status == "active":
            total += 20
        elif nopriz_status == "manual_check_required":
            total += 10
        # not_found = 0 баллов

        # Сверка ТЗ (Таблица Владимира): 0-30 баллов
        sverka = raw_results.get("sverka") or {}
        max_score += 30
        if sverka:
            total_req = sverka.get("total_requirements", 0) if isinstance(sverka, dict) else getattr(sverka, "total_requirements", 0)
            met_req = sverka.get("met_requirements", 0) if isinstance(sverka, dict) else getattr(sverka, "met_requirements", 0)
            if total_req > 0:
                total += (met_req / total_req) * 30

        if max_score == 0:
            return 70.0  # Базовый уровень если нет данных

        return (total / max_score) * 100



    # ──────────────────────────────────────────
    #  PDF по ГОСТ Р 7.0.97-2016
    # ──────────────────────────────────────────

    def generate_pdf_report(self, report: ReportInput) -> bytes:
        """
        Генерирует PDF-документ по ГОСТ Р 7.0.97-2016.

        Returns:
            bytes — содержимое PDF-файла (для send_document в Telegram).
        """
        try:
            from reportlab.lib.pagesizes import A4
            from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
            from reportlab.lib.units import cm
            from reportlab.lib import colors
            from reportlab.platypus import (
                SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
                HRFlowable, KeepTogether
            )
            from reportlab.pdfbase import pdfmetrics
            from reportlab.pdfbase.ttfonts import TTFont
            from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
        except ImportError as e:
            log.error(f"reportlab не установлен: {e}")
            return b""

        # ── Шрифты (кириллица) ────────────────
        import os
        font_registered = False

        # Пары (regular, bold) — проверяем по порядку
        font_candidates = [
            (
                "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            ),
            (
                "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
                "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            ),
            (
                "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
                "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
            ),
        ]
        for regular_path, bold_path in font_candidates:
            if os.path.exists(regular_path):
                try:
                    pdfmetrics.registerFont(TTFont("DejaVu", regular_path))
                    # Bold: используем regular если bold-файл не найден
                    bold_src = bold_path if os.path.exists(bold_path) else regular_path
                    pdfmetrics.registerFont(TTFont("DejaVuBold", bold_src))
                    font_registered = True
                    log.info(f"PDF шрифт: {regular_path}")
                    break
                except Exception as fe:
                    log.warning(f"Шрифт {regular_path} не загрузился: {fe}")

        base_font = "DejaVu" if font_registered else "Helvetica"
        bold_font = "DejaVuBold" if font_registered else "Helvetica-Bold"

        # ── Стили ─────────────────────────────
        buf = io.BytesIO()
        doc = SimpleDocTemplate(
            buf, pagesize=A4,
            rightMargin=2.5 * cm, leftMargin=3.0 * cm,
            topMargin=2.0 * cm, bottomMargin=2.0 * cm,
            title=f"Заключение {report.document_id}",
            author="МособлГосЭкспертиза"
        )

        styles = getSampleStyleSheet()
        style_normal = ParagraphStyle(
            "Normal_RU", fontName=base_font, fontSize=11,
            leading=16, spaceAfter=4
        )
        style_h1 = ParagraphStyle(
            "H1_RU", fontName=bold_font, fontSize=14,
            leading=20, spaceAfter=10, alignment=TA_CENTER
        )
        style_h2 = ParagraphStyle(
            "H2_RU", fontName=bold_font, fontSize=12,
            leading=18, spaceAfter=6, spaceBefore=10
        )
        style_small = ParagraphStyle(
            "Small_RU", fontName=base_font, fontSize=9,
            leading=13, textColor=colors.grey
        )
        style_center = ParagraphStyle(
            "Center_RU", fontName=base_font, fontSize=11,
            leading=16, alignment=TA_CENTER
        )
        style_table = ParagraphStyle(
            "Table_RU", fontName=base_font, fontSize=9,
            leading=11
        )

        # ── Вердикт → цвет ─────────────────────
        verdict_color = {
            "APPROVED":       colors.green,
            "RETURNED":       colors.red,
            "PENDING_EXPERT": colors.orange,
        }.get(report.verdict, colors.grey)

        story = []

        # ── 1. Шапка документа ─────────────────
        story.append(Paragraph(
            "ГОСУДАРСТВЕННОЕ АВТОНОМНОЕ УЧРЕЖДЕНИЕ<br/>"
            "МОСКОВСКОЙ ОБЛАСТИ «МОСОБЛГОСЭКСПЕРТИЗА»",
            style_center
        ))
        story.append(Spacer(1, 0.3 * cm))
        story.append(HRFlowable(width="100%", thickness=1.5, color=colors.black))
        story.append(Spacer(1, 0.4 * cm))

        story.append(Paragraph(
            "ЗАКЛЮЧЕНИЕ О СООТВЕТСТВИИ<br/>"
            "ПРОЕКТНОЙ ДОКУМЕНТАЦИИ",
            style_h1
        ))
        story.append(Spacer(1, 0.3 * cm))

        # ── 2. Реквизиты ───────────────────────
        today = datetime.now().strftime("%d.%m.%Y")
        reqs = [
            ["Документ (ID):", report.document_id],
            ["Шифр проекта:", report.cipher or "—"],
            ["Объект:", (report.object_name or "—")[:80]],
            ["ГИП:", report.gip_name or "—"],
            ["Дата проверки:", today],
        ]
        req_table = Table(reqs, colWidths=[4.5 * cm, 11.0 * cm])
        req_table.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (0, -1), bold_font),
            ("FONTNAME", (1, 0), (1, -1), base_font),
            ("FONTSIZE", (0, 0), (-1, -1), 10),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.whitesmoke, colors.white]),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(req_table)
        story.append(Spacer(1, 0.5 * cm))

        # ── 3. Вердикт ─────────────────────────
        story.append(HRFlowable(width="100%", thickness=0.5, color=colors.lightgrey))
        story.append(Spacer(1, 0.3 * cm))
        verdict_text = {
            "APPROVED":       "✓ СООТВЕТСТВУЕТ — формальный контроль пройден",
            "RETURNED":       "✗ ВОЗВРАТ НА ДОРАБОТКУ",
            "PENDING_EXPERT": "⚠ ТРЕБУЕТСЯ ЭКСПЕРТНАЯ ПРОВЕРКА",
        }.get(report.verdict, report.verdict)

        verdict_style = ParagraphStyle(
            "Verdict", fontName=bold_font, fontSize=13,
            leading=20, textColor=verdict_color, alignment=TA_CENTER,
            spaceAfter=4
        )
        story.append(Paragraph(verdict_text, verdict_style))
        story.append(Paragraph(report.verdict_reason, style_center))
        story.append(Spacer(1, 0.5 * cm))

        # ── 3.5 Таблица ТЭП (XML → ПЗ) ───────────────
        if report.tep_area or report.tep_volume or report.tep_floors:
            story.append(Paragraph("1.1. ТЕХНИКО-ЭКОНОМИЧЕСКИЕ ПОКАЗАТЕЛИ (ТЭП)", style_h2))
            tep_icon = "✓" if report.tep_compliant else "✗" if report.tep_compliant is False else "⚠"
            tep_status = "Совпадает" if report.tep_compliant else "Расхождения" if report.tep_compliant is False else "Требуется проверка"
            story.append(Paragraph(f"{tep_icon} Кросс-валидация ТЭП: <b>{tep_status}</b>", style_normal))
            tep_data = [["Параметр", "Значение (XML)"]]
            if report.tep_area:
                tep_data.append(["Площадь (S)", f"{report.tep_area} кв.м"])
            if report.tep_volume:
                tep_data.append(["Объём (V)", f"{report.tep_volume} куб.м"])
            if report.tep_floors:
                tep_data.append(["Этажность", report.tep_floors])
            if report.tep_build_area:
                tep_data.append(["Площадь застройки", f"{report.tep_build_area} кв.м"])
            if len(tep_data) > 1:
                tep_tbl = Table(tep_data, colWidths=[6.0*cm, 9.5*cm])
                tep_tbl.setStyle(TableStyle([
                    ("FONTNAME", (0, 0), (-1, 0), bold_font),
                    ("FONTNAME", (0, 1), (-1, -1), base_font),
                    ("FONTSIZE", (0, 0), (-1, -1), 10),
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
                    ("BACKGROUND", (0, 0), (-1, 0), colors.Color(0.2, 0.4, 0.6)),
                    ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.white]),
                ]))
                story.append(tep_tbl)
            story.append(Spacer(1, 0.3 * cm))

        # ── 4. Сводная таблица проверок ─────────
        story.append(Paragraph("1. РЕЗУЛЬТАТЫ АВТОМАТИЧЕСКИХ ПРОВЕРОК", style_h2))

        # Группируем замечания: critical → warning → info
        grouped = {"critical": [], "warning": [], "info": []}
        for s in report.sections:
            grouped.get(s.severity.lower(), grouped["info"]).append(s)

        summary_data = [["Код", "Уровень", "Замечание", "Норм. ссылка"]]
        severity_labels = {
            "critical": "КРИТИЧНО",
            "warning": "Предупреждение",
            "info": "Инфо",
        }
        severity_colors = {
            "critical": colors.Color(1, 0.85, 0.85),
            "warning": colors.Color(1, 0.96, 0.8),
            "info": colors.Color(0.9, 0.95, 1.0),
        }
        row_colors = []
        for sev in ("critical", "warning", "info"):
            for item in grouped[sev]:
                summary_data.append([
                    item.code,
                    severity_labels[sev],
                    Paragraph(item.message, style_table),
                    Paragraph(item.norm_ref if item.norm_ref else "—", style_table)
                ])
                row_colors.append(severity_colors[sev])

        if len(summary_data) > 1:
            col_w = [1.8 * cm, 2.7 * cm, 8.0 * cm, 3.0 * cm]
            tbl = Table(summary_data, colWidths=col_w, repeatRows=1)
            tbl_styles = [
                ("FONTNAME", (0, 0), (-1, 0), bold_font),
                ("FONTNAME", (0, 1), (-1, -1), base_font),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
                ("BACKGROUND", (0, 0), (-1, 0), colors.Color(0.2, 0.4, 0.6)),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white]),
            ]
            # Покраска строк по severity
            for idx, c in enumerate(row_colors, start=1):
                tbl_styles.append(("BACKGROUND", (0, idx), (-1, idx), c))
            tbl.setStyle(TableStyle(tbl_styles))
            story.append(tbl)
        else:
            story.append(Paragraph("Замечаний не выявлено.", style_normal))
        story.append(Spacer(1, 0.5 * cm))

        # ── 4.5 Детализация всех 13 разделов PP963 ──────
        if report.pp963_sections:
            story.append(Paragraph(
                f"1.2. ПРОВЕРКА РАЗДЕЛОВ ПД (ПП №963) — "
                f"{report.pp963_sections_passed}/{report.pp963_sections_checked}",
                style_h2
            ))
            sec_data = [["Код", "Раздел", "Статус", "Уверенность", "Замечания / Нормы"]]
            for sec in report.pp963_sections:
                status_str = "✓ Пройден" if sec.passed else "✗ Не пройден"
                conf_str = f"{sec.confidence:.0%}" if sec.confidence > 0 else "—"
                remarks_str = "; ".join(sec.remarks) if sec.remarks else "—"
                if sec.norm_refs:
                    remarks_str += f" [{', '.join(sec.norm_refs)}]"
                sec_data.append([
                    sec.code,
                    Paragraph(sec.name, style_table),
                    status_str,
                    conf_str,
                    Paragraph(remarks_str, style_table)
                ])
            sec_tbl = Table(sec_data, colWidths=[1.2*cm, 4.3*cm, 2.2*cm, 1.8*cm, 6.0*cm], repeatRows=1)
            sec_style = [
                ("FONTNAME", (0, 0), (-1, 0), bold_font),
                ("FONTNAME", (0, 1), (-1, -1), base_font),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("GRID", (0, 0), (-1, -1), 0.3, colors.grey),
                ("BACKGROUND", (0, 0), (-1, 0), colors.Color(0.2, 0.4, 0.6)),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
            for idx, sec in enumerate(report.pp963_sections, start=1):
                bg = colors.Color(0.9, 1.0, 0.9) if sec.passed else colors.Color(1.0, 0.92, 0.85)
                sec_style.append(("BACKGROUND", (0, idx), (-1, idx), bg))
            sec_tbl.setStyle(TableStyle(sec_style))
            story.append(sec_tbl)
            story.append(Spacer(1, 0.3 * cm))

        # ── 4.6 Кросс-проверка ГПЗУ↔ПЗ (п.28) ─────────
        if report.gpzu_findings:
            story.append(Paragraph("1.3. КРОСС-ПРОВЕРКА ГПЗУ ↔ ПЗ (п. 28)", style_h2))
            for f in report.gpzu_findings[:5]:
                story.append(Paragraph(f"• ⚠ {f[:150]}", style_normal))
            story.append(Spacer(1, 0.3 * cm))

        # ── 4.7 Кросс-проверка ТУ↔ИОС (п.58) ──────────
        if report.tu_findings:
            story.append(Paragraph("1.4. КРОСС-ПРОВЕРКА ТУ ↔ ИОС (п. 58)", style_h2))
            for f in report.tu_findings[:5]:
                story.append(Paragraph(f"• ⚠ {f[:150]}", style_normal))
            story.append(Spacer(1, 0.3 * cm))

        # ── 4.8 Сверка ТЗ/ПЗ (Таблица Владимира) ──────
        if report.sverka_items:
            rate_str = f"{report.sverka_rate:.0%}"
            sv_icon = "✓" if report.sverka_rate >= 0.8 else ("⚠" if report.sverka_rate >= 0.5 else "✗")
            story.append(Paragraph(
                f"1.5. СВЕРКА ТЗ/ПЗ — {sv_icon} {report.sverka_compliant}/{report.sverka_total} ({rate_str})",
                style_h2
            ))
            violations = [i for i in report.sverka_items if i.compliant is False]
            skipped = [i for i in report.sverka_items if i.compliant is None]
            if violations:
                story.append(Paragraph("<b>Нарушения:</b>", style_normal))
                for v in violations[:10]:
                    story.append(Paragraph(f"• ✗ {v.requirement[:120]}", style_normal))
            if skipped:
                story.append(Paragraph("<b>Пропущено (нет данных):</b>", style_normal))
                for s in skipped[:5]:
                    story.append(Paragraph(f"• ⚠ {s.requirement[:120]}", style_normal))
            story.append(Spacer(1, 0.3 * cm))

        # ── 5. PP154 (если есть) ─────────────────
        if report.pp154_errors or report.pp154_warnings:
            story.append(Paragraph("2. ПРОВЕРКА СХЕМЫ ТЕПЛОСНАБЖЕНИЯ (ПП №154)", style_h2))
            if report.pp154_errors:
                story.append(Paragraph("<b>Критические нарушения:</b>", style_normal))
                for err in report.pp154_errors:
                    story.append(Paragraph(f"• {err}", style_normal))
            if report.pp154_warnings:
                story.append(Paragraph("<b>Предупреждения:</b>", style_normal))
                for w in report.pp154_warnings:
                    story.append(Paragraph(f"• {w}", style_normal))
            story.append(Spacer(1, 0.3 * cm))

        # ── 5.5 Раздел 12 (Смета) ────────────────
        if report.estimate_found or report.estimate_issues:
            story.append(Paragraph("2.5. ПРОВЕРКА СМЕТНОЙ ДОКУМЕНТАЦИИ (Раздел 12)", style_h2))
            if report.estimate_issues:
                story.append(Paragraph("<b>Критические нарушения по смете:</b>", style_normal))
                for iss in report.estimate_issues:
                    story.append(Paragraph(f"• ✗ {iss}", style_normal))
            else:
                story.append(Paragraph("✓ Замечаний к комплектации смет не выявлено (ССР утвержден, локальные сметы присутствуют).", style_normal))
            story.append(Spacer(1, 0.3 * cm))

        # ── 5.6 Файлы с низким DPI (пропущены) ───
        if report.low_dpi_files:
            story.append(Paragraph("2.6. ДОКУМЕНТЫ С НИЗКИМ КАЧЕСТВОМ СКАНА", style_h2))
            story.append(Paragraph("<b>⚠️ Следующие файлы не были проанализированы (разрешение менее 300 DPI):</b>", style_normal))
            for f_name, dpi in report.low_dpi_files[:10]:
                story.append(Paragraph(f"• {f_name} ({dpi} DPI)", style_normal))
            if len(report.low_dpi_files) > 10:
                story.append(Paragraph(f"<i>...и ещё {len(report.low_dpi_files) - 10} файлов скрыто</i>", style_normal))
            story.append(Spacer(1, 0.3 * cm))

        # ── 5.7 Графические разделы (АР, КР, МГН) ──
        story.append(Paragraph("2.7. ПРОВЕРКА ГРАФИЧЕСКИХ РАЗДЕЛОВ (Разделы 3, 4 и 10)", style_h2))
        story.append(Paragraph("<b>⚠️ Требуется обязательная ручная проверка экспертом:</b>", style_normal))
        story.append(Paragraph("• <b>Раздел 3 (АР):</b> Архитектурные решения (в т.ч. поэтажные планы)", style_normal))
        story.append(Paragraph("• <b>Раздел 4 (КР):</b> Конструктивные и объемно-планировочные решения", style_normal))
        story.append(Paragraph("• <b>МГН:</b> Схемы обеспечения доступа инвалидов (ОДИ)", style_normal))
        story.append(Spacer(1, 0.3 * cm))

        # ── 6. НОПРИЗ ────────────────────────────
        if report.nopriz_status:
            story.append(Paragraph("3. ПРОВЕРКА ГИП В РЕЕСТРЕ НОПРИЗ (пп. 66-67 ПП №963)", style_h2))
            nr_text = {
                "active": "✓ Специалист найден и активен в реестре НОПРИЗ",
                "not_found": "✗ Специалист НЕ найден в реестре НОПРИЗ — требуется проверка",
                "manual_check_required": "⚠ Автоматическая проверка НОПРИЗ не выполнена (требуется ручная)"
            }.get(report.nopriz_status, report.nopriz_status)
            story.append(Paragraph(nr_text, style_normal))
            story.append(Spacer(1, 0.3 * cm))

        # ── 7. Completeness Score ─────────────────
        if report.completeness_score > 0:
            cs = report.completeness_score
            cs_color = colors.green if cs >= 70 else (colors.orange if cs >= 40 else colors.red)
            cs_style = ParagraphStyle(
                "CS", fontName=bold_font, fontSize=12,
                leading=18, textColor=cs_color, alignment=TA_CENTER,
                spaceBefore=8, spaceAfter=4
            )
            story.append(HRFlowable(width="100%", thickness=0.5, color=colors.lightgrey))
            story.append(Paragraph(f"Completeness Score: {cs:.0f}%", cs_style))
            story.append(Spacer(1, 0.2 * cm))

        # ── 8. Подпись ───────────────────────────
        story.append(HRFlowable(width="100%", thickness=0.5, color=colors.lightgrey))
        story.append(Spacer(1, 0.4 * cm))
        story.append(Paragraph(
            f"Автоматическая система МособлГосЭкспертиза — DocumentAnalyzer v2.0<br/>"
            f"Дата формирования отчёта: {today}<br/>"
            f"Нормативная база: ПП РФ №963 (01.09.2022), ПП РФ №154, Приказ Минстроя №421/пр",
            style_small
        ))

        # ── Сборка ───────────────────────────────
        doc.build(story)
        return buf.getvalue()


# ──────────────────────────────────────────────────────────────────────────────
#  Быстрый тест (python src/agents/reporting/report_agent.py)
# ──────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import tempfile, os
    logging.basicConfig(level=logging.INFO)

    agent = ReportGeneratorAgent()

    report_input = ReportInput(
        document_id="TEST-2025-001",
        verdict="PENDING_EXPERT",
        verdict_reason="Требуется проверка эксперта: расхождение ТЭП в разделах 1 и 3",
        object_name="Жилой дом со встроенными помещениями по ул. Центральная, 15",
        cipher="ЖД-2025-001",
        gip_name="Иванов Иван Иванович",
        sections=[
            ReportSection("FC-001", "info", "XML Пояснительной записки найден", "ПП №963"),
            ReportSection("FC-002", "info", "Версия XSD v01.06 ≥ 01.05", "Приказ №421/пр"),
            ReportSection("FC-003", "warning", "ИУЛ не найден в пакете", "ГОСТ Р ЭП"),
            ReportSection("PP963-TEP", "critical", "Расхождение площади: Раздел 1 = 1500 кв.м, Раздел 3 = 1550 кв.м",
                         "ПП №963 п.16"),
        ],
        nopriz_status="active",
        pp154_errors=["Дефицит мощности: источник 10 МВт < нагрузка 10.5 МВт + потери 0.8 МВт"],
        pp154_warnings=["Горизонт планирования 10 лет < 15 лет"],
        # Новые поля
        tep_area="1500",
        tep_volume="4500",
        tep_floors="3",
        tep_compliant=False,
        pp963_sections=[
            PP963SectionDetail("01", "Пояснительная записка", True, 0.95),
            PP963SectionDetail("02", "ПЗУ", False, 0.88, remarks=["Нет расчета инсоляции"], norm_refs=["СанПиН"]),
        ],
        pp963_sections_passed=1,
        pp963_sections_checked=2,
        gpzu_findings=["Отсутствует пересечение границ участка"],
        tu_findings=["Не совпадает мощность водоснабжения. ТУ: 10м3/ч, ИОС: 12м3/ч"],
        sverka_items=[
            SverkaItem("Наличие парковочных мест", False, "В ПЗ заявлено 10, по ТЗ нужно 15"),
            SverkaItem("Высота потолков 3м", True, ""),
        ],
        sverka_total=2,
        sverka_compliant=1,
        sverka_rate=0.5,
        completeness_score=68.5,
    )

    pdf_bytes = agent.generate_pdf_report(report_input)

    out_path = "/tmp/test_report_gost.pdf"
    with open(out_path, "wb") as f:
        f.write(pdf_bytes)

    print(f"✅ PDF создан: {out_path} ({len(pdf_bytes):,} байт)")
    print("Откройте файл для проверки оформления.")
