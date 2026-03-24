"""
XmlParser — парсер и валидатор XML пояснительной записки по схеме XSD v01.05.
Пп. 1, 3, 8 чек-листа приемки (ПП РФ №963).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import xmlschema
from lxml import etree

# Путь к XSD-схемам (относительно корня проекта)
_XSD_DIR = Path(__file__).parents[3] / "xsd"

SUPPORTED_VERSIONS: dict[str, Path] = {
    "01.04": _XSD_DIR / "explanatorynote-01-04.xsd",
    "01.05": _XSD_DIR / "explanatorynote-01-05.xsd",
    "01.06": _XSD_DIR / "explanatorynote-01-06.xsd",
}

DEFAULT_VERSION = "01.05"

ObjectType = Literal["NonIndustrialObject", "IndustrialObject", "LinearObject"]


@dataclass
class ChiefEngineerInfo:
    """Сведения о ГИПе из блока Signers (п. 42 v1.04, v1.05)."""
    full_name: str = ""
    snils: str = ""          # СНИЛС (обязательный с v1.04+)
    nopriz_id: str = ""      # Идентификационный номер в НРС НОПРИЗ
    role: str = "ChiefEngineer"


@dataclass
class TEIRecord:
    """Технико-экономический показатель."""
    name: str
    value: str
    unit: str = ""           # Единица измерения (ОКЕИ)
    unit_code: str = ""      # Код ОКЕИ


@dataclass
class DocumentRef:
    """Ссылка на документ из состава проектной документации."""
    doc_number: str          # Код документа: 01.01, 02.03 ...
    doc_name: str = ""
    has_iul: bool = False    # Наличие ИУЛ (п. 50 v1.04+)


@dataclass
class ParsedExplanatoryNote:
    """Результат разбора пояснительной записки XML."""
    schema_version: str = ""
    cipher: str = ""                        # ExplanatoryNoteNumber (шифр ПЗ)
    year: str = ""                          # ExplanatoryNoteYear
    object_type: ObjectType | None = None   # Тип объекта
    object_name: str = ""                   # Name объекта
    construction_type: str = ""             # Вид работ
    address: str = ""
    chief_engineer: ChiefEngineerInfo = field(default_factory=ChiefEngineerInfo)
    chief_architect: ChiefEngineerInfo | None = None  # необязательный
    tei: list[TEIRecord] = field(default_factory=list)
    power_indicators: list[TEIRecord] = field(default_factory=list)
    documents: list[DocumentRef] = field(default_factory=list)
    used_norms_count: int = 0
    energy_efficiency_class: str = ""
    is_valid: bool = False
    validation_errors: list[str] = field(default_factory=list)
    raw_xml_path: str = ""


class XmlParser:
    """
    Парсер XML-файлов пояснительных записок.

    Поддерживает версии схемы: 01.04, 01.05, 01.06.
    Выполняет:
    - XSD-валидацию (жёсткую или мягкую)
    - Извлечение ключевых полей для последующих агентов
    - Проверку наличия СНИЛС и номера НОПРИЗ у ГИПа (п. 42 v1.05)
    """

    def __init__(
        self,
        schema_version: str = DEFAULT_VERSION,
        strict: bool = True,
    ) -> None:
        """
        Args:
            schema_version: Версия XSD-схемы для валидации.
            strict: Если True — бросить исключение при ошибке валидации.
                    Если False — вернуть результат с is_valid=False и ошибками.
        """
        if schema_version not in SUPPORTED_VERSIONS:
            raise ValueError(
                f"Неподдерживаемая версия схемы: {schema_version}. "
                f"Доступны: {list(SUPPORTED_VERSIONS)}"
            )
        self.schema_version = schema_version
        self.strict = strict
        xsd_path = SUPPORTED_VERSIONS[schema_version]
        if not xsd_path.exists():
            raise FileNotFoundError(f"XSD-схема не найдена: {xsd_path}")
        # Официальная XSD от Минстроя содержит дубликаты xml:id='Name' (баг в схеме).
        # Используем lax-режим загрузки схемы — валидация XML-документов при этом не ослабляется.
        self._xsd_schema = xmlschema.XMLSchema11(str(xsd_path), validation="lax")

    # ------------------------------------------------------------------ #
    #  Public API                                                          #
    # ------------------------------------------------------------------ #

    def parse(self, xml_path: str | Path) -> ParsedExplanatoryNote:
        """
        Разобрать XML-файл пояснительной записки.

        Args:
            xml_path: Путь к .xml файлу.

        Returns:
            ParsedExplanatoryNote — результат разбора.

        Raises:
            FileNotFoundError: файл не найден.
            xmlschema.XMLSchemaValidationError: при strict=True и ошибке схемы.
        """
        xml_path = Path(xml_path)
        if not xml_path.exists():
            raise FileNotFoundError(f"XML-файл не найден: {xml_path}")

        result = ParsedExplanatoryNote(raw_xml_path=str(xml_path))

        # 1. XSD-валидация
        errors = self._validate(xml_path)
        result.validation_errors = errors
        result.is_valid = len(errors) == 0

        if errors and self.strict:
            raise xmlschema.XMLSchemaValidationError(
                self._xsd_schema,
                f"Ошибки валидации ({len(errors)}): {errors[0]}",
            )

        # 2. Разбор дерева (даже при ошибках — best-effort)
        try:
            tree = etree.parse(str(xml_path))
            root = tree.getroot()
            self._extract_fields(root, result)
        except etree.XMLSyntaxError as exc:
            result.validation_errors.append(f"Синтаксическая ошибка XML: {exc}")
            result.is_valid = False

        return result

    def validate_only(self, xml_path: str | Path) -> list[str]:
        """
        Только валидация без разбора. Возвращает список ошибок (пустой = ОК).
        """
        return self._validate(Path(xml_path))

    # ------------------------------------------------------------------ #
    #  Internal methods                                                    #
    # ------------------------------------------------------------------ #

    def _validate(self, xml_path: Path) -> list[str]:
        errors: list[str] = []
        try:
            for error in self._xsd_schema.iter_errors(str(xml_path)):
                errors.append(str(error.reason))
        except xmlschema.XMLSchemaParseError as exc:
            errors.append(f"Ошибка загрузки схемы: {exc}")
        return errors

    def _extract_fields(self, root: etree._Element, result: ParsedExplanatoryNote) -> None:
        ns = self._get_namespace(root)

        # Атрибуты корневого элемента
        result.schema_version = root.get("SchemaVersion", "")
        result.cipher = self._text(root, f"{ns}ExplanatoryNoteNumber")
        result.year = self._text(root, f"{ns}ExplanatoryNoteYear")

        # Тип объекта (один из трёх)
        for obj_type in ("NonIndustrialObject", "IndustrialObject", "LinearObject"):
            obj_el = root.find(f"{ns}{obj_type}")
            if obj_el is not None:
                result.object_type = obj_type  # type: ignore[assignment]
                self._extract_object(obj_el, result, ns)
                break

        # Signers → ChiefEngineer / ChiefArchitect
        signers = root.find(f"{ns}Signers")
        if signers is not None:
            self._extract_signers(signers, result, ns)

        # UsedNorms — количество применяемых нормативов
        used_norms = root.find(f"{ns}UsedNorms")
        if used_norms is not None:
            result.used_norms_count = len(used_norms)

    def _extract_object(
        self,
        obj_el: etree._Element,
        result: ParsedExplanatoryNote,
        ns: str,
    ) -> None:
        result.object_name = self._text(obj_el, f"{ns}Name")
        result.construction_type = self._text(obj_el, f"{ns}ConstructionType")

        # Адрес
        addr_el = obj_el.find(f"{ns}Address")
        if addr_el is not None:
            result.address = self._concat_address(addr_el, ns)

        # ТЭП (мощность и показатели)
        for pi in obj_el.findall(f"{ns}PowerIndicator"):
            result.power_indicators.append(self._parse_tei(pi, ns))
        for tei in obj_el.findall(f"{ns}TEI"):
            result.tei.append(self._parse_tei(tei, ns))

        # Класс энергоэффективности
        ee_el = obj_el.find(f"{ns}EnergyEfficiency")
        if ee_el is not None:
            cls_el = ee_el.find(f".//{ns}EfficiencyClass") or ee_el.find(f".//{ns}Class")
            if cls_el is not None:
                result.energy_efficiency_class = (cls_el.text or "").strip()

        # Состав проектной документации (документы)
        pd_el = obj_el.find(f"{ns}ProjectDocumentation")
        if pd_el is not None:
            self._extract_documents(pd_el, result, ns)

    def _extract_signers(
        self,
        signers: etree._Element,
        result: ParsedExplanatoryNote,
        ns: str,
    ) -> None:
        for role_tag, target_attr in (
            ("ChiefEngineer", "chief_engineer"),
            ("ChiefArchitect", "chief_architect"),
        ):
            signer_el = signers.find(f".//{ns}{role_tag}")
            if signer_el is None and role_tag == "ChiefEngineer":
                # В 01.06 ГИП называется ChiefProjectEngineer
                signer_el = signers.find(f".//{ns}ChiefProjectEngineer")
                
            if signer_el is not None:
                info = ChiefEngineerInfo(role=role_tag)
                # ФИО из PersonInfo / FullName (01.05) или прямо внутри (01.06)
                person = signer_el.find(f".//{ns}PersonInfo") or signer_el
                info.full_name = (
                    self._text(person, f"{ns}FullName")
                    or self._build_full_name(person, ns)
                )
                info.snils = self._text(signer_el, f".//{ns}SNILS") or ""
                info.nopriz_id = (
                    self._text(signer_el, f".//{ns}NRSId")
                    or self._text(signer_el, f".//{ns}NoprizId")
                    or self._text(signer_el, f".//{ns}RegistrationId")
                    or self._text(signer_el, f".//{ns}NOPRIZ") # v01.06
                    or ""
                )
                if target_attr == "chief_engineer":
                    result.chief_engineer = info
                else:
                    result.chief_architect = info

    def _extract_documents(
        self,
        pd_el: etree._Element,
        result: ParsedExplanatoryNote,
        ns: str,
    ) -> None:
        for doc_el in pd_el.iter(f"{ns}Document"):
            doc_number = self._text(doc_el, f"{ns}DocNumber")
            doc_name = self._text(doc_el, f"{ns}DocName") or ""
            has_iul = doc_el.find(f".//{ns}IULFile") is not None
            if doc_number:
                result.documents.append(
                    DocumentRef(
                        doc_number=doc_number,
                        doc_name=doc_name,
                        has_iul=has_iul,
                    )
                )

    # ------------------------------------------------------------------ #
    #  Helpers                                                             #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _get_namespace(el: etree._Element) -> str:
        """Извлечь пространство имён вида '{uri}' или пустую строку."""
        tag = el.tag
        if tag.startswith("{"):
            return "{" + tag.split("}")[0][1:] + "}"
        return ""

    @staticmethod
    def _text(parent: etree._Element, xpath: str) -> str:
        el = parent.find(xpath)
        if el is not None and el.text:
            return el.text.strip()
        return ""

    @staticmethod
    def _build_full_name(el: etree._Element, ns: str) -> str:
        parts = []
        # v01.05 использует LastName/FirstName/MiddleName
        # v01.06 использует FamilyName/FirstName/SecondName
        for tags in (("LastName", "FamilyName"), ("FirstName", "FirstName"), ("MiddleName", "SecondName")):
            child = el.find(f".//{ns}{tags[0]}") or el.find(f".//{ns}{tags[1]}")
            if child is not None and child.text:
                parts.append(child.text.strip())
        return " ".join(parts)

    @staticmethod
    def _concat_address(addr_el: etree._Element, ns: str) -> str:
        parts = []
        for tag in ("Region", "City", "Street", "House"):
            child = addr_el.find(f".//{ns}{tag}")
            if child is not None and child.text:
                parts.append(child.text.strip())
        text_el = addr_el.find(f".//{ns}AddressText")
        if text_el is not None and text_el.text:
            return text_el.text.strip()
        return ", ".join(parts)

    @staticmethod
    def _parse_tei(el: etree._Element, ns: str) -> TEIRecord:
        name = XmlParser._text(el, f"{ns}Name")
        value = XmlParser._text(el, f"{ns}Value")
        
        # Единица измерения
        unit = XmlParser._text(el, f"{ns}UnitName") or XmlParser._text(el, f"{ns}Unit")
        measure_code = XmlParser._text(el, f"{ns}Measure")
        
        okei_code = XmlParser._text(el, f"{ns}OKEICode") or XmlParser._text(el, f"{ns}UnitCode") or measure_code
        
        # Маппинг частых кодов ОКЕИ в строительстве (ПЗ XML)
        OkeiMap = {
            "055": "кв.м",
            "113": "куб.м",
            "006": "м",
            "796": "шт.",
            "003": "мм",
        }
        
        if not unit and okei_code in OkeiMap:
            unit = OkeiMap[okei_code]
        elif not unit and measure_code:
            # Если код неизвестен, сохраним как есть, но это лучше, чем ничего
            unit = measure_code
            
        return TEIRecord(name=name, value=value, unit=unit, unit_code=okei_code)
