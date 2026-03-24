# /core/project_search.py

import os
import sys

# Добавляем корень проекта, чтобы работали импорты
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from rag_search import NormSearch
from src.agents.groq_client import call_llm

def retrieve_context(query: str, project_id: str, top_k: int = 10):
    """
    Извлекает контекст из нашей базы данных Weaviate.
    (Параметр project_id сохранен для совместимости со старым кодом,
    но поиск ведется по единой нормативной базе проекта).
    """
    search_client = NormSearch()
    try:
        # Используем гибридный поиск Weaviate
        results = search_client.hybrid(query=query, top_k=top_k, alpha=0.5)
        contexts = []
        for r in results:
            contexts.append({
                "text": f"[{r.doc_title} - {r.breadcrumb}] {r.raw_text}",
                "score": r.score
            })
        return contexts
    finally:
        search_client.close()


def ask_project(question: str, project_id: str):
    contexts = retrieve_context(question, project_id)

    if not contexts:
        return "Информация не найдена в базе нормативных документов."

    # сортировка по релевантности
    contexts = sorted(contexts, key=lambda x: x["score"], reverse=True)

    # Дебаг: выводим топ-5 retrieved контекстов
    print("\n=== RETRIEVED CONTEXT ===")
    for c in contexts[:5]:
        print(f"SCORE: {c['score']:.4f}")
        print(c["text"][:300])
        print("------------------")

    # Берём только реально релевантные куски. 
    # У Weaviate score может быть другим, поэтому снизим порог отсечения (например, > 0.0)
    # В NormSearch уже возвращаются только релевантные, поэтому просто берем топ-5
    filtered = [c["text"] for c in contexts[:5]]

    if not filtered:
        return "Недостаточно данных в документах"

    context_text = "\n\n".join(filtered)

    system_prompt = (
        "Ты аналитическая система проверки проектной документации. "
        "Отвечай ТОЛЬКО на основе предоставленного контекста. "
        "Если в контексте нет информации для ответа — ответь строго: \"Недостаточно данных в документах\". "
        "Ответь кратко и однозначно. Если ответа нет — напиши \"не найдено\"."
    )
    
    user_prompt = f"Контекст:\n{context_text}\n\nВопрос: {question}"

    try:
        # Вызываем Llama-3.3-70b через Groq
        response = call_llm(
            model="gpt-oss-120b",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.1
        )
        return response.strip()

    except Exception as e:
        return f"Ошибка LLM (Groq): {str(e)}"

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Использование: python project_search.py <project_id> \"<вопрос>\"")
        print("Пример: python project_search.py doc_1 \"какие нормы пожарной безопасности?\"")
        sys.exit(1)
        
    proj_id = sys.argv[1]
    question = sys.argv[2]
    
    print(f"🔎 Ищу по базе Weaviate ответ на вопрос (ID={proj_id}):\n'{question}'")
    
    answer = ask_project(question, proj_id)
    
    print("\n🤖 === ОТВЕТ 70B GROQ === 🤖")
    print(answer)
    