# MAIL_OFF выключает почту, но отключение чатов и /mailexport работают
import os, sys, json, asyncio, sqlite3
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.update(GMAIL_CLIENT_ID="a", GMAIL_CLIENT_SECRET="b", GMAIL_REFRESH_TOKEN="c", MAIL_OFF="1")
import mail

assert mail.MAIL_OFF and not mail.ENABLED
mail.db = sqlite3.connect(":memory:"); mail.owner_id = 77; mail.init_db()
mail.db.execute("CREATE TABLE messages (chat_id INTEGER, chat_title TEXT, sender_name TEXT, media_path TEXT, created INTEGER)")
mail.kv_set("auto_cleanup", "1"); mail.kv_set("disabled_rules", '["соцсети (Facebook, LinkedIn…)"]')
mail.add_rule("from", "news@shop.com"); mail.add_rule("subject", "webinar")
mail.db.execute("INSERT INTO gmail_items (thread_id,status,subject,sender,snippet,created,email,msg_date) "
                "VALUES ('t1','kept','Билет','El Al','…',1,'x@elal.com',2)")
mail.db.commit()

sent, files = [], []
async def tg(m, **p): sent.append((m, p)); return {"message_id": 5}
async def tg_file(m, field, name, content, **p): files.append((m, name, json.loads(content), p)); return {}
async def boom(*a, **k): raise AssertionError("Gmail не должен вызываться при MAIL_OFF")
mail.tg, mail.tg_file, mail.gmail = tg, tg_file, boom

async def run():
    await mail.on_message({"text": "📬 Письма"})
    await mail.on_callback({"id": "1", "data": "c:k:t1", "message": {"chat": {"id": 77}, "message_id": 9}})
    await mail.on_callback({"id": "2", "data": "mute:555:v", "message": {"chat": {"id": 77}, "message_id": 9}})
    await mail.on_message({"text": "/mailexport"})
asyncio.run(run())

texts = [p.get("text", "") for m, p in sent if m in ("sendMessage", "answerCallbackQuery")]
assert any("переехала" in t for t in texts), texts
assert mail.is_muted(555), "отключение чатов должно работать"
assert len(files) == 1 and files[0][0] == "sendDocument"
state = files[0][2]
assert state["kind"] == "tg-tracker-mail-export" and state["owner_id"] == 77
assert state["kv"]["auto_cleanup"] == "1" and "соцсети" in state["kv"]["disabled_rules"]
assert [r["value"] for r in state["custom_rules"]] == ["news@shop.com", "webinar"]
assert state["gmail_items"][0]["status"] == "kept"
assert "77" in files[0][3]["caption"]
print("ok")
