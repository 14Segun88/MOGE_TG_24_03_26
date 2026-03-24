"""
RAG Indexer — индексация нормативных документов в Weaviate
МособлГосЭкспертиза | Спринт 4

Что делает:
  1. Читает .md файлы из /mnt/d/rag_data/raw/
  2. Нарезает на смысловые чанки по заголовкам (Smart Chunking)
  3. Создаёт векторы через sentence-transformers (локально, бесплатно)
  4. Загружает в Weaviate с метаданными (отдел, раздел ПД, документ)

Запуск:
  pip install weaviate-client sentence-transformers
  python3 rag_indexer.py
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Iterator

import weaviate
from weaviate.classes.config import Configure, Property, DataType, VectorDistances
from weaviate.classes.data import DataObject
from openai import OpenAI

# ─────────────────────────────────────────────
#  Настройки
# ─────────────────────────────────────────────
RAW_DIR       = Path("/mnt/d/rag_data/raw")
META_DIR      = Path("/mnt/d/rag_data/meta")
WEAVIATE_URL  = "http://localhost:8080"

# Эмбеддинг-модель: nomic-embed-text через LM Studio (768 измерений, вс.вс. 768 и отличный RU)
LM_STUDIO_URL = "http://172.31.128.1:1234/v1"
EMBED_MODEL   = "text-embedding-nomic-embed-text-v1.5"

# Параметры чанкинга
CHUNK_MAX_CHARS  = 1500   # макс. символов в чанке (≈1000 токенов)
CHUNK_MIN_CHARS  = 100    # чанки меньше этого — выбрасываем
CHUNK_OVERLAP    = 100    # перекрытие между чанками (контекст)

# Коллекция в Weaviate
COLLECTION_NAME  = "NormativeDoc"

# Batch размер при загрузке
BATCH_SIZE = 50

# Паттерны для фильтрации нежелательного контента
# (судебные решения, реклама, новости попадают с CNTD как "похожие документы")
JUNK_PATTERNS = [
    re.compile(r"\d{2}-\d{4}/\d{4}"),           # судебные дела: 33-1976/2020
    re.compile(r"регуляторная гильотина", re.I), # рекламные статьи CNTD
    re.compile(r"судебная практика",     re.I),
    re.compile(r"арбитражный суд",       re.I),
    re.compile(r"kp\.ru|rg\.ru|ria\.ru", re.I),  # новостные сайты
]

# Паттерны для определения МУСОРНОГО файла целиком
# (если содержит — весь файл пропускаем)
JUNK_FILE_PATTERNS = [
    re.compile(r"регуляторная гильотина",   re.I),
    re.compile(r"sudrf\.cntd\.ru",          re.I),  # судебные решения
    re.compile(r"судебных\s+заседани",      re.I),
    re.compile(r"\d{2}-\d{4}/\d{4}.*\d{4}"),       # номера дел (3+ штук)
]


# ─────────────────────────────────────────────
#  Smart Chunker (по заголовкам Markdown)
# ─────────────────────────────────────────────
def smart_chunk(markdown: str, meta: dict) -> Iterator[dict]:
    """
    Нарезает Markdown по заголовкам (#, ##, ###).
    Принцип: каждый чанк несёт контекст родительских заголовков.

    Пример:
        ## Глава 6. Пути эвакуации
        ### 6.1 Общие требования
        6.1.1 Ширина пути не менее 1.2м...

    → Чанк будет содержать оба заголовка + текст пункта.
    Так LLM всегда знает, из какой Главы и Статьи пришёл ответ.
    """
    # Разбиваем на блоки по заголовкам
    heading_re = re.compile(r"^(#{1,4})\s+(.+)$", re.MULTILINE)
    splits = list(heading_re.finditer(markdown))

    if not splits:
        # Нет заголовков → режем по абзацам
        yield from _chunk_plain_text(markdown, meta, breadcrumb="")
        return

    # Сохраняем breadcrumb (путь заголовков) для контекста
    heading_stack: list[tuple[int, str]] = []  # (уровень, текст)

    for i, match in enumerate(splits):
        level = len(match.group(1))
        heading = match.group(2).strip()

        # Обрезаем стек до текущего уровня
        heading_stack = [(l, h) for l, h in heading_stack if l < level]
        heading_stack.append((level, heading))

        # Текст между этим заголовком и следующим
        start = match.end()
        end   = splits[i + 1].start() if i + 1 < len(splits) else len(markdown)
        block = markdown[start:end].strip()

        if not block:
            continue

        breadcrumb = " → ".join(h for _, h in heading_stack)

        # Если блок большой — режем дальше по абзацам
        if len(block) > CHUNK_MAX_CHARS:
            yield from _chunk_plain_text(block, meta, breadcrumb)
        elif len(block) >= CHUNK_MIN_CHARS:
            chunk = _make_chunk(block, meta, breadcrumb, heading)
            if _is_quality_chunk(chunk):
                yield chunk


def _chunk_plain_text(text: str, meta: dict, breadcrumb: str) -> Iterator[dict]:
    """Режет обычный текст на чанки с перекрытием."""
    paragraphs = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]
    current = ""
    for para in paragraphs:
        if len(current) + len(para) > CHUNK_MAX_CHARS and current:
            if len(current) >= CHUNK_MIN_CHARS:
                yield _make_chunk(current, meta, breadcrumb, "")
            # Перекрытие: берём хвост текущего чанка
            current = current[-CHUNK_OVERLAP:] + "\n\n" + para
        else:
            current = (current + "\n\n" + para).strip() if current else para
    if len(current) >= CHUNK_MIN_CHARS:
        chunk = _make_chunk(current, meta, breadcrumb, "")
        if _is_quality_chunk(chunk):
            yield chunk


def _is_quality_chunk(chunk: dict) -> bool:
    """Проверяет, что чанк не содержит мусорный текст."""
    text = chunk["raw_text"]
    for pattern in JUNK_PATTERNS:
        if pattern.search(text):
            return False
    return True


def _is_junk_file(markdown: str) -> bool:
    """Проверяет, что весь файл — это мусор (судебное решение, реклама).
    
    Мы НЕ проверяем наличие нормативной лексики — документы из манифеста
    нормативны по определению. Мы только отсеиваем явно неправильно скачанные файлы.
    """
    # Проверяем первые 2000 символов
    sample = markdown[:2000]
    for pattern in JUNK_FILE_PATTERNS:
        if pattern.search(sample):
            return True
    return False


def _make_chunk(text: str, meta: dict, breadcrumb: str, heading: str) -> dict:
    """Формирует структуру чанка для Weaviate."""

    # Для поиска LLM отдаём: заголовки + текст (полный контекст)
    full_text = f"[{meta['title']}]\n[{breadcrumb}]\n\n{text}" if breadcrumb else f"[{meta['title']}]\n\n{text}"

    # Определяем: это таблица или текст?
    is_table = bool(re.search(r"^\|.+\|", text, re.MULTILINE))

    return {
        "text":        full_text,        # то, что идёт в вектор и в поиск
        "raw_text":    text,             # чистый текст без breadcrumb
        "breadcrumb":  breadcrumb,       # путь заголовков (для отладки)
        "heading":     heading,          # текущий заголовок
        "doc_id":      meta["id"],
        "doc_title":   meta["title"],
        "dept":        meta["dept"],
        "section":     meta["section"],
        "doc_status":  meta.get("status", "active"),
        "source_url":  meta["url"],
        "is_table":    is_table,
        "chars":       len(text),
    }


# ─────────────────────────────────────────────
#  Weaviate — схема коллекции
# ─────────────────────────────────────────────
def ensure_collection(client: weaviate.WeaviateClient) -> None:
    """Создаёт коллекцию NormativeDoc если её ещё нет."""
    if client.collections.exists(COLLECTION_NAME):
        print(f"✅ Коллекция '{COLLECTION_NAME}' уже существует")
        return

    client.collections.create(
        name=COLLECTION_NAME,
        description="Нормативные документы строительной экспертизы (ФЗ, ПП РФ, СП, ГОСТ)",
        vectorizer_config=Configure.Vectorizer.none(),  # Векторы передаём сами
        vector_index_config=Configure.VectorIndex.hnsw(
            distance_metric=VectorDistances.COSINE,
            ef_construction=128,
            max_connections=32,
        ),
        properties=[
            Property(name="text",       data_type=DataType.TEXT,   description="Полный текст чанка с breadcrumb"),
            Property(name="raw_text",   data_type=DataType.TEXT,   description="Чистый текст без заголовков"),
            Property(name="breadcrumb", data_type=DataType.TEXT,   description="Путь заголовков: Глава → Статья"),
            Property(name="heading",    data_type=DataType.TEXT,   description="Текущий заголовок раздела"),
            Property(name="doc_id",     data_type=DataType.TEXT,   description="ID документа (PP-87, SP-42...)"),
            Property(name="doc_title",  data_type=DataType.TEXT,   description="Название документа"),
            Property(name="dept",       data_type=DataType.TEXT,   description="Отдел экспертизы (kap/ito/gochs...)"),
            Property(name="section",    data_type=DataType.TEXT,   description="Раздел ПД (01, 05.1, all...)"),
            Property(name="doc_status", data_type=DataType.TEXT,   description="Статус документа (active/cancelled)"),
            Property(name="source_url", data_type=DataType.TEXT,   description="URL источника"),
            Property(name="is_table",   data_type=DataType.BOOL,   description="Содержит ли таблицу"),
            Property(name="chars",      data_type=DataType.INT,    description="Длина чанка в символах"),
        ],
    )
    print(f"✅ Коллекция '{COLLECTION_NAME}' создана с HNSW индексом")


# ─────────────────────────────────────────────
#  Основной пайплайн индексации
# ─────────────────────────────────────────────
def index_documents(client: weaviate.WeaviateClient, embed_client: OpenAI) -> None:
    """Читает .md файлы, создаёт чанки, векторизует и загружает в Weaviate."""

    md_files = list(RAW_DIR.glob("*.md"))
    if not md_files:
        print(f"❌ Нет .md файлов в {RAW_DIR}")
        return

    print(f"📂 Найдено {len(md_files)} документов для индексации")

    collection = client.collections.get(COLLECTION_NAME)

    total_chunks  = 0
    skipped_files = 0

    # Загружаем батчами для скорости
    for md_file in md_files:
        doc_id    = md_file.stem
        meta_path = META_DIR / f"{doc_id}.json"

        if not meta_path.exists():
            print(f"⚠ Нет метаданных для {doc_id}, пропускаем")
            skipped_files += 1
            continue

        meta     = json.loads(meta_path.read_text("utf-8"))
        markdown = md_file.read_text("utf-8")

        # ── Фильтр качества на уровне файла ──────────────────────
        if len(markdown) < 300:
            print(f"  ⚠ Слишком короткий файл ({len(markdown)} байт), пропускаем: {doc_id}")
            skipped_files += 1
            continue

        if _is_junk_file(markdown):
            print(f"  ⚠ Мусорный файл (судебное решение/реклама), пропускаем: {doc_id}")
            skipped_files += 1
            continue

        # Нарезка на чанки
        chunks = list(smart_chunk(markdown, meta))
        if not chunks:
            print(f"⚠ Нет чанков: {doc_id}")
            continue

        print(f"  📄 {doc_id}: {len(chunks)} чанков ...", end="", flush=True)

        # Векторизация чанков через nomic-embed-text (LM Studio)
        vectors = []
        for chunk in chunks:
            # nomic-embed-text требует префикс 'search_document:' для индексируемых документов
            prefixed = f"search_document: {chunk['text'][:2000]}"
            resp = embed_client.embeddings.create(
                model=EMBED_MODEL,
                input=prefixed,
            )
            vectors.append(resp.data[0].embedding)
            time.sleep(0.05)  # небольшая пауза чтобы не перегрузить LM Studio

        # Загружаем в Weaviate (vectors — это list[list[float]] из API)
        objects = [
            DataObject(
                properties={k: v for k, v in chunk.items() if k != "text"},
                vector=vectors[i],  # уже list[float] — .tolist() не нужен
            )
            for i, chunk in enumerate(chunks)
        ]

        # Вставляем порциями
        with collection.batch.fixed_size(batch_size=BATCH_SIZE) as batch:
            for obj in objects:
                batch.add_object(
                    properties=obj.properties,
                    vector=obj.vector,
                )

        total_chunks += len(chunks)
        print(f" ✅ (итого: {total_chunks})")

    print(f"\n{'═' * 50}")
    print(f"✅ Индексация завершена!")
    print(f"   Документов:  {len(md_files) - skipped_files}")
    print(f"   Чанков:      {total_chunks}")
    print(f"   Пропущено:   {skipped_files}")
    print(f"   Коллекция:   {COLLECTION_NAME} @ {WEAVIATE_URL}")
    print(f"{'═' * 50}")


# ─────────────────────────────────────────────
#  Точка входа
# ─────────────────────────────────────────────
def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="RAG Indexer — МособлГосЭкспертиза")
    parser.add_argument("--reindex", action="store_true",
                        help="Удалить коллекцию и переиндексировать с нуля")
    args = parser.parse_args()

    print("🚀 RAG Indexer — МособлГосЭкспертиза")
    print(f"   Источник:  {RAW_DIR}")
    print(f"   Weaviate:  {WEAVIATE_URL}")
    print(f"   Модель:    {EMBED_MODEL}")

    # 1. Подключение к Weaviate
    print("\n📡 Подключение к Weaviate...")
    client = weaviate.connect_to_local(host="localhost", port=8080)
    print(f"   Версия Weaviate: {client.get_meta().get('version', '?')}")

    # Пересоздание коллекции если --reindex
    if args.reindex and client.collections.exists(COLLECTION_NAME):
        print(f"🗑  Удаляем старую коллекцию '{COLLECTION_NAME}'...")
        client.collections.delete(COLLECTION_NAME)
        print("   Удалено.")

    print(f"\n🤖 Загрузка модели nomic-embed-text через LM Studio...")
    embed_client = OpenAI(base_url=LM_STUDIO_URL, api_key=os.getenv("LM_STUDIO_API_KEY", "lm-studio"))
    # Проверка доступности LM Studio
    try:
        test_resp = embed_client.embeddings.create(model=EMBED_MODEL, input="test")
        embed_dim = len(test_resp.data[0].embedding)
        print(f"   Модель доступна, вектор: {embed_dim} измерений")
    except Exception as e:
        print(f"   ❌ LM Studio недоступен: {e}")
        print(f"   Проверьте что nomic-embed-text загружен в LM Studio")
        client.close()
        return

    # 3. Схема
    ensure_collection(client)

    # 4. Индексация
    print("\n📦 Начало индексации...")
    t0 = time.time()
    index_documents(client, embed_client)
    print(f"⏱ Время: {time.time() - t0:.1f} сек.")

    client.close()


if __name__ == "__main__":
    main()
