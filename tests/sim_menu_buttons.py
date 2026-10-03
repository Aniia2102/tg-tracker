import os,sys,tempfile
_TMP=tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import os,asyncio,sqlite3,json
os.environ.update(API_ID="1",API_HASH="x",SESSION="",BOT_TOKEN="9:a",GMAIL_CLIENT_ID="a",GMAIL_CLIENT_SECRET="b",GMAIL_REFRESH_TOKEN="c")
import mail
mail.db=sqlite3.connect(":memory:"); mail.owner_id=1; mail.init_db()
mail.kv_set("menu_shown","1"); mail.kv_set("initial_read","1")
async def lm(q): return ["u1","u2"]
mail.list_messages=lm
async def gm(*a,**k): return {}
mail.gmail=gm
sent=[]
async def tg(m,**p): sent.append((m,p)); return {"message_id":3}
mail.tg=tg
async def run():
    await mail.setup_menu()
    for t in ["🔁 Автопрочтение","📊 Статистика","📖 Прочитать всё","🔁 Автопрочтение"]:
        await mail.on_message({"text":t})
asyncio.run(run())
for m,p in sent:
    if m=="sendMessage": print("==",(p.get("text") or "")[:200].replace("\n"," / "))
print([r["text"] for row in mail.MENU_KB["keyboard"] for r in row])
