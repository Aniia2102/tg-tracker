# export_mail.py шлёт файл и не трогает Telethon/getUpdates
import os, sys, json, asyncio, sqlite3, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
path = os.path.join(tempfile.mkdtemp(), "t.db")
os.environ.update(DB_PATH=path, BOT_TOKEN="9:a", GMAIL_CLIENT_ID="a", GMAIL_CLIENT_SECRET="b", GMAIL_REFRESH_TOKEN="c")
import mail, export_mail
mail.db = sqlite3.connect(path); mail.init_db(); mail.add_rule("from", "a@b.com"); mail.kv_set("auto_cleanup", "1")
got = []
async def tg_file(m, field, name, content, **p): got.append((name, json.loads(content), p))
mail.tg_file = tg_file
class Stop(Exception): pass
async def nosleep(_): raise Stop
export_mail.asyncio.sleep = nosleep
try:
    asyncio.run(export_mail.main(1082739603))
except Stop:
    pass
assert got and got[0][0] == "mail_export.json"
st = got[0][1]
assert st["owner_id"] == 1082739603 and st["custom_rules"][0]["value"] == "a@b.com" and st["kv"]["auto_cleanup"] == "1"
assert got[0][2]["chat_id"] == 1082739603
print("ok")
