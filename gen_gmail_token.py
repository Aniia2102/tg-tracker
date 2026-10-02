"""
Получить доступ бота к Gmail (запускается ОДИН раз у себя на Mac).

    pip3 install google-auth-oauthlib
    python3 gen_gmail_token.py

Перед запуском положи рядом файл client_secret.json, скачанный из
Google Cloud (OAuth client, тип Desktop app).

Откроется браузер: войди в ilinaanna001@gmail.com и разреши доступ.
Если Google напишет «Приложение не проверено» — нажми
«Дополнительно» → «Перейти на страницу …» (это твоё же приложение).

Скрипт напечатает три значения — впиши их в Railway → Variables:
  GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, GMAIL_REFRESH_TOKEN
Никому их не показывай — это доступ к твоей почте.
"""

import json
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

# чтение, пометки (звёздочка), перемещение в Корзину. Безвозвратно удалять не может.
SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]

secret = Path(__file__).with_name("client_secret.json")
if not secret.exists():
    raise SystemExit(f"Не нашла {secret}. Скачай его из Google Cloud и положи рядом со скриптом.")

flow = InstalledAppFlow.from_client_secrets_file(str(secret), SCOPES)
creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")

info = json.loads(secret.read_text())
client = info.get("installed") or info.get("web")

print("\nВпиши в Railway → Variables:\n")
print(f"GMAIL_CLIENT_ID={client['client_id']}")
print(f"GMAIL_CLIENT_SECRET={client['client_secret']}")
print(f"GMAIL_REFRESH_TOKEN={creds.refresh_token}")
