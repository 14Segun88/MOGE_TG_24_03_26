"""
Реестр стратегий сравнения.

Позволяет регистрировать и выбирать стратегию по уровню риска и режиму сравнения.
Архитектурная точка расширения: новые стратегии добавляются здесь без изменения движка.
"""
from __future__ import annotations

import logging
from typing import Optional

from app.models.mapping import CompareMode, RiskLevel

from .base import BaseCompareStrategy
from .scalar import MediumScalarStrategy, StrictScalarStrategy

logger = logging.getLogger(__name__)


class StrategyRegistry:
    """
    Реестр стратегий сравнения.
    Singleton-like объект, создаётся один раз при старте приложения.
    """

    def __init__(self) -> None:
        self._strategies: dict[str, BaseCompareStrategy] = {}
        self._register_defaults()

    def _register_defaults(self) -> None:
        """Регистрация стратегий по умолчанию."""
        self.register("strict_scalar", StrictScalarStrategy())
        self.register("medium_scalar", MediumScalarStrategy())

    def register(self, name: str, strategy: BaseCompareStrategy) -> None:
        """Регистрирует стратегию под заданным именем."""
        self._strategies[name] = strategy
        logger.debug("Стратегия '%s' зарегистрирована", name)

    def get(self, name: str) -> Optional[BaseCompareStrategy]:
        """Возвращает стратегию по имени или None."""
        return self._strategies.get(name)

    def resolve(
        self,
        risk: Optional[RiskLevel],
        compare_mode: Optional[CompareMode],
    ) -> BaseCompareStrategy:
        """
        Выбирает стратегию на основе уровня риска и режима сравнения.

        Логика выбора:
        - risk=Низкий  → strict_scalar (жёсткое сравнение)
        - risk=Средний → medium_scalar (мягкая нормализация)
        - compare_mode=Жёсткое → strict_scalar (приоритет над риском)
        - compare_mode=Среднее/Мягкое → medium_scalar
        - по умолчанию → strict_scalar
        """
        # Явный режим сравнения имеет приоритет
        if compare_mode == CompareMode.STRICT:
            return self._strategies["strict_scalar"]
        if compare_mode in (CompareMode.SOFT, CompareMode.MEDIUM):
            return self._strategies["medium_scalar"]

        # Далее — на основе риска
        if risk == RiskLevel.LOW:
            return self._strategies["strict_scalar"]
        if risk == RiskLevel.MEDIUM:
            return self._strategies["medium_scalar"]

        # Fallback
        logger.debug(
            "Стратегия не определена для risk=%s, compare_mode=%s — использую strict",
            risk, compare_mode,
        )
        return self._strategies["strict_scalar"]


# Глобальный экземпляр реестра
default_registry = StrategyRegistry()
