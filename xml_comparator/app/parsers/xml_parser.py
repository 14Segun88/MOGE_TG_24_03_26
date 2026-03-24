"""
XML-парсер на базе lxml.
Предоставляет:
  - XmlDocument — обёртка над lxml-деревом с удобным API
  - XPathResolver — вычисляет XPath-выражения и возвращает ExtractedValue
  - load_xml_document() — фабрика из байт или пути
"""
from __future__ import annotations

import logging
import re
from io import BytesIO
from pathlib import Path
from typing import Union

from lxml import etree

from app.models.comparison import DocumentMeta, ExtractedValue

logger = logging.getLogger(__name__)

# Шаблон {ТипОбъекта} в XPath из маппинга — подстановочный знак.
# Заменяем его на реальный тип объекта после определения корневого элемента.
_OBJECT_TYPE_PLACEHOLDER = r"\{ТипОбъекта\}"

# Соответствие корневых элементов ПЗ → имени узла {ТипОбъекта}
PZ_OBJECT_TYPE_MAP: dict[str, str] = {
    "NonIndustrialObject": "NonIndustrialObject",
    "IndustrialObject": "IndustrialObject",
    "LinearObject": "LinearObject",
}


class XmlDocument:
    """
    Обёртка над lxml-деревом документа.
    Хранит метаданные и предоставляет метод xpath().
    """

    def __init__(
        self,
        tree: etree._ElementTree,
        file_name: str,
        file_size: int,
        document_type: str,
    ) -> None:
        self._tree = tree
        self._root = tree.getroot()
        self.file_name = file_name
        self.file_size = file_size
        self.document_type = document_type
        self._object_type: str | None = self._detect_object_type()

    def _detect_object_type(self) -> str | None:
        """
        Определяет тип объекта для ПЗ по дочерним элементам корня.
        Для ЗнП не нужно, но метод безопасно возвращает None.
        """
        root_tag = self._root.tag
        # Убираем namespace-prefix если есть
        local = etree.QName(root_tag).localname if "{" in root_tag else root_tag

        if local == "ExplanatoryNote":
            for candidate in PZ_OBJECT_TYPE_MAP:
                if self._root.find(candidate) is not None:
                    return candidate
        return None

    @property
    def root_element(self) -> str:
        tag = self._root.tag
        return etree.QName(tag).localname if "{" in tag else tag

    @property
    def schema_version(self) -> str | None:
        return self._root.get("SchemaVersion")

    def resolve_xpath(self, xpath_expr: str) -> ExtractedValue:
        """
        Вычисляет XPath-выражение относительно корня документа.
        Автоматически подставляет {ТипОбъекта} для ПЗ.
        """
        resolved = self._resolve_placeholders(xpath_expr)

        try:
            results = self._tree.xpath(resolved)
        except etree.XPathEvalError as exc:
            logger.warning("XPath eval error для '%s': %s", resolved, exc)
            return ExtractedValue(raw_values=[], xpath_used=resolved)
        except etree.XPathError as exc:
            logger.warning("XPath error для '%s': %s", resolved, exc)
            return ExtractedValue(raw_values=[], xpath_used=resolved)

        values = _extract_text_values(results)
        return ExtractedValue(
            raw_values=values,
            is_multi=len(values) > 1,
            is_empty=len(values) == 0,
            xpath_used=resolved,
        )

    def _resolve_placeholders(self, xpath_expr: str) -> str:
        """Заменяет {ТипОбъекта} на реальное имя узла."""
        if "{ТипОбъекта}" in xpath_expr and self._object_type:
            return re.sub(_OBJECT_TYPE_PLACEHOLDER, self._object_type, xpath_expr)
        return xpath_expr

    def build_meta(self) -> DocumentMeta:
        return DocumentMeta(
            document_type=self.document_type,
            schema_version=self.schema_version,
            root_element=self.root_element,
            file_name=self.file_name,
            file_size_bytes=self.file_size,
        )


def _extract_text_values(results: list) -> list[str]:
    """
    Извлекает текстовые значения из результатов XPath.
    Обрабатывает элементы, атрибуты и текстовые узлы.
    """
    texts: list[str] = []
    for item in results:
        if isinstance(item, etree._Element):
            # Элемент: берём text + tail потомков через itertext
            text = "".join(item.itertext()).strip()
            if text:
                texts.append(text)
            elif item.get is not None:
                # Элемент без текста — пропускаем (не добавляем пустую строку)
                pass
        elif isinstance(item, str):
            # Атрибут или text()
            val = item.strip()
            if val:
                texts.append(val)
        elif isinstance(item, (int, float)):
            texts.append(str(item))
        elif isinstance(item, etree._ElementUnicodeResult):
            val = str(item).strip()
            if val:
                texts.append(val)
    return texts


def load_xml_document(
    source: Union[bytes, Path, str],
    file_name: str,
    document_type: str,
) -> XmlDocument:
    """
    Фабричная функция: загружает XML из байт или пути к файлу.

    Parameters
    ----------
    source       : bytes или Path
    file_name    : имя файла (для метаданных)
    document_type: логический тип ("pz" / "znp" / произвольный)
    """
    if isinstance(source, (str, Path)):
        path = Path(source)
        data = path.read_bytes()
        file_size = path.stat().st_size
    else:
        data = source
        file_size = len(data)

    try:
        parser = etree.XMLParser(remove_comments=True, recover=True)
        tree = etree.parse(BytesIO(data), parser)
    except etree.XMLSyntaxError as exc:
        raise ValueError(f"Ошибка синтаксиса XML в '{file_name}': {exc}") from exc

    return XmlDocument(
        tree=tree,
        file_name=file_name,
        file_size=file_size,
        document_type=document_type,
    )
