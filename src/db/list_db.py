from sqlalchemy.orm import Session
from src.db.database import SessionLocal
from src.db.models import DisagreementLog

def list_disagreements():
    db = SessionLocal()
    logs = db.query(DisagreementLog).all()
    
    print(f"--- РАЗНОГЛАСИЯ В БАЗЕ ДАННЫХ ({len(logs)}) ---")
    for log in logs:
        status = "✅ Проверено" if log.is_reviewed else "⏳ Ожидает эксперта"
        print(f"\nID: {log.id} | Документ: {log.document_id} | Агент: {log.agent_name} | Статус: {status}")
        print(f"Уверенность AI: {log.confidence:.2f}")
        print(f"Ответ системы:\n{log.ai_decision}")
        if log.is_reviewed:
            print(f"Решение эксперта: {log.expert_decision}")
            print(f"Цитата/Прецедент добавлен в RAG: {'Да' if log.added_to_rag else 'Нет'}")
            
    db.close()

if __name__ == "__main__":
    list_disagreements()
