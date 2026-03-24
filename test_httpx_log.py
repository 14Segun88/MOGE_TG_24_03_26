import asyncio
from telegram import Bot
from telegram.request import HTTPXRequest
import logging

logging.basicConfig(level=logging.INFO)

async def test():
    req = HTTPXRequest()
    bot = Bot("8557876344:AAEJSTx22il06RYoKWqI_cM9_eLbT4HCyJI", request=req)
    try:
        await bot.send_document(chat_id=5965363034, document=b"hello_world", filename="doc.txt")
    except Exception as e:
        print("FAILED:", type(e))

asyncio.run(test())
