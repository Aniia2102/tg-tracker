import os,sys,tempfile
_TMP=tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import os,asyncio,sqlite3,json
os.environ.update(API_ID="1",API_HASH="x",SESSION="",BOT_TOKEN="9:a",GMAIL_CLIENT_ID="a",GMAIL_CLIENT_SECRET="b",GMAIL_REFRESH_TOKEN="c")
import mail
mail.db=sqlite3.connect(":memory:"); mail.owner_id=1; mail.init_db()
async def lm(q): return [f"u{i}" for i in range(1500)] if "is:unread" in q else []
mail.list_messages=lm
mods=[]
async def gm(method,path,**kw): mods.append((path,len(kw["json"]["ids"]),kw["json"].get("removeLabelIds"))); return {}
mail.gmail=gm
sent=[]
async def tg(m,**p): sent.append((m,p)); return {"message_id":3}
mail.tg=tg
async def run():
    await mail.setup_menu()
    await mail.on_message({"text":"📖 Прочитать всё"})
    await mail.on_message({"text":"⚙️ Настройки"})
    await mail.on_callback({"id":"1","data":"ar","message":{"chat":{"id":1},"message_id":3}})
asyncio.run(run())
print(mods)
for m,p in sent:
    print("==",m, (p.get("text") or "")[:600])
    if p.get("reply_markup"): print("   KB:", json.dumps(p["reply_markup"],ensure_ascii=False)[:300])
    if m=="setMyCommands": print("   ", [c["command"] for c in p["commands"]])
print("auto_read:", mail.kv_get("auto_read"))
