"""
Pydantic v2 схемы для API Gateway.
Входные и выходные модели для всех эндпоинтов.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


# ─────────────────────────────────────────────
#  Общие перечисления
# ─────────────────────────────────────────────

class TaskStatus(str, Enum):
    PENDING   = "pending"
    RUNNING   = "running"
    DONE      = "done"
    FAILED    = "failed"


class Severity(str, Enum):
    CRITICAL  = "critical"
    WARNING   = "warning"
    INFO      = "info"


class ObjectType(str, Enum):
    NON_INDUSTRIAL = "NonIndustrialObject"
    INDUSTRIAL     = "IndustrialObject"
    LINEAR         = "LinearObject"


# ─────────────────────────────────────────────
#  Ответ на загрузку файла (POST /upload)
# ─────────────────────────────────────────────

class UploadResponse(BaseModel):
    task_id: UUID
    status: TaskStatus = TaskStatus.PENDING
    message: str = "Пакет принят в обработку"
    estimated_seconds: int = Field(default=10,
        description="Примерное время обработки в секундах")


# ─────────────────────────────────────────────
#  Результаты анализа (GET /results/{task_id})
# ─────────────────────────────────────────────

class IssueOut(BaseModel):
    code: str               # FC-001, FC-002 ...
    severity: Severity
    message: str
    file_name: str = ""
    norm_ref: str = ""


class ChiefEngineerOut(BaseModel):
    full_name: str = ""
    snils: str = ""
    nopriz_id: str = ""
    snils_present: bool = False
    nopriz_id_present: bool = False


class FileOut(BaseModel):
    name: str
    file_type: str
    size_kb: float
    is_scan: bool = False
    min_dpi: int | None = None
    suspected_section: str = ""


class XmlSummaryOut(BaseModel):
    schema_version: str = ""
    cipher: str = ""
    year: str = ""
    object_type: ObjectType | None = None
    object_name: str = ""
    construction_type: str = ""
    address: str = ""
    chief_engineer: ChiefEngineerOut
    energy_efficiency_class: str = ""
    tei_count: int = 0
    documents_count: int = 0
    used_norms_count: int = 0
    is_valid: bool = False
    validation_errors: list[str] = Field(default_factory=list)


class FormalCheckOut(BaseModel):
    is_compliant: bool
    critical_count: int
    warning_count: int
    xml_found: bool
    xml_version_ok: bool
    iul_present: bool
    missing_sections: list[str] = Field(default_factory=list)
    issues: list[IssueOut] = Field(default_factory=list)


# ─────────────────────────────────────────────
#  PP963 — проверка разделов ПД
# ─────────────────────────────────────────────

class PP963SectionOut(BaseModel):
    section_code: str           # "01", "03", "09"...
    section_name: str
    passed: bool
    remarks: list[str] = Field(default_factory=list)
    norm_refs: list[str] = Field(default_factory=list)
    confidence: float = 0.0


class PP963ReportOut(BaseModel):
    tep_compliant: bool = True
    tep_discrepancies: list[str] = Field(default_factory=list)
    sections: list[PP963SectionOut] = Field(default_factory=list)
    sections_checked: int = 0
    sections_passed: int = 0
    rag_chunks_used: int = 0
    llm_model: str = ""


# ─────────────────────────────────────────────
#  Сверка (Таблица Владимира)
# ─────────────────────────────────────────────

class SverkaItemOut(BaseModel):
    requirement: str = ""
    expected: str = ""
    found_in_pd: str = ""
    compliant: bool | None = None
    comment: str = ""


class SverkaCheckOut(BaseModel):
    source_file: str = ""
    total_items: int = 0
    compliant_count: int = 0
    non_compliant_count: int = 0
    skipped_count: int = 0
    compliance_rate: float = 0.0
    is_compliant: bool = False
    items: list[SverkaItemOut] = Field(default_factory=list)
    error: str = ""



class NoprizCheckOut(BaseModel):
    found: bool | None = None   # None = не удалось проверить
    status: str = ""            # "active" / "not_found" / "manual_check_required"
    message: str = ""
    fio: str = ""
    reg_number: str = ""


class EstimateResultOut(BaseModel):
    found: bool = False
    ssr_approved: bool | None = None
    estimate_files: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)


class AnalysisResultOut(BaseModel):
    task_id: UUID
    status: TaskStatus
    created_at: datetime
    completed_at: datetime | None = None
    error: str | None = None

    # Классификация файлов
    files: list[FileOut] = Field(default_factory=list)
    total_files: int = 0
    xml_files_count: int = 0
    pdf_files_count: int = 0
    scan_files_count: int = 0

    # XML-анализ
    xml_summary: XmlSummaryOut | None = None

    # Формальные проверки
    formal_check: FormalCheckOut | None = None

    # Текст ПЗ извлечённый из PDF (если ПЗ не в XML)
    pd_text_extracted: str | None = None

    # Сверка по таблице ТЗ/ПЗ (таблица Владимира)
    sverka_check: SverkaCheckOut | None = None

    # PP963 — проверка разделов ПД
    pp963_report: PP963ReportOut | None = None

    # Смета (Раздел 12/11)
    estimate_report: EstimateResultOut | None = None

    # PP154 — проверка схем теплоснабжения
    pp154_report: Any | None = None

    # НОПРИЗ — проверка ГИП
    nopriz_check: NoprizCheckOut | None = None

    # Итоговый вердикт
    verdict: str = ""       # "APPROVED" | "RETURNED" | "PENDING_EXPERT"
    verdict_reason: str = ""

    # PDF-заключение по ГОСТ (ReportGeneratorAgent)
    pdf_report: bytes | None = Field(default=None, exclude=True)  # не сериализуем в JSON



# ─────────────────────────────────────────────
#  Здоровье сервиса (GET /health)
# ─────────────────────────────────────────────

class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = "1.0.0"
    xsd_version: str = "01.05"
    timestamp: datetime = Field(default_factory=datetime.utcnow)
