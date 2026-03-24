import asyncio
from pathlib import Path
from uuid import uuid4
from src.api.pipeline import _run_pipeline

async def main():
    zip_path = Path("Test/50-1-2-3-005906-2026 Документация представленная.zip")
    zip_bytes = zip_path.read_bytes()
    
    print(f"Analyzing {zip_path}")
    result = await _run_pipeline(uuid4(), zip_bytes)
    
    out_path = Path("/tmp/bot_out_local.json")
    out_path.write_text(result.model_dump_json())
    print(f"Saved to {out_path}")

if __name__ == "__main__":
    asyncio.run(main())
