import os,sys,tempfile
_TMP=tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import os,asyncio,sqlite3,json
os.environ.update(API_ID="1",API_HASH="x",SESSION="",BOT_TOKEN="9:a",GMAIL_CLIENT_ID="a",GMAIL_CLIENT_SECRET="b",GMAIL_REFRESH_TOKEN="c")
import mail
mail.db=sqlite3.connect(":memory:"); mail.owner_id=1; mail.init_db()
async def lt(q,limit=None): return ["t1","t2","t3"]
mail.list_threads=lt
async def lm(q): return ["m1","m2"] if "shop.com" in q or "скидка" in q else []
mail.list_messages=lm
async def tm(tid): return {"subject":f"Тема {tid}","sender":"Shop" if tid!="t2" else "Мама","email":"news@shop.com" if tid!="t2" else "mom@gmail.com","snippet":"…"}
mail.thread_meta=tm
async def gm(*a,**k): return {}
mail.gmail=gm
trashed=[]
async def tr(ids): trashed.extend(ids); return len(ids)
mail.trash_many=tr
sent=[]
async def tg(m,**p): sent.append((m,p)); return {"message_id":77}
mail.tg=tg
async def cb(d): await mail.on_callback({"id":"x","data":d,"message":{"chat":{"id":1},"message_id":77}})
def last(m=None):
    for mm,p in reversed(sent):
        if m is None or mm==m: return p
async def run():
    await mail.on_message({"text":"📋 Правила"})
    p=last("sendMessage"); print(p["text"]); print([[b["text"] for b in r] for r in p["reply_markup"]["inline_keyboard"]])
    await cb("r:t:0"); print("toggle ->", last("answerCallbackQuery")["text"], mail.disabled_rules())
    print("active names:", [r[0] for r in mail.active_rules()][:3])
    await cb("r:a:from"); print("prompt:", last("sendMessage")["text"][:50])
    await mail.on_message({"text":"привет мир"}); print("bad:", last("sendMessage")["text"])
    await mail.on_message({"text":"Shop.com"}); print("added:", [p["text"] for m,p in sent[-2:] if m=="sendMessage"][0])
    await cb("r:a:subject"); await mail.on_message({"text":"📬 Письма"})
    print("awaiting cancelled:", mail.kv_get("awaiting"))
    await cb("r:a:subject"); await mail.on_message({"text":'"скидка"'})
    print("rules:", mail.custom_rules())
    print("queries:", [(r[1],r[2]) for r in mail.active_rules() if r[0].startswith(("от","тема"))])
    # card block
    sent.clear(); await mail.send_digest()
    p=last("sendMessage"); print([[b["text"] for b in r] for r in p["reply_markup"]["inline_keyboard"]])
    await cb("c:b:t1"); print("block:", last("answerCallbackQuery")["text"], "trashed:", trashed)
    print("card now:", last("editMessageText")["text"].replace("\n"," / ")[:90])
    await cb("r:d:1"); print("after delete rule:", mail.custom_rules())
asyncio.run(run())
q_sender=mail.rule_query("from:(x.com)", sender=True); q_broad=mail.rule_query("category:promotions")
assert "category:purchases" not in q_sender and "cyber.org.il" in q_sender and "is:starred" in q_sender
assert "category:purchases" in q_broad
print("protection levels ok")
