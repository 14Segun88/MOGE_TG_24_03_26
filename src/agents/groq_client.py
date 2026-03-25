import os
import itertools
import logging
from typing import List, Optional
from groq import Groq
from dotenv import load_dotenv

# Загружаем переменные из .env
load_dotenv()

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
    log.warning("❌ Ни один GROQ_API_KEY не найден в .env! Будет использован LM Studio fallback.")

GROQ_API_KEYS = _keys

# Round-Robin iterator
_key_iterator = itertools.cycle(GROQ_API_KEYS)

def get_groq_client() -> 'Groq | OpenAI':
    """Возвращает Groq-клиент или OpenAI-совместимый клиент LM Studio (fallback)."""
    if _keys:
        return Groq(api_key=next(_key_iterator))
    
    # Fallback to LM Studio
    try:
        from openai import OpenAI
    except ImportError:
        log.error("❌ Библиотека 'openai' не установлена. Fallback на LM Studio невозможен.")
        raise
    
    lm_url = os.environ.get("LM_STUDIO_URL", "http://172.31.128.1:1234/v1")
    lm_key = os.environ.get("LM_STUDIO_API_KEY", "sk-lm-V6B8mgjk:7DFHluGBuv2U6bmhuoZ5")
    log.warning(f"⚠️ GROQ_API_KEY не найден. Переключаюсь на LM Studio: {lm_url}")
    return OpenAI(base_url=lm_url, api_key=lm_key)


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
    "gpt-oss-120b":       "llama-3.3-70b-versatile",
    "gpt-oss-20b":        "llama-3.1-70b-versatile",
    "qwen3-32b":          "llama-3.3-70b-versatile",    # Временно, пока Groq не подтвердит Qwen ID
    "qwen-32b-preview":   "llama-3.3-70b-versatile",
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
    
    # ── Попытка вызова Groq ───────────────────────────
    try:
        # Если мы работаем через LM Studio (OpenAI клиент), у нас нет actual_id в списке моделей Groq.
        if hasattr(client, 'base_url') and '1234' in str(client.base_url):
            actual_id = "qwen3.5-4b-claude-4.6-opus-reasoning-distilled" 
            log.debug(f"Using local LLM (LM Studio): {actual_id}")

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

    except Exception as e:
        # Если ошибка 403 (Forbidden) или 429 (Rate Limit) и у нас есть ключи — пробуем LM Studio как последний шанс
        if _keys and ("403" in str(e) or "429" in str(e) or "Forbidden" in str(e)):
            log.warning(f"⚠️ Ошибка Groq ({e}). Пробую локальный fallback на LM Studio...")
            try:
                from openai import OpenAI
                lm_url = os.environ.get("LM_STUDIO_URL", "http://172.31.128.1:1234/v1")
                lm_key = os.environ.get("LM_STUDIO_API_KEY", "sk-lm-V6B8mgjk:7DFHluGBuv2U6bmhuoZ5")
                lm_client = OpenAI(base_url=lm_url, api_key=lm_key)
                
                # Для локальной модели обычно используем то, что загружено
                # Но попробуем явно указать qwen если это возможно
                resp = lm_client.chat.completions.create(
                    model="qwen3.5-4b-claude-4.6-opus-reasoning-distilled", 
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user",   "content": user_prompt},
                    ],
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                return resp.choices[0].message.content
            except Exception as lm_e:
                log.error(f"❌ Fallback на LM Studio также не удался: {lm_e}")
                raise e
        else:
            log.error(f"❌ Ошибка вызова LLM: {e}")
            raise e
