"""
Запусти ОДИН раз у себя на Mac, чтобы получить SESSION для Railway:

    pip3 install telethon
    python3 gen_session.py

Введи api_id, api_hash, номер телефона и код из Telegram.
Скопируй напечатанную строку в переменную SESSION на Railway.
Никому её не показывай — это полный доступ к аккаунту.
"""

from telethon.sessions import StringSession
from telethon.sync import TelegramClient

api_id = int(input("api_id: ").strip())
api_hash = input("api_hash: ").strip()

with TelegramClient(StringSession(), api_id, api_hash) as client:
    print("\nТвоя SESSION (скопируй целиком):\n")
    print(client.session.save())
