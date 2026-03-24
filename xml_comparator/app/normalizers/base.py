"""
Базовый интерфейс нормализатора значений.
Каждый нормализатор принимает строку и возвращает нормализованную строку.
Нормализаторы можно комбинировать в цепочку (pipeline).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional


class BaseNormalizer(ABC):
    """Абстрактный нормализатор одного значения."""

    @abstractmethod
    def normalize(self, value: Optional[str]) -> Optional[str]:
        ...

    def __call__(self, value: Optional[str]) -> Optional[str]:
        return self.normalize(value)


class NormalizerPipeline(BaseNormalizer):
    """
    Цепочка нормализаторов: применяет каждый по порядку.
    Если хотя бы один вернул None — дальше передаётся None.
    """

    def __init__(self, *normalizers: BaseNormalizer) -> None:
        self._normalizers = normalizers

    def normalize(self, value: Optional[str]) -> Optional[str]:
        result = value
        for n in self._normalizers:
            result = n.normalize(result)
        return result
