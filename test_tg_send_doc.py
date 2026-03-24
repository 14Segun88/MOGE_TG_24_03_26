import asyncio
from telegram import Bot
import os
import logging

logging.basicConfig(level=logging.INFO)

token = "REDACTED_BOT_TOKEN"
bot = Bot(token)

async def test():
    print("Testing sendDocument...")
    try:
        await bot.send_document(chat_id=5965363034, document=b"hello_world123", filename="test.pdf", caption="Test")
        print("Document sent!")
    except Exception as e:
        print("FAILED:", e)

asyncio.run(test())
