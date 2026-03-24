import json
from src.agents.knowledge_base.agent import KnowledgeBaseAgent

def main():
    print("Инициализация KnowledgeBaseAgent...")
    # Инициализация с таймаутом на реранкер и Weaviate
    agent = KnowledgeBaseAgent()
    
    question = "Какая ширина эвакуационного пути должна быть в школе?"
    print(f"\nВопрос: {question}")
    
    # Ищем результат
    res = agent.ask(question=question, dept="gochs", top_k=3)
    
    print("\n" + "="*50)
    print("СГЕНЕРИРОВАННЫЙ ОТВЕТ:\n")
    print(res.answer)
    print("="*50)
    
    print(f"\nУверенность (RAG): {res.confidence:.2f}")
    if res.citations:
        print("\nИСТОЧНИКИ:")
        for idx, c in enumerate(res.citations):
            print(f"[{idx+1}] {c['doc_title']} ({c['breadcrumb']})")

if __name__ == "__main__":
    main()
