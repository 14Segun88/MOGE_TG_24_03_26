import os
from dataclasses import dataclass
from typing import Optional, List, Dict
from dotenv import load_dotenv

# Импортируем наш умный поиск
from rag_search import NormSearch, SearchResult
from src.agents.groq_client import call_llm, MODEL_RAG_AGENT
from src.db.database import SessionLocal
from src.db.models import DisagreementLog

load_dotenv()

@dataclass
class KnowledgeBaseResponse:
    """Ответ агента базы знаний."""
    answer: str
    citations: List[Dict[str, str]]  # list of dicts with doc_title, section, source_url
    confidence: float
    raw_results: List[SearchResult]


class KnowledgeBaseAgent:
    """
    Агент знаний (Умный поисковик).
    
    Отвечает за поиск информации по векторной базе законов (Weaviate),
    формирование контекста и получение ответа от LLM (Qwen).
    """

    def __init__(self):
        # Инициализируем поиск (Weaviate + nomic-embed + reranker)
        self.search = NormSearch()
        
        print(f"🤖 KnowledgeBaseAgent: инициализация завершена. Готов к вызовам через Groq API ({MODEL_RAG_AGENT})")

    def ask(self, question: str, dept: Optional[str] = None, top_k: int = 5) -> KnowledgeBaseResponse:
        """
        Задаёт вопрос базе знаний.
        1. Ищет топ_k кусков текста через RAG.
        2. Отдаёт куски в LLM.
        3. Возвращает ответ.
        """
        print(f"\n[KnowledgeBaseAgent] ❓ Вопрос: {question}")
        
        # 1. Поиск релевантных кусков закона
        results = self.search.hybrid(query=question, dept=dept, top_k=top_k, alpha=0.5)
        
        if not results:
            print("[KnowledgeBaseAgent] ⚠ Ничего не найдено в базе данных.")
            return KnowledgeBaseResponse(
                answer="К сожалению, в нормативной базе не найдено информации по вашему запросу.",
                citations=[],
                confidence=0.0,
                raw_results=[]
            )

        print(f"[KnowledgeBaseAgent] 📚 Найдено чанков: {len(results)}")
        
        # 2. Формируем контекст для LLM
        context_parts = []
        citations = []
        for i, r in enumerate(results):
            # Сохраняем цитату
            citations.append({
                "doc_title": r.doc_title,
                "breadcrumb": r.breadcrumb,
                "source_url": r.source_url,
                "score": r.score
            })
            
            # Добавляем текст в промпт
            table_note = " [ЭТО ТАБЛИЦА, ЧИТАЙ ВНИМАТЕЛЬНО]" if r.is_table else ""
            context_parts.append(f"--- ДОКУМЕНТ {i+1} ---\n{r.doc_title} ({r.breadcrumb}){table_note}\n{r.raw_text}\n")
        
        full_context = "\n".join(context_parts)
        
        # 3. Запрос к LLM (собираем prompt)
        system_prompt = (
            "Ты — ИИ-эксперт государственной строительной экспертизы МособлГосЭкспертиза. "
            "Твоя задача — давать юридически точные ответы на вопросы инженеров и проверять соответствие проектов законам.\n\n"
            "ПРАВИЛА:\n"
            "1. Отвечай ТОЛЬКО на основе предоставленных выдержек из законов (Контекст).\n"
            "2. Если в контексте нет ответа на вопрос — честно скажи 'В нормативной базе нет точного ответа'. Не придумывай законы.\n"
            "3. ВСЕГДА ссылайся на точные пункты, статьи или таблицы из контекста (например: 'Согласно п. 4.3 СП 1.13130...').\n"
            "4. Будь краток и профессионален."
        )

        user_prompt = f"Контекст из нормативной базы:\n\n{full_context}\n\nВОПРОС: {question}\nОТВЕТЬ ОПИРАЯСЬ ТОЛЬКО НА КОНТЕКСТ ВЫШЕ:"

        print(f"[KnowledgeBaseAgent] 🧠 Генерация ответа через модель {MODEL_RAG_AGENT} (Groq API)...")
        
        try:
            # Вызов LLM через наш балансировщик паролей
            answer = call_llm(
                model=MODEL_RAG_AGENT,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=0.1
            )
            
            # Вычисляем простую уверенность (улучшим позже)
            # Временно возьмем максимальный score из Weaviate
            confidence = max((r.score for r in results), default=0.0)

            print(f"[KnowledgeBaseAgent] ✅ Ответ сгенерирован (уверенность RAG: {confidence:.2f})")
            
            # -----------------------------------------------------------------
            # HITL INTERCEPT: Логируем спорные случаи (уверенность < 0.70)
            # -----------------------------------------------------------------
            if confidence < 0.70:
                print("[KnowledgeBaseAgent] ⚠️ Уверенность ниже 70%. Логируем в Disagreement Log для эксперта.")
                try:
                    db = SessionLocal()
                    new_log = DisagreementLog(
                        document_id="RAG_Direct_Query", # Для прямых вопросов из чата
                        agent_name="KnowledgeBaseAgent",
                        ai_decision=f"ВОПРОС: {question}\n\nОТВЕТ: {answer}",
                        confidence=confidence,
                        is_reviewed=False
                    )
                    db.add(new_log)
                    db.commit()
                    db.close()
                except Exception as db_err:
                    print(f"[KnowledgeBaseAgent] ❌ Ошибка записи в БД HITL: {db_err}")

            return KnowledgeBaseResponse(
                answer=answer,
                citations=citations,
                confidence=confidence,
                raw_results=results
            )
            
        except Exception as e:
            print(f"[KnowledgeBaseAgent] ❌ Ошибка LLM API: {e}")
            return KnowledgeBaseResponse(
                answer=f"Произошла ошибка при генерации ответа: {e}",
                citations=citations,
                confidence=0.0,
                raw_results=results
            )
