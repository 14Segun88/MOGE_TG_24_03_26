"""
RAG Ask — полный RAG-цикл: поиск в Weaviate + ответ через LM Studio (qwen2.5-3b)
МособлГосЭкспертиза | Спринт 4

Использование:
  python3 rag_ask.py "какова ширина эвакуационного пути в школе?"
  python3 rag_ask.py "требования к теплозащите жилых зданий" --dept ito
  python3 rag_ask.py "состав проектной документации" --top 5
"""

from __future__ import annotations

import os
import sys
from dotenv import load_dotenv

load_dotenv()

from rag_search import NormSearch, SearchResult
from openai import OpenAI

# ─────────────────────────────────────────────
#  Конфигурация
# ─────────────────────────────────────────────
LM_STUDIO_URL   = os.getenv("LM_STUDIO_URL",   "http://172.31.128.1:1234/v1")
LM_STUDIO_MODEL = os.getenv("LM_STUDIO_MODEL", "qwen2.5-3b-instruct")

# Системный промпт для эксперта по строительным нормам
SYSTEM_PROMPT = """Ты — ИИ-эксперт государственной строительной экспертизы (ГАУ МО «МособлГосЭкспертиза»).
Твоя задача — точно отвечать на вопросы по нормативным требованиям к проектной документации.

ПРАВИЛА:
1. Отвечай ТОЛЬКО на основе предоставленных нормативных документов.
2. ВСЕГДА указывай конкретный источник: название документа, пункт/статью/таблицу.
3. Если информации в предоставленных фрагментах недостаточно — так и скажи, не выдумывай.
4. Если нормы противоречат друг другу — укажи это явно.
5. Формат ответа: сначала суть, потом обоснование со ссылками."""


def build_context(results: list[SearchResult]) -> str:
    """Форматирует найденные чанки в контекст для LLM."""
    if not results:
        return "Релевантные нормативные документы не найдены."

    parts = []
    for i, r in enumerate(results, 1):
        table_note = " [ТАБЛИЦА]" if r.is_table else ""
        parts.append(
            f"--- Источник {i}{table_note} ---\n"
            f"Документ: {r.doc_title}\n"
            f"Раздел: {r.breadcrumb}\n"
            f"URL: {r.source_url}\n"
            f"Текст:\n{r.raw_text}\n"
        )
    return "\n".join(parts)


def ask(
    question: str,
    dept:      str | None = None,
    section:   str | None = None,
    top_k:     int        = 5,
    alpha:     float      = 0.5,
    verbose:   bool       = False,
) -> str:
    """
    Полный RAG-цикл:
      1. Гибридный поиск в Weaviate
      2. Передача контекста в qwen2.5-3b
      3. Возврат ответа со ссылками на нормы
    """
    # 1. Поиск
    searcher = NormSearch()
    try:
        results = searcher.hybrid(
            query=question,
            dept=dept,
            section=section,
            top_k=top_k,
            alpha=alpha,
        )
    finally:
        searcher.close()

    if verbose:
        print(f"\n🔍 Найдено {len(results)} релевантных фрагментов:")
        for r in results:
            print(f"  [{r.score:.3f}] {r.doc_title[:60]} | {r.breadcrumb[:50]}")
        print()

    # 2. Формируем контекст
    context = build_context(results)

    # 3. Запрос к qwen2.5-3b через LM Studio
    client = OpenAI(
        base_url=LM_STUDIO_URL,
        api_key=os.getenv("LM_STUDIO_API_KEY", "lm-studio"),   # Читаем из .env
    )

    user_message = f"""Вопрос по строительным нормам: {question}

Вот релевантные фрагменты из нормативных документов:

{context}

Ответь на вопрос строго на основе приведённых источников. Укажи конкретный пункт/статью/таблицу."""

    response = client.chat.completions.create(
        model=LM_STUDIO_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",   "content": user_message},
        ],
        temperature=0.1,    # низкая температура = более точные, менее творческие ответы
        max_tokens=1024,
    )

    return response.choices[0].message.content


# ─────────────────────────────────────────────
#  CLI
# ─────────────────────────────────────────────
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="RAG Ask — ИИ-эксперт по строительным нормам")
    parser.add_argument("question",  type=str,                help="Вопрос по нормам")
    parser.add_argument("--dept",    type=str, default=None,  help="Отдел (kap/ito/gochs/iig/oos/tim)")
    parser.add_argument("--section", type=str, default=None,  help="Раздел ПД (09, 05.4...)")
    parser.add_argument("--top",     type=int, default=5,     help="Кол-во источников (default: 5)")
    parser.add_argument("--alpha",   type=float, default=0.5, help="BM25/Vector баланс (0-1)")
    parser.add_argument("--verbose", action="store_true",     help="Показать найденные источники")
    args = parser.parse_args()

    print(f"🤖 ИИ-Эксперт МособлГосЭкспертиза")
    print(f"📡 LM Studio: {LM_STUDIO_URL} | Модель: {LM_STUDIO_MODEL}")
    print(f"❓ Вопрос: {args.question}")
    print("─" * 70)

    try:
        answer = ask(
            question=args.question,
            dept=args.dept,
            section=args.section,
            top_k=args.top,
            alpha=args.alpha,
            verbose=args.verbose,
        )
        print("\n📋 ОТВЕТ ЭКСПЕРТА:\n")
        print(answer)
        print("\n" + "─" * 70)
    except Exception as e:
        print(f"\n❌ Ошибка: {e}")
        print(f"   Проверьте что LM Studio запущен на {LM_STUDIO_URL}")
        sys.exit(1)
