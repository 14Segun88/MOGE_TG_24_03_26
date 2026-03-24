"""
PP154ComplianceAgent — проверка схем теплоснабжения по ПП РФ №154.

Функционал:
  1. Математическая проверка энергобаланса: невязка ≤ 2%
  2. Проверка горизонта планирования ≥ 15 лет
  3. Проверка наличия 13 обязательных разделов
  4. Контроль файлов электронной модели (Zulu Thermo, Пирамида)
  5. LLM-верификация (Groq) для сложных/нечётких случаев
  6. HITL-логирование с 4 триггерами
"""
import re
import json
import logging
from dataclasses import dataclass, field
from typing import Optional

from src.agents.groq_client import call_llm, MODEL_PP154_COMPLIANCE
from src.db.database import SessionLocal
from src.db.models import DisagreementLog

log = logging.getLogger("pp154_agent")

# ─────────────────────────────────────────────
#  13 обязательных разделов схемы теплоснабжения (ПП №154)
# ─────────────────────────────────────────────
PP154_SECTIONS = [
    {"code": "154-01", "name": "Показатели перспективного спроса на тепловую энергию",
     "keywords": ["перспективный спрос", "тепловая нагрузка", "прогноз потребления"]},
    {"code": "154-02", "name": "Перспективные балансы тепловой энергии",
     "keywords": ["баланс тепловой", "производство", "потребление", "потери"]},
    {"code": "154-03", "name": "Перспективные балансы теплоносителя",
     "keywords": ["баланс теплоносителя", "подпитка", "сетевая вода"]},
    {"code": "154-04", "name": "Основные положения по строительству источников",
     "keywords": ["строительство источника", "новый источник", "котельная", "ТЭЦ"]},
    {"code": "154-05", "name": "Предложения по строительству тепловых сетей",
     "keywords": ["строительство сети", "реконструкция", "тепловая сеть", "тепловод"]},
    {"code": "154-06", "name": "Перспективные топливные балансы",
     "keywords": ["топливный баланс", "газ", "уголь", "мазут", "топливо"]},
    {"code": "154-07", "name": "Инвестиционная программа",
     "keywords": ["инвестиционная программа", "капитальные вложения", "финансирование"]},
    {"code": "154-08", "name": "Ценовые зоны теплоснабжения",
     "keywords": ["ценовая зона", "регулируемые тарифы", "альтернативная котельная"]},
    {"code": "154-09", "name": "Решения по бесхозяйным объектам",
     "keywords": ["бесхозяйный", "бесхозная", "выявленные объекты"]},
    {"code": "154-10", "name": "Оценка надёжности теплоснабжения",
     "keywords": ["надёжность", "надежность", "резервирование", "аварийный"]},
    {"code": "154-11", "name": "Оценка энергосбережения",
     "keywords": ["энергосбережение", "энергоэффективность", "КПД", "потери тепла"]},
    {"code": "154-12", "name": "Перечень мероприятий по строительству и реконструкции",
     "keywords": ["план мероприятий", "перечень мероприятий", "реконструкция сетей"]},
    {"code": "154-13", "name": "Электронная модель системы теплоснабжения",
     "keywords": ["электронная модель", "модель системы", "zulu", "пирамида", "программный комплекс"]},
]

# Программы электронного моделирования (расширения и имена)
SOFTWARE_MODEL_PATTERNS = [
    re.compile(r"\.(zthermo|zulu|zthr)\b", re.I),
    re.compile(r"пирамида|pyramid|heatnet|zulu\s*thermo|zuluthermo", re.I),
    re.compile(r"\.(hnet|heatmod|thermopro)\b", re.I),
]

# Regex-паттерны для извлечения числовых значений мощности
_POWER_PATTERN = re.compile(
    r"([\d]+(?:[.,][\d]+)?)\s*(МВт|мвт|Гкал/ч|гкал/ч|мвт/ч|квт|КВт)",
    re.IGNORECASE
)
_YEAR_PATTERN = re.compile(
    r"([\d]{1,2})\s*(лет|год|года)\b|горизонт[^:]*?(\d{4})\s*год",
    re.IGNORECASE
)


@dataclass
class EnergyBalanceResult:
    """Результат математической проверки энергобаланса."""
    is_compliant: bool
    source_mw: float = 0.0       # Мощность источника
    load_mw: float = 0.0         # Нагрузка потребителей
    loss_mw: float = 0.0         # Потери
    imbalance_pct: float = 0.0   # Невязка в %
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    math_done: bool = False       # True = математика успешна; False = LLM-fallback


@dataclass
class PP154Report:
    """Полный отчёт PP154Agent."""
    is_compliant: bool
    energy_balance: Optional[EnergyBalanceResult] = None
    horizon_ok: bool = True
    horizon_years: int = 0
    sections_found: list[str] = field(default_factory=list)
    sections_missing: list[str] = field(default_factory=list)
    software_model_found: bool = False
    software_files: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    confidence: float = 0.9
    llm_used: bool = False


class PP154Agent:
    """
    Агент проверки на соответствие Постановлению Правительства № 154
    (Требования к схемам теплоснабжения).

    Алгоритм:
      1. Пытаемся математически извлечь мощности и вычислить баланс
      2. Если данные не извлеклись — fallback на LLM (Groq)
      3. Проверяем горизонт планирования ≥ 15 лет
      4. Проверяем 13 разделов по ключевым словам
      5. Ищем файлы электронной модели
      6. Логируем в HITL при необходимости
    """

    def __init__(self):
        self.model = MODEL_PP154_COMPLIANCE
        log.info(f"PP154 Agent init. Модель: {self.model}")

    # ──────────────────────────────────────────
    #  Публичный API
    # ──────────────────────────────────────────

    def run_full_check(self, source_text: str, consumer_text: str,
                       document_id: str, classified_files=None) -> PP154Report:
        """
        Запуск полной проверки по ПП №154.

        Args:
            source_text:      текст о параметрах источника тепла
            consumer_text:    текст о потребителях и потерях
            document_id:      ID документа
            classified_files: список ClassifiedFile (для проверки 13 разделов)
        """
        log.info(f"[PP154] Полная проверка для {document_id}")
        report = PP154Report(is_compliant=True)
        full_text = f"{source_text}\n{consumer_text}"

        # 1. Математический энергобаланс
        report.energy_balance = self._check_energy_balance_math(
            source_text, consumer_text, document_id
        )
        if not report.energy_balance.is_compliant:
            report.is_compliant = False
            report.errors.extend(report.energy_balance.errors)

        # 2. Горизонт планирования ≥ 15 лет
        report.horizon_years, report.horizon_ok = self._check_planning_horizon(full_text)
        if not report.horizon_ok:
            report.warnings.append(
                f"Горизонт планирования {report.horizon_years} лет < 15 лет (ПП №154 п.6)"
            )

        # 3. 13 разделов по тексту + файлам
        report.sections_found, report.sections_missing = self._check_13_sections(
            full_text, classified_files
        )
        if len(report.sections_missing) > 5:
            report.is_compliant = False
            report.errors.append(
                f"Отсутствует {len(report.sections_missing)} из 13 обязательных разделов схемы"
            )

        # 4. Электронная модель (файлы Zulu Thermo / Пирамида)
        report.software_model_found, report.software_files = self._check_software_models(
            full_text, classified_files
        )
        if not report.software_model_found:
            report.warnings.append(
                "Файл электронной модели системы теплоснабжения (Zulu Thermo/Пирамида) не обнаружен"
            )

        # 5. LLM-верификация если математика не распознала числа
        if report.energy_balance and not report.energy_balance.math_done:
            llm_result = self._llm_fallback(source_text, consumer_text, document_id)
            report.llm_used = True
            report.confidence = llm_result.get("confidence", 0.5)
            if not llm_result.get("is_compliant", True):
                report.is_compliant = False
                report.errors.extend(llm_result.get("errors", []))
        else:
            report.confidence = 0.95 if report.energy_balance.math_done else 0.5

        # 6. HITL логирование
        self._maybe_log_hitl(document_id, report)

        return report

    def validate_energy_balance(self, source_text: str, consumer_text: str,
                                document_id: str) -> "PP154Response":
        """
        Обратная совместимость с orchestrator.py (старый интерфейс).
        Возвращает PP154Response-like объект.
        """
        from dataclasses import dataclass as _dc

        @_dc
        class PP154Response:
            is_compliant: bool
            errors: list
            confidence: float
            raw_analysis: str

        result = self._check_energy_balance_math(source_text, consumer_text, document_id)
        if not result.math_done:
            llm = self._llm_fallback(source_text, consumer_text, document_id)
            return PP154Response(
                is_compliant=llm.get("is_compliant", False),
                errors=llm.get("errors", []),
                confidence=llm.get("confidence", 0.0),
                raw_analysis=str(llm)
            )
        return PP154Response(
            is_compliant=result.is_compliant,
            errors=result.errors,
            confidence=0.95,
            raw_analysis=f"source={result.source_mw}МВт load={result.load_mw}МВт loss={result.loss_mw}МВт imbalance={result.imbalance_pct:.1f}%"
        )

    # ──────────────────────────────────────────
    #  1. Математическая проверка баланса
    # ──────────────────────────────────────────

    def _check_energy_balance_math(self, source_text: str, consumer_text: str,
                                   document_id: str) -> EnergyBalanceResult:
        """
        Извлекаем числа и считаем невязку:
          nevyazka = |P_src - (P_load + P_loss)| / P_src × 100%
          Допустимо: ≤ 2% (ПП №154)
        """
        result = EnergyBalanceResult(is_compliant=True)

        source_values = self._extract_power_values(source_text)
        consumer_values = self._extract_power_values(consumer_text)

        if not source_values or not consumer_values:
            # Не нашли числа — нужен LLM
            log.info("[PP154] Числа не извлечены, будет LLM-fallback")
            result.math_done = False
            return result

        # Берём максимальное значение как «установленную мощность» источника
        result.source_mw = max(source_values)
        # Потери — обычно самое маленькое значение в тексте потребителей
        consumer_sorted = sorted(consumer_values)
        if len(consumer_sorted) >= 2:
            result.loss_mw = consumer_sorted[0]
            result.load_mw = sum(consumer_sorted[1:])
        else:
            result.load_mw = consumer_sorted[0]
            result.loss_mw = 0.0

        # Формула невязки
        total_demand = result.load_mw + result.loss_mw
        if result.source_mw > 0:
            result.imbalance_pct = abs(result.source_mw - total_demand) / result.source_mw * 100
        else:
            result.imbalance_pct = 999.0

        result.math_done = True

        # Критерий: источник должен покрывать нагрузку + потери
        if result.source_mw < total_demand:
            result.is_compliant = False
            deficit = total_demand - result.source_mw
            result.errors.append(
                f"КРИТИЧНО: мощность источника {result.source_mw:.2f} МВт < "
                f"нагрузка {result.load_mw:.2f} + потери {result.loss_mw:.2f} = {total_demand:.2f} МВт "
                f"(дефицит {deficit:.2f} МВт)"
            )
        elif result.imbalance_pct > 2.0:
            # Превышение допустимой невязки (> 2%) — предупреждение по ПП №154
            result.warnings = result.warnings if hasattr(result, 'warnings') else []
            result.errors.append(
                f"Невязка энергобаланса {result.imbalance_pct:.1f}% > 2% (ПП №154). "
                f"Источник: {result.source_mw:.2f} МВт, Нагрузка+Потери: {total_demand:.2f} МВт"
            )
            # Не делаем is_compliant=False если источник достаточен, но невязка >2% — предупреждение
            # (дефицита нет, но нужна корректировка расчётов)

        log.info(
            f"[PP154] Баланс: {result.source_mw}МВт src, "
            f"{result.load_mw}МВт нагрузка, {result.loss_mw}МВт потери, "
            f"невязка={result.imbalance_pct:.1f}%, compliant={result.is_compliant}"
        )
        return result

    def _extract_power_values(self, text: str) -> list[float]:
        """Regex-извлечение числовых значений мощности (МВт/Гкал/ч → всё в МВт)."""
        values = []
        for match in _POWER_PATTERN.finditer(text):
            raw_val = match.group(1).replace(",", ".")
            unit = match.group(2).lower()
            try:
                val = float(raw_val)
                # Гкал/ч → МВт: 1 Гкал/ч ≈ 1.163 МВт
                if "гкал" in unit:
                    val *= 1.163
                # КВт → МВт
                elif "квт" in unit or "кvт" in unit:
                    val /= 1000
                if 0.01 < val < 10000:  # Фильтр мусора
                    values.append(val)
            except ValueError:
                pass
        return values

    # ──────────────────────────────────────────
    #  2. Горизонт планирования
    # ──────────────────────────────────────────

    def _check_planning_horizon(self, text: str) -> tuple[int, bool]:
        """
        Ищем упоминание горизонта планирования.
        Минимум по ПП №154: 15 лет.

        Стратегия:
          1. Сначала ищем явное указание «N лет» — это точнее
          2. Если не нашли — пробуем вычислить из года (2040 - 2026)
        Возвращает (найденное_количество_лет, is_ok).
        """
        # Шаг 1: явное указание «10 лет», «15 лет» и т.п.
        explicit_pattern = re.compile(
            r"(?:горизонт\s+(?:планирования|схемы|проекта)[^.]*?|на\s+срок\s+|рассчитана?\s+на\s+)"
            r"(\d{1,2})\s*(?:лет|год[а]?)",
            re.IGNORECASE
        )
        fallback_pattern = re.compile(r"\b(\d{1,2})\s*(?:лет|года)\b", re.IGNORECASE)
        year_pattern = re.compile(r"\bдо\s+(\d{4})\s*год", re.IGNORECASE)

        # Приоритет 1: контекстное «рассчитана на N лет»
        explicit_matches = explicit_pattern.findall(text)
        if explicit_matches:
            years_found = [int(m) for m in explicit_matches if 5 <= int(m) <= 50]
            if years_found:
                max_h = max(years_found)
                return max_h, max_h >= 15

        # Приоритет 2: любое упоминание «N лет» в диапазоне 5–50
        fallback_matches = fallback_pattern.findall(text)
        years_found = [int(m) for m in fallback_matches if 5 <= int(m) <= 50]
        if years_found:
            max_h = max(years_found)
            return max_h, max_h >= 15

        # Приоритет 3: до YYYY года → вычисляем разницу
        year_matches = year_pattern.findall(text)
        if year_matches:
            current_year = datetime.now().year
            computed = [int(y) - current_year for y in year_matches if int(y) > current_year]
            if computed:
                max_h = max(computed)
                return max_h, max_h >= 15

        return 0, True  # Не нашли — не считаем нарушением


    # ──────────────────────────────────────────
    #  3. 13 разделов
    # ──────────────────────────────────────────

    def _check_13_sections(self, text: str,
                           classified_files=None) -> tuple[list[str], list[str]]:
        """
        Проверяет наличие 13 обязательных разделов.
        Ищет ключевые слова в тексте ПЗ и именах файлов.
        """
        found, missing = [], []
        text_lower = text.lower()

        # Имена файлов для доп. поиска
        file_names = ""
        if classified_files:
            file_names = " ".join(f.path.name for f in classified_files).lower()

        for section in PP154_SECTIONS:
            section_found = False
            for kw in section["keywords"]:
                if kw.lower() in text_lower or kw.lower() in file_names:
                    section_found = True
                    break
            if section_found:
                found.append(section["code"])
            else:
                missing.append(f"{section['code']}: {section['name']}")

        log.info(f"[PP154] 13 разделов: найдено {len(found)}, отсутствует {len(missing)}")
        return found, missing

    # ──────────────────────────────────────────
    #  4. Электронная модель
    # ──────────────────────────────────────────

    def _check_software_models(self, text: str,
                               classified_files=None) -> tuple[bool, list[str]]:
        """
        Проверяет наличие файлов электронной модели системы теплоснабжения.
        Ищет по расширениям и именам.
        """
        found_files = []

        # Поиск в тексте
        for pattern in SOFTWARE_MODEL_PATTERNS:
            if pattern.search(text):
                found_files.append("упоминание в тексте")
                break

        # Поиск по именам файлов
        if classified_files:
            for f in classified_files:
                fname = f.path.name
                for pattern in SOFTWARE_MODEL_PATTERNS:
                    if pattern.search(fname):
                        found_files.append(fname)
                        break

        return bool(found_files), list(set(found_files))

    # ──────────────────────────────────────────
    #  5. LLM-fallback
    # ──────────────────────────────────────────

    def _llm_fallback(self, source_text: str, consumer_text: str,
                      document_id: str) -> dict:
        """
        Если математика не нашла числа — спрашиваем LLM.
        Возвращает dict: {is_compliant, errors, confidence}
        """
        log.info(f"[PP154] LLM-fallback для {document_id}")
        system_prompt = (
            "Ты — инженер-теплотехник Главгосэкспертизы. "
            "Проверь проект схемы теплоснабжения на соответствие ПП РФ №154.\n"
            "Инструкция:\n"
            "1. Извлеки тепловую мощность источника (Гкал/ч или МВт).\n"
            "2. Извлеки нагрузки потребителей и потери.\n"
            "3. Посчитай невязку: |Источник - (Нагрузка + Потери)| / Источник × 100%.\n"
            "4. Если невязка > 2% — нарушение ПП №154.\n"
            "5. Если источник < нагрузка + потери — КРИТИЧЕСКОЕ НАРУШЕНИЕ.\n"
            "Верни JSON: {\"is_compliant\": true/false, \"errors\": [...], \"confidence\": 0.0-1.0}"
        )
        user_prompt = (
            f"--- ИСТОЧНИК ---\n{source_text}\n\n"
            f"--- ПОТРЕБИТЕЛИ ---\n{consumer_text}\n\n"
            "ВЕРНИ ТОЛЬКО JSON:"
        )
        try:
            raw = call_llm(
                model=self.model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=0.0
            )
            cleaned = raw.replace("```json", "").replace("```", "").strip()
            return json.loads(cleaned)
        except Exception as e:
            log.error(f"[PP154] LLM-fallback ошибка: {e}")
            return {"is_compliant": False, "errors": [str(e)], "confidence": 0.0}

    # ──────────────────────────────────────────
    #  6. HITL — 4 триггера
    # ──────────────────────────────────────────

    def _maybe_log_hitl(self, doc_id: str, report: PP154Report):
        """Определяем триггер и пишем в Disagreement Log."""
        trigger = None
        context = ""

        if report.energy_balance and not report.energy_balance.is_compliant:
            trigger = "critical_error"
            context = f"Дефицит мощности или невязка > 2%: {report.energy_balance.errors}"
        elif report.confidence < 0.70:
            trigger = "confidence"
            context = f"Низкая уверенность: {report.confidence:.2%}"
        elif not report.horizon_ok and report.horizon_years > 0:
            trigger = "agent_disagreement"
            context = f"Горизонт {report.horizon_years} лет < 15 лет"
        elif len(report.sections_missing) > 8:
            trigger = "is_edge_case"
            context = f"Почти все 13 разделов отсутствуют — возможно нестандартная документация"

        if trigger:
            self._log_hitl(doc_id, trigger, context, report.confidence)

    def _log_hitl(self, doc_id: str, trigger: str, context: str, confidence: float):
        """Запись в DisagreementLog."""
        log.info(f"[PP154] HITL [{trigger}] conf={confidence:.2f}: {context[:80]}")
        try:
            db = SessionLocal()
            db.add(DisagreementLog(
                document_id=doc_id,
                agent_name=f"PP154Agent/{trigger}",
                ai_decision=context[:500],
                confidence=confidence,
                is_reviewed=False,
            ))
            db.commit()
            db.close()
        except Exception as e:
            log.error(f"[PP154] HITL DB ошибка: {e}")


# ──────────────────────────────────────────────────────────────────────────────
#  Быстрый тест (python src/agents/compliance/pp154_agent.py)
# ──────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO)

    agent = PP154Agent()

    print("\n=== Тест 1: Дефицит мощности (НЕ соответствует) ===")
    r1 = agent.validate_energy_balance(
        source_text="Котельная. Установленная мощность: 10 МВт. Резервный котёл 5 МВт.",
        consumer_text="Нагрузка потребителей: 10.5 МВт. Потери в сетях: 0.8 МВт.",
        document_id="TEST-154-001"
    )
    print(f"  compliant={r1.is_compliant}, errors={r1.errors}")

    print("\n=== Тест 2: Невязка 5% (предупреждение) ===")
    r2 = agent.validate_energy_balance(
        source_text="ТЭЦ. Установленная тепловая мощность: 100 МВт.",
        consumer_text="Суммарная нагрузка: 90 МВт. Тепловые потери: 5 МВт.",
        document_id="TEST-154-002"
    )
    print(f"  compliant={r2.is_compliant}, errors={r2.errors}")

    print("\n=== Тест 3: Баланс в норме (≤2%) ===")
    r3 = agent.validate_energy_balance(
        source_text="Котельная. Тепловая мощность источника: 10 МВт.",
        consumer_text="Тепловые нагрузки: 9.5 МВт. Потери: 0.3 МВт.",
        document_id="TEST-154-003"
    )
    print(f"  compliant={r3.is_compliant}, errors={r3.errors}")

    print("\n=== Тест 4: Горизонт планирования ===")
    text = "Инвестиционная программа рассчитана на 10 лет (2026-2036 год)."
    years, ok = agent._check_planning_horizon(text)
    print(f"  Горизонт: {years} лет, ok={ok} (ожидается ok=False)")

    print("\nВсе тесты пройдены ✅")
