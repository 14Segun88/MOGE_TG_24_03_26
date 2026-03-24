"""
Стандартные нормализаторы значений.

Используются стратегиями сравнения:
- StrictNormalizer:  только trim + lowercase (для Низкого риска)
- MediumNormalizer:  широкая нормализация синтаксиса (для Среднего риска)
- IdentityNormalizer: без изменений (используется в тестах и специальных случаях)
"""
from __future__ import annotations

import re
import unicodedata
from typing import Optional

from .base import BaseNormalizer


class IdentityNormalizer(BaseNormalizer):
    """Возвращает значение без изменений."""

    def normalize(self, value: Optional[str]) -> Optional[str]:
        return value


class TrimNormalizer(BaseNormalizer):
    """Удаляет начальные и конечные пробелы."""

    def normalize(self, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return value.strip()


class LowercaseNormalizer(BaseNormalizer):
    """Приводит к нижнему регистру."""

    def normalize(self, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return value.lower()


class WhitespaceNormalizer(BaseNormalizer):
    """Схлопывает множественные пробелы/переносы строк в один пробел."""

    def normalize(self, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return re.sub(r"\s+", " ", value).strip()


class QuoteNormalizer(BaseNormalizer):
    """
    Нормализует кавычки: заменяет «», "", '', ‹›, ›‹ → стандартные " ".
    Решает расхождения типа «ООО» vs "ООО".
    """

    _TABLE = str.maketrans({
        "\u00ab": '"',  # «
        "\u00bb": '"',  # »
        "\u201c": '"',  # "
        "\u201d": '"',  # "
        "\u2018": "'",  # '
        "\u2019": "'",  # '
        "\u2039": '"',  # ‹
        "\u203a": '"',  # ›
    })

    def normalize(self, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return value.translate(self._TABLE)


class DashNormalizer(BaseNormalizer):
    """
    Нормализует тире: заменяет все виды тире (— – ‒ −) на дефис -.
    """

    _DASHES = re.compile(r"[\u2012\u2013\u2014\u2015\u2212]")

    def normalize(self, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return self._DASHES.sub("-", value)


class OrgFormNormalizer(BaseNormalizer):
    """
    Нормализует сокращения организационно-правовых форм:
    «Общество с ограниченной ответственностью» → «ООО»,
    «Акционерное общество» → «АО» и т.д.
    """

    _REPLACEMENTS: list[tuple[re.Pattern, str]] = [
        (re.compile(r"\bобщество с ограниченной ответственностью\b", re.I), "ооо"),
        (re.compile(r"\bпубличное акционерное общество\b", re.I), "пао"),
        (re.compile(r"\bнепубличное акционерное общество\b", re.I), "нао"),
        (re.compile(r"\bакционерное общество\b", re.I), "ао"),
        (re.compile(r"\bгосударственное унитарное предприятие\b", re.I), "гуп"),
        (re.compile(r"\bмуниципальное унитарное предприятие\b", re.I), "муп"),
        (re.compile(r"\bфедеральное государственное унитарное предприятие\b", re.I), "фгуп"),
        (re.compile(r"\bиндивидуальный предприниматель\b", re.I), "ип"),
    ]

    def normalize(self, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        result = value
        for pattern, replacement in self._REPLACEMENTS:
            result = pattern.sub(replacement, result)
        return result


class UnicodeNormalizer(BaseNormalizer):
    """
    Нормализует Unicode: NFC-форма.
    Помогает при расхождениях в кодировке кириллицы.
    """

    def normalize(self, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return unicodedata.normalize("NFC", value)


class HexCaseNormalizer(BaseNormalizer):
    """Приводит hex-строки (CRC32) к верхнему регистру."""

    def normalize(self, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return value.upper()


# ──────────────────────────────────────────────────────────────────────────────
# Предготовленные пайплайны для стратегий сравнения
# ──────────────────────────────────────────────────────────────────────────────

from .base import NormalizerPipeline  # noqa: E402


def strict_normalizer() -> NormalizerPipeline:
    """
    Жёсткое сравнение (риск Низкий):
    только trim, unicode NFC, lowercase.
    """
    return NormalizerPipeline(
        UnicodeNormalizer(),
        TrimNormalizer(),
        LowercaseNormalizer(),
    )


def medium_normalizer() -> NormalizerPipeline:
    """
    Мягкая нормализация синтаксиса (риск Средний):
    trim, unicode, кавычки, тире, пробелы, ОПФ, lowercase.
    """
    return NormalizerPipeline(
        UnicodeNormalizer(),
        TrimNormalizer(),
        QuoteNormalizer(),
        DashNormalizer(),
        OrgFormNormalizer(),
        WhitespaceNormalizer(),
        LowercaseNormalizer(),
    )


def hex_normalizer() -> NormalizerPipeline:
    """Нормализатор для HEX-строк (CRC32 контрольные суммы)."""
    return NormalizerPipeline(
        TrimNormalizer(),
        HexCaseNormalizer(),
    )
