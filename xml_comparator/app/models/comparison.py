"""
Модели результата сравнения: CheckResult, ComparisonReport.
Эти модели используются как выходные данные API и движка сравнения.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field

from .mapping import RiskLevel


class CheckStatus(str, Enum):
    """Статус одной проверки."""
    SUCCESS = "success"           # значения совпадают
    FAILED = "failed"             # значения не совпадают
    SKIPPED = "skipped"           # пропущено (нет XPath)
    MISSING_PZ = "missing_pz"     # XPath ПЗ вернул пустой результат
    MISSING_ZNP = "missing_znp"   # XPath ЗнП вернул пустой результат
    MISSING_BOTH = "missing_both" # оба XPath вернули пустые результаты
    ERROR = "error"               # внутренняя ошибка обработки
    ONLY_PZ = "only_pz"           # правило только для ПЗ (нет XPath ЗнП)
    ONLY_ZNP = "only_znp"         # правило только для ЗнП (нет XPath ПЗ)


class ExtractedValue(BaseModel):
    """Результат извлечения значения по XPath из XML."""
    raw_values: list[str] = Field(default_factory=list, description="Необработанные значения (узлы)")
    is_multi: bool = Field(default=False, description="XPath вернул несколько значений")
    is_empty: bool = Field(default=True, description="Результат пустой")
    xpath_used: str = Field(default="", description="XPath-выражение, которое применялось")

    @property
    def scalar(self) -> Optional[str]:
        """Первое значение или None."""
        return self.raw_values[0] if self.raw_values else None


class CheckResult(BaseModel):
    """
    Результат одной проверки правила маппинга.
    Соответствует одной строке в checks[] итогового JSON-отчёта.
    """
    rule_id: str = Field(description="Уникальный идентификатор правила")
    section: str = Field(description="Раздел маппинга")
    subsection: str = Field(description="Подраздел")

    # Человекочитаемые заголовки из XSL
    label_pz: str = Field(description="Заголовок поля ПЗ")
    label_znp: str = Field(description="Заголовок поля ЗнП")

    xpath_pz: str = Field(description="XPath для ПЗ (шаблон из маппинга)")
    xpath_znp: str = Field(description="XPath для ЗнП (шаблон из маппинга)")
    xpath_pz_resolved: str = Field(default="", description="XPath для ПЗ после подстановки {ТипОбъекта}")
    xpath_znp_resolved: str = Field(default="", description="XPath для ЗнП после подстановки {ТипОбъекта}")
    risk: Optional[str] = Field(default=None, description="Уровень риска")
    compare_mode: Optional[str] = Field(default=None, description="Режим сравнения")

    # Поля XSD и обязательность (из маппинга)
    field_name_pz: str = Field(default="", description="Техническое имя поля в ПЗ (XSD)")
    required_pz: str = Field(default="", description="Обязательность поля ПЗ по XSD (Да/Нет)")
    field_name_znp: str = Field(default="", description="Техническое имя поля в ЗнП (XSD)")
    required_znp: str = Field(default="", description="Обязательность поля ЗнП по XSD (Да/Нет)")

    status: CheckStatus = Field(description="Статус проверки")
    message: str = Field(default="", description="Человекочитаемое сообщение о результате")

    # Сырые и нормализованные значения
    source_value_pz: Optional[Any] = Field(default=None, description="Значение из ПЗ (сырое)")
    source_value_znp: Optional[Any] = Field(default=None, description="Значение из ЗнП (сырое)")
    normalized_value_pz: Optional[Any] = Field(default=None, description="Нормализованное значение ПЗ")
    normalized_value_znp: Optional[Any] = Field(default=None, description="Нормализованное значение ЗнП")

    # Поле "ожидалось" для удобства фронта/аудита
    expected_value: Optional[Any] = Field(
        default=None,
        description="Ожидаемое значение (из ЗнП — задание на проектирование)"
    )

    # Мета
    is_multi_pz: bool = Field(default=False, description="XPath ПЗ вернул несколько значений")
    is_multi_znp: bool = Field(default=False, description="XPath ЗнП вернул несколько значений")
    applicability: str = Field(default="", description="Применимость правила")
    comment: str = Field(default="", description="Комментарий к правилу")
    reason: str = Field(default="", description="Причина правила")

    # Дополнительные детали (для сложных случаев: списки, расхождения)
    details: dict[str, Any] = Field(default_factory=dict, description="Дополнительные детали")

    # Человекочитаемые пояснения об особенностях сравнения для данного правила.
    # Позволяет специалисту понять логику без погружения в исходный код.
    comparison_notes: list[str] = Field(
        default_factory=list,
        description=(
            "Особенности сравнения для данного правила: режим, нормализация, "
            "специальная логика. Позволяет понять результат без изучения кода."
        ),
    )


class ComparisonSummary(BaseModel):
    """Итоговая статистика по всей проверке."""
    total_rules: int = Field(description="Всего правил в маппинге")
    total_compared: int = Field(description="Правил, по которым выполнялось сравнение")
    total_skipped: int = Field(description="Правил, пропущенных (нет XPath)")

    found_in_pz: int = Field(description="Правил, где XPath ПЗ нашёл значение")
    found_in_znp: int = Field(description="Правил, где XPath ЗнП нашёл значение")

    success_count: int = Field(description="Проверок успешных (совпадение)")
    failed_count: int = Field(description="Проверок с расхождением")
    error_count: int = Field(description="Проверок с ошибкой обработки")

    missing_in_pz_count: int = Field(description="Значение отсутствует только в ПЗ")
    missing_in_znp_count: int = Field(description="Значение отсутствует только в ЗнП")
    missing_in_both_count: int = Field(description="Значение отсутствует в обоих документах")



class DocumentMeta(BaseModel):
    """Метаданные загруженного XML-документа."""
    document_type: str = Field(description="Тип документа (pz / znp / custom)")
    schema_version: Optional[str] = Field(default=None, description="Версия схемы из атрибута @SchemaVersion")
    root_element: str = Field(description="Имя корневого элемента XML")
    file_name: str = Field(description="Имя файла")
    file_size_bytes: int = Field(description="Размер файла в байтах")


class ComparisonReportMeta(BaseModel):
    """Метаданные отчёта о сравнении."""
    generated_at: datetime = Field(description="Дата и время генерации отчёта")
    mapping_file: str = Field(description="Имя файла маппинга")
    mapping_rules_total: int = Field(description="Всего правил загружено из маппинга")
    document_pz: DocumentMeta = Field(description="Метаданные документа ПЗ")
    document_znp: DocumentMeta = Field(description="Метаданные документа ЗнП")
    comparator_version: str = Field(default="1.0.0", description="Версия компаратора")


class SectionSummary(BaseModel):
    """Краткая статистика по одному разделу маппинга."""
    section: str
    total: int
    success: int
    failed: int
    skipped: int
    missing: int


class ComparisonReport(BaseModel):
    """
    Полный отчёт о сравнении двух XML-документов по маппингу.
    Является корневой моделью JSON-ответа API.
    """
    metadata: ComparisonReportMeta
    summary: ComparisonSummary
    sections: list[SectionSummary] = Field(
        default_factory=list,
        description="Статистика по разделам маппинга"
    )
    checks: list[CheckResult]
