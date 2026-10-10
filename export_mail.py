"""
Разовый экспорт почты для переезда в life-bots/mail.

Трекер уже работает в life-bots/tracker, поэтому здесь не запускаем ни Telethon,
ни опрос бота: скрипт читает настройки почты из /data/tracker.db, присылает
Анне файл mail_export.json через бота-уведомителя и засыпает.
Запуск (startCommand на Railway): python export_mail.py <OWNER_ID>
"""

import asyncio
import os
import sqlite3
import sys

import aiohttp

import mail


async def main(owner: int) -> None:
    db = sqlite3.connect(os.environ.get("DB_PATH", "/data/tracker.db"))
    async with aiohttp.ClientSession() as http:
        mail.http, mail.db, mail.owner_id, mail.bot_token = http, db, owner, os.environ["BOT_TOKEN"]
        mail.init_db()
        state = mail.export_state()
        print(f"Экспорт: правил {len(state['custom_rules'])}, писем {len(state['gmail_items'])}", flush=True)
        await mail.send_export()
        print("Файл отправлен", flush=True)
    while True:  # не перезапускаться и не слать файл второй раз
        await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1])))
