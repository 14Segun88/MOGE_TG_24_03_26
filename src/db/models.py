from sqlalchemy import Column, Integer, String, Float, Text, Boolean, DateTime
from sqlalchemy.sql import func
from .database import Base

class DisagreementLog(Base):
    """
    Таблица для логирования "Разногласий" (Disagreement Log)
    Здесь сохраняются случаи, когда:
    1. Уверенность LLM (confidence) < 70%
    2. Агенты не согласны друг с другом
    3. Эксперт вручную отметил ответ системы как неверный
    """
    __tablename__ = "disagreement_log"

    id = Column(Integer, primary_key=True, index=True)
    
    # Метаданные об инциденте
    document_id = Column(String, index=True, nullable=False) # Ссылка на проверяемый XML/PDF
    agent_name = Column(String, index=True, nullable=False)  # Какой агент выдал ответ (напр. PP963Agent)
    
    # Что сказала система
    ai_decision = Column(Text, nullable=False) # Вывод модели ("Площадь 1000м2")
    confidence = Column(Float, nullable=False) # Уверенность (0.0 - 1.0)
    
    # Решение эксперта
    is_reviewed = Column(Boolean, default=False) # Проверено ли человеком
    expert_decision = Column(Text, nullable=True) # Правильный ответ от человека
    expert_comment = Column(Text, nullable=True) # Комментарий (почему ИИ ошибся)
    
    # Интеграция с RAG
    added_to_rag = Column(Boolean, default=False) # Был ли этот кейс векторизован в Weaviate как прецедент
    
    # Временные метки
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
