"""
train/generate_dataset.py — Генерация Q&A датасета из Weaviate RAG чанков
МособлГосЭкспертиза | Fine-tuning Pipeline

Что делает:
  1. Читает все чанки из Weaviate (коллекция NormativeDoc)
  2. Для каждого чанка через qwen2.5-3b (LM Studio) генерирует Q&A пары
  3. Форматирует в ChatML (совместимо с Unsloth для qwen)
  4. Сохраняет в train/dataset.jsonl

Запуск:
  python3 train/generate_dataset.py             # полная генерация
  python3 train/generate_dataset.py --dry-run   # тест первых 10 чанков
"""

from __future__ import annotations

import json
import os
import time
import argparse
from pathlib import Path
from openai import OpenAI

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

import weaviate

# ─────────────────────────────────────────────
#  Настройки
# ─────────────────────────────────────────────
WEAVIATE_HOST   = "localhost"
WEAVIATE_PORT   = 8080
COLLECTION_NAME = "NormativeDoc"
LM_STUDIO_URL   = "http://172.31.128.1:1234/v1"
LM_STUDIO_MODEL = "qwen2.5-3b-instruct"
OUTPUT_FILE     = Path(__file__).parent / "dataset.jsonl"

# Промпт для генерации вопроса из чанка
QUESTION_SYSTEM = """Ты помощник, который создаёт вопросы по российскому строительному законодательству.
Дан фрагмент нормативного документа. Сформулируй ОДИН конкретный практический вопрос,
на который можно ответить, используя этот фрагмент.
Вопрос должен быть таким, какой мог бы задать эксперт строительной экспертизы.
Отвечай ТОЛЬКО вопросом, без пояснений."""

# Промпт для генерации ответа из чанка
ANSWER_SYSTEM = """Ты — ИИ-эксперт государственной строительной экспертизы.
Отвечай строго на основе предоставленного фрагмента нормативного документа.
Всегда указывай точное название документа и пункт/статью как источник.
Ответ должен быть чётким, юридически корректным, на русском языке."""


def get_all_chunks(client: weaviate.WeaviateClient) -> list[dict]:
    """Выгружает все чанки из Weaviate."""
    col = client.collections.get(COLLECTION_NAME)
    results = []
    
    # Загружаем все объекты пагинированно
    for item in col.iterator(return_properties=[
        "raw_text", "doc_title", "breadcrumb", "source_url", "dept", "doc_id"
    ]):
        text = item.properties.get("raw_text", "").strip()
        if len(text) > 100:  # только содержательные чанки
            results.append({
                "id":         str(item.uuid),
                "raw_text":   text,
                "doc_title":  item.properties.get("doc_title", ""),
                "breadcrumb": item.properties.get("breadcrumb", ""),
                "source_url": item.properties.get("source_url", ""),
                "dept":       item.properties.get("dept", ""),
                "doc_id":     item.properties.get("doc_id", ""),
            })
    
    return results


def is_relevant_chunk(chunk: dict) -> bool:
    """Фильтрует нерелевантные чанки по doc_id блэклисту.
    
    Все 35 документов в Weaviate нормативны по определению.
    Исключаем только конкретно известные мусорные doc_id.
    """
    SKIP_DOC_IDS = {"FZ-416", "GOST-BIM-2", "FZ-384", "SP-256"}
    return chunk.get("doc_id", "") not in SKIP_DOC_IDS


def generate_qa(client: OpenAI, chunk: dict) -> list[dict] | None:
    """Генерирует 1-2 Q&A пары из одного чанка."""
    context = (
        f"Документ: {chunk['doc_title']}\n"
        f"Раздел: {chunk['breadcrumb']}\n"
        f"Текст:\n{chunk['raw_text']}"
    )

    try:
        # 1. Генерируем вопрос
        q_resp = client.chat.completions.create(
            model=LM_STUDIO_MODEL,
            messages=[
                {"role": "system", "content": QUESTION_SYSTEM},
                {"role": "user",   "content": f"Фрагмент документа:\n\n{context}"},
            ],
            temperature=0.7,
            max_tokens=150,
            timeout=60,    # таймаут 60 сек — не зависнем
        )
        question = q_resp.choices[0].message.content.strip()

        # 2. Генерируем ответ
        a_resp = client.chat.completions.create(
            model=LM_STUDIO_MODEL,
            messages=[
                {"role": "system", "content": ANSWER_SYSTEM},
                {"role": "user",   "content": f"Вопрос: {question}\n\nИсточник:\n{context}"},
            ],
            temperature=0.1,
            max_tokens=400,
            timeout=90,    # ответы длиннее
        )
        answer = a_resp.choices[0].message.content.strip()

        if not question or not answer:
            return None

        # Формат ChatML для Unsloth/qwen fine-tuning
        return [{
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Ты — ИИ-эксперт государственной строительной экспертизы "
                        "МособлГосЭкспертиза. Ты отвечаешь на вопросы по российскому "
                        "строительному законодательству: СП, ГОСТ, ФЗ, ПП РФ. "
                        "Всегда указывай точный источник — название документа и пункт."
                    )
                },
                {"role": "user",      "content": question},
                {"role": "assistant", "content": answer},
            ],
            # Метаданные для отладки (не идут в обучение)
            "_meta": {
                "doc_id":   chunk["doc_id"],
                "doc_title":chunk["doc_title"],
                "chunk_id": chunk["id"],
            }
        }]

    except Exception as e:
        print(f"  ⚠ Ошибка генерации: {e}")
        return None


def main():
    parser = argparse.ArgumentParser(description="Генератор Q&A датасета из RAG")
    parser.add_argument("--dry-run", action="store_true",
                        help="Сгенерировать только первые 10 чанков для проверки")
    parser.add_argument("--limit",  type=int, default=0,
                        help="Ограничить кол-во чанков (0 = все)")
    args = parser.parse_args()

    print("🚀 Генерация Q&A датасета из Weaviate RAG")
    print(f"   LM Studio: {LM_STUDIO_URL} | Модель: {LM_STUDIO_MODEL}")
    print(f"   Weaviate:  {WEAVIATE_HOST}:{WEAVIATE_PORT}")
    print(f"   Вывод:     {OUTPUT_FILE}\n")

    # Подключаемся к Weaviate
    wv_client = weaviate.connect_to_local(host=WEAVIATE_HOST, port=WEAVIATE_PORT)
    chunks = get_all_chunks(wv_client)
    wv_client.close()
    print(f"📂 Загружено чанков: {len(chunks)}")

    # Лимит для dry-run или --limit
    if args.dry_run:
        chunks = chunks[:10]
        print(f"🧪 Dry-run режим: обрабатываем первые {len(chunks)} чанков\n")
    elif args.limit > 0:
        chunks = chunks[:args.limit]

    # Подключаемся к LM Studio
    llm_client = OpenAI(base_url=LM_STUDIO_URL, api_key=os.getenv("LM_STUDIO_API_KEY", "lm-studio"))

    total_examples = 0
    skipped = 0

    out_file = OUTPUT_FILE if not args.dry_run else Path("/tmp/dataset_dryrun.jsonl")
    
    # ВОЗОБНОВЛЕНИЕ РАБОТЫ (RESUME)
    start_index = 0
    if not args.dry_run and out_file.exists():
        with open(out_file, "r", encoding="utf-8") as f:
            lines = f.readlines()
            start_index = len(lines)
            total_examples = start_index
            print(f"🔄 Прочитано {start_index} готовых примеров из {out_file.name}. Продолжаем с чанка #{start_index + 1}.")
    
    # Открываем в режиме дозаписи ('a')
    with open(out_file, "a" if not args.dry_run else "w", encoding="utf-8") as f:
        for i, chunk in enumerate(chunks):
            # Пропуск уже обработанных чанков
            if i < start_index:
                continue
            
            doc_label = f"{chunk['doc_id']} | {chunk['breadcrumb'][:50]}"
            print(f"  [{i+1}/{len(chunks)}] {doc_label}...", end="", flush=True)
            if not is_relevant_chunk(chunk):
                skipped += 1
                print(" ⏭ нерелевантный")
                continue

            qa_pairs = generate_qa(llm_client, chunk)

            if qa_pairs:
                for pair in qa_pairs:
                    # Не пишем _meta в файл датасета
                    out = {"messages": pair["messages"]}
                    f.write(json.dumps(out, ensure_ascii=False) + "\n")
                    total_examples += 1
                print(f" ✅ (+{len(qa_pairs)})")
            else:
                skipped += 1
                print(" ⚠ пропущен")

            # Rate limiting — не перегружаем LM Studio
            time.sleep(0.3)

    print(f"\n{'═' * 55}")
    print(f"✅ Датасет готов!")
    print(f"   Примеров:   {total_examples}")
    print(f"   Пропущено:  {skipped}")
    print(f"   Файл:       {out_file}")
    print(f"{'═' * 55}")

    if args.dry_run:
        print("\n📋 Первые 2 примера:")
        with open(out_file) as f:
            for j, line in enumerate(f):
                if j >= 2:
                    break
                ex = json.loads(line)
                print(f"\n--- Пример {j+1} ---")
                for msg in ex["messages"]:
                    role = msg["role"].upper()
                    content = msg["content"][:200]
                    print(f"[{role}]: {content}...")


if __name__ == "__main__":
    main()
