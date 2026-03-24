"""
RAG Search — гибридный поиск по нормативным документам
МособлГосЭкспертиза | Спринт 4

Использование:
  from rag_search import NormSearch
  search = NormSearch()
  results = search.hybrid("ширина пути эвакуации школа", dept="gochs", top_k=5)
  for r in results:
      print(r)
"""

from __future__ import annotations

import os
import logging
from dataclasses import dataclass, field
from typing import Optional

import weaviate
from weaviate.classes.query import HybridFusion, MetadataQuery
from dotenv import load_dotenv

log = logging.getLogger(__name__)

load_dotenv()

COLLECTION_NAME  = "NormativeDoc"
WEAVIATE_URL     = "http://localhost:8080"

# Эмбеддинги — nomic-embed-text через LM Studio (1536 измерения вс.вс. 768)
LM_STUDIO_URL    = os.getenv("LM_STUDIO_URL",   "http://172.31.128.1:1234/v1")
EMBED_MODEL      = "text-embedding-nomic-embed-text-v1.5"

# Реранкер — cross-encoder (загружается локально, ~100 МБ)
RERANK_MODEL     = "cross-encoder/ms-marco-MiniLM-L-6-v2"
RERANK_FACTOR    = 4    # берём top_k * RERANK_FACTOR из Weaviate, переранжируем до top_k


@dataclass
class SearchResult:
    """Один результат поиска."""
    doc_id:     str
    doc_title:  str
    dept:       str
    section:    str
    breadcrumb: str
    heading:    str
    raw_text:   str
    source_url: str
    is_table:   bool
    score:      float = 0.0

    def __str__(self) -> str:
        table_note = " [📊 таблица]" if self.is_table else ""
        return (
            f"{'─' * 60}\n"
            f"📋 {self.doc_title}{table_note}\n"
            f"📌 {self.breadcrumb}\n"
            f"🔗 {self.source_url}\n"
            f"📝 {self.raw_text[:400]}{'...' if len(self.raw_text) > 400 else ''}\n"
        )


class NormSearch:
    """
    Гибридный поиск по нормативным документам строительной экспертизы.

    Гибридный поиск = BM25 (точные слова) + Vector (смысл)
    Это критически важно для юридических текстов:
      - «СП 42.13330 таблица 1.1» → найдёт ТОЧНО эту таблицу (BM25)
      - «расстояние от школы до котельной» → найдёт по смыслу (Vector)
    """

    def __init__(self) -> None:
        # skip_init_checks=True отключает запрос к pypi.org за версией клиента
        self._client   = weaviate.connect_to_local(host="localhost", port=8080, skip_init_checks=True)
        self._col      = self._client.collections.get(COLLECTION_NAME)

        # Клиент LM Studio для эмбеддингов (опционально — может быть недоступен)
        try:
            from openai import OpenAI
            self._embed_client = OpenAI(base_url=LM_STUDIO_URL, api_key=os.getenv("LM_STUDIO_API_KEY", "lm-studio"))
        except Exception:
            self._embed_client = None

        # CrossEncoder (sentence_transformers) — ОТКЛЮЧЁН: вызывает segfault в asyncio
        # при загрузке через torch native C-extension. Используем BM25 fallback.
        self._reranker = None

        log.info("NormSearch: подключён к Weaviate. Реранкер: BM25 (CrossEncoder отключён).")

    def _embed(self, text: str) -> list[float] | None:
        """Векторизует текст через nomic-embed-text в LM Studio. Возвращает None если недоступен."""
        if not self._embed_client:
            return None
        try:
            prefixed = f"search_query: {text}"
            resp = self._embed_client.embeddings.create(
                model=EMBED_MODEL,
                input=prefixed,
                timeout=3.0,  # 3 сек таймаут — LM Studio может быть не запущен
            )
            return resp.data[0].embedding
        except Exception as e:
            log.debug(f"LM Studio embeddings недоступны ({e}), переходим на BM25")
            return None

    def _rerank(
        self,
        query: str,
        results: list[SearchResult],
        top_k: int,
    ) -> list[SearchResult]:
        """Переранжирует результаты через cross-encoder — финальный отбор.
        
        Если реранкер не загружен — возвращает первые top_k по score Weaviate.
        """
        if not results:
            return results
        
        # Graceful fallback если реранкер недоступен
        if self._reranker is None:
            return results[:top_k]
        
        # Формируем пары (вопрос, текст)
        pairs = [(query, r.raw_text) for r in results]
        scores = self._reranker.predict(pairs)
        
        # Сортируем по score реранкера и возвращаем топ_k
        ranked = sorted(zip(scores, results), key=lambda x: x[0], reverse=True)
        for score, result in ranked:
            result.score = round(float(score), 4)
        return [r for _, r in ranked[:top_k]]


    def close(self) -> None:
        try:
            self._client.close()
        except Exception:
            pass

    def __del__(self):
        self.close()

    # ── Основной метод: гибридный поиск ─────────────────────────────
    def hybrid(
        self,
        query: str,
        dept: Optional[str]    = None,
        section: Optional[str] = None,
        tables_only: bool      = False,
        top_k: int             = 5,
        alpha: float           = 0.5,
    ) -> list[SearchResult]:
        """
        Гибридный поиск.

        Если LM Studio недоступен — деградирует до BM25 (keyword).
        alpha=0.5 — баланс BM25 и Vector
        """
        query_vector = self._embed(query)  # None если LM Studio не запущен
        filters = self._build_filters(dept, section, tables_only)

        if query_vector is None:
            # LM Studio недоступен — используем только BM25
            log.warning("NormSearch: LM Studio недоступен → BM25-only поиск")
            return self.keyword(query, dept=dept, top_k=top_k)

        # Берём top_k * RERANK_FACTOR кандидатов — реранкер отберёт лучшие
        candidates_n = min(top_k * RERANK_FACTOR, 50)

        response = self._col.query.hybrid(
            query=query,
            vector=query_vector,
            alpha=alpha,
            fusion_type=HybridFusion.RELATIVE_SCORE,
            limit=candidates_n,
            filters=filters,
            return_metadata=MetadataQuery(score=True),
            return_properties=[
                "raw_text", "breadcrumb", "heading",
                "doc_id", "doc_title", "dept", "section",
                "source_url", "is_table", "doc_status",
            ],
        )

        candidates = [self._to_result(obj) for obj in response.objects]
        return self._rerank(query, candidates, top_k)

    # ── Только векторный поиск (семантический) ───────────────────────
    def semantic(
        self,
        query: str,
        dept: Optional[str] = None,
        top_k: int = 5,
    ) -> list[SearchResult]:
        """Чисто векторный поиск — для общих вопросов по строительству."""
        vector  = self._embed(query)
        filters = self._build_filters(dept)
        response = self._col.query.near_vector(
            near_vector=vector,
            limit=top_k,
            filters=filters,
            return_metadata=MetadataQuery(distance=True),
            return_properties=[
                "raw_text", "breadcrumb", "heading",
                "doc_id", "doc_title", "dept", "section",
                "source_url", "is_table",
            ],
        )
        return [self._to_result(obj) for obj in response.objects]

    # ── Только BM25 (точный поиск) ───────────────────────────────────
    def keyword(
        self,
        query: str,
        dept: Optional[str] = None,
        top_k: int = 5,
    ) -> list[SearchResult]:
        """BM25 — для поиска точных формулировок, номеров статей, кодов."""
        filters = self._build_filters(dept)
        response = self._col.query.bm25(
            query=query,
            limit=top_k,
            filters=filters,
            return_metadata=MetadataQuery(score=True),
            return_properties=[
                "raw_text", "breadcrumb", "heading",
                "doc_id", "doc_title", "dept", "section",
                "source_url", "is_table",
            ],
        )
        return [self._to_result(obj) for obj in response.objects]

    def _build_filters(
        self,
        dept: Optional[str]    = None,
        section: Optional[str] = None,
        tables_only: bool      = False,
    ):
        from weaviate.classes.query import Filter
        f = None
        if dept:
            f = Filter.by_property("dept").equal(dept)
        if section:
            sec_f = Filter.by_property("section").equal(section)
            f = f & sec_f if f else sec_f
        if tables_only:
            tbl_f = Filter.by_property("is_table").equal(True)
            f = f & tbl_f if f else tbl_f
        # Только действующие документы (не отменённые)
        active_f = Filter.by_property("doc_status").equal("active")
        f = f & active_f if f else active_f
        return f

    def _to_result(self, obj) -> SearchResult:
        p = obj.properties
        score = 0.0
        if obj.metadata:
            score = obj.metadata.score or obj.metadata.distance or 0.0
        return SearchResult(
            doc_id     = p.get("doc_id", ""),
            doc_title  = p.get("doc_title", ""),
            dept       = p.get("dept", ""),
            section    = p.get("section", ""),
            breadcrumb = p.get("breadcrumb", ""),
            heading    = p.get("heading", ""),
            raw_text   = p.get("raw_text", ""),
            source_url = p.get("source_url", ""),
            is_table   = p.get("is_table", False),
            score      = round(score, 4),
        )

    def stats(self) -> dict:
        """Статистика коллекции."""
        count = self._col.aggregate.over_all(total_count=True).total_count
        tables = self._col.aggregate.over_all(
            filters=None,
            total_count=False,
        )
        return {"total_chunks": count, "collection": COLLECTION_NAME}


# ─────────────────────────────────────────────
#  CLI для быстрого тестирования
# ─────────────────────────────────────────────
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="RAG Search — нормативные документы")
    parser.add_argument("query",          type=str,           help="Поисковый запрос")
    parser.add_argument("--dept",         type=str,           help="Отдел (kap/ito/gochs/iig/oos/tim)")
    parser.add_argument("--section",      type=str,           help="Раздел ПД (09, 05.4...)")
    parser.add_argument("--tables",       action="store_true",help="Искать только в таблицах")
    parser.add_argument("--mode",         choices=["hybrid", "semantic", "keyword"], default="hybrid")
    parser.add_argument("--top",          type=int, default=5)
    parser.add_argument("--alpha",        type=float, default=0.5,
                        help="BM25/Vector баланс (0=BM25, 1=Vector)")
    args = parser.parse_args()

    print(f"🔍 Поиск: «{args.query}» | режим: {args.mode} | alpha={args.alpha}")
    print("─" * 60)

    s = NormSearch()
    try:
        if args.mode == "hybrid":
            results = s.hybrid(args.query, dept=args.dept, section=args.section,
                               tables_only=args.tables, top_k=args.top, alpha=args.alpha)
        elif args.mode == "semantic":
            results = s.semantic(args.query, dept=args.dept, top_k=args.top)
        else:
            results = s.keyword(args.query, dept=args.dept, top_k=args.top)

        if not results:
            print("❌ Ничего не найдено")
        for r in results:
            print(r)
    finally:
        s.close()
