"""
Расширитель шаблонных правил для попарного сопоставления списков документов.

Когда MappingRule содержит list_xpath_pz / list_xpath_znp, это «шаблонное» правило:
  - xpath_pz / xpath_znp — ОТНОСИТЕЛЬНЫЕ пути внутри каждого элемента-родителя
  - list_key_pz / list_key_znp — относительный XPath типа/кода для идентификации

Алгоритм сопоставления «2 из 3»:
  Каждый элемент списка идентифицируется тремя критериями:
    1. Тип (list_key_pz / list_key_znp) — обязателен.
       Документ без значения типа пропускается целиком.
    2. Имя файла (match_filename_pz / match_filename_znp) — сравнение с нормализацией «Среднее».
    3. Контрольная сумма (match_checksum_pz / match_checksum_znp) — case-insensitive.

  Если совпало ≥2 критерия → пара сопоставлена.
  Если <2 → документ считается несопоставленным (PZ-only или ZNP-only).

Раскрытие:
  Для каждой сопоставленной пары создаются конкретные правила с абсолютными XPath
  через позиционный предикат Document[N] (1-based).
  Для несопоставленных создаются правила с пустым xpath для отсутствующей стороны.
"""
from __future__ import annotations

import logging
from typing import NamedTuple, Optional

from lxml import etree

from app.models.mapping import MappingRule
from app.normalizers.standard import medium_normalizer, hex_normalizer

logger = logging.getLogger(__name__)

# Нормализаторы для сравнения при сопоставлении
_medium_norm = medium_normalizer()
_hex_norm = hex_normalizer()


class _GroupKey(NamedTuple):
    list_xpath_pz: str
    list_xpath_znp: str
    list_key_pz: str
    list_key_znp: str
    match_filename_pz: str
    match_filename_znp: str
    match_checksum_pz: str
    match_checksum_znp: str


class _DocInfo(NamedTuple):
    """Данные об одном элементе списка документов для сопоставления."""
    type_val: str       # значение типа (DocType / @Type)
    filename_val: str   # нормализованное имя файла (пустая строка если нет)
    checksum_val: str   # нормализованная контрольная сумма (пустая строка если нет)
    position: int       # позиция в XML (1-based)


def expand_list_rules(
    rules: list[MappingRule],
    tree_pz: etree._ElementTree,
    tree_znp: etree._ElementTree,
) -> list[MappingRule]:
    """
    Раскрывает шаблонные правила (is_list_template=True) в конкретные.
    Обычные правила передаются без изменений.

    Parameters
    ----------
    rules    : список правил из маппинга (могут содержать шаблоны)
    tree_pz  : lxml-дерево документа ПЗ
    tree_znp : lxml-дерево документа ЗнП

    Returns
    -------
    Плоский список правил: шаблоны заменены конкретными, порядок сохранён.
    """
    # Быстрая проверка: если шаблонов нет — возвращаем как есть
    if not any(r.is_list_template for r in rules):
        return rules

    # Собираем уникальные группы и порядок их первого появления
    group_first_idx: dict[_GroupKey, int] = {}
    group_rules: dict[_GroupKey, list[MappingRule]] = {}

    for idx, rule in enumerate(rules):
        if not rule.is_list_template:
            continue
        gk = _make_group_key(rule)
        if gk not in group_first_idx:
            group_first_idx[gk] = idx
            group_rules[gk] = []
        group_rules[gk].append(rule)

    # Раскрываем каждую группу через алгоритм «2 из 3»
    expanded: dict[_GroupKey, list[MappingRule]] = {}
    for gk, tmpl_rules in group_rules.items():
        pz_docs = _extract_doc_infos(
            tree_pz, gk.list_xpath_pz, gk.list_key_pz,
            gk.match_filename_pz, gk.match_checksum_pz,
        )
        znp_docs = _extract_doc_infos(
            tree_znp, gk.list_xpath_znp, gk.list_key_znp,
            gk.match_filename_znp, gk.match_checksum_znp,
        )

        # Фильтруем документы без типа — они не участвуют в сопоставлении
        pz_docs = [d for d in pz_docs if d.type_val]
        znp_docs = [d for d in znp_docs if d.type_val]

        if not pz_docs and not znp_docs:
            logger.info(
                "Список документов пуст в обоих файлах: ПЗ=%s, ЗнП=%s",
                gk.list_xpath_pz, gk.list_xpath_znp,
            )
            expanded[gk] = []
            continue

        pairs = _match_docs_2of3(pz_docs, znp_docs)
        matched_count = sum(1 for p, z in pairs if p and z)
        logger.info(
            "Группа '%s': %d ПЗ-документов, %d ЗнП-документов, %d пар сопоставлено",
            gk.list_xpath_pz, len(pz_docs), len(znp_docs), matched_count,
        )

        concrete: list[MappingRule] = []
        for pz_doc, znp_doc in pairs:
            for tmpl in tmpl_rules:
                concrete.append(_make_concrete_rule(tmpl, pz_doc, znp_doc, gk))
        expanded[gk] = concrete

    # Собираем итоговый список, вставляя раскрытые правила вместо шаблонных
    result: list[MappingRule] = []
    inserted: set[_GroupKey] = set()

    for rule in rules:
        if not rule.is_list_template:
            result.append(rule)
            continue

        gk = _make_group_key(rule)
        if gk not in inserted:
            result.extend(expanded.get(gk, []))
            inserted.add(gk)
        # Следующие шаблоны той же группы пропускаем — уже включены в expanded[gk]

    return result


# ──────────────────────────────────────────────────────────────────────────────
# Вспомогательные функции
# ──────────────────────────────────────────────────────────────────────────────

def _make_group_key(rule: MappingRule) -> _GroupKey:
    return _GroupKey(
        list_xpath_pz=rule.list_xpath_pz or "",
        list_xpath_znp=rule.list_xpath_znp or "",
        list_key_pz=rule.list_key_pz or "",
        list_key_znp=rule.list_key_znp or "",
        match_filename_pz=rule.match_filename_pz or "",
        match_filename_znp=rule.match_filename_znp or "",
        match_checksum_pz=rule.match_checksum_pz or "",
        match_checksum_znp=rule.match_checksum_znp or "",
    )


def _extract_doc_infos(
    tree: etree._ElementTree,
    list_xpath: str,
    key_subpath: str,
    filename_subpath: str,
    checksum_subpath: str,
) -> list[_DocInfo]:
    """
    Извлекает данные об элементах списка документов из XML-дерева.

    Для каждого элемента, соответствующего list_xpath, извлекает:
    - тип (key_subpath)
    - нормализованное имя файла (filename_subpath, нормализация «Среднее»)
    - нормализованную контрольную сумму (checksum_subpath, uppercase)
    """
    if not list_xpath:
        return []
    try:
        elements = tree.xpath(list_xpath)
    except etree.XPathError as exc:
        logger.warning("XPath error при извлечении документов '%s': %s", list_xpath, exc)
        return []

    result: list[_DocInfo] = []
    for pos, elem in enumerate(elements, start=1):
        if not isinstance(elem, etree._Element):
            continue
        type_val = _get_field(elem, key_subpath).strip() if key_subpath else ""
        filename_raw = _get_field(elem, filename_subpath) if filename_subpath else ""
        checksum_raw = _get_field(elem, checksum_subpath) if checksum_subpath else ""

        filename_norm = _medium_norm.normalize(filename_raw) or ""
        checksum_norm = _hex_norm.normalize(checksum_raw) or ""

        result.append(_DocInfo(type_val, filename_norm, checksum_norm, pos))
    return result


def _get_field(elem: etree._Element, subpath: str) -> str:
    """Извлекает текстовое значение по относительному XPath из элемента."""
    if not subpath:
        return ""
    try:
        vals = elem.xpath(subpath)
    except etree.XPathError:
        return ""
    if not vals:
        return ""
    v = vals[0]
    if isinstance(v, etree._Element):
        return "".join(v.itertext())
    return str(v)


def _match_docs_2of3(
    pz_docs: list[_DocInfo],
    znp_docs: list[_DocInfo],
) -> list[tuple[Optional[_DocInfo], Optional[_DocInfo]]]:
    """
    Сопоставляет документы ПЗ и ЗнП по принципу «2 из 3».

    Критерии:
      1. Тип (точное совпадение)
      2. Имя файла (нормализованное medium, точное совпадение нормализованных значений)
      3. Контрольная сумма (uppercase, точное совпадение)

    Документ считается сопоставленным, если совпало ≥2 критерия.
    Каждый ZNP-документ используется максимум в одной паре (жадное сопоставление).

    Returns
    -------
    Список пар (pz_doc, znp_doc). None означает отсутствие пары:
    - (pz_doc, None)  → документ есть только в ПЗ
    - (None, znp_doc) → документ есть только в ЗнП
    """
    matched_znp_positions: set[int] = set()
    pairs: list[tuple[Optional[_DocInfo], Optional[_DocInfo]]] = []

    for pz_doc in pz_docs:
        best_znp: Optional[_DocInfo] = None
        best_score = 0

        for znp_doc in znp_docs:
            if znp_doc.position in matched_znp_positions:
                continue
            score = _match_score(pz_doc, znp_doc)
            if score > best_score:
                best_score = score
                best_znp = znp_doc

        if best_score >= 2 and best_znp is not None:
            matched_znp_positions.add(best_znp.position)
            pairs.append((pz_doc, best_znp))
        else:
            pairs.append((pz_doc, None))

    # Несопоставленные ZNP-документы
    for znp_doc in znp_docs:
        if znp_doc.position not in matched_znp_positions:
            pairs.append((None, znp_doc))

    return pairs


def _match_score(pz: _DocInfo, znp: _DocInfo) -> int:
    """Считает число совпавших критериев идентификации (0..3)."""
    score = 0
    if pz.type_val and znp.type_val and pz.type_val == znp.type_val:
        score += 1
    if pz.filename_val and znp.filename_val and pz.filename_val == znp.filename_val:
        score += 1
    if pz.checksum_val and znp.checksum_val and pz.checksum_val == znp.checksum_val:
        score += 1
    return score


def _make_concrete_rule(
    template: MappingRule,
    pz_doc: Optional[_DocInfo],
    znp_doc: Optional[_DocInfo],
    group: _GroupKey,
) -> MappingRule:
    """
    Создаёт конкретное правило из шаблонного для заданной пары документов.

    Использует позиционные предикаты [N] (1-based) вместо предикатов по ключу.
    Это гарантирует корректную адресацию даже при нескольких документах одного типа.

    Если pz_doc is None — xpath_pz = "" (отсутствует в ПЗ).
    Если znp_doc is None — xpath_znp = "" (отсутствует в ЗнП).
    """
    field_pz = template.xpath_pz.strip()
    field_znp = template.xpath_znp.strip()

    if pz_doc is not None:
        abs_xpath_pz = (
            f"{group.list_xpath_pz}[{pz_doc.position}]/{field_pz}"
            if field_pz else f"{group.list_xpath_pz}[{pz_doc.position}]"
        )
    else:
        abs_xpath_pz = ""

    if znp_doc is not None:
        abs_xpath_znp = (
            f"{group.list_xpath_znp}[{znp_doc.position}]/{field_znp}"
            if field_znp else f"{group.list_xpath_znp}[{znp_doc.position}]"
        )
    else:
        abs_xpath_znp = ""

    type_label = (
        (pz_doc.type_val if pz_doc else None)
        or (znp_doc.type_val if znp_doc else "?")
    )
    pz_pos_str = str(pz_doc.position) if pz_doc else "-"
    znp_pos_str = str(znp_doc.position) if znp_doc else "-"

    pair_label = f"код: {type_label}, ПЗ[{pz_pos_str}]->ЗнП[{znp_pos_str}]"
    new_section = f"{template.section} [{pair_label}]"
    new_rule_id = f"{template.rule_id}_P{pz_pos_str}Z{znp_pos_str}"

    return template.model_copy(update={
        "rule_id": new_rule_id,
        "section": new_section,
        "xpath_pz": abs_xpath_pz,
        "xpath_znp": abs_xpath_znp,
        # Сбрасываем list-поля — движок обрабатывает как обычное правило
        "list_xpath_pz": None,
        "list_xpath_znp": None,
        "list_key_pz": None,
        "list_key_znp": None,
        "match_filename_pz": None,
        "match_filename_znp": None,
        "match_checksum_pz": None,
        "match_checksum_znp": None,
    })


def _to_str(value) -> str:
    """Приводит lxml-значение к строке."""
    if isinstance(value, etree._Element):
        return "".join(value.itertext())
    return str(value)
