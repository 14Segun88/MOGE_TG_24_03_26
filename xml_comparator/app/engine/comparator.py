"""
Движок сравнения XML-документов.

ComparisonEngine:
  - принимает два XmlDocument и список MappingRule
  - для каждого правила извлекает значения, выбирает стратегию, выполняет сравнение
  - возвращает список CheckResult

Движок не знает про FastAPI — это чистый сервисный слой.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from app.models.comparison import CheckResult, CheckStatus, ExtractedValue
from app.models.mapping import CompareMode, MappingRule, RiskLevel
from app.parsers.xml_parser import XmlDocument
from app.strategies.registry import StrategyRegistry, default_registry
from app.engine.rule_expander import expand_list_rules

# Извлекает значение из строки вида "fixed '01.06'" → "01.06"
_FIXED_VALUE_RE = re.compile(r"fixed\s+'([^']+)'", re.IGNORECASE)

# Паттерн для мягкого сравнения: значение считается «пустым» если содержит
# слово «не» как отдельное слово (без учёта регистра).
# Примеры: «Не требуется», «не представляется», «Не разрабатывается»
_SOFT_EMPTY_PATTERN = re.compile(r"(?<!\w)не(?!\w)", re.IGNORECASE | re.UNICODE)

# Паттерн отрицания для режима BOOLEAN_SOFT (ПЗ):
# значение считается «отсутствующим» если содержит «не», «нет», «отсутствует»
# как отдельные слова. Примеры: «Не предусмотрено», «Нет», «Не разрабатывается».
_BOOLEAN_SOFT_NEGATION_PATTERN = re.compile(
    r"(?<!\w)(?:не|нет|отсутствует|отсутствуют)(?!\w)",
    re.IGNORECASE | re.UNICODE,
)

logger = logging.getLogger(__name__)


def _build_comparison_notes(rule: MappingRule) -> list[str]:
    """
    Формирует список пояснений об особенностях сравнения для данного правила.
    Вставляется в каждый CheckResult как поле comparison_notes,
    чтобы специалист мог понять логику без погружения в исходный код.
    """
    notes: list[str] = []

    # 1. Режим сравнения
    if rule.compare_mode is None:
        notes.append("Режим сравнения не задан — правило пропускается или обрабатывается по умолчанию.")
    elif rule.compare_mode == CompareMode.STRICT:
        notes.append(
            "Режим: Жёсткое сравнение. "
            "Нормализация: trim + Unicode NFC + lowercase. "
            "Значения должны совпадать побуквенно после нормализации."
        )
    elif rule.compare_mode == CompareMode.MEDIUM:
        notes.append(
            "Режим: Среднее сравнение. "
            "Нормализация: trim + Unicode NFC + кавычки → \" + тире → - + "
            "расшифровка ОПФ (ООО→«Общество с ограниченной ответственностью» и т.д.) + "
            "схлопывание пробелов + lowercase."
        )
    elif rule.compare_mode == CompareMode.SOFT:
        notes.append(
            "Режим: Мягкое сравнение. "
            "Значения, содержащие слово «не» как отдельное слово "
            "(«Не требуется», «Не разрабатывается»), считаются семантически пустыми. "
            "Если оба документа имеют значение (не отрицание) — проверяется факт наличия, "
            "а не буквальное совпадение."
        )
    elif rule.compare_mode == CompareMode.FIXED:
        notes.append(
            "Режим: Фиксированное значение. "
            "Каждый документ проверяется независимо на своё ожидаемое значение "
            "из полей type_pz / type_znp маппинга (формат: fixed 'X.XX'). "
            "Используется для проверки версий схем XML."
        )
    elif rule.compare_mode == CompareMode.BOOLEAN_SOFT:
        notes.append(
            "Режим: Булево-мягкое сравнение (BOOLEAN_SOFT). "
            "ЗнП содержит булев атрибут true/false. "
            "Если ЗнП = false — ПЗ должно быть пустым или содержать отрицание "
            "(«не», «нет», «отсутствует» как отдельное слово) — это не ошибка. "
            "Если ЗнП = true — ПЗ должно содержать непустое утвердительное значение "
            "(без слов «не», «нет», «отсутствует»). "
            "Применяется для поля PeoplePermanentStay (постоянное пребывание людей)."
        )
    elif rule.compare_mode == CompareMode.IFC_SOFT:
        notes.append(
            "Режим: ИМ-мягкое сравнение (IFC_SOFT). "
            "ЗнП содержит текстовые требования к информационной модели. "
            "Если ЗнП содержит отрицание («не», «нет», «отсутствует») — ИМ не требуется; "
            "ПЗ не должно содержать файлов .ifc/.xml. "
            "Если ЗнП содержит утвердительный текст — ИМ требуется; "
            "ПЗ должно содержать хотя бы один файл с расширением .ifc или .xml."
        )

    # 2. Уровень риска
    if rule.risk is None:
        pass  # нет риска — ничего особенного
    elif rule.risk == RiskLevel.LOW:
        notes.append(
            "Риск: Низкий. "
            "Поле считается критичным — расхождение фиксируется как ошибка. "
            "Применяется StrictScalarStrategy."
        )
    elif rule.risk == RiskLevel.MEDIUM:
        notes.append(
            "Риск: Средний. "
            "Применяется MediumScalarStrategy с расширенной нормализацией. "
            "Небольшие расхождения в форматировании могут быть проигнорированы."
        )
    elif rule.risk == RiskLevel.HIGH:
        notes.append(
            "Риск: Высокий. "
            "Форматы поля в ПЗ и ЗнП принципиально различаются "
            "(например, текстовое поле vs. числовой код). "
            "Сравнение носит информационный характер — расхождение может быть ожидаемым."
        )

    # 3. Множественные значения
    if rule.is_list_template:
        notes.append(
            "Правило является шаблоном списка: движок автоматически раскрывает его "
            "в несколько конкретных правил по уникальным ключам, "
            "найденным в обоих документах."
        )

    # 4. Только в одном документе
    if rule.has_pz and not rule.has_znp:
        notes.append(
            "Правило только для ПЗ: в ЗнП аналогичного поля нет. "
            "Сравнение не выполняется — фиксируется факт наличия значения в ПЗ."
        )
    elif rule.has_znp and not rule.has_pz:
        notes.append(
            "Правило только для ЗнП: в ПЗ аналогичного поля нет. "
            "Сравнение не выполняется — фиксируется факт наличия значения в ЗнП."
        )

    # 5. Подстановка типа объекта ПЗ
    if rule.xpath_pz and "{ТипОбъекта}" in rule.xpath_pz:
        notes.append(
            "XPath ПЗ содержит плейсхолдер {ТипОбъекта}. "
            "Движок автоматически подставляет тип объекта на основе корневого элемента ПЗ: "
            "NonIndustrialObject, IndustrialObject или LinearObject."
        )

    return notes


class ComparisonEngine:
    """
    Движок сравнения двух XML-документов по набору правил маппинга.

    Параметр `registry` позволяет подключить кастомный реестр стратегий
    (полезно для тестирования и расширений).
    """

    def __init__(self, registry: Optional[StrategyRegistry] = None) -> None:
        self._registry = registry or default_registry

    def run(
        self,
        doc_pz: XmlDocument,
        doc_znp: XmlDocument,
        rules: list[MappingRule],
    ) -> list[CheckResult]:
        """
        Запускает сравнение для каждого правила маппинга.

        Returns
        -------
        Список CheckResult в том же порядке, что и правила.
        """
        # Раскрываем шаблонные правила (попарное сопоставление по ключу)
        expanded_rules = expand_list_rules(rules, doc_pz._tree, doc_znp._tree)

        results: list[CheckResult] = []
        for rule in expanded_rules:
            try:
                result = self._process_rule(rule, doc_pz, doc_znp)
            except Exception as exc:
                logger.exception("Ошибка при обработке правила %s: %s", rule.rule_id, exc)
                result = self._make_error_result(rule, str(exc))
            results.append(result)
        return results

    # ──────────────────────────────────────────────────────────────────────────
    # Внутренняя логика
    # ──────────────────────────────────────────────────────────────────────────

    def _process_rule(
        self,
        rule: MappingRule,
        doc_pz: XmlDocument,
        doc_znp: XmlDocument,
    ) -> CheckResult:
        base = self._base_result(rule)

        # 1. Риск Высокий — сравниваем, но фиксируем предупреждение в reason
        # (форматы могут различаться, результат носит информационный характер)

        # Извлекаем XPath с подстановкой {ТипОбъекта} (для ПЗ) заранее
        val_pz = doc_pz.resolve_xpath(rule.xpath_pz) if rule.has_pz else None
        val_znp = doc_znp.resolve_xpath(rule.xpath_znp) if rule.has_znp else None

        # Сохраняем resolved-xpaths в результат для отображения в интерфейсе
        resolved_xpaths: dict = {}
        if val_pz is not None:
            resolved_xpaths["xpath_pz_resolved"] = val_pz.xpath_used
        if val_znp is not None:
            resolved_xpaths["xpath_znp_resolved"] = val_znp.xpath_used
        if resolved_xpaths:
            base = base.model_copy(update=resolved_xpaths)

        # 2. Правило только для ПЗ (нет XPath ЗнП)
        if rule.has_pz and not rule.has_znp:
            return base.model_copy(update={
                "status": CheckStatus.ONLY_PZ,
                "source_value_pz": val_pz.raw_values,
                "is_multi_pz": val_pz.is_multi,
                "message": "Поле есть только в ПЗ — в ЗнП аналога нет",
            })

        # 3. Правило только для ЗнП (нет XPath ПЗ)
        if rule.has_znp and not rule.has_pz:
            return base.model_copy(update={
                "status": CheckStatus.ONLY_ZNP,
                "source_value_znp": val_znp.raw_values,
                "is_multi_znp": val_znp.is_multi,
                "message": "Поле есть только в ЗнП — в ПЗ аналога нет",
            })

        # 4. У правила нет XPath вообще — пропускаем
        if not rule.has_pz and not rule.has_znp:
            return base.model_copy(update={
                "status": CheckStatus.SKIPPED,
                "message": "Поле пропущено — XPath не задан ни для ПЗ, ни для ЗнП",
            })

        # 5. Фиксированная валидация: каждый документ проверяется на своё значение
        if rule.compare_mode == CompareMode.FIXED:
            return self._compare_fixed(rule, val_pz, val_znp, base)

        # 5b. Булево-мягкое сравнение (BOOLEAN_SOFT):
        if rule.compare_mode == CompareMode.BOOLEAN_SOFT:
            return self._compare_boolean_soft(rule, val_pz, val_znp, base)

        # 5c. ИМ-мягкое сравнение (IFC_SOFT):
        if rule.compare_mode == CompareMode.IFC_SOFT:
            return self._compare_ifc_soft(rule, val_pz, val_znp, base)

        # 6. Выполняем сравнение
        return self._compare_values(rule, val_pz, val_znp, base)

    @staticmethod
    def _is_boolean_soft_negation(values: list[str]) -> bool:
        """
        Для режима BOOLEAN_SOFT: значение считается «отрицательным» (нет постоянного пребывания),
        если все непустые строки содержат хотя бы одно из слов: «не», «нет», «отсутствует».
        Примеры: «Не предусмотрено», «Нет», «Не разрабатывается», «отсутствует».
        """
        return bool(values) and all(
            _BOOLEAN_SOFT_NEGATION_PATTERN.search(v) for v in values if v.strip()
        )

    @staticmethod
    def _is_soft_empty(values: list[str]) -> bool:
        """
        Для мягкого (SOFT) сравнения: значение считается отсутствующим,
        если в нём встречается слово «не» как отдельное слово.
        Примеры: «Не требуется», «Не представляется», «Не разрабатывается».
        """
        return bool(values) and all(
            _SOFT_EMPTY_PATTERN.search(v) for v in values if v.strip()
        )

    def _compare_values(
        self,
        rule: MappingRule,
        val_pz: ExtractedValue,
        val_znp: ExtractedValue,
        base: CheckResult,
    ) -> CheckResult:
        """Выполняет сравнение двух ExtractedValue согласно правилу."""

        is_soft = rule.compare_mode == CompareMode.SOFT

        # Для мягкого сравнения: поля с «не» считаются отсутствующими
        if is_soft:
            if not val_pz.is_empty and self._is_soft_empty(val_pz.raw_values):
                val_pz = val_pz.model_copy(update={"raw_values": [], "is_empty": True, "is_multi": False})
            if not val_znp.is_empty and self._is_soft_empty(val_znp.raw_values):
                val_znp = val_znp.model_copy(update={"raw_values": [], "is_empty": True, "is_multi": False})

        _high_risk_note = " ⚠ Форматы полей различаются — сравнение приблизительное" if rule.risk == RiskLevel.HIGH else ""

        # Отсутствие значений — не ошибка, отдельный статус
        if val_pz.is_empty and val_znp.is_empty:
            return base.model_copy(update={
                "status": CheckStatus.MISSING_BOTH,
                "message": f"Поле отсутствует в обоих документах{_high_risk_note}",
                "source_value_pz": None,
                "source_value_znp": None,
                "is_multi_pz": False,
                "is_multi_znp": False,
            })

        if val_pz.is_empty:
            return base.model_copy(update={
                "status": CheckStatus.MISSING_PZ,
                "message": f"Поле не заполнено в ПЗ{_high_risk_note}",
                "source_value_pz": None,
                "source_value_znp": val_znp.raw_values,
                "expected_value": val_znp.raw_values[0] if val_znp.raw_values else None,
                "is_multi_znp": val_znp.is_multi,
            })

        if val_znp.is_empty:
            pz_raw = val_pz.raw_values[0] if val_pz.raw_values else "—"
            return base.model_copy(update={
                "status": CheckStatus.MISSING_ZNP,
                "message": f"Поле не заполнено в ЗнП, значение в ПЗ: «{pz_raw}»{_high_risk_note}",
                "source_value_pz": val_pz.raw_values,
                "source_value_znp": None,
                "expected_value": None,
                "is_multi_pz": val_pz.is_multi,
            })

        # Для мягкого сравнения: если оба поля присутствуют — сравнение значений не проводится,
        # достаточно факта наличия поля в обоих документах.
        if is_soft:
            return base.model_copy(update={
                "status": CheckStatus.SUCCESS,
                "message": "Поле присутствует в обоих документах (мягкое сравнение — значения не проверяются)" + _high_risk_note,
                "source_value_pz": val_pz.raw_values if val_pz.is_multi else val_pz.scalar,
                "source_value_znp": val_znp.raw_values if val_znp.is_multi else val_znp.scalar,
                "is_multi_pz": val_pz.is_multi,
                "is_multi_znp": val_znp.is_multi,
            })

        # Оба значения есть — применяем стратегию
        strategy = self._registry.resolve(rule.risk, rule.compare_mode)
        strategy_result = strategy.compare(val_pz.raw_values, val_znp.raw_values)

        status = CheckStatus.SUCCESS if strategy_result.is_equal else CheckStatus.FAILED

        expected = (
            strategy_result.normalized_znp
            if not isinstance(strategy_result.normalized_znp, list)
            else val_znp.raw_values
        )

        if status == CheckStatus.SUCCESS:
            msg = "Значения совпадают" + (_high_risk_note if rule.risk == RiskLevel.HIGH else "")
        else:
            expected_raw = val_pz.raw_values[0] if not val_pz.is_multi else val_pz.raw_values
            actual_raw = val_znp.raw_values[0] if not val_znp.is_multi else val_znp.raw_values
            msg = f"Расхождение: ПЗ содержит «{expected_raw}», а ЗнП — «{actual_raw}»"
            if rule.risk == RiskLevel.HIGH:
                msg += " ⚠ Форматы полей различаются — расхождение может быть ожидаемым"

        return base.model_copy(update={
            "status": status,
            "message": msg,
            "source_value_pz": val_pz.raw_values if val_pz.is_multi else val_pz.scalar,
            "source_value_znp": val_znp.raw_values if val_znp.is_multi else val_znp.scalar,
            "normalized_value_pz": strategy_result.normalized_pz,
            "normalized_value_znp": strategy_result.normalized_znp,
            "expected_value": expected,
            "is_multi_pz": val_pz.is_multi,
            "is_multi_znp": val_znp.is_multi,
            "details": strategy_result.details,
        })

    def _compare_boolean_soft(
        self,
        rule: MappingRule,
        val_pz: ExtractedValue,
        val_znp: ExtractedValue,
        base: CheckResult,
    ) -> CheckResult:
        """
        Режим BOOLEAN_SOFT — для полей типа «Постоянное пребывание людей» (PeoplePermanentStay).

        Логика:
          ЗнП содержит булев атрибут (true / false):
          • false → постоянного пребывания нет.
                    ПЗ должно быть пустым или содержать отрицание («не», «нет», «отсутствует»).
                    Наличие значения с отрицанием или отсутствие значения — ОК.
          • true  → постоянное пребывание есть.
                    ПЗ должно содержать непустое значение без отрицания.
          • Значение ЗнП не распознано / отсутствует → статус MISSING_ZNP.

        Значения «не», «нет», «отсутствует» в ПЗ считаются семантически равными пустому полю
        (аналогично тому, как /Document/Content/Object/InformationModel обрабатывает отрицания).
        """
        znp_raw = val_znp.scalar.strip().lower() if val_znp.scalar else ""

        if val_znp.is_empty or znp_raw not in ("true", "false"):
            return base.model_copy(update={
                "status": CheckStatus.MISSING_ZNP,
                "source_value_pz": val_pz.raw_values if not val_pz.is_empty else None,
                "source_value_znp": val_znp.raw_values if not val_znp.is_empty else None,
                "message": (
                    "Значение атрибута ЗнП не распознано как булево (ожидается true/false); "
                    f"получено: «{val_znp.scalar}»"
                    if not val_znp.is_empty
                    else "Атрибут ЗнП отсутствует — не удалось определить наличие постоянного пребывания"
                ),
            })

        znp_flag = znp_raw == "true"

        # ПЗ пусто или содержит отрицание?
        pz_is_negated = val_pz.is_empty or self._is_boolean_soft_negation(val_pz.raw_values)

        if not znp_flag:
            # ЗнП = false → в ПЗ ожидаем отсутствие/отрицание
            if pz_is_negated:
                return base.model_copy(update={
                    "status": CheckStatus.SUCCESS,
                    "source_value_pz": val_pz.raw_values if not val_pz.is_empty else None,
                    "source_value_znp": val_znp.scalar,
                    "message": (
                        "ЗнП: постоянного пребывания нет (false). "
                        "ПЗ: значение отсутствует или содержит отрицание — соответствует."
                    ),
                })
            else:
                return base.model_copy(update={
                    "status": CheckStatus.FAILED,
                    "source_value_pz": val_pz.raw_values,
                    "source_value_znp": val_znp.scalar,
                    "message": (
                        "ЗнП: постоянного пребывания нет (false). "
                        f"ПЗ: содержит утвердительное значение «{val_pz.scalar}» — расхождение."
                    ),
                })
        else:
            # ЗнП = true → в ПЗ ожидаем непустое значение без отрицания
            if not pz_is_negated and not val_pz.is_empty:
                return base.model_copy(update={
                    "status": CheckStatus.SUCCESS,
                    "source_value_pz": val_pz.raw_values if val_pz.is_multi else val_pz.scalar,
                    "source_value_znp": val_znp.scalar,
                    "message": (
                        "ЗнП: постоянное пребывание есть (true). "
                        "ПЗ: содержит утвердительное значение — соответствует."
                    ),
                })
            else:
                return base.model_copy(update={
                    "status": CheckStatus.FAILED,
                    "source_value_pz": val_pz.raw_values if not val_pz.is_empty else None,
                    "source_value_znp": val_znp.scalar,
                    "message": (
                        "ЗнП: постоянное пребывание есть (true). "
                        "ПЗ: значение отсутствует или содержит отрицание — расхождение."
                    ),
                })

    @staticmethod
    def _has_ifc_or_xml_file(values: list[str]) -> bool:
        """
        Возвращает True, если среди имён файлов есть хотя бы один с расширением .ifc или .xml
        (без учёта регистра).
        """
        return any(v.strip().lower().endswith((".ifc", ".xml")) for v in values if v.strip())

    def _compare_ifc_soft(
        self,
        rule: MappingRule,
        val_pz: ExtractedValue,
        val_znp: ExtractedValue,
        base: CheckResult,
    ) -> CheckResult:
        """
        Режим IFC_SOFT — для поля «Информационная модель».

        Логика:
          ЗнП содержит текстовые требования к ИМ:
          • Отрицание («не», «нет», «отсутствует») → ИМ не требуется.
            ПЗ не должно содержать файлов .ifc/.xml → OK.
            Если ПЗ содержит такие файлы → FAILED.
          • Утвердительный текст → ИМ требуется.
            ПЗ должно содержать хотя бы один файл .ifc или .xml → OK.
            Если ПЗ не содержит → FAILED.
          • ЗнП пусто → MISSING_ZNP.
        """
        if val_znp.is_empty:
            return base.model_copy(update={
                "status": CheckStatus.MISSING_ZNP,
                "source_value_pz": val_pz.raw_values if not val_pz.is_empty else None,
                "source_value_znp": None,
                "message": "Поле требований к ИМ отсутствует в ЗнП — невозможно определить наличие требования",
            })

        znp_is_negated = self._is_boolean_soft_negation(val_znp.raw_values)
        pz_has_model = self._has_ifc_or_xml_file(val_pz.raw_values)
        pz_files_display = val_pz.raw_values if not val_pz.is_empty else None

        if znp_is_negated:
            # ЗнП: ИМ не требуется
            if not pz_has_model:
                return base.model_copy(update={
                    "status": CheckStatus.SUCCESS,
                    "source_value_pz": pz_files_display,
                    "source_value_znp": val_znp.raw_values if val_znp.is_multi else val_znp.scalar,
                    "message": (
                        "ЗнП: подготовка ИМ не требуется (содержит отрицание). "
                        "ПЗ: файлы .ifc/.xml отсутствуют — соответствует."
                    ),
                })
            else:
                ifc_files = [v for v in val_pz.raw_values if v.strip().lower().endswith((".ifc", ".xml"))]
                return base.model_copy(update={
                    "status": CheckStatus.FAILED,
                    "source_value_pz": val_pz.raw_values,
                    "source_value_znp": val_znp.raw_values if val_znp.is_multi else val_znp.scalar,
                    "message": (
                        "ЗнП: подготовка ИМ не требуется (содержит отрицание). "
                        f"ПЗ: содержит файл(ы) модели {ifc_files} — расхождение."
                    ),
                })
        else:
            # ЗнП: ИМ требуется
            if pz_has_model:
                ifc_files = [v for v in val_pz.raw_values if v.strip().lower().endswith((".ifc", ".xml"))]
                return base.model_copy(update={
                    "status": CheckStatus.SUCCESS,
                    "source_value_pz": val_pz.raw_values,
                    "source_value_znp": val_znp.raw_values if val_znp.is_multi else val_znp.scalar,
                    "message": (
                        "ЗнП: подготовка ИМ требуется. "
                        f"ПЗ: содержит файл(ы) модели {ifc_files} — соответствует."
                    ),
                })
            else:
                return base.model_copy(update={
                    "status": CheckStatus.FAILED,
                    "source_value_pz": pz_files_display,
                    "source_value_znp": val_znp.raw_values if val_znp.is_multi else val_znp.scalar,
                    "message": (
                        "ЗнП: подготовка ИМ требуется. "
                        "ПЗ: не содержит файлов с расширением .ifc или .xml — расхождение."
                    ),
                })

    def _compare_fixed(
        self,
        rule: MappingRule,
        val_pz: ExtractedValue,
        val_znp: ExtractedValue,
        base: CheckResult,
    ) -> CheckResult:
        """
        Валидация фиксированных версий схем.

        Ожидаемые значения берутся из type_pz/type_znp в формате "fixed 'X.XX'".
        Каждый документ проверяется на своё значение независимо.
        Если хотя бы одно не совпадает — статус FAILED (ошибка).
        """
        def extract_expected(type_str: Optional[str]) -> Optional[str]:
            if not type_str:
                return None
            m = _FIXED_VALUE_RE.search(type_str)
            return m.group(1) if m else None

        exp_pz = extract_expected(rule.type_pz)
        exp_znp = extract_expected(rule.type_znp)

        actual_pz = val_pz.scalar if not val_pz.is_empty else None
        actual_znp = val_znp.scalar if not val_znp.is_empty else None

        errors: list[str] = []

        if exp_pz is not None:
            if actual_pz is None:
                errors.append(f"ПЗ: значение отсутствует, ожидалось '{exp_pz}'")
            elif actual_pz.strip() != exp_pz:
                errors.append(f"ПЗ: '{actual_pz}' ≠ '{exp_pz}'")

        if exp_znp is not None:
            if actual_znp is None:
                errors.append(f"ЗнП: значение отсутствует, ожидалось '{exp_znp}'")
            elif actual_znp.strip() != exp_znp:
                errors.append(f"ЗнП: '{actual_znp}' ≠ '{exp_znp}'")

        is_ok = len(errors) == 0
        status = CheckStatus.SUCCESS if is_ok else CheckStatus.FAILED
        message = (
            f"Версии схем соответствуют требованиям (ПЗ: «{actual_pz}», ЗнП: «{actual_znp}»)"
            if is_ok
            else "Версия схемы не совпадает с ожидаемой: " + "; ".join(errors)
        )

        return base.model_copy(update={
            "status": status,
            "message": message,
            "source_value_pz": actual_pz,
            "source_value_znp": actual_znp,
            "expected_value": f"ПЗ='{exp_pz}', ЗнП='{exp_znp}'",
            "details": {
                "expected_pz": exp_pz,
                "expected_znp": exp_znp,
                "actual_pz": actual_pz,
                "actual_znp": actual_znp,
                "errors": errors,
            },
        })

    def _base_result(self, rule: MappingRule) -> CheckResult:
        """Создаёт базовый CheckResult из правила с дефолтными полями."""
        return CheckResult(
            rule_id=rule.rule_id,
            section=rule.section,
            subsection=rule.subsection,
            label_pz=rule.label_pz or rule.field_name_pz or rule.xpath_pz,
            label_znp=rule.label_znp or rule.field_name_znp or rule.xpath_znp,
            xpath_pz=rule.xpath_pz,
            xpath_znp=rule.xpath_znp,
            risk=rule.risk.value if rule.risk else None,
            compare_mode=rule.compare_mode.value if rule.compare_mode else None,
            status=CheckStatus.ERROR,
            applicability=rule.applicability,
            comment=rule.comment,
            reason=rule.reason,
            field_name_pz=rule.field_name_pz or "",
            required_pz=rule.required_pz or "",
            field_name_znp=rule.field_name_znp or "",
            required_znp=rule.required_znp or "",
            comparison_notes=_build_comparison_notes(rule),
        )

    def _make_error_result(self, rule: MappingRule, error_msg: str) -> CheckResult:
        """Создаёт CheckResult со статусом ERROR."""
        base = self._base_result(rule)
        return base.model_copy(update={
            "status": CheckStatus.ERROR,
            "message": f"Ошибка обработки правила: {error_msg}",
            "details": {"exception": error_msg},
        })
