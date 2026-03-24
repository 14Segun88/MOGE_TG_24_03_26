"""
Загрузчик маппинга из файла mapping_PZ_ZnP.json.

Поддерживает два источника:
1. JSON-файл по пути на диске.
2. JSON из байтового потока (для загрузки через API multipart).
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Union

from app.models.mapping import CompareMode, MappingRule, RiskLevel

logger = logging.getLogger(__name__)

_RISK_NORMALIZE: dict[str, RiskLevel] = {v.value.lower(): v for v in RiskLevel}
_COMPARE_NORMALIZE: dict[str, CompareMode] = {v.value.lower(): v for v in CompareMode}
# Дополнительные псевдонимы для удобства записи в JSON-маппинге
_COMPARE_NORMALIZE["булево-мягкое"] = CompareMode.BOOLEAN_SOFT
_COMPARE_NORMALIZE["boolean_soft"] = CompareMode.BOOLEAN_SOFT
_COMPARE_NORMALIZE["boolean-soft"] = CompareMode.BOOLEAN_SOFT
_COMPARE_NORMALIZE["им-мягкое"] = CompareMode.IFC_SOFT
_COMPARE_NORMALIZE["ifc_soft"] = CompareMode.IFC_SOFT
_COMPARE_NORMALIZE["ifc-soft"] = CompareMode.IFC_SOFT


def _normalize_risk(value: str) -> RiskLevel | None:
    return _RISK_NORMALIZE.get(str(value).strip().lower())


def _normalize_compare(value: str) -> CompareMode | None:
    return _COMPARE_NORMALIZE.get(str(value).strip().lower())


def _dict_to_rule(row: dict, rule_id: str) -> MappingRule:
    """Преобразует именованный объект из mapping_rows JSON в MappingRule."""

    def get(key: str) -> str:
        v = row.get(key)
        return str(v).strip() if v is not None else ""

    def get_opt(key: str) -> str | None:
        v = get(key)
        return v or None

    # Предпочитаем явный rule_id из JSON; если не задан — используем авто-сгенерированный
    effective_rule_id = get("rule_id") or rule_id

    return MappingRule(
        rule_id=effective_rule_id,
        section=get("section"),
        subsection=get("subsection"),
        label_pz=get("label_pz"),
        field_name_pz=get("field_name_pz"),
        xpath_pz=get("xpath_pz"),
        required_pz=get("required_pz"),
        xpath_znp=get("xpath_znp"),
        field_name_znp=get("field_name_znp"),
        label_znp=get("label_znp"),
        required_znp=get("required_znp"),
        type_pz=get("type_pz"),
        type_znp=get("type_znp"),
        compare_mode=_normalize_compare(get("compare_mode")),
        risk=_normalize_risk(get("risk")),
        reason=get("reason"),
        applicability=get("applicability"),
        comment=get("comment"),
        list_xpath_pz=get_opt("list_xpath_pz"),
        list_xpath_znp=get_opt("list_xpath_znp"),
        list_key_pz=get_opt("list_key_pz"),
        list_key_znp=get_opt("list_key_znp"),
        match_filename_pz=get_opt("match_filename_pz"),
        match_filename_znp=get_opt("match_filename_znp"),
        match_checksum_pz=get_opt("match_checksum_pz"),
        match_checksum_znp=get_opt("match_checksum_znp"),
    )


def load_mapping_from_json(path: Union[str, Path]) -> list[MappingRule]:
    """Загружает маппинг из JSON-файла.

    JSON должен содержать ключ "mapping_rows" — список объектов с именованными полями.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Файл маппинга не найден: {path}")

    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data.get("mapping_rows")
    if rows is None:
        raise ValueError(f"JSON-файл '{path}' не содержит ключ 'mapping_rows'")

    rules: list[MappingRule] = []
    for i, row in enumerate(rows, start=1):
        rule_id = f"R{i:03d}"
        rules.append(_dict_to_rule(row, rule_id))

    logger.info("Загружено %d правил маппинга из %s", len(rules), path.name)
    return rules


def load_mapping_from_path(path: Union[str, Path]) -> list[MappingRule]:
    """Загружает маппинг из JSON-файла по пути."""
    return load_mapping_from_json(path)


def load_mapping_from_module(path: Union[str, Path]) -> list[MappingRule]:
    """Загружает маппинг из JSON-файла по пути. Псевдоним для load_mapping_from_json."""
    return load_mapping_from_json(path)


def load_mapping_from_bytes(data: bytes, filename: str = "mapping.json") -> list[MappingRule]:
    """Загружает маппинг из байтового потока JSON-файла.

    Используется в API при загрузке файла через multipart.
    """
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="wb") as tmp:
        tmp.write(data)
        tmp_path = tmp.name

    try:
        return load_mapping_from_json(tmp_path)
    finally:
        os.unlink(tmp_path)
