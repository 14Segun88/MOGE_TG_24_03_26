import asyncio
from bot import _send_long
from telegram import Bot
import os

token = "8557876344:AAEJSTx22il06RYoKWqI_cM9_eLbT4HCyJI"
bot = Bot(token)

class MockMessage:
    async def reply_text(self, text, parse_mode):
        print(f"-> Sending chunk of length: {len(text)}")
        await bot.send_message(chat_id=5965363034, text=text, parse_mode=parse_mode)

async def test():
    msg = MockMessage()
    val = "<b>Test</b>\n" * 500
    print("Total length:", len(val))
    await _send_long(msg, val, "HTML")
    print("Done!")

asyncio.run(test())
