"""
Модели маппинга: описание одного правила сравнения полей двух XML-документов.
Используются при загрузке Excel-маппинга и передаются в движок сравнения.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class RiskLevel(str, Enum):
    """Уровень риска правила сравнения."""
    LOW = "Низкий"
    MEDIUM = "Средний"
    HIGH = "Высокий"


class CompareMode(str, Enum):
    """Режим сравнения, зафиксированный в маппинге."""
    STRICT = "Жёсткое"
    SOFT = "Мягкое"
    MEDIUM = "Среднее"
    FIXED = "Фиксированное"  # каждый документ валидируется на своё значение из type_pz/type_znp
    BOOLEAN_SOFT = "Булево-мягкое"  # ЗнП содержит true/false; false → ПЗ пусто/«не», true → ПЗ содержит значение без отрицания
    IFC_SOFT = "ИМ-мягкое"          # ЗнП содержит текст требований к ИМ (может быть отрицанием); ПЗ — имена файлов модели; соответствие = хотя бы один .ifc/.xml файл в ПЗ


class MappingRule(BaseModel):
    """
    Одна строка маппинга — правило сопоставления поля из документа-источника
    (PZ, ПЗ) с полем документа-цели (ZNP, ЗнП).

    Поля именуются по смыслу, а не по порядку колонок JSON,
    чтобы код читался независимо от структуры файла.
    """

    rule_id: str = Field(description="Уникальный идентификатор правила")
    section: str = Field(description="Раздел маппинга (блок верхнего уровня)")
    subsection: str = Field(description="Подраздел / наименование поля")

    # --- Поля источника (ПЗ) ---
    label_pz: str = Field(description="Заголовок XSL (ПЗ) — человекочитаемое наименование")
    field_name_pz: str = Field(description="Техническое имя поля в ПЗ (из XSD)")
    xpath_pz: str = Field(description="XPath для извлечения значения из ПЗ")
    required_pz: Optional[str] = Field(default="", description="Обязательность поля в ПЗ по XSD (Да/Нет)")
    type_pz: Optional[str] = Field(default="", description="Тип данных поля в ПЗ")

    # --- Поля цели (ЗнП) ---
    label_znp: str = Field(description="Заголовок XSL (ЗнП) — человекочитаемое наименование")
    field_name_znp: str = Field(description="Техническое имя поля в ЗнП (из XSD)")
    xpath_znp: str = Field(description="XPath для извлечения значения из ЗнП")
    required_znp: Optional[str] = Field(default="", description="Обязательность поля в ЗнП по XSD")
    type_znp: Optional[str] = Field(default="", description="Тип данных поля в ЗнП")

    # --- Параметры сравнения ---
    compare_mode: Optional[CompareMode] = Field(
        default=None, description="Режим сравнения (Жёсткое / Мягкое / Среднее)"
    )
    risk: Optional[RiskLevel] = Field(
        default=None, description="Уровень риска"
    )

    # --- Пояснения ---
    reason: str = Field(default="", description="Причина/обоснование правила")
    applicability: str = Field(default="", description="Применимость (Все типы / Только пр/нп / ...)")
    comment: str = Field(default="", description="Дополнительный комментарий")

    # --- Поля для попарного сопоставления списков документов ---
    # Когда заданы list_xpath_pz/list_xpath_znp, правило является «шаблоном»: движок раскрывает
    # его в N конкретных правил — по одному на каждую сопоставленную пару документов.
    # xpath_pz / xpath_znp при этом содержат ОТНОСИТЕЛЬНЫЕ пути внутри элемента-родителя.
    #
    # Сопоставление «2 из 3»: документы идентифицируются по трём критериям:
    #   1. Тип (list_key_pz / list_key_znp) — обязателен, при отсутствии документ пропускается
    #   2. Имя файла (match_filename_pz / match_filename_znp) — нормализация «Среднее»
    #   3. Контрольная сумма (match_checksum_pz / match_checksum_znp) — case-insensitive
    # Если совпало ≥2 критерия — пара сопоставлена. Если <2 — документ несопоставлен.
    list_xpath_pz: Optional[str] = Field(
        default=None,
        description="XPath к коллекции элементов в ПЗ (напр. /ExplanatoryNote/…/Document)"
    )
    list_xpath_znp: Optional[str] = Field(
        default=None,
        description="XPath к коллекции элементов в ЗнП (напр. /Document/Content/…/DocumentInfo)"
    )
    list_key_pz: Optional[str] = Field(
        default=None,
        description="Относительный XPath типа/кода внутри элемента ПЗ (напр. DocType)"
    )
    list_key_znp: Optional[str] = Field(
        default=None,
        description="Относительный XPath типа/кода внутри элемента ЗнП (напр. @Type)"
    )
    match_filename_pz: Optional[str] = Field(
        default=None,
        description="Относительный XPath имени файла внутри элемента ПЗ (напр. File/FileName)"
    )
    match_filename_znp: Optional[str] = Field(
        default=None,
        description="Относительный XPath имени файла внутри элемента ЗнП (напр. File/Name)"
    )
    match_checksum_pz: Optional[str] = Field(
        default=None,
        description="Относительный XPath контрольной суммы внутри элемента ПЗ (напр. File/FileChecksum)"
    )
    match_checksum_znp: Optional[str] = Field(
        default=None,
        description="Относительный XPath контрольной суммы внутри элемента ЗнП (напр. File/Checksum)"
    )

    @property
    def is_list_template(self) -> bool:
        """Правило является шаблоном для попарного сопоставления списков."""
        return bool(self.list_xpath_pz and self.list_xpath_znp)

    @property
    def has_pz(self) -> bool:
        """Правило содержит XPath для ПЗ."""
        return bool(self.xpath_pz.strip())

    @property
    def has_znp(self) -> bool:
        """Правило содержит XPath для ЗнП."""
        return bool(self.xpath_znp.strip())

    @property
    def is_comparable(self) -> bool:
        """Правило можно сравнивать: оба XPath присутствуют."""
        return self.has_pz and self.has_znp
