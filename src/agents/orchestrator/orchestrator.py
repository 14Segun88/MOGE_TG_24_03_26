import json
import logging
from typing import Dict, Any

from src.agents.groq_client import call_llm, MODEL_ORCHESTRATOR
from src.api.schemas import XmlSummaryOut

log = logging.getLogger("orchestrator")

class Orchestrator:
    """
    Главный Архитектор (Orchestrator).
    Отвечает только за маршрутизацию: анализирует метаданные и кусок текста,
    чтобы решить, какие профильные проверки (PP963, PP154) нужно запустить.
    """
    def __init__(self):
        self.model = MODEL_ORCHESTRATOR
        log.info(f"🧠 Orchestrator инициализирован. Модель: {self.model}")

    def decide_agents(self, xml_summary: XmlSummaryOut | None, text_excerpt: str) -> Dict[str, bool]:
        """
        Решает, какие агенты нужны для данного пакета документов.

        Returns:
            Dict вида: {"run_pp963": True, "run_pp154": False}
        """
        # По умолчанию безопасный фолбэк — запускаем основную экспертизу
        plan = {
            "run_pp963": True,
            "run_pp154": False
        }

        # Если есть четкие признаки из XML, можно даже не дергать LLM для простых случаев
        if xml_summary and xml_summary.object_type == "IndustrialObject":
            plan["run_pp154"] = True

        system_prompt = (
            "Ты — Главный Архитектор системы госэкспертизы проекта.\n"
            "Твоя задача решить, нужно ли запускать специализированного агента PP154 "
            "(Агент экспертизы схем теплоснабжения, котельных, генерации тепла).\n\n"
            "Агент PP963 (Общая экспертиза ПД по ПП РФ №963) запускается всегда.\n\n"
            "Верни строго JSON объект с одним ключом:\n"
            '{"run_pp154": true} или {"run_pp154": false}\n\n'
            "Включай run_pp154 = true ТОЛЬКО если текст явно говорит о тепловых путях, котельных, "
            "схемах теплоснабжения, энергобалансе или генерации тепла."
        )

        obj_name = xml_summary.object_name if xml_summary else "Неизвестно"
        user_prompt = f"Объект: {obj_name}\n\nФрагмент текста из файлов:\n{text_excerpt[:2000]}\n\nОПРЕДЕЛИ НЕОБХОДИМЫЕ ПРОВЕРКИ (ТОЛЬКО JSON):"

        try:
            raw_route = call_llm(
                model=self.model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=0.0,
                max_tokens=64
            )
            # Очистка от маркдауна
            cleaned = raw_route.replace("```json", "").replace("```", "").strip()
            # Немного эвристики на случай если LLM вернет просто код
            start_idx = cleaned.find("{")
            end_idx = cleaned.rfind("}")
            if start_idx != -1 and end_idx != -1:
                cleaned = cleaned[start_idx:end_idx+1]
                
            decision = json.loads(cleaned)
            if "run_pp154" in decision:
                plan["run_pp154"] = bool(decision["run_pp154"])
                
            log.info(f"[Orchestrator] 🗺️ Маршрутизация: PP963={plan['run_pp963']}, PP154={plan['run_pp154']}")
        except Exception as e:
            log.warning(f"[Orchestrator] ⚠ Ошибка LLM-маршрутизации: {e}. Используем fallback.")

        return plan

