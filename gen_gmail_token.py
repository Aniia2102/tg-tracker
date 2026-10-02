"""
Получить доступ бота к Gmail (запускается ОДИН раз у себя на Mac).

    pip3 install google-auth-oauthlib
    python3 gen_gmail_token.py

1. Скрипт спросит Client ID и Client Secret из Google Cloud.
2. Откроется браузер — войди в нужный Gmail и разреши доступ.
   Если Google пишет «Приложение не проверено» — нажми
   «Дополнительно» → «Перейти на страницу …» (это твоё же приложение).
3. Скрипт напечатает три значения — вставь их в Variables на Railway:
   GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, GMAIL_REFRESH_TOKEN.
   Никому их не показывай: это доступ к твоей почте.
"""

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]

client_id = input("Client ID: ").strip()
client_secret = input("Client Secret: ").strip()

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
