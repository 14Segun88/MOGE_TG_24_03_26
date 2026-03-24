import asyncio
from telegram import Bot
import os
import logging

logging.basicConfig(level=logging.INFO)

token = "8557876344:AAEJSTx22il06RYoKWqI_cM9_eLbT4HCyJI"
bot = Bot(token)

async def test():
    print("Testing sendDocument...")
    try:
        await bot.send_document(chat_id=5965363034, document=b"hello_world123", filename="test.pdf", caption="Test")
        print("Document sent!")
    except Exception as e:
        print("FAILED:", e)

asyncio.run(test())
