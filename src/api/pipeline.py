"""
Пайплайн обработки ZIP-пакета.
FileClassifier → XmlParser → FormalCheckRunner → AnalysisResultOut
"""
from __future__ import annotations

import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from uuid import UUID

from ..agents.document_analyzer import FileClassifier, FormalCheckRunner, XmlParser
from ..agents.document_analyzer.file_classifier import FileType
from ..api.schemas import (
    AnalysisResultOut,
    ChiefEngineerOut,
    FileOut,
    FormalCheckOut,
    IssueOut,
    NoprizCheckOut,
    ObjectType,
    PP963ReportOut,
    PP963SectionOut,
    Severity,
    SverkaCheckOut,
    SverkaItemOut,
    TaskStatus,
    XmlSummaryOut,
)
from ..api.task_store import TaskStatus as TS
from ..api.task_store import update_task

import logging
log = logging.getLogger("pipeline")


import contextlib
import shutil


@contextlib.contextmanager
def _safe_fitz_open(path):
    """
    Безопасно открывает PDF через fitz (PyMuPDF).
    Если путь содержит кириллицу / символ № / спецсимволы (проблема WSL),
    копирует файл в tmp-директорию с безопасным ASCII-именем и открывает оттуда.
    """
    import fitz as _fitz
    import tempfile, os

    try:
        doc = _fitz.open(str(path))
        yield doc
        doc.close()
    except Exception:
        # Копируем в безопасный tmp-путь
        suffix = Path(path).suffix or ".pdf"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp_path = tmp.name

        try:
            shutil.copy2(str(path), tmp_path)
            doc = _fitz.open(tmp_path)
            yield doc
            doc.close()
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass


import asyncio

def _process_single_pdf(f) -> tuple[str, str]:
    """Вспомогательная синхронная функция: читает 1 PDF, возвращает (путь, текст). Лимит 5000 симв."""
    path_str = str(f.path)
    file_text = ""
    
    if getattr(f, "is_scan", False):
        if getattr(f, "min_dpi", None) is not None and f.min_dpi < 300:
            log.warning(f"Пропуск OCR для {f.path.name}: DPI ({f.min_dpi}) < 300")
            return path_str, ""
        try:
            import pdf2image
            import pytesseract
            
            log.info(f"Запуск OCR для скана: {f.path.name}")
            # Берем максимум 5 страниц, чтобы не тратить время
            images = pdf2image.convert_from_path(path_str, first_page=1, last_page=5)
            for img in images:
                page_text = pytesseract.image_to_string(img, lang="rus+eng")
                file_text += page_text + "\n"
                if len(file_text) > 5000:
                    file_text = file_text[:5000]
                    break
        except Exception as err:
            log.warning(f"Ошибка OCR для {f.path.name}: {err}")
    else:
        try:
            with _safe_fitz_open(f.path) as doc:
                for page in doc:
                    file_text += page.get_text() + "\n"
                    if len(file_text) > 5000:
                        file_text = file_text[:5000]
                        break
        except Exception as err:
            log.warning(f"PDF read error {f.path.name}: {err}")
            
    return path_str, file_text


async def _read_pdf_text_async(classified_files) -> dict[str, str]:
    """Асинхронно читает текст из всех PDF в пакете. Возвращает {путь: текст}"""
    pdf_files = [f for f in classified_files if str(f.path).lower().endswith(".pdf")]
    
    tasks = []
    for f in pdf_files:
        tasks.append(asyncio.to_thread(_process_single_pdf, f))
        
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    texts = {}
    for res in results:
        if isinstance(res, Exception):
            log.error(f"Ошибка в параллельном чтении PDF: {res}")
        else:
            path_str, text = res
            if text:
                texts[path_str] = text
    return texts


async def process_zip(task_id: UUID, zip_bytes: bytes) -> None:
    """
    Фоновая задача: распаковать ZIP и прогнать весь пайплайн.
    Результат сохраняется в task_store.
    """
    await update_task(task_id, status=TS.RUNNING)

    try:
        result = await _run_pipeline(task_id, zip_bytes)
        await update_task(
            task_id,
            status=TS.DONE,
            result=result,
            completed_at=datetime.utcnow(),
        )
    except Exception as exc:
        await update_task(
            task_id,
            status=TS.FAILED,
            error=str(exc),
            completed_at=datetime.utcnow(),
        )


async def _run_pipeline(task_id: UUID, zip_bytes: bytes) -> AnalysisResultOut:
    """Синхронная логика пайплайна (запускается в executor в роутере)."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)

        # ── 1. Распаковка ZIP ──────────────────────────────────────────
        zip_path = tmp_path / "package.zip"
        zip_path.write_bytes(zip_bytes)

        if not zipfile.is_zipfile(zip_path):
            raise ValueError("Загруженный файл не является ZIP-архивом")

        extract_dir = tmp_path / "extracted"
        extract_dir.mkdir()
        with zipfile.ZipFile(zip_path, "r") as zf:
            for info in zf.infolist():
                # Пропускаем мусор от macOS и Windows
                if "__MACOSX" in info.filename or "Zone.Identifier" in info.filename:
                    continue
                
                # Чиним кодировку кириллицы (накатываем UTF-8 либо CP866)
                try:
                    raw_bytes = info.filename.encode("cp437")
                    try:
                        name = raw_bytes.decode("utf-8")
                    except UnicodeDecodeError:
                        name = raw_bytes.decode("cp866")
                except Exception:
                    name = info.filename
                
                # Обрезаем части пути, если они длиннее 100 символов (Защита Errno 36 File name too long)
                safe_name = "/".join([p[:100] for p in name.split("/")])
                target_path = extract_dir / safe_name
                
                if info.is_dir() or not target_path.name:
                    target_path.mkdir(parents=True, exist_ok=True)
                    continue
                
                target_path.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, open(target_path, "wb") as dst:
                    import shutil
                    shutil.copyfileobj(src, dst)

        # ── 2. FileClassifier ──────────────────────────────────────────
        fc = FileClassifier()
        classified = fc.classify_directory(extract_dir)

        files_out = []
        for f in classified:
            # Для стаб-файлов извлекаем реальный размер из заголовка
            raw = f.path.read_bytes()[:80]
            import re as _re
            stub_match = _re.search(rb"stub:(\d+)KB", raw)
            size_kb = int(stub_match.group(1)) if stub_match else round(f.size_bytes / 1024, 2)
            
            # Вернем "старое" оформление: полный путь файла (склеенный через _)
            full_name = str(f.path.relative_to(extract_dir)).replace("/", "_")
            
            files_out.append(FileOut(
                name=full_name,
                file_type=f.file_type.value,
                size_kb=size_kb,
                is_scan=f.is_scan,
                min_dpi=f.min_dpi,
                suspected_section=f.suspected_section,
            ))

        xml_pz_files = [f for f in classified if f.file_type == FileType.XML_PZ]
        
        # Отфильтруем XML-файлы ПЗ, чтобы случайно не взять XML результатов изысканий (ИГДИ и т.п.)
        from ..agents.document_analyzer.formal_check_runner import _IZY_FOLDER_KEYWORDS, _PD_FOLDER_KEYWORDS
        filtered_xml_pz = [
            f for f in xml_pz_files
            if not any(kw in str(f.path).lower() for kw in _IZY_FOLDER_KEYWORDS)
        ]
        pd_xml = [
            f for f in filtered_xml_pz
            if any(kw in str(f.path).lower() for kw in _PD_FOLDER_KEYWORDS) or "пз" in f.path.name.lower() or "pz" in f.path.name.lower()
        ]
        xml_pz_files = pd_xml if pd_xml else filtered_xml_pz

        pdf_files    = [f for f in classified if f.file_type in (FileType.PDF_TEXT, FileType.PDF_SCAN)]
        scan_files   = [f for f in classified if f.is_scan]

        # ── 3. XmlParser (берём первый XML ПЗ) ────────────────────────
        parsed_xml = None
        xml_summary: XmlSummaryOut | None = None

        if xml_pz_files:
            import os
            from ..agents.document_analyzer.xml_parser import SUPPORTED_VERSIONS

            # Пробуем версии от новейшей к старейшей — берём первую успешную
            env_version = os.getenv("XSD_VERSION", "01.06")
            versions_to_try = [env_version] + [
                v for v in sorted(SUPPORTED_VERSIONS.keys(), reverse=True)
                if v != env_version
            ]

            parser = None
            for ver in versions_to_try:
                candidate = XmlParser(schema_version=ver, strict=False)
                candidate_result = candidate.parse(xml_pz_files[0].path)
                # Берём ту версию, при которой файл валиден ИЛИ хотя бы не ломается по структуре
                schema_in_file = candidate_result.schema_version
                if schema_in_file == ver or not candidate_result.validation_errors:
                    parser = candidate
                    parsed_xml = candidate_result
                    break
            if parser is None:
                # Берём просто env_version без strict
                parser = XmlParser(schema_version=env_version, strict=False)
                parsed_xml = parser.parse(xml_pz_files[0].path)

            ce = parsed_xml.chief_engineer
            xml_summary = XmlSummaryOut(
                schema_version=parsed_xml.schema_version,
                cipher=parsed_xml.cipher,
                year=parsed_xml.year,
                object_type=_map_object_type(parsed_xml.object_type),
                object_name=parsed_xml.object_name,
                construction_type=parsed_xml.construction_type,
                address=parsed_xml.address,
                chief_engineer=ChiefEngineerOut(
                    full_name=ce.full_name,
                    snils=ce.snils,
                    nopriz_id=ce.nopriz_id,
                    snils_present=bool(ce.snils),
                    nopriz_id_present=bool(ce.nopriz_id),
                ),
                energy_efficiency_class=parsed_xml.energy_efficiency_class,
                tei_count=len(parsed_xml.tei),
                documents_count=len(parsed_xml.documents),
                used_norms_count=parsed_xml.used_norms_count,
                is_valid=parsed_xml.is_valid,
                validation_errors=parsed_xml.validation_errors[:10],  # срезаем
            )

        # ── 3.5 PDF-fallback: читаем текст ПЗ если XML нет ──────────────
        import re as _re
        pdf_texts_dict = await _read_pdf_text_async(classified)
        pd_text_all = "\n".join(pdf_texts_dict.values())

        log.info(f"PDF-текст извлечён: {len(pd_text_all):,} символов из {len(pdf_files)} PDF-файлов")

        # Если XML отсутствует — ищем ГИП в тексте PDF
        if not parsed_xml:
            from ..agents.document_analyzer.xml_parser import ChiefEngineerInfo, ParsedExplanatoryNote

            snils = ""
            fio_gip = ""

            if pd_text_all:
                log.info("ПЗ в XML не найдена, извлекаем ГИП из PDF-текста")

                # СНИЛС: несколько форматов
                for snils_pat in [
                    r"\b(\d{3}[-–]\d{3}[-–]\d{3}[-–]\s*\d{2})\b",   # 080-864-940-92
                    r"\b(\d{3}\s\d{3}\s\d{3}\s\d{2})\b",              # 080 864 940 92
                    r"СНИЛС[:\s]+(\d[\d\s\-–]{9,13}\d)",              # СНИЛС: 080-864-940 92
                    r"SNILS[:\s]+(\d[\d\s\-–]{9,13}\d)",
                ]:
                    m = _re.search(snils_pat, pd_text_all, _re.IGNORECASE)
                    if m:
                        snils = m.group(1).strip()
                        log.info(f"PDF-fallback СНИЛС найден: '{snils}' (паттерн: {snils_pat[:30]})")
                        break

                # ФИО ГИПа — ищем строку ПОСЛЕ маркера
                for gip_pat in [
                    r"ГИП[:\s]+([А-ЯЁ][а-яё]+ [А-ЯЁ]\.[А-ЯЁ]\.)",           # ГИП: Иванов И.И.
                    r"ГИП[:\s]+([А-ЯЁ][а-яё]+ [А-ЯЁ][а-яё]+ [А-ЯЁ][а-яё]+)", # ГИП: Иванов Иван Иванович
                    r"[Гг]лавный инженер проекта[:\s]+([А-ЯЁ][а-яё]+ [А-ЯЁ]\.[А-ЯЁ]\.)",
                    r"[Гг]лавный инженер проекта[:\s]+([А-ЯЁ][а-яё]+ [А-ЯЁ][а-яё]+ [А-ЯЁ][а-яё]+)",
                    r"ГАП[:\s]+([А-ЯЁ][а-яё]+ [А-ЯЁ]\.[А-ЯЁ]\.)",
                ]:
                    m = _re.search(gip_pat, pd_text_all)
                    if m:
                        fio_gip = m.group(1).strip()
                        log.info(f"PDF-fallback ФИО ГИП: '{fio_gip}'")
                        break

                if not snils and not fio_gip:
                    log.warning("PDF-fallback: СНИЛС и ФИО ГИП не найдены в тексте PDF")

                # Шифр проекта
                cipher_m = _re.search(r"[Шш]ифр[:\s]+([А-ЯA-Zа-яa-z0-9\-./]+)", pd_text_all)

            fake_ce = ChiefEngineerInfo(snils=snils, full_name=fio_gip)
            fake_parsed = ParsedExplanatoryNote()
            fake_parsed.chief_engineer = fake_ce
            fake_parsed.cipher = cipher_m.group(1) if (pd_text_all and cipher_m) else ""
            parsed_xml = fake_parsed
            log.info(f"PDF-fallback итог: СНИЛС='{snils}', ФИО='{fio_gip}', Шифр='{fake_parsed.cipher}'")

        # ── 4. FormalCheckRunner ─────────────────────────────────────────────
        runner = FormalCheckRunner()
        fc_result = runner.run(classified, parsed_xml=parsed_xml)

        formal_out = FormalCheckOut(
            is_compliant=fc_result.is_compliant,
            critical_count=fc_result.critical_count,
            warning_count=fc_result.warning_count,
            xml_found=fc_result.xml_found,
            xml_version_ok=fc_result.xml_version_ok,
            iul_present=fc_result.iul_present,
            missing_sections=fc_result.missing_sections,
            issues=[
                IssueOut(
                    code=i.code,
                    severity=Severity(i.severity),
                    message=i.message,
                    file_name=i.file_name,
                    norm_ref=i.norm_ref,
                )
                for i in fc_result.issues
            ],
        )

        # ── 4.5. Orchestrator (Маршрутизация) ──────────────────────────
        from ..agents.orchestrator.orchestrator import Orchestrator
        orchestrator = Orchestrator()
        
        # Готовим выжимку текста (первые 3000 символов) для LLM
        text_excerpt = pd_text_all[:3000] if pd_text_all else ""

        import asyncio as _asyncio
        plan = await _asyncio.to_thread(
            orchestrator.decide_agents, xml_summary, text_excerpt
        )

        # ── 5. PP963ComplianceAgent (кросс-валидация ТЭП) ────────────
        pp963_out: PP963ReportOut | None = None
        # КРИТИЧНО: Если нет XML ПЗ (FC-001 критическая), достоверная кросс-валидация ТЭП
        # невозможна — блокируем агент и выставляем статус "Невозможно проверить".
        _xml_critical_missing = not formal_out.xml_found and not formal_out.xml_found
        _fc001_issues = [i for i in fc_result.issues if i.code == "FC-001" and i.severity == "critical"]
        _tep_blocked = len(_fc001_issues) > 0  # XML вообще не найден
        
        if plan.get("run_pp963", True):
            if _tep_blocked:
                log.warning("PP963: Кросс-валидация ТЭП заблокирована — XML ПЗ отсутствует (FC-001 critical). Нет эталонного источника данных.")
                pp963_out = PP963ReportOut(
                    llm_model="blocked",
                    tep_compliant=False,
                    tep_discrepancies=["⛔ Кросс-валидация ТЭП невозможна: XML Пояснительная записка не найдена. Нет машиночитаемого эталона для сравнения (FC-001)."],
                )
            else:
                try:
                    from ..agents.compliance.pp963_agent import PP963Agent
                    pp963 = PP963Agent()

                    # FIX: Извлекаем ТЭП из XML ПЗ перед кросс-валидацией
                    tep_from_xml = pp963.extract_tep_from_xml(parsed_xml)
                    if tep_from_xml:
                        log.info(
                            f"PP963: ТЭП из XML ПЗ: "
                            f"площадь={tep_from_xml.get('total_area', '?')}, "
                            f"этажей={tep_from_xml.get('floors', '?')}, "
                            f"объём={tep_from_xml.get('construction_volume', '?')}, "
                            f"вместимость={tep_from_xml.get('capacity', '?')}"
                        )
                    else:
                        log.warning("PP963: ТЭП из XML ПЗ не извлечены (tei пустой)")

                    pp963_out = _run_pp963(pp963, parsed_xml, classified, str(task_id))

                    # Если ТЭП успешно извлечены — добавим их как инфо-строку всегда
                    if tep_from_xml and pp963_out:
                        raw = tep_from_xml.get("raw_text", "")
                        if raw:
                            tep_info_str = (
                                f"ℹ️ ТЭП из XML: "
                                f"S={tep_from_xml.get('total_area', '—')}, "
                                f"V={tep_from_xml.get('construction_volume', '—')}, "
                                f"эт={tep_from_xml.get('floors', '—')}"
                            )
                            if not getattr(pp963_out, "tep_discrepancies", None):
                                pp963_out.tep_discrepancies = [tep_info_str]
                            else:
                                pp963_out.tep_discrepancies.insert(0, tep_info_str)
                except Exception as exc:
                    log.warning(f"PP963Agent упал: {exc}")

        # ── 5.5. PP154ComplianceAgent (Теплоснабжение) ───────────────────
        pp154_out = None
        import os
        if plan.get("run_pp154", False) or os.getenv("FORCE_PP154") == "1":
            try:
                from ..agents.compliance.pp154_agent import PP154Agent
                pp154 = PP154Agent()
                pp154_report = await _asyncio.to_thread(
                    pp154.run_full_check, pd_text_all, pd_text_all, str(task_id), classified
                )
                pp154_out = pp154_report
                log.info(f"PP154Agent завершил работу. is_compliant={pp154_out.is_compliant}")
            except Exception as exc:
                log.warning(f"PP154Agent упал: {exc}")

        # ── 5.6. Раздел 12: Смета ───────────────────────────────────────
        estimate_report = None
        try:
            from ..agents.document_analyzer.estimate_checker import EstimateChecker
            log.info("[Orchestrator] Вызов агента: EstimateChecker (Раздел 12 Смета)")
            est_result = EstimateChecker.check(classified, xml_summary, pdf_texts_dict)
            if est_result.found or est_result.issues:
                from .schemas import EstimateResultOut
                estimate_report = EstimateResultOut(
                    found=est_result.found,
                    ssr_approved=est_result.ssr_approved,
                    estimate_files=est_result.estimate_files,
                    issues=est_result.issues
                )
        except Exception as exc:
            log.warning(f"EstimateChecker упал: {exc}")

        # ── 5.7. SverkaChecker (Таблица Владимира) ────────────────────
        sverka_out: SverkaCheckOut | None = None
        try:
            import os as _os
            # Путь к таблице сверки релативно папки проекта
            project_root = _os.path.dirname(_os.path.abspath(__file__))  # src/api
            project_root = _os.path.dirname(_os.path.dirname(project_root))  # корень
            sverka_path = _os.path.join(project_root, "Таблица_сравнения_ТЗ_и_ПЗ_развернутая.docx")

            if _os.path.exists(sverka_path) and pd_text_all:
                from ..agents.compliance.sverka_checker import SverkaChecker
                checker = SverkaChecker(sverka_path)
                svr_result = await _asyncio.to_thread(checker.check, pd_text_all)
                sverka_out = SverkaCheckOut(
                    source_file="Таблица_сравнения_ТЗ_и_ПЗ_развернутая.docx",
                    total_items=svr_result.total_items,
                    compliant_count=svr_result.compliant_count,
                    non_compliant_count=svr_result.non_compliant_count,
                    skipped_count=svr_result.skipped_count,
                    compliance_rate=svr_result.compliance_rate,
                    is_compliant=svr_result.is_compliant,
                    error=svr_result.error,
                    items=[
                        SverkaItemOut(
                            requirement=i.requirement[:200],
                            expected=i.expected[:100],
                            found_in_pd=i.found_in_pd,
                            compliant=i.compliant,
                            comment=i.comment,
                        )
                        for i in svr_result.items[:50]  # топ-50 для Лога
                    ]
                )
                log.info(f"SverkaChecker: {sverka_out.compliant_count}/{sverka_out.total_items} соответствуют")
            elif not pd_text_all:
                log.warning("Сверка: текст ПД пустой — сверка пропущена")
            else:
                log.warning(f"Файл сверки не найден: {sverka_path}")
        except Exception as exc:
            log.warning(f"SverkaChecker упал: {exc}")

        # ── 6. НОПРИЗ — проверка ГИП в реестре ────────────────────
        nopriz_out: NoprizCheckOut | None = None
        try:
            ce = parsed_xml.chief_engineer if parsed_xml else None
            # Запускаем если есть хоть что-то: СНИЛС, nopriz_id или ФИО
            if ce and (ce.snils or ce.nopriz_id or ce.full_name):
                snils  = ce.snils or ""
                fio    = ce.full_name or ""
                log.info(f"NOPRIZ: ГИП='{fio}', СНИЛС='{snils}', NOPRIZ_ID='{ce.nopriz_id}'")

                from ..agents.external_integration.nopriz_agent import ExternalIntegrationAgent
                import asyncio as _asyncio
                nopriz = ExternalIntegrationAgent(headless=True)
                nres = await _asyncio.to_thread(
                    nopriz.verify_specialist, snils, fio
                )
                nopriz_out = NoprizCheckOut(
                    found=nres.get("found"),
                    status=nres.get("status", ""),
                    message=nres.get("message", ""),
                    fio=nres.get("specialist_data", {}).get("fio", "") or fio,
                    reg_number=nres.get("specialist_data", {}).get("reg_number", ""),
                )
            else:
                log.warning("NOPRIZ: ГИП не найден в XML — проверка пропущена")
                nopriz_out = NoprizCheckOut(
                    found=None,
                    status="skipped",
                    message="Данные о ГИП отсутствуют в пакете (нет СНИЛС/ФИО в XML)",
                )
        except Exception as exc:
            log.warning(f"NOPRIZ проверка упала: {exc}")

        # ── 7. Вердикт ────────────────────────────────────────────────
        verdict, verdict_reason = _make_verdict(fc_result, parsed_xml, pp963_out)

        # ── 8. ReportGeneratorAgent — PDF по ГОСТ Р 7.0.97-2016 ───────
        pdf_bytes: bytes | None = None
        try:
            from ..agents.reporting.report_agent import (
                ReportGeneratorAgent, ReportInput, ReportSection as RSection,
                PP963SectionDetail, SverkaItem,
            )

            # Собираем замечания из FormalCheck → ReportSection
            rpt_sections: list[RSection] = []
            for issue in fc_result.issues:
                rpt_sections.append(RSection(
                    code=issue.code,
                    severity=issue.severity,   # "critical" / "warning" / "info"
                    message=issue.message,
                    norm_ref=issue.norm_ref or "",
                ))
            # Добавляем PP963 разделы (не прошедшие) в основную таблицу
            if pp963_out and pp963_out.sections:
                for sec in pp963_out.sections:
                    if not sec.passed:
                        rpt_sections.append(RSection(
                            code=f"PP963-{sec.section_code}",
                            severity="warning",
                            message=f"Раздел {sec.section_code}: confidence={sec.confidence:.2f}",
                            norm_ref=", ".join(sec.norm_refs[:2]) if sec.norm_refs else "",
                        ))

            # --- Собираем данные для 7 новых секций PDF ---

            # ТЭП из XML
            tep_area = ""
            tep_volume = ""
            tep_floors = ""
            tep_build_area = ""
            tep_compliant_val = None
            if parsed_xml and hasattr(parsed_xml, 'tei'):
                # Простая логика извлечения по ключевым словам (как в pp963_agent)
                for rec in parsed_xml.tei:
                    name_l = rec.name.lower()
                    if "площадь" in name_l and "застройк" not in name_l:
                        if not tep_area: tep_area = str(rec.value)
                    elif "объем" in name_l or "объём" in name_l:
                        if not tep_volume: tep_volume = str(rec.value)
                    elif "этаж" in name_l:
                        if not tep_floors: tep_floors = str(rec.value)
                    elif "застройк" in name_l:
                        if not tep_build_area: tep_build_area = str(rec.value)
            if pp963_out:
                tep_compliant_val = pp963_out.tep_compliant

            # ГПЗУ и ТУ findings (из tep_discrepancies)
            gpzu_findings = []
            tu_findings = []
            if pp963_out and pp963_out.tep_discrepancies:
                for d in pp963_out.tep_discrepancies:
                    if d.startswith("[ГПЗУ"):
                        gpzu_findings.append(d.replace("[ГПЗУ↔ПЗ] ", ""))
                    elif d.startswith("[ТУ"):
                        tu_findings.append(d.replace("[ТУ↔ИОС] ", ""))

            # Все 13 разделов PP963
            pp963_detail = []
            pp963_checked = 0
            pp963_passed_cnt = 0
            if pp963_out and pp963_out.sections:
                pp963_checked = pp963_out.sections_checked
                pp963_passed_cnt = pp963_out.sections_passed
                for sec in pp963_out.sections:
                    pp963_detail.append(PP963SectionDetail(
                        code=sec.section_code,
                        name=sec.section_name,
                        passed=sec.passed,
                        confidence=sec.confidence,
                        remarks=list(sec.remarks),
                        norm_refs=list(sec.norm_refs),
                    ))

            # Сверка ТЗ/ПЗ
            sverka_items_pdf = []
            sverka_total_cnt = 0
            sverka_compliant_cnt = 0
            sverka_rate_val = 0.0
            if sverka_out and sverka_out.items:
                sverka_total_cnt = sverka_out.total_items
                sverka_compliant_cnt = sverka_out.compliant_count
                sverka_rate_val = sverka_out.compliance_rate
                for si in sverka_out.items:
                    sverka_items_pdf.append(SverkaItem(
                        requirement=si.requirement,
                        compliant=si.compliant,
                        comment=si.comment,
                    ))

            # Completeness Score
            rpt_agent_temp = ReportGeneratorAgent()
            cs_raw = {
                "formal_check": formal_out,
                "pp963": pp963_out,
                "nopriz": nopriz_out,
                "sverka": {
                    "total_requirements": sverka_total_cnt,
                    "met_requirements": sverka_compliant_cnt,
                } if sverka_out else {},
            }
            completeness_val = rpt_agent_temp._calculate_completeness_score(cs_raw)

            report_input = ReportInput(
                document_id=str(task_id),
                verdict=verdict,
                verdict_reason=verdict_reason,
                object_name=(xml_summary.object_name if xml_summary else "") or "",
                cipher=(xml_summary.cipher if xml_summary else "") or "",
                gip_name=(
                    xml_summary.chief_engineer.full_name
                    if xml_summary and xml_summary.chief_engineer else ""
                ) or "",
                sections=rpt_sections,
                nopriz_status=(nopriz_out.status if nopriz_out else ""),
                pp154_errors=pp154_out.errors if pp154_out else [],
                pp154_warnings=pp154_out.warnings if pp154_out else [],
                estimate_found=estimate_report.found if estimate_report else False,
                estimate_ssr_approved=estimate_report.ssr_approved if estimate_report else None,
                estimate_issues=estimate_report.issues if estimate_report else [],
                low_dpi_files=[
                    (f.path.name, f.min_dpi) 
                    for f in classified 
                    if getattr(f, "is_scan", False) and getattr(f, "min_dpi", None) is not None and f.min_dpi < 300
                ],
                # --- Новые поля ---
                tep_area=tep_area,
                tep_volume=tep_volume,
                tep_floors=tep_floors,
                tep_build_area=tep_build_area,
                tep_compliant=tep_compliant_val,
                gpzu_findings=gpzu_findings,
                tu_findings=tu_findings,
                sverka_items=sverka_items_pdf,
                sverka_total=sverka_total_cnt,
                sverka_compliant=sverka_compliant_cnt,
                sverka_rate=sverka_rate_val,
                pp963_sections=pp963_detail,
                pp963_sections_checked=pp963_checked,
                pp963_sections_passed=pp963_passed_cnt,
                completeness_score=completeness_val,
            )

            rpt_agent = ReportGeneratorAgent()
            pdf_bytes = rpt_agent.generate_pdf_report(report_input)
            log.info(f"ReportGenerator: PDF готов ({len(pdf_bytes):,} байт)")
        except Exception as exc:
            log.warning(f"ReportGeneratorAgent упал (PDF не создан): {exc}")

        return AnalysisResultOut(
            task_id=task_id,
            status=TaskStatus.DONE,
            created_at=datetime.utcnow(),
            completed_at=datetime.utcnow(),
            files=files_out,
            total_files=len(classified),
            xml_files_count=len(xml_pz_files),
            pdf_files_count=len(pdf_files),
            scan_files_count=len(scan_files),
            xml_summary=xml_summary,
            formal_check=formal_out,
            pd_text_extracted=pd_text_all[:50000] if pd_text_all else None,
            sverka_check=sverka_out,
            pp963_report=pp963_out,
            estimate_report=estimate_report,
            pp154_report=pp154_out,
            nopriz_check=nopriz_out,
            verdict=verdict,
            verdict_reason=verdict_reason,
            pdf_report=pdf_bytes,
        )


def _map_object_type(obj_type: str | None) -> ObjectType | None:
    mapping = {
        "NonIndustrialObject": ObjectType.NON_INDUSTRIAL,
        "IndustrialObject":    ObjectType.INDUSTRIAL,
        "LinearObject":        ObjectType.LINEAR,
    }
    return mapping.get(obj_type) if obj_type else None


def _make_verdict(fc_result, parsed_xml, pp963_out=None) -> tuple[str, str]:
    """Вердикт на основе формального контроля и PP963."""
    if fc_result.critical_count > 0:
        return (
            "RETURNED",
            f"Пакет возвращён на доработку: "
            f"{fc_result.critical_count} критических замечания. "
            f"Первое: {fc_result.issues[0].message if fc_result.issues else '—'}"
        )
    if pp963_out and not pp963_out.tep_compliant:
        return (
            "PENDING_EXPERT",
            f"Требуется проверка эксперта: расхождение ТЭП — {'; '.join(pp963_out.tep_discrepancies[:2])}"
        )
    if pp963_out and pp963_out.sections_checked > 0:
        failed = pp963_out.sections_checked - pp963_out.sections_passed
        if failed > 0:
            return (
                "PENDING_EXPERT",
                f"Требуется эксперт: {failed} из {pp963_out.sections_checked} разделов не прошли проверку ПП 963"
            )
    if fc_result.warning_count > 3:
        return (
            "PENDING_EXPERT",
            f"Требуется проверка эксперта: {fc_result.warning_count} предупреждений"
        )
    return "APPROVED", "Формальный контроль пройден успешно"


def _run_pp963(pp963, parsed_xml, classified, doc_id: str) -> PP963ReportOut:
    """Запуск PP963Agent: кросс-валидация ТЭП + ТУ/ГПЗУ + проверка 12 разделов через RAG."""
    report = PP963ReportOut(llm_model=str(pp963.model))

    if not parsed_xml:
        return report

    # 1. Кросс-валидация ТЭП
    pz_text = getattr(parsed_xml, 'object_name', '') or ''
    tei_data = getattr(parsed_xml, 'tei', []) or []

    if pz_text and tei_data:
        sec1_text = f"Пояснительная записка. Объект: {pz_text}. ТЭП: {tei_data[:5]}"
        sec2_text = f"Данные из разделов ПД: {[f.path.name for f in classified[:10]]}"

        res = pp963.validate_tep_consistency(sec1_text, sec2_text, doc_id)
        report.tep_compliant = res.is_compliant
        report.tep_discrepancies = res.discrepancies

    # 1.5. Кросс-проверка ИРД: ТУ и ГПЗУ
    try:
        import fitz as _fitz_pp

        def _quick_read(path) -> str:
            """Читаем первые 3000 символов PDF/TXT для кросс-проверки."""
            try:
                if str(path).lower().endswith(".pdf"):
                    with _fitz_pp.open(str(path)) as doc:
                        return "".join(p.get_text() for p in doc[:5])[:3000]
                elif str(path).lower().endswith(".txt"):
                    return path.read_text(encoding="utf-8", errors="ignore")[:3000]
            except Exception:
                pass
            return ""

        # ТУ: файлы с "ту", "тех_усл", "техусл" в именах
        # ─── ТУ: Технические условия (ИРД) ─────────────────────────────────
        # Ищем по ПОЛНОМУ пути (включая папки), не только по имени файла
        tu_keywords = [
            "_ту", "-ту", " ту ", "тех_усл", "техусл", "техническ",
            "tu_", "-tu", "_tu",
            "/ту/", "/ту ", "технические условия",
            "/008", "/ирд", "исходно-разреш", "исх_раз",
        ]
        tu_files = [
            f for f in classified
            if any(kw in str(f.path).lower().replace("\\", "/") for kw in tu_keywords)
        ]

        # ─── ГПЗУ: Градостроительный план земельного участка ────────────────
        gpzu_keywords = [
            "гпзу", "gpzu", "градостр", "гпзу_", "_гпзу",
            "/гпзу", "/007", "/ирд/",
        ]
        gpzu_files = [
            f for f in classified
            if any(kw in str(f.path).lower().replace("\\", "/") for kw in gpzu_keywords)
        ]

        # ─── ИОС: Инженерные системы (раздел 5) ─────────────────────────────
        ios_keywords = [
            "_иос", "-иос", "_ios", "иос_", "электро", "инжен",
            "тепло", "водоснаб", "канализ", "вентил", "кондиц",
            "/005", "/иос", "раздел пд 5",
        ]
        ios_files = [
            f for f in classified
            if any(kw in str(f.path).lower().replace("\\", "/") for kw in ios_keywords)
        ]

        # ─── ПЗ: Пояснительная записка для сравнения с ГПЗУ ─────────────────
        pz_keywords = [
            "_пз", "-пз", "_pz", "поясн", "пз_", "пз-ул",
            "/001", "/пз", "001 пз", "пояснительная записка",
        ]
        pz_files = [
            f for f in classified
            if any(kw in str(f.path).lower().replace("\\", "/") for kw in pz_keywords)
            and f.path.suffix.lower() in (".pdf", ".txt", ".docx")
        ]

        if tu_files and ios_files:
            tu_text = _quick_read(tu_files[0].path)
            ios_text = _quick_read(ios_files[0].path)
            tu_result = pp963.cross_check_tu(tu_text, ios_text, doc_id)
            if not tu_result.get("skipped") and not tu_result.get("ok"):
                findings = tu_result.get("findings", [])
                report.tep_discrepancies = (report.tep_discrepancies or []) + [
                    f"[ТУ↔ИОС] {f}" for f in findings[:3]
                ]
                report.tep_compliant = False
                log.info(f"PP963: ТУ vs ИОС — найдены расхождения: {findings[:2]}")
            elif not tu_result.get("skipped"):
                log.info(f"PP963: ТУ vs ИОС — расхождений не найдено (conf={tu_result.get('confidence', 0):.2f})")

        if gpzu_files and pz_files:
            gpzu_text = _quick_read(gpzu_files[0].path)
            pz_file_text = _quick_read(pz_files[0].path)
            gpzu_result = pp963.cross_check_gpzu(gpzu_text, pz_file_text, doc_id)
            if not gpzu_result.get("skipped") and not gpzu_result.get("ok"):
                findings = gpzu_result.get("findings", [])
                report.tep_discrepancies = (report.tep_discrepancies or []) + [
                    f"[ГПЗУ↔ПЗ] {f}" for f in findings[:3]
                ]
                report.tep_compliant = False
                log.info(f"PP963: ГПЗУ vs ПЗ — найдены расхождения: {findings[:2]}")
            elif not gpzu_result.get("skipped"):
                log.info(f"PP963: ГПЗУ vs ПЗ — расхождений не найдено (conf={gpzu_result.get('confidence', 0):.2f})")

    except Exception as e:
        log.warning(f"PP963: кросс-проверка ТУ/ГПЗУ упала: {e}")

    # 2. Проверка 12 разделов ПД через RAG
    try:
        section_results = pp963.check_all_sections(parsed_xml, classified, doc_id)
        sections_out = []
        rag_total = 0
        passed_count = 0

        for sr in section_results:
            sections_out.append(PP963SectionOut(
                section_code=sr.code,
                section_name=sr.name,
                passed=sr.passed,
                remarks=sr.remarks,
                norm_refs=sr.norm_refs,
                confidence=sr.confidence,
            ))
            if sr.passed:
                passed_count += 1
            rag_total += len(sr.norm_refs)

        report.sections = sections_out
        report.sections_checked = len(sections_out)
        report.sections_passed = passed_count
        report.rag_chunks_used = rag_total
    except Exception as e:
        log.warning(f"PP963 check_all_sections упал: {e}")

    return report

