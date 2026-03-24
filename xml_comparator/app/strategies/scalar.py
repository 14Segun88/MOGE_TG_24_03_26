"""
Стратегии сравнения скалярных значений (один элемент или одна строка).

StrictScalarStrategy  — для риска Низкий (жёсткое сравнение)
MediumScalarStrategy  — для риска Средний (мягкая нормализация синтаксиса)
"""
from __future__ import annotations

from typing import Optional

from app.normalizers.base import BaseNormalizer
from app.normalizers.standard import medium_normalizer, strict_normalizer

from .base import BaseCompareStrategy, StrategyResult


def _compare_scalar(
    normalizer: BaseNormalizer,
    values_pz: list[str],
    values_znp: list[str],
) -> StrategyResult:
    """
    Общая логика скалярного сравнения.
    При нескольких значениях: сравниваем множества нормализованных значений.
    """
    if not values_pz and not values_znp:
        return StrategyResult(
            is_equal=True,
            normalized_pz=None,
            normalized_znp=None,
            message="Оба значения отсутствуют",
            details={"multi": False},
        )

    norm_pz = [normalizer.normalize(v) for v in values_pz]
    norm_znp = [normalizer.normalize(v) for v in values_znp]

    is_multi = len(values_pz) > 1 or len(values_znp) > 1

    if is_multi:
        set_pz = set(v for v in norm_pz if v is not None)
        set_znp = set(v for v in norm_znp if v is not None)
        is_equal = set_pz == set_znp
        missing_in_znp = list(set_pz - set_znp)
        missing_in_pz = list(set_znp - set_pz)
        details: dict = {
            "multi": True,
            "count_pz": len(values_pz),
            "count_znp": len(values_znp),
            "missing_in_znp": missing_in_znp,
            "missing_in_pz": missing_in_pz,
        }
        msg = (
            "Множества значений совпадают" if is_equal
            else f"Расхождение: в ЗнП отсутствуют {missing_in_znp}, в ПЗ отсутствуют {missing_in_pz}"
        )
        return StrategyResult(
            is_equal=is_equal,
            normalized_pz=norm_pz,
            normalized_znp=norm_znp,
            message=msg,
            details=details,
        )

    # Скалярный случай
    pz_val: Optional[str] = norm_pz[0] if norm_pz else None
    znp_val: Optional[str] = norm_znp[0] if norm_znp else None
    is_equal = pz_val == znp_val

    return StrategyResult(
        is_equal=is_equal,
        normalized_pz=pz_val,
        normalized_znp=znp_val,
        message="Значения совпадают" if is_equal else f"Ожидалось: «{pz_val}», получено: «{znp_val}»",
        details={"multi": False},
    )


class StrictScalarStrategy(BaseCompareStrategy):
    """
    Жёсткое сравнение: trim + unicode + lowercase.
    Применяется при risk=Низкий и compare_mode=Жёсткое.
    """

    def __init__(self) -> None:
        self._normalizer = strict_normalizer()

    @property
    def name(self) -> str:
        return "strict_scalar"

    def compare(self, values_pz: list[str], values_znp: list[str]) -> StrategyResult:
        return _compare_scalar(self._normalizer, values_pz, values_znp)


class MediumScalarStrategy(BaseCompareStrategy):
    """
    Мягкое сравнение: широкая нормализация (кавычки, тире, ОПФ, пробелы, lowercase).
    Применяется при risk=Средний и compare_mode=Мягкое/Среднее.
    """

    def __init__(self) -> None:
        self._normalizer = medium_normalizer()

    @property
    def name(self) -> str:
        return "medium_scalar"

    def compare(self, values_pz: list[str], values_znp: list[str]) -> StrategyResult:
        return _compare_scalar(self._normalizer, values_pz, values_znp)
