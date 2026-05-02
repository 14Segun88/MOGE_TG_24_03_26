import os
os.environ["WCS_SKIP_VERSION_CHECK"] = "true"

from src.agents.knowledge_base.agent import KnowledgeBaseAgent

def trigger_rag_intercept():
    agent = KnowledgeBaseAgent()
    # Sending a query that likely won't yield 0.70+ confidence
    response = agent.ask("какого цвета должен быть фасад больницы?", top_k=3)
    
    print("\n\n--- RAG Response ---")
    print(f"Confidence: {response.confidence}")
    print(f"Answer: {response.answer}")

if __name__ == "__main__":
    trigger_rag_intercept()
