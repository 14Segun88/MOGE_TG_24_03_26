"""
RAG Indexer — индексация нормативных документов в Weaviate
МособлГосЭкспертиза | Спринт 4

Что делает:
  1. Читает .md файлы из /mnt/d/rag_data/raw/
  2. Нарезает на смысловые чанки по заголовкам (Smart Chunking)
  3. Создаёт векторы через nomic-embed-text в LM Studio
  4. Загружает в Weaviate с метаданными (отдел, раздел ПД, документ)

Запуск:
  python3 rag_indexer.py --reindex
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

# Эмбеддинг-модель: nomic-embed-text через LM Studio
LM_STUDIO_URL = "http://172.31.128.1:1234/v1"
EMBED_MODEL   = "text-embedding-nomic-embed-text-v1.5"
LM_STUDIO_API_KEY = os.getenv("LM_STUDIO_API_KEY", "sk-lm-V6B8mgjk:7DFHluGBuv2U6bmhuoZ5")

# Параметры чанкинга
CHUNK_MAX_CHARS  = 1500   # макс. символов в чанке
CHUNK_MIN_CHARS  = 100    # чанки меньше этого — выбрасываем
CHUNK_OVERLAP    = 100    # перекрытие

# Коллекция в Weaviate
COLLECTION_NAME  = "NormativeDoc"
BATCH_SIZE = 50

# Паттерны для фильтрации нежелательного контента в ЧАНКАХ
JUNK_PATTERNS = [
    re.compile(r"\d{2}-\d{4}/\d{4}"),           # судебные дела
    re.compile(r"регуляторная гильотина", re.I), 
    re.compile(r"судебная практика",     re.I),
    re.compile(r"арбитражный суд",       re.I),
    re.compile(r"kp\.ru|rg\.ru|ria\.ru", re.I),
]

# Паттерны для определения явно мусорного файла целиком
JUNK_FILE_PATTERNS = [
    re.compile(r"регуляторная гильотина",   re.I),
    re.compile(r"sudrf\.cntd\.ru",          re.I),
    re.compile(r"судебных\s+заседани",      re.I),
    re.compile(r"\d{2}-\d{4}/\d{4}.*\d{4}"),
    re.compile(r"Получить спецпредложение", re.I),
    re.compile(r"некоммерческой версии КонсультантПлюс", re.I),
]

def _is_junk_file(markdown: str) -> bool:
    """Проверяет, что весь файл — это мусор (судебное решение, реклама).
    
    ВАЖНО: Если файл >5к символов, мы его считаем полезным (полный текст).
    """
    if len(markdown) > 5000:
        return False
    sample = markdown[:2000]
    for pattern in JUNK_FILE_PATTERNS:
        if pattern.search(sample):
            return True
    return False

# ─────────────────────────────────────────────
#  Smart Chunker
# ─────────────────────────────────────────────
def smart_chunk(markdown: str, meta: dict) -> Iterator[dict]:
    heading_re = re.compile(r"^(#{1,4})\s+(.+)$", re.MULTILINE)
    splits = list(heading_re.finditer(markdown))

    if not splits:
        yield from _chunk_plain_text(markdown, meta, breadcrumb="")
        return

    heading_stack: list[tuple[int, str]] = []
    for i, match in enumerate(splits):
        level = len(match.group(1))
        heading = match.group(2).strip()
        heading_stack = [(l, h) for l, h in heading_stack if l < level]
        heading_stack.append((level, heading))

        start = match.end()
        end   = splits[i + 1].start() if i + 1 < len(splits) else len(markdown)
        block = markdown[start:end].strip()

        if not block: continue
        breadcrumb = " → ".join(h for _, h in heading_stack)

        if len(block) > CHUNK_MAX_CHARS:
            yield from _chunk_plain_text(block, meta, breadcrumb)
        elif len(block) >= CHUNK_MIN_CHARS:
            chunk = _make_chunk(block, meta, breadcrumb, heading)
            if _is_quality_chunk(chunk):
                yield chunk

def _chunk_plain_text(text: str, meta: dict, breadcrumb: str) -> Iterator[dict]:
    paragraphs = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]
    current = ""
    for para in paragraphs:
        if len(current) + len(para) > CHUNK_MAX_CHARS and current:
            if len(current) >= CHUNK_MIN_CHARS:
                yield _make_chunk(current, meta, breadcrumb, "")
            current = current[-CHUNK_OVERLAP:] + "\n\n" + para
        else:
            current = (current + "\n\n" + para).strip() if current else para
    if len(current) >= CHUNK_MIN_CHARS:
        chunk = _make_chunk(current, meta, breadcrumb, "")
        if _is_quality_chunk(chunk):
            yield chunk

def _is_quality_chunk(chunk: dict) -> bool:
    text = chunk["raw_text"]
    for pattern in JUNK_PATTERNS:
        if pattern.search(text):
            return False
    return True

def _make_chunk(text: str, meta: dict, breadcrumb: str, heading: str) -> dict:
    full_text = f"[{meta['title']}]\n[{breadcrumb}]\n\n{text}" if breadcrumb else f"[{meta['title']}]\n\n{text}"
    is_table = bool(re.search(r"^\|.+\|", text, re.MULTILINE))
    return {
        "text":        full_text,
        "raw_text":    text,
        "breadcrumb":  breadcrumb,
        "heading":     heading,
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
#  Weaviate Schema
# ─────────────────────────────────────────────
def ensure_collection(client: weaviate.WeaviateClient) -> None:
    if client.collections.exists(COLLECTION_NAME):
        print(f"✅ Коллекция '{COLLECTION_NAME}' ожидает заполнения")
        return

    client.collections.create(
        name=COLLECTION_NAME,
        vectorizer_config=Configure.Vectorizer.none(),
        vector_index_config=Configure.VectorIndex.hnsw(
            distance_metric=VectorDistances.COSINE,
        ),
        properties=[
            Property(name="text",       data_type=DataType.TEXT),
            Property(name="raw_text",   data_type=DataType.TEXT),
            Property(name="breadcrumb", data_type=DataType.TEXT),
            Property(name="heading",    data_type=DataType.TEXT),
            Property(name="doc_id",     data_type=DataType.TEXT),
            Property(name="doc_title",  data_type=DataType.TEXT),
            Property(name="dept",       data_type=DataType.TEXT),
            Property(name="section",    data_type=DataType.TEXT),
            Property(name="doc_status", data_type=DataType.TEXT),
            Property(name="source_url", data_type=DataType.TEXT),
            Property(name="is_table",   data_type=DataType.BOOL),
            Property(name="chars",      data_type=DataType.INT),
        ],
    )
    print(f"✅ Коллекция '{COLLECTION_NAME}' создана")

# ─────────────────────────────────────────────
#  Main Loop
# ─────────────────────────────────────────────
def index_documents(client: weaviate.WeaviateClient, embed_client: OpenAI) -> None:
    md_files = list(RAW_DIR.glob("*.md"))
    if not md_files: return
    
    # Сортируем по размеру, чтобы сначала видеть большие
    md_files.sort(key=lambda f: f.stat().st_size, reverse=True)
    
    collection = client.collections.get(COLLECTION_NAME)
    total_chunks  = 0
    skipped_files = 0

    for md_file in md_files:
        doc_id    = md_file.stem
        meta_path = META_DIR / f"{doc_id}.json"

        if not meta_path.exists():
            skipped_files += 1
            continue

        meta     = json.loads(meta_path.read_text("utf-8"))
        markdown = md_file.read_text("utf-8")

        if len(markdown) < 300:
            skipped_files += 1
            continue

        if _is_junk_file(markdown):
            print(f"  ⚠ Пропуск мусора: {doc_id}")
            skipped_files += 1
            continue

        chunks = list(smart_chunk(markdown, meta))
        if not chunks: continue

        print(f"  📄 {doc_id} ({len(markdown)} симв): {len(chunks)} чанков ...", end="", flush=True)

        vectors = []
        for chunk in chunks:
            prefixed = f"search_document: {chunk['text'][:3000]}" # Больше контекста для nomic
            resp = embed_client.embeddings.create(model=EMBED_MODEL, input=prefixed)
            vectors.append(resp.data[0].embedding)
            time.sleep(0.02)

        objects = [
            DataObject(
                properties={k: v for k, v in chunk.items() if k != "text"},
                vector=vectors[i]
            ) for i, chunk in enumerate(chunks)
        ]

        with collection.batch.fixed_size(batch_size=BATCH_SIZE) as batch:
            for obj in objects:
                batch.add_object(properties=obj.properties, vector=obj.vector)

        total_chunks += len(chunks)
        print(f" ✅")

    print(f"\n✅ ИТОГО: {total_chunks} чанков в {COLLECTION_NAME}")

def main() -> None:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--reindex", action="store_true")
    args = parser.parse_args()

    client = weaviate.connect_to_local(host="localhost", port=8080)
    if args.reindex and client.collections.exists(COLLECTION_NAME):
        client.collections.delete(COLLECTION_NAME)

    embed_client = OpenAI(base_url=LM_STUDIO_URL, api_key=LM_STUDIO_API_KEY)
    ensure_collection(client)
    index_documents(client, embed_client)
    client.close()

if __name__ == "__main__":
    main()
