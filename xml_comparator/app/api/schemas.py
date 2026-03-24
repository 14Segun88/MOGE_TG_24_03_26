"""
Pydantic-схемы запросов и ответов API.
Отдельный файл — чтобы модели ответа API не зависели напрямую от внутренних моделей.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from app.models.comparison import ComparisonReport


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = "1.0.0"


class ErrorResponse(BaseModel):
    detail: str
    error_type: Optional[str] = None


class XsdErrorDetail(BaseModel):
    """Одна ошибка XSD-валидации."""
    line:    int
    column:  int
    level:   str
    message: str


class XsdFileValidation(BaseModel):
    """Результат валидации одного файла."""
    file:        str
    is_valid:    bool
    xsd_schema:  str
    error_count: int
    parse_error: Optional[str] = None
    errors:      list[XsdErrorDetail] = Field(default_factory=list)


class XsdValidationSummary(BaseModel):
    """Результат XSD-валидации обоих файлов — всегда присутствует в ответе."""
    pz_valid:  bool
    znp_valid: bool
    pz:        XsdFileValidation
    znp:       XsdFileValidation


class CompareResponse(BaseModel):
    """Ответ на запрос сравнения."""
    report:         ComparisonReport
    xsd_validation: Optional[XsdValidationSummary] = None
