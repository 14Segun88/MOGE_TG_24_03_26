import os
import weaviate
from openai import OpenAI
from src.db.database import SessionLocal
from src.db.models import DisagreementLog

# Конфигурация
WEAVIATE_URL = "http://localhost:8080"
COLLECTION_NAME = "NormativeDoc"
LM_STUDIO_URL = os.getenv("LM_STUDIO_URL", "http://172.31.128.1:1234/v1")
EMBED_MODEL = "text-embedding-nomic-embed-text-v1.5"

def _embed_text(text: str) -> list[float]:
    """Векторизует текст через nomic-embed-text в LM Studio."""
    client = OpenAI(base_url=LM_STUDIO_URL, api_key=os.getenv("LM_STUDIO_API_KEY", "lm-studio"))
    prefixed = f"search_query: {text}"
    resp = client.embeddings.create(
        model=EMBED_MODEL,
        input=prefixed,
    )
    return resp.data[0].embedding

def inject_precedent_to_weaviate(log_id: int):
    """
    Берет запись из лога разногласий, где эксперт подтвердил ошибку и ввел правильный ответ.
    Векторизует этот прецедент и загружает его в основную коллекцию Weaviate
    со специальным тегом "precedent".
    """
    db = SessionLocal()
    log = db.query(DisagreementLog).filter(DisagreementLog.id == log_id).first()
    
    if not log:
        print(f"❌ Запись с ID {log_id} не найдена.")
        db.close()
        return
        
    if not log.is_reviewed or not log.expert_decision:
        print(f"⚠ Разногласие {log_id} еще не обработано экспертом или нет решения.")
        db.close()
        return
        
    if log.added_to_rag:
        print(f"✔ Разногласие {log_id} уже добавлено в базу знаний RAG.")
        db.close()
        return
        
    print(f"🔄 Векторизация прецедента #{log_id}...")
    
    # Формируем текст прецедента
    precedent_text = (
        f"РЕШЕНИЕ ЭКСПЕРТА ГОСЭКСПЕРТИЗЫ ПО СПОРНОМУ ВОПРОСУ:\n"
        f"Вопрос/Спорный момент: {log.ai_decision}\n"
        f"Правильное решение: {log.expert_decision}\n"
        f"Обоснование: {log.expert_comment or 'Нет'}\n"
    )
    
    # Пытаемся подключиться к Weaviate с пропуском проверки версий
    client = weaviate.connect_to_local(host="localhost", port=8080, skip_init_checks=True)
    try:
        col = client.collections.get(COLLECTION_NAME)
        vector = _embed_text(precedent_text)
        
        # Загружаем как нормативный документ, но с признаком action="precedent" 
        # (в данном случае мы используем поле section или doc_title для метки)
        properties = {
            "doc_id": f"precedent_{log.id}",
            "doc_title": f"Прецедент (Human-in-The-Loop) #{log.id}",
            "dept": "all",
            "section": "precedent",
            "breadcrumb": "База Прецедентов Госэкспертизы",
            "heading": "Решение Эксперта",
            "raw_text": precedent_text,
            "source_url": f"local://hitl/{log.id}",
            "is_table": False,
            "doc_status": "active"
        }
        
        col.data.insert(
            properties=properties,
            vector=vector
        )
        
        # Отмечаем в SQL, что прецедент загружен
        log.added_to_rag = True
        db.commit()
        print(f"✅ Прецедент #{log_id} успешно добавлен в базу RAG Weaviate!")
        
    except Exception as e:
        print(f"❌ Ошибка при инъекции в Weaviate: {e}")
    finally:
        client.close()
        db.close()

if __name__ == "__main__":
    print("--- Утилита Инъекции Прецедентов в RAG ---")
    print("Для добавления в RAG вызовите `inject_precedent_to_weaviate(ID)`")
    # inject_precedent_to_weaviate(1)
