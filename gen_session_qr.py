"""
Получить SESSION для Railway через QR-код (без кода из Telegram).

    pip3 install telethon qrcode
    python3 gen_session_qr.py

1. Введи api_id и api_hash.
2. В терминале появится QR-код.
3. На телефоне: Telegram → Настройки → Устройства → Подключить устройство
   (Settings → Devices → Link Desktop Device) и отсканируй QR.
4. Если включена двухэтапная проверка — введи облачный пароль.
5. Скопируй напечатанную строку в переменную SESSION на Railway.
   Никому её не показывай — это полный доступ к аккаунту.
"""

import asyncio
import getpass

import qrcode
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from telethon.sessions import StringSession


def show_qr(url: str) -> None:
    qr = qrcode.QRCode(border=1)
    qr.add_data(url)
    qr.print_ascii(invert=True)


async def main() -> None:
    api_id = int(input("api_id: ").strip())
    api_hash = input("api_hash: ").strip()

    client = TelegramClient(StringSession(), api_id, api_hash)
    await client.connect()

    qr_login = await client.qr_login()
    while True:
        print("\nОтсканируй QR: Telegram → Настройки → Устройства → Подключить устройство\n")
        show_qr(qr_login.url)
        try:
            await qr_login.wait(timeout=60)
            break
        except asyncio.TimeoutError:
            print("\nQR устарел, показываю новый…")
            await qr_login.recreate()
        except SessionPasswordNeededError:
            await client.sign_in(password=getpass.getpass("Облачный пароль (2FA): "))
            break

    me = await client.get_me()
    if me.bot:
        raise SystemExit("Это бот, а нужен твой личный аккаунт.")
    print(f"\nВошла как {me.first_name} (id {me.id}).")
    print("\nТвоя SESSION (скопируй целиком):\n")
    print(client.session.save())
    await client.disconnect()


asyncio.run(main())
