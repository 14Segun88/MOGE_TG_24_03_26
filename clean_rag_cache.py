import os
import json
from pathlib import Path
from rag_crawler import DOCUMENTS

RAW_DIR = Path("/mnt/d/rag_data/raw")
META_DIR = Path("/mnt/d/rag_data/meta")

def clean_cache():
    allowed_ids = {d["id"] for d in DOCUMENTS}
    print(f"🧹 Очистка кэша RAG (разрешено {len(allowed_ids)} ID)...")
    
    removed_count = 0
    
    # MD файлы
    for f in RAW_DIR.glob("*.md"):
        if f.stem not in allowed_ids:
            print(f"🗑 Удаление мусора: {f.name}")
            f.unlink()
            removed_count += 1
            
    # JSON метаданные
    for f in META_DIR.glob("*.json"):
        if f.stem not in allowed_ids:
            f.unlink()
            
    print(f"✨ Удалено {removed_count} лишних файлов.")

if __name__ == "__main__":
    clean_cache()
