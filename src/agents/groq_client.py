import os
import itertools
import logging
from typing import List, Optional
from groq import Groq

log = logging.getLogger("groq_client")

# ─────────────────────────────────────────────
#  Глобальные счетчики вызовов моделей
# ─────────────────────────────────────────────
MODEL_USAGE_COUNTERS = {
    "llama-3.3-70b-versatile": 0,
    "qwen-qwq-32b": 0,
    "llama-3.1-8b-instant": 0,
}

def record_model_usage(model_id: str):
    """Увеличивает счетчик использования конкретной модели."""
    real_id = model_id
    if "120b" in model_id.lower() or "70b" in model_id.lower():
        real_id = "llama-3.3-70b-versatile"
    elif "qwen" in model_id.lower() or "32b" in model_id.lower():
        real_id = "qwen-qwq-32b"
    elif "20b" in model_id.lower() or "8b" in model_id.lower():
        real_id = "llama-3.1-8b-instant"
        
    if real_id in MODEL_USAGE_COUNTERS:
        MODEL_USAGE_COUNTERS[real_id] += 1
    else:
        MODEL_USAGE_COUNTERS[real_id] = MODEL_USAGE_COUNTERS.get(real_id, 0) + 1

# Пул API ключей (Round-Robin). Ключи читаются ТОЛЬКО из .env!
# Для одного ключа: GROQ_API_KEY=gsk_...
# Для нескольких:   GROQ_API_KEY_1=gsk_... GROQ_API_KEY_3=gsk_...
_keys = []
for var in ["GROQ_API_KEY", "GROQ_API_KEY_1", "GROQ_API_KEY_2", "GROQ_API_KEY_3"]:
    val = os.environ.get(var)
    if val and val not in _keys:
        _keys.append(val)

if not _keys:
    raise RuntimeError("❌ Ни один GROQ_API_KEY не найден в .env! Заполните файл .env (см. .env.example)")

GROQ_API_KEYS = _keys

# Round-Robin iterator
_key_iterator = itertools.cycle(GROQ_API_KEYS)

def get_groq_client() -> Groq:
    """Возвращает Groq-клиент со следующим ключом (Round-Robin)."""
    return Groq(api_key=next(_key_iterator))


# ---------------------------------------------------------------------------
# КОНСТАНТЫ МОДЕЛЕЙ ПО РОЛЯМ
# (см. MEMORY.md → раздел "Распределение моделей по агентам")
# ---------------------------------------------------------------------------

# 🧠 Оркестратор + PP963 + PP154 (сложная логика, кросс-валидация)
MODEL_ORCHESTRATOR     = "gpt-oss-120b"
MODEL_PP963_COMPLIANCE = "gpt-oss-120b"
MODEL_PP154_COMPLIANCE = "gpt-oss-120b"

# 📚 RAG / KnowledgeBase (умный ответ строго по контексту)
MODEL_RAG_AGENT = "qwen3-32b"

# 📄 ReportGenerator (скорость важнее размера)
MODEL_REPORT_GENERATOR = "gpt-oss-20b"

# ⚡ Быстрые задачи: HITL, парсинг ФИО, ExternalIntegration
MODEL_EXTERNAL_INTEGRATION = "llama-3.1-8b-instant"
MODEL_HITL                 = "llama-3.1-8b-instant"


# ---------------------------------------------------------------------------
# МАППИНГ → РЕАЛЬНЫЕ GROQ API ID (актуально март 2026)
# Источник: https://console.groq.com/docs/models
# ---------------------------------------------------------------------------
_MODEL_ALIAS_MAP = {
    # Наш алиас           → Реальный Groq model ID
    "gpt-oss-120b":       "llama-3.3-70b-versatile",   # 70B = лучший доступный на Groq
    "gpt-oss-20b":        "llama-3.1-8b-instant",      # 8B быстрый
    "qwen3-32b":          "qwen-qwq-32b",               # QwQ-32B = reasoning Qwen на Groq
    "qwen-32b-preview":   "qwen-qwq-32b",
    "llama-3.1-8b-instant": "llama-3.1-8b-instant",
    "llama-3.3-70b-versatile": "llama-3.3-70b-versatile",
}


def call_llm(
    model: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.1,
    max_tokens: int = 4090,
) -> str:
    """
    Универсальная обёртка вызова Groq с Round-Robin балансировкой ключей.

    Args:
        model:         Логический алиас (напр. MODEL_ORCHESTRATOR = 'gpt-oss-120b').
        system_prompt: Системная инструкция.
        user_prompt:   Пользовательский запрос / RAG-контекст.
        temperature:   0.0–0.1 для экспертных задач, 0.3–0.7 для генерации текста.
        max_tokens:    Максимум токенов в ответе.

    Returns:
        Текст ответа модели.
    """
    actual_id = _MODEL_ALIAS_MAP.get(model, model)
    if actual_id != model:
        log.debug(f"groq_client: '{model}' → '{actual_id}'")

    record_model_usage(actual_id)

    client = get_groq_client()
    response = client.chat.completions.create(
        model=actual_id,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ],
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return response.choices[0].message.content
