"""
FastAPI-зависимости (Depends).

Предоставляют переиспользуемые объекты: движок сравнения, кеш маппинга.
Используются через механизм внедрения зависимостей FastAPI.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Optional

from app.engine.comparator import ComparisonEngine
from app.mapping.loader import load_mapping_from_path
from app.models.mapping import MappingRule
from app.strategies.registry import StrategyRegistry, default_registry

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_comparison_engine() -> ComparisonEngine:
    """Возвращает синглтон движка сравнения."""
    return ComparisonEngine(registry=default_registry)


def get_mapping_from_path(mapping_path: str) -> list[MappingRule]:
    """
    Загружает маппинг из файла.
    При повторных вызовах с тем же путём — кешируется в рамках процесса.
    """
    return _cached_mapping(mapping_path)


@lru_cache(maxsize=8)
def _cached_mapping(mapping_path: str) -> list[MappingRule]:
    return load_mapping_from_path(Path(mapping_path))
