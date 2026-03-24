import logging
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional, List, Dict

# Импортируем нашего агента
from src.agents.knowledge_base.agent import KnowledgeBaseAgent

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("KnowledgeBaseAPI")

# Инициализация FastAPI и Агента
app = FastAPI(
    title="KnowledgeBaseAgent API",
    description="Микросервис Умного Поиска (RAG + Qwen) для строительной экспертизы МособлГосЭкспертиза.",
    version="1.0.0"
)

# Ленивая инициализация агента при старте сервера
agent = None

@app.on_event("startup")
async def startup_event():
    global agent
    logger.info("Инициализация KnowledgeBaseAgent...")
    try:
        agent = KnowledgeBaseAgent()
        logger.info("KnowledgeBaseAgent готов к приему запросов.")
    except Exception as e:
        logger.error(f"Ошибка при инициализации агента: {e}")
        # Не падаем, чтобы healthcheck мог работать, но запросы будут отдавать 503

@app.on_event("shutdown")
async def shutdown_event():
    global agent
    if agent and hasattr(agent, "search"):
        agent.search.close()
        logger.info("KnowledgeBaseAgent остановлен.")


# ── Схемы данных (Pydantic) ──────────────────────────────────────────

class AskRequest(BaseModel):
    question: str
    dept: Optional[str] = None
    top_k: int = 5

class Citation(BaseModel):
    doc_title: str
    breadcrumb: str
    source_url: str
    score: float

class AskResponse(BaseModel):
    answer: str
    citations: List[Citation]
    confidence: float

# ── Эндпоинты ──────────────────────────────────────────────────────────

@app.get("/health")
async def health_check():
    """Проверка доступности сервиса."""
    if agent is None:
        raise HTTPException(status_code=503, detail="Агент ещё не инициализирован или произошла ошибка при запуске.")
    return {"status": "ok", "service": "KnowledgeBaseAgent"}


@app.post("/ask", response_model=AskResponse)
async def ask_question(request: AskRequest):
    """
    Задать вопрос интерфейсу базы знаний (RAG + LLM).
    
    - **question**: Суть вопроса (например, "Ширина эвакуационного пути?")
    - **dept**: Отдел (например, "gochs", "kap", "ito"). Если не передан — ищет по всем.
    - **top_k**: Сколько чанков брать из базы данных для ответа (по умолчанию 5).
    """
    if agent is None:
        raise HTTPException(status_code=503, detail="Агент базы знаний недоступен.")
    
    logger.info(f"Получен вопрос: {request.question} (Отдел: {request.dept})")
    
    try:
        # Вызов логики агента
        response = agent.ask(
            question=request.question,
            dept=request.dept,
            top_k=request.top_k
        )
        
        return AskResponse(
            answer=response.answer,
            citations=[Citation(**c) for c in response.citations],
            confidence=response.confidence
        )
        
    except Exception as e:
        logger.error(f"Ошибка при обработке вопроса: {e}")
        raise HTTPException(status_code=500, detail=f"Внутренняя ошибка сервера: {str(e)}")

# Для запуска через `uvicorn src.agents.knowledge_base.server:app --host 0.0.0.0 --port 8765`
