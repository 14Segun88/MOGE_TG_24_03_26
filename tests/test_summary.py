import sys
import asyncio
from pathlib import Path
from src.api.pipeline import _run_pipeline
from bot import _format_summary
from datetime import datetime
from uuid import uuid4
import os

os.environ["BOT_TOKEN"] = "test"
os.environ["ADMIN_ID"] = "123"

async def test():
    zip_path = "/home/segun/Практика в машинном обучении/Test/50-1-2-3-005906-2026 Документация представленная.zip"
    with open(zip_path, "rb") as f:
        zip_bytes = f.read()

    print("Running pipeline locally...")
    result = await _run_pipeline(uuid4(), zip_bytes)
    
    print("Formatting summary...")
    try:
        summary = _format_summary(result, "Test Package", 10.0)
        print("Summary generated safely!")
        print("Length:", len(summary))
    except Exception as e:
        print("ERROR IN format_summary:", e)
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test())
