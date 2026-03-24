"""
FastAPI-роутер для API сравнения XML.

Эндпоинты:
  POST /compare          — сравнение двух XML по загружаемому маппингу
  POST /compare/preset   — сравнение по маппингу из конфига сервера
  GET  /health           — проверка работоспособности сервиса
  GET  /mapping/rules    — список правил из загруженного маппинга
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated, Optional

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import HTMLResponse, JSONResponse

from app.api.dependencies import get_comparison_engine, get_mapping_from_path
from app.api.schemas import CompareResponse, ErrorResponse, HealthResponse, XsdValidationSummary, XsdFileValidation
from app.engine.comparator import ComparisonEngine
from app.mapping.loader import load_mapping_from_bytes, load_mapping_from_path
from app.parsers.xml_parser import load_xml_document
from app.reports.builder import build_report
from app.reports.html_report import generate_html_from_dict
from app.reports.storage import save_report, make_report_dir
from app.reports.xsd_validator import (
    XSD_PATH_PZ, XSD_PATH_ZNP,
    validate_xml as _validate_xml,
    build_validation_error_html,
    build_validation_error_json,
)

logger = logging.getLogger(__name__)
router = APIRouter()


def _run_xsd_validation(
    bytes_pz: bytes,
    bytes_znp: bytes,
    pz_filename: str,
    znp_filename: str,
    report_dir: Optional[Path] = None,
) -> Optional[XsdValidationSummary]:
    """
    Валидирует оба XML по XSD-схемам независимо от последующего сравнения.
    Сохраняет validation.html / validation.json в report_dir (если передана).
    Возвращает XsdValidationSummary — всегда, даже если оба файла валидны.
    Если XSD-файлы не найдены — возвращает None (пропускаем, логируем).
    """
    if not XSD_PATH_PZ.exists() or not XSD_PATH_ZNP.exists():
        logger.warning(
            "XSD-схемы не найдены (%s | %s) — валидация пропускается",
            XSD_PATH_PZ, XSD_PATH_ZNP,
        )
        return None

    pz_result  = _validate_xml(bytes_pz,  XSD_PATH_PZ)
    znp_result = _validate_xml(bytes_znp, XSD_PATH_ZNP)

    def _to_file_validation(result, filename: str) -> XsdFileValidation:
        from app.api.schemas import XsdErrorDetail
        return XsdFileValidation(
            file=filename,
            is_valid=result.is_valid,
            xsd_schema=result.xsd_path,
            error_count=result.error_count,
            parse_error=result.parse_error,
            errors=[
                XsdErrorDetail(line=e.line, column=e.column, level=e.level, message=e.message)
                for e in result.errors
            ],
        )

    summary = XsdValidationSummary(
        pz_valid=pz_result.is_valid,
        znp_valid=znp_result.is_valid,
        pz=_to_file_validation(pz_result, pz_filename),
        znp=_to_file_validation(znp_result, znp_filename),
    )

    # Сохраняем HTML/JSON-отчёт валидации в переданную папку
    if report_dir is not None:
        try:
            import json as _json
            error_html = build_validation_error_html(pz_result, znp_result, pz_filename, znp_filename)
            error_json = build_validation_error_json(pz_result, znp_result, pz_filename, znp_filename)
            (report_dir / "validation.json").write_text(
                _json.dumps(error_json, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            (report_dir / "validation.html").write_text(error_html, encoding="utf-8")
            logger.info("Сохранён отчёт XSD-валидации: %s", report_dir)
        except Exception as exc:
            logger.warning("Не удалось сохранить отчёт XSD-валидации: %s", exc)

    return summary


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Проверка работоспособности сервиса",
)
async def health_check() -> HealthResponse:
    return HealthResponse()


@router.post(
    "/compare",
    response_model=CompareResponse,
    summary="Сравнить ПЗ и ЗнП по загруженному маппингу",
    description=(
        "Принимает три файла: XML ПЗ, XML ЗнП, JSON-маппинг. "
        "Возвращает подробный отчёт о расхождениях."
    ),
    responses={
        400: {"model": ErrorResponse, "description": "Ошибка формата файла"},
    },
)
async def compare_documents(
    file_pz: Annotated[
        UploadFile,
        File(description="XML-файл Пояснительной записки (ПЗ), корневой элемент ExplanatoryNote"),
    ],
    file_znp: Annotated[
        UploadFile,
        File(description="XML-файл Задания на проектирование (ЗнП), корневой элемент Document"),
    ],
    file_mapping: Annotated[
        UploadFile,
        File(description="JSON-файл маппинга (*.json)"),
    ],
) -> CompareResponse:
    """
    Основной эндпоинт. Все три файла передаются как multipart/form-data.
    """
    # 1. Загружаем байты файлов
    try:
        bytes_pz = await file_pz.read()
        bytes_znp = await file_znp.read()
        bytes_mapping = await file_mapping.read()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Ошибка чтения файлов: {exc}",
        ) from exc

    # 2. XSD-валидация (независима от сравнения — всегда выполняется)
    pz_fname  = file_pz.filename  or "pz.xml"
    znp_fname = file_znp.filename or "znp.xml"

    from app.config import settings  # локальный импорт избегает circular
    report_dir = make_report_dir(settings.reports_dir) if settings.reports_dir else None

    xsd_validation = _run_xsd_validation(bytes_pz, bytes_znp, pz_fname, znp_fname, report_dir=report_dir)

    # 3. Парсим XML
    try:
        doc_pz = load_xml_document(bytes_pz, pz_fname, "pz")
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Ошибка разбора ПЗ: {exc}",
        ) from exc

    try:
        doc_znp = load_xml_document(bytes_znp, znp_fname, "znp")
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Ошибка разбора ЗнП: {exc}",
        ) from exc

    # 4. Загружаем маппинг
    try:
        rules = load_mapping_from_bytes(bytes_mapping, file_mapping.filename or "mapping.json")
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Ошибка загрузки маппинга: {exc}",
        ) from exc

    if not rules:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Файл маппинга не содержит ни одного правила",
        )

    # 4. Выполняем сравнение
    engine = get_comparison_engine()
    checks = engine.run(doc_pz, doc_znp, rules)

    # 5. Строим отчёт
    report = build_report(
        checks=checks,
        meta_pz=doc_pz.build_meta(),
        meta_znp=doc_znp.build_meta(),
        mapping_file=file_mapping.filename or "mapping.json",
        total_rules_in_mapping=len(rules),
    )

    logger.info(
        "Сравнение завершено: %s правил, success=%s, failed=%s, skipped=%s",
        report.summary.total_rules,
        report.summary.success_count,
        report.summary.failed_count,
        report.summary.total_skipped,
    )

    save_report(report.model_dump(mode="json"), settings.reports_dir, pz_xml=bytes_pz, znp_xml=bytes_znp, report_dir=report_dir)

    return CompareResponse(report=report, xsd_validation=xsd_validation)


@router.post(
    "/compare/preset",
    response_model=CompareResponse,
    summary="Сравнить ПЗ и ЗнП по серверному маппингу",
    description=(
        "Принимает два XML-файла. Маппинг берётся из конфига сервера "
        "(параметр MAPPING_FILE_PATH в переменных окружения)."
    ),
    responses={
        400: {"model": ErrorResponse, "description": "Ошибка формата файла"},
        404: {"description": "Файл маппинга не найден на сервере"},
    },
)
async def compare_documents_preset(
    file_pz: Annotated[
        UploadFile,
        File(description="XML-файл ПЗ"),
    ],
    file_znp: Annotated[
        UploadFile,
        File(description="XML-файл ЗнП"),
    ],
    mapping_path: Annotated[
        Optional[str],
        Query(description="Путь к файлу маппинга на сервере (по умолчанию — из конфига)"),
    ] = None,
) -> CompareResponse:
    from app.config import settings  # локальный импорт избегает circular

    resolved_path = mapping_path or settings.mapping_file_path
    if not resolved_path:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Путь к файлу маппинга не задан. Используйте /compare с загрузкой файла.",
        )

    if not Path(resolved_path).exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Файл маппинга не найден: {resolved_path}",
        )

    bytes_pz = await file_pz.read()
    bytes_znp = await file_znp.read()

    # XSD-валидация (независима от сравнения — всегда выполняется)
    pz_fname  = file_pz.filename  or "pz.xml"
    znp_fname = file_znp.filename or "znp.xml"

    report_dir = make_report_dir(settings.reports_dir) if settings.reports_dir else None

    xsd_validation = _run_xsd_validation(bytes_pz, bytes_znp, pz_fname, znp_fname, report_dir=report_dir)

    try:
        doc_pz  = load_xml_document(bytes_pz,  pz_fname,  "pz")
        doc_znp = load_xml_document(bytes_znp, znp_fname, "znp")
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    rules = get_mapping_from_path(resolved_path)

    engine = get_comparison_engine()
    checks = engine.run(doc_pz, doc_znp, rules)

    report = build_report(
        checks=checks,
        meta_pz=doc_pz.build_meta(),
        meta_znp=doc_znp.build_meta(),
        mapping_file=Path(resolved_path).name,
        total_rules_in_mapping=len(rules),
    )

    save_report(report.model_dump(mode="json"), settings.reports_dir, pz_xml=bytes_pz, znp_xml=bytes_znp, report_dir=report_dir)

    return CompareResponse(report=report, xsd_validation=xsd_validation)


@router.post(
    "/compare/html",
    response_class=HTMLResponse,
    summary="Сравнить ПЗ и ЗнП — вернуть HTML-отчёт",
    description=(
        "Принимает три файла: XML ПЗ, XML ЗнП, JSON-маппинг. "
        "Возвращает готовый HTML-отчёт для просмотра в браузере."
    ),
    responses={
        400: {"model": ErrorResponse, "description": "Ошибка формата файла"},
    },
)
async def compare_documents_html(
    file_pz: Annotated[UploadFile, File(description="XML-файл ПЗ")],
    file_znp: Annotated[UploadFile, File(description="XML-файл ЗнП")],
    file_mapping: Annotated[UploadFile, File(description="JSON-файл маппинга")],
) -> HTMLResponse:
    try:
        bytes_pz = await file_pz.read()
        bytes_znp = await file_znp.read()
        bytes_mapping = await file_mapping.read()
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"Ошибка чтения файлов: {exc}") from exc

    try:
        doc_pz = load_xml_document(bytes_pz, file_pz.filename or "pz.xml", "pz")
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"Ошибка разбора ПЗ: {exc}") from exc

    try:
        doc_znp = load_xml_document(bytes_znp, file_znp.filename or "znp.xml", "znp")
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"Ошибка разбора ЗнП: {exc}") from exc

    try:
        rules = load_mapping_from_bytes(bytes_mapping, file_mapping.filename or "mapping.json")
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"Ошибка загрузки маппинга: {exc}") from exc

    if not rules:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Файл маппинга не содержит ни одного правила")

    engine = get_comparison_engine()
    checks = engine.run(doc_pz, doc_znp, rules)
    report = build_report(
        checks=checks,
        meta_pz=doc_pz.build_meta(),
        meta_znp=doc_znp.build_meta(),
        mapping_file=file_mapping.filename or "mapping.json",
        total_rules_in_mapping=len(rules),
    )

    from app.config import settings
    save_report(report.model_dump(mode="json"), settings.reports_dir, pz_xml=bytes_pz)

    html = generate_html_from_dict(report.model_dump(mode="json"), pz_xml=bytes_pz)
    return HTMLResponse(content=html)


@router.post(
    "/compare/preset/html",
    response_class=HTMLResponse,
    summary="Сравнить ПЗ и ЗнП по серверному маппингу — вернуть HTML-отчёт",
    responses={
        400: {"model": ErrorResponse, "description": "Ошибка формата файла"},
        404: {"description": "Файл маппинга не найден на сервере"},
    },
)
async def compare_documents_preset_html(
    file_pz: Annotated[UploadFile, File(description="XML-файл ПЗ")],
    file_znp: Annotated[UploadFile, File(description="XML-файл ЗнП")],
    mapping_path: Annotated[
        Optional[str],
        Query(description="Путь к файлу маппинга на сервере (по умолчанию — из конфига)"),
    ] = None,
) -> HTMLResponse:
    from app.config import settings

    resolved_path = mapping_path or settings.mapping_file_path
    if not resolved_path:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail="Путь к файлу маппинга не задан.")

    if not Path(resolved_path).exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"Файл маппинга не найден: {resolved_path}")

    bytes_pz = await file_pz.read()
    bytes_znp = await file_znp.read()

    try:
        doc_pz = load_xml_document(bytes_pz, file_pz.filename or "pz.xml", "pz")
        doc_znp = load_xml_document(bytes_znp, file_znp.filename or "znp.xml", "znp")
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    rules = get_mapping_from_path(resolved_path)
    engine = get_comparison_engine()
    checks = engine.run(doc_pz, doc_znp, rules)
    report = build_report(
        checks=checks,
        meta_pz=doc_pz.build_meta(),
        meta_znp=doc_znp.build_meta(),
        mapping_file=Path(resolved_path).name,
        total_rules_in_mapping=len(rules),
    )

    save_report(report.model_dump(mode="json"), settings.reports_dir, pz_xml=bytes_pz)
    html = generate_html_from_dict(report.model_dump(mode="json"), pz_xml=bytes_pz)
    return HTMLResponse(content=html)


@router.get(
    "/mapping/rules",
    summary="Получить список правил из файла маппинга",
)
async def list_mapping_rules(
    mapping_path: Annotated[
        str,
        Query(description="Путь к файлу маппинга на сервере"),
    ],
    section: Annotated[
        Optional[str],
        Query(description="Фильтр по разделу"),
    ] = None,
    risk: Annotated[
        Optional[str],
        Query(description="Фильтр по уровню риска (Низкий / Средний / Высокий)"),
    ] = None,
) -> dict:
    if not Path(mapping_path).exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Файл маппинга не найден: {mapping_path}",
        )

    rules = get_mapping_from_path(mapping_path)

    filtered = rules
    if section:
        filtered = [r for r in filtered if section.lower() in r.section.lower()]
    if risk:
        filtered = [r for r in filtered if r.risk and r.risk.value.lower() == risk.lower()]

    return {
        "total": len(rules),
        "filtered": len(filtered),
        "rules": [r.model_dump() for r in filtered],
    }
