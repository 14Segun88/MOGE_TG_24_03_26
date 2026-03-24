"""
Базовый интерфейс стратегии сравнения.

Каждая стратегия принимает ExtractedValue из ПЗ и ЗнП,
применяет нужную нормализацию и возвращает (is_equal, norm_pz, norm_znp, details).

Архитектура Strategy Pattern позволяет добавлять:
- специализированные стратегии для списков (ListCompareStrategy)
- стратегии для комплексных типов (ComplexCompareStrategy)
- внешние стратегии из плагинов
без изменения движка сравнения.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class StrategyResult:
    """Результат применения стратегии сравнения."""
    is_equal: bool
    normalized_pz: Optional[Any]
    normalized_znp: Optional[Any]
    details: dict[str, Any] = field(default_factory=dict)
    message: str = ""


class BaseCompareStrategy(ABC):
    """Абстрактная стратегия сравнения двух значений."""

    @abstractmethod
    def compare(
        self,
        values_pz: list[str],
        values_znp: list[str],
    ) -> StrategyResult:
        """
        Сравнивает списки значений из ПЗ и ЗнП.

        Parameters
        ----------
        values_pz  : список строковых значений из ПЗ (из XPath)
        values_znp : список строковых значений из ЗнП (из XPath)

        Returns
        -------
        StrategyResult
        """
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """Название стратегии (для отладки и логов)."""
        ...
