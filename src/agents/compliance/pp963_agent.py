"""
PP963ComplianceAgent — проверка 12 разделов ПД по ПП РФ №963.
Использует RAG (Weaviate) для поиска нормативных требований
и LLM (Groq) для анализа соответствия.

Интегрирован в pipeline.py → bot.py.
"""
import json
import logging
from dataclasses import dataclass, field
from typing import List, Optional

from src.agents.groq_client import call_llm, MODEL_PP963_COMPLIANCE
from src.db.database import SessionLocal
from src.db.models import DisagreementLog

log = logging.getLogger("pp963_agent")

# ─────────────────────────────────────────────
#  Чек-лист 12 разделов ПД по ПП №963
# ─────────────────────────────────────────────
SECTIONS_CHECKLIST = [
    {"code": "01", "name": "Пояснительная записка",
     "rag_query": "требования к составу пояснительной записки ПП 963 раздел 1",
     "pp963_refs": ["п.16"]},
    
    # FIX: Добавлено явное требование по изысканиям для Экзаменатора
    {"code": "01.1", "name": "Данные инженерных изысканий",
     "rag_query": "сведения о подготовке отчетной документации о выполнении инженерных изысканий ПП 963",
     "pp963_refs": ["п.10 г", "п.16"]},
     
    {"code": "02", "name": "Схема планировочной организации (СПОЗУ)",
     "rag_query": "состав СПОЗУ схема планировочной организации ПП 963",
     "pp963_refs": ["п.17"]},
    {"code": "03", "name": "Архитектурные решения (АР)",
     "rag_query": "требования архитектурные решения раздел 3 ПП 963",
     "pp963_refs": ["п.18"]},
    {"code": "04", "name": "Конструктивные и объёмно-планировочные решения (КР)",
     "rag_query": "конструктивные решения несущие конструкции ПП 963",
     "pp963_refs": ["п.19"]},
    {"code": "05", "name": "Сведения об инженерном оборудовании (ИОС)",
     "rag_query": "инженерные системы ИОС электроснабжение водоснабжение ПП 963",
     "pp963_refs": ["п.20"]},
    {"code": "06", "name": "Проект организации строительства (ПОС)",
     "rag_query": "проект организации строительства ПОС ПП 963",
     "pp963_refs": ["п.24"]},
    {"code": "07", "name": "Проект организации работ по сносу",
     "rag_query": "проект организации работ по сносу демонтажу ПП 963",
     "pp963_refs": ["п.25"]},
    {"code": "08", "name": "Перечень мероприятий по охране ОС",
     "rag_query": "охрана окружающей среды мероприятия раздел 8 ПП 963",
     "pp963_refs": ["п.26"]},
    {"code": "09", "name": "Мероприятия по обеспечению пожарной безопасности",
     "rag_query": "пожарная безопасность мероприятия раздел 9 ФЗ 123 СП 1.13130",
     "pp963_refs": ["п.28"]},
    {"code": "10", "name": "Мероприятия по обеспечению доступа инвалидов",
     "rag_query": "доступ инвалидов маломобильных групп населения ПП 963",
     "pp963_refs": ["п.31"]},
    {"code": "11", "name": "Смета на строительство",
     "rag_query": "сметная документация ССР стоимость строительства ПП 963",
     "pp963_refs": ["п.28"]},
    {"code": "12", "name": "Иная документация (ЭЭ, ТУ)",
     "rag_query": "технические условия энергоэффективность ПП 963",
     "pp963_refs": ["п.58"]},
]

# Триггер is_edge_case: нестандартные типы объектов
EDGE_CASE_KEYWORDS = [
    "атомн", "ядерн", "гидростанц", "военн", "оборон",
    "космодром", "подзем", "шахт", "метрополитен", "тоннел"
]


def _extract_json(text: str) -> dict:
    """Извлекает и парсит JSON из сырого ответа LLM."""
    cleaned = text.replace("```json", "").replace("```", "").strip()
    
    # 1. Прямой парсинг
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
        
    # 2. Парсинг первого и последнего блока скобок { ... }
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1:
        try:
            return json.loads(cleaned[start:end+1])
        except json.JSONDecodeError:
            pass
            
    # 3. Если LLM не закрыла JSON (частая проблема при отсечении по max_tokens)
    if start != -1 and end == -1:
        try:
            return json.loads(cleaned[start:] + "\n}")
        except json.JSONDecodeError:
            pass
            
    raise ValueError("Не удалось извлечь валидный JSON из ответа")


@dataclass
class PP963Response:
    is_compliant: bool
    discrepancies: list[str]
    confidence: float
    raw_analysis: str


@dataclass
class SectionResult:
    code: str
    name: str
    passed: bool
    remarks: list[str] = field(default_factory=list)
    norm_refs: list[str] = field(default_factory=list)
    confidence: float = 0.0


class PP963Agent:
    """
    Агент проверки на соответствие Постановлению Правительства № 963.
    
    Функционал:
    1. Кросс-валидация ТЭП между разделами (validate_tep_consistency)
    2. Проверка 12 разделов ПД по чек-листу с RAG (check_all_sections)
    3. HITL-логирование с 4 триггерами
    """

    def __init__(self):
        self.model = MODEL_PP963_COMPLIANCE
        self._rag_search = None
        log.info(f"PP963 Agent init. Модель: {self.model}")

    def _get_rag(self):
        """Ленивая инициализация NormSearch (Weaviate)."""
        if self._rag_search is None:
            try:
                from rag_search import NormSearch
                self._rag_search = NormSearch()
                log.info("PP963: RAG NormSearch подключён")
            except Exception as exc:
                log.warning(f"PP963: RAG недоступен: {exc}")
        return self._rag_search

    # ─────────────────────────────────────────────
    #  0. Извлечение ТЭП из XML ПЗ (FIX)
    # ─────────────────────────────────────────────
    def extract_tep_from_xml(self, parsed_xml) -> dict:
        """
        Извлекает ТЭП (технико-экономические показатели) из уже разобранного XML ПЗ.

        FIX: XML-парсер уже читает TEIRecord в поле tei, но PP963Agent их не использовал.
        Теперь возвращаем структурированный словарь с ТЭП для отчёта и кросс-валидации.

        Returns:
            dict с ключами: total_area, floors, underground_floors, construction_volume,
                            capacity, footprint_area, energy_class, raw_text
        """
        if parsed_xml is None:
            return {}

        tei_list = getattr(parsed_xml, "tei", []) or []
        power_list = getattr(parsed_xml, "power_indicators", []) or []
        all_tei = tei_list + power_list

        if not all_tei:
            log.warning("PP963: ТЭП не найдены в XML ПЗ (поле tei пустое)")
            return {}

        # Ключевые слова для стандартных ТЭП
        # FIX: добавлены реальные имена тегов из XML школы (ПЗ 01.06)
        _AREA_KW    = ("площадь объекта", "общая площадь", "площадь зданий",
                       "общая площадь здания", "площадь здания")
        _AREA_EXACT = ("площадь",)
        
        _FLOORS_KW  = ("этажность", "количество этажей", "надземных этажей",
                       "количество надземных", "надземных этажей", "число этажей")
        _UNDER_KW   = ("подземных этажей", "подземная", "количество подземных")
        _VOLUME_KW  = ("строительный объём", "строительный объем", "объём здания",
                       "объем здания", "строит. объем")
        _CAPAS_KW   = ("вместимость", "количество обучающихся", "количество мест",
                       "количество жителей", "пропускная способность",
                       "количество учащихся", "проектная мощность")
        _FOOT_KW    = ("площадь застройки", "площадь пятна застройки")
        _ENERGY_KW  = ("класс энергоэффективности", "класс энергосбережения")

        def _find(keywords: tuple, exact: tuple = ()) -> str:
            # Сперва ищем точные совпадения
            if exact:
                for rec in all_tei:
                    if rec.name.lower().strip() in exact:
                        val = str(rec.value).strip()
                        return f"{val} {rec.unit}".strip()
            # Затем ищем по подстроке
            for rec in all_tei:
                name_l = rec.name.lower()
                if any(kw in name_l for kw in keywords):
                    val = str(rec.value).strip()
                    return f"{val} {rec.unit}".strip()
            return ""

        # Умный поиск этажей (суммирование над/подземных, если нет общей)
        def _find_floors() -> str:
            # 1. Сначала ищем общую этажность
            overall = _find(("этажность", "количество этажей", "число этажей"), exact=("этажность", "количество этажей"))
            if overall:
                return overall
                
            # 2. Если общей нет, пробуем сложить надземные и подземные
            above = _find(("надземных", "надземные"))
            below = _find(("подземных", "подземные"))
            
            try:
                import re
                a_val = int(re.search(r"\d+", above).group()) if above else 0
                b_val = int(re.search(r"\d+", below).group()) if below else 0
                if a_val > 0:
                    return str(a_val + b_val)
            except Exception:
                pass
            
            # 3. Fallback на старый поиск
            return _find(_FLOORS_KW)


        result = {
            "total_area":         _find(_AREA_KW, _AREA_EXACT),
            "floors":             _find_floors(),
            "underground_floors": _find(_UNDER_KW),
            "construction_volume":_find(_VOLUME_KW),
            "capacity":           _find(_CAPAS_KW),
            "footprint_area":     _find(_FOOT_KW),
            "energy_class":       _find(_ENERGY_KW),
        }

        # Удаляем пустые значения
        result = {k: v for k, v in result.items() if v}

        # Текстовый блок для отчёта
        lines = ["=== ТЭП из XML ПЗ ==="]
        labels = {
            "total_area":         "Площадь объекта",
            "floors":             "Этажность",
            "underground_floors": "Подземных этажей",
            "construction_volume":"Строительный объём",
            "capacity":           "Вместимость",
            "footprint_area":     "Площадь застройки",
            "energy_class":       "Класс энергоэффективности",
        }
        for key, label in labels.items():
            if key in result:
                lines.append(f"  {label}: {result[key]}")

        # Добавляем все остальные ТЭП которые не попали в стандартные
        known_keys = set(labels.keys())
        for rec in all_tei:
            name_l = rec.name.lower()
            matched = any(
                any(kw in name_l for kw in kws)
                for kws in (_AREA_KW, _FLOORS_KW, _UNDER_KW, _VOLUME_KW,
                            _CAPAS_KW, _FOOT_KW, _ENERGY_KW)
            )
            if not matched:
                unit = f" {rec.unit}" if rec.unit else ""
                lines.append(f"  {rec.name}: {rec.value}{unit}")

        result["raw_text"] = "\n".join(lines)
        log.info(f"PP963: Извлечено ТЭП из XML: {[k for k in result if k != 'raw_text']}")
        return result

    # ─────────────────────────────────────────────
    #  1. Кросс-валидация ТЭП (существующая логика)
    # ─────────────────────────────────────────────
    def validate_tep_consistency(self, section1_text: str, section2_text: str, document_id: str) -> PP963Response:
        """Сравнивает ТЭП из двух разных разделов проектной документации."""
        log.info(f"PP963: кросс-валидация ТЭП для {document_id}")

        system_prompt = (
            "Ты — строгий и внимательный эксперт Главгосэкспертизы России. "
            "Твоя задача — проверить проектную документацию на соответствие ПП РФ № 963. "
            "Тебе будут даны выдержки из двух разных разделов одного проекта.\n"
            "Инструкция:\n"
            "1. Найди все ТЭП (площадь, этажность, объем, вместимость и т.д.).\n"
            "2. Сравни их значения. Они должны совпадать точно.\n"
            "3. Если есть расхождения — это КРИТИЧЕСКАЯ ОШИБКА.\n"
            "4. Верни JSON: { 'is_compliant': true/false, 'discrepancies': [...], 'confidence': 0.0-1.0 }"
        )

        user_prompt = (
            f"--- РАЗДЕЛ 1 ---\n{section1_text}\n\n"
            f"--- РАЗДЕЛ 2 ---\n{section2_text}\n\n"
            f"Сравни ТЭП и ВЕРНИ ТОЛЬКО JSON:"
        )

        try:
            raw_response = call_llm(
                model=self.model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=0.0
            )
            
            try:
                result_dict = _extract_json(raw_response)
            except ValueError as ve:
                log.error(f"PP963: JSON parse error: {ve}")
                return PP963Response(False, ["Ошибка формата ответа LLM (не JSON)"], 0.0, raw_response)

            response = PP963Response(
                is_compliant=result_dict.get("is_compliant", False),
                discrepancies=result_dict.get("discrepancies", []),
                confidence=float(result_dict.get("confidence", 0.0)),
                raw_analysis=raw_response
            )

            # HITL: confidence < 0.80 или критическая ошибка
            if response.confidence < 0.80 or (not response.is_compliant and response.confidence < 0.90):
                self._log_hitl(document_id, "confidence",
                              f"ТЭП валидация: confidence={response.confidence:.2f}, compliant={response.is_compliant}",
                              response.confidence)

            return response

        except Exception as e:
            log.error(f"PP963: системная ошибка: {e}")
            return PP963Response(False, [str(e)], 0.0, "")

    # ─────────────────────────────────────────────
    #  2. Проверка 12 разделов ПД по чек-листу
    # ─────────────────────────────────────────────
    def check_all_sections(self, parsed_xml, classified_files, document_id: str) -> list[SectionResult]:
        """Проверяет каждый раздел ПД по чек-листу PP963 через RAG."""
        results = []
        rag = self._get_rag()

        # Проверка edge_case
        obj_name = getattr(parsed_xml, 'object_name', '') or '' if parsed_xml else ''
        if any(kw in obj_name.lower() for kw in EDGE_CASE_KEYWORDS):
            self._log_hitl(document_id, "is_edge_case",
                          f"Нестандартный объект: {obj_name[:200]}", 0.5)

        # Какие разделы реально присутствуют в пакете
        present_sections = set()
        if classified_files:
            for f in classified_files:
                sec = getattr(f, 'suspected_section', '') or ''
                if sec:
                    # Сохраняем и "03.01", и "03" для гибкого поиска
                    present_sections.add(sec)
                    code_main = sec.split('.')[0].zfill(2)
                    present_sections.add(code_main)
                    
                    # Особый случай для изысканий: если есть "1.1", "01.1", "1.2"
                    if sec.startswith("1.") or sec.startswith("01."):
                        present_sections.add("01.1")

        log.info(f"PP963: Начинаем проверку {len(SECTIONS_CHECKLIST)} разделов: {[s['code'] for s in SECTIONS_CHECKLIST]}")
        log.info(f"PP963: Доступные разделы из файлов: {present_sections}")

        for section in SECTIONS_CHECKLIST:
            code = section["code"]
            name = section["name"]
            
            # Если код "01.1" - проверяем его явное наличие
            section_present = code in present_sections
            
            # Для "01.1" делаем поблажку: если есть просто "01", считаем что изыскания могут быть внутри ПЗ
            if code == "01.1" and not section_present and "01" in present_sections:
                section_present = True

            sec_result = SectionResult(code=code, name=name, passed=section_present)

            if not section_present:
                sec_result.remarks.append(f"Не представлены в полном объеме сведения/раздел: {name} (код {code})")
                sec_result.norm_refs = section["pp963_refs"]
                sec_result.passed = False
                results.append(sec_result)
                continue

            # RAG: ищем нормативные требования для этого раздела
            if rag:
                try:
                    rag_results = rag.hybrid(query=section["rag_query"], top_k=2, alpha=0.5)
                    if rag_results:
                        sec_result.norm_refs = [
                            f"{r.doc_title[:50]} ({r.breadcrumb[:40]})"
                            for r in rag_results
                        ]
                        sec_result.confidence = max(r.score for r in rag_results)
                        
                        # Для изысканий (01.1) повысим порог уверенности RAG, 
                        # чтобы отсеивать левые результаты (например "избирательная документация")
                        if code == "01.1" and sec_result.confidence < 0.65:
                            sec_result.passed = False
                            sec_result.remarks.append(f"Не представлены в полном объеме сведения/раздел: {name} (код {code})")
                        else:
                            sec_result.passed = True
                except Exception as e:
                    log.warning(f"PP963: RAG запрос упал для раздела {code}: {e}")

            results.append(sec_result)

        return results

    # ─────────────────────────────────────────────
    #  3. HITL — логирование с 4 триггерами
    # ─────────────────────────────────────────────
    def _log_hitl(self, doc_id: str, trigger: str, context: str, confidence: float):
        """
        Единый метод логирования в HITL с указанием триггера.
        
        Триггеры:
          - confidence: уверенность LLM < 0.7
          - critical_error: критическое несоответствие
          - agent_disagreement: конфликт между агентами
          - is_edge_case: нестандартный тип объекта
        """
        log.info(f"PP963 HITL [{trigger}]: {context[:80]}... (conf={confidence:.2f})")
        try:
            db = SessionLocal()
            new_log = DisagreementLog(
                document_id=doc_id,
                agent_name=f"PP963Agent/{trigger}",
                ai_decision=context[:500],
                confidence=confidence,
                is_reviewed=False,
            )
            db.add(new_log)
            db.commit()
            db.close()
        except Exception as db_err:
            log.error(f"PP963: HITL DB ошибка: {db_err}")

    def check_agent_disagreement(self, doc_id: str, pp963_compliant: bool, fc_missing_sections: list):
        """
        Триггер agent_disagreement: PP963 говорит ОК, но FormalCheck нашёл отсутствующие разделы.
        """
        if pp963_compliant and fc_missing_sections:
            self._log_hitl(
                doc_id, "agent_disagreement",
                f"PP963 Agent: compliant=True, но FormalCheckRunner обнаружил "
                f"отсутствующие разделы: {fc_missing_sections}",
                0.4
            )

    # ─────────────────────────────────────────────
    #  4. Кросс-проверка Технических Условий (ТУ)
    # ─────────────────────────────────────────────
    def cross_check_tu(self, tu_text: str, ios_text: str, document_id: str) -> dict:
        """
        Сверяет разрешённую мощность/ресурс из ТУ с расчётными данными в разделе ИОС.
        Возвращает dict: {ok: bool, findings: [...], confidence: float}
        """
        if not tu_text or not ios_text:
            return {"ok": True, "findings": [], "confidence": 0.0, "skipped": True}

        log.info(f"PP963: Кросс-проверка ТУ vs ИОС для {document_id}")

        system_prompt = (
            "Ты — эксперт технического надзора Госэкспертизы России. "
            "Тебе переданы выдержки из Технических условий (ТУ) на подключение к сетям "
            "и из раздела Инженерных систем (ИОС) проекта.\n"
            "Задача: найти расхождение между разрешёнными мощностями/объёмами в ТУ "
            "и расчётными значениями в проекте.\n"
            "Верни JSON: {\"ok\": true/false, \"findings\": [\"...расхождение...\"], \"confidence\": 0.0-1.0}"
        )
        user_prompt = (
            f"--- ТЕХНИЧЕСКИЕ УСЛОВИЯ ---\n{tu_text[:2000]}\n\n"
            f"--- РАЗДЕЛ ИОС (Инженерные системы) ---\n{ios_text[:2000]}\n\n"
            "Найди расхождения мощностей, давлений, расходов. ВЕРНИ ТОЛЬКО JSON:"
        )

        try:
            raw = call_llm(
                model=self.model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=0.0,
                max_tokens=600,
            )
            try:
                result = _extract_json(raw)
                conf = float(result.get("confidence", 0.5))
                if conf < 0.75:
                    self._log_hitl(document_id, "confidence",
                                   f"ТУ vs ИОС: confidence={conf:.2f}", conf)
                return {
                    "ok": result.get("ok", True),
                    "findings": result.get("findings", []),
                    "confidence": conf,
                    "skipped": False,
                }
            except ValueError:
                log.warning("PP963: ТУ vs ИОС — ошибка извлечения JSON")
                return {"ok": False, "findings": ["Ошибка извлечения JSON из ИОС/ТУ ответа"], "confidence": 0.0, "skipped": False}
        except Exception as e:
            log.warning(f"PP963: cross_check_tu ошибка: {e}")

        return {"ok": True, "findings": [], "confidence": 0.0, "skipped": True}

    # ─────────────────────────────────────────────
    #  5. Кросс-проверка ГПЗУ
    # ─────────────────────────────────────────────
    def cross_check_gpzu(self, gpzu_text: str, pz_text: str, document_id: str) -> dict:
        """
        Сверяет параметры (площадь, этажность, адрес, разрешённое использование)
        из ГПЗУ с данными Пояснительной записки.
        Возвращает dict: {ok: bool, findings: [...], confidence: float}
        """
        if not gpzu_text or not pz_text:
            return {"ok": True, "findings": [], "confidence": 0.0, "skipped": True}

        log.info(f"PP963: Кросс-проверка ГПЗУ vs ПЗ для {document_id}")

        system_prompt = (
            "Ты — эксперт Госэкспертизы. Тебе переданы выдержки из ГПЗУ "
            "(Градостроительного плана земельного участка) и из Пояснительной записки (ПЗ).\n"
            "Задача: найти расхождения по следующим параметрам:\n"
            "1. Адрес / кадастровый номер участка\n"
            "2. Максимальная площадь застройки, этажность, высота\n"
            "3. Вид разрешённого использования\n"
            "4. Отступы от границ / красных линий\n"
            "Верни JSON: {\"ok\": true/false, \"findings\": [\"...расхождение...\"], \"confidence\": 0.0-1.0}"
        )
        user_prompt = (
            f"--- ГПЗУ ---\n{gpzu_text[:2000]}\n\n"
            f"--- ПОЯСНИТЕЛЬНАЯ ЗАПИСКА (ПЗ) ---\n{pz_text[:2000]}\n\n"
            "Найди расхождения параметров. ВЕРНИ ТОЛЬКО JSON:"
        )

        try:
            raw = call_llm(
                model=self.model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=0.0,
                max_tokens=600,
            )
            try:
                result = _extract_json(raw)
                conf = float(result.get("confidence", 0.5))
                if conf < 0.75:
                    self._log_hitl(document_id, "confidence",
                                   f"ГПЗУ vs ПЗ: confidence={conf:.2f}", conf)
                return {
                    "ok": result.get("ok", True),
                    "findings": result.get("findings", []),
                    "confidence": conf,
                    "skipped": False,
                }
            except ValueError:
                log.warning("PP963: ГПЗУ vs ПЗ — ошибка извлечения JSON")
                return {"ok": False, "findings": ["Ошибка извлечения JSON из ГПЗУ/ПЗ ответа"], "confidence": 0.0, "skipped": False}
        except Exception as e:
            log.warning(f"PP963: cross_check_gpzu ошибка: {e}")

        return {"ok": True, "findings": [], "confidence": 0.0, "skipped": True}


if __name__ == "__main__":
    print("--- Тестовый запуск PP963 Agent ---")
    agent = PP963Agent()

    # Тест ТЭП
    test_sec1 = "В соответствии с проектом общая площадь здания составляет 1450.5 кв.м. Этажность: 3."
    test_sec2 = "Архитектурные решения: Здание трехэтажное, общая площадь помещений 1450.5 кв.м."
    res = agent.validate_tep_consistency(test_sec1, test_sec2, "Test-Doc-001")
    print(f"ТЭП: compliant={res.is_compliant}, confidence={res.confidence}")

    # Тест edge_case
    agent.check_agent_disagreement("Test-Doc-002", True, ["03.01", "05.01"])
