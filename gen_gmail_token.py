"""
Получить доступ бота к Gmail (запускается ОДИН раз у себя на Mac).

    pip3 install google-auth-oauthlib
    cd ~/Downloads
    python3 gen_gmail_token.py

1. Если рядом лежит скачанный из Google Cloud файл client_secret_….json,
   скрипт возьмёт Client ID и Secret из него сам. Иначе спросит их.
2. Откроется браузер — войди в нужный Gmail и разреши доступ.
   Если Google пишет «Приложение не проверено» — нажми
   «Дополнительно» → «Перейти на страницу …» (это твоё же приложение).
3. Скрипт напечатает три значения — вставь их в Variables на Railway:
   GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, GMAIL_REFRESH_TOKEN.
   Никому их не показывай: это доступ к твоей почте.

Если в браузере «OAuth client was not found» — клиент ещё не активировался
у Google: подожди 5–10 минут и запусти скрипт снова.
"""

import glob
import json
import os

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]


def find_json():
    here = os.path.dirname(os.path.abspath(__file__))
    for folder in (here, os.getcwd(), os.path.expanduser("~/Downloads")):
        files = sorted(glob.glob(os.path.join(folder, "client_secret*.json")), key=os.path.getmtime)
        if files:
            return files[-1]
    return None


path = find_json()
if path:
    with open(path) as f:
        data = json.load(f)
    info = data.get("installed") or data.get("web") or {}
    client_id, client_secret = info["client_id"], info["client_secret"]
    print(f"Взяла ключи из файла {os.path.basename(path)}")
else:
    # вставка может прийти с переносами строк — склеиваем, пока значение не полное
    client_id = ""
    print("Вставь Client ID и нажми Enter:")
    while not client_id.endswith(".apps.googleusercontent.com"):
        line = "".join(input().split())
        if not line and client_id:
            break
        client_id += line
    client_secret = ""
    print("Вставь Client Secret и нажми Enter:")
    while len(client_secret) < 35:
        line = "".join(input().split())
        if not line and client_secret:
            break
        client_secret += line

if not client_id.endswith(".apps.googleusercontent.com"):
    raise SystemExit(
        "Client ID должен заканчиваться на .apps.googleusercontent.com — "
        "похоже, он скопировался не целиком."
    )

flow = InstalledAppFlow.from_client_config(
    {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    },
    SCOPES,
)
creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")

if not creds.refresh_token:
    raise SystemExit("Google не выдал refresh token — запусти скрипт ещё раз.")

print("\nГотово! Вставь в Railway → Variables:\n")
print(f"GMAIL_CLIENT_ID={client_id}")
print(f"GMAIL_CLIENT_SECRET={client_secret}")
print(f"GMAIL_REFRESH_TOKEN={creds.refresh_token}")
