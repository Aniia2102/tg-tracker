import os,sys,tempfile
_TMP=tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import os,asyncio,sqlite3,json
os.environ.update(API_ID="1",API_HASH="x",SESSION="",BOT_TOKEN="9:a",GMAIL_CLIENT_ID="a",GMAIL_CLIENT_SECRET="b",GMAIL_REFRESH_TOKEN="c")
import mail
mail.db=sqlite3.connect(":memory:"); mail.owner_id=1; mail.init_db()
inbox=["t1","t2","t3"]
async def lt(q,limit=None): return inbox
mail.list_threads=lt
calls=[]
async def gm(method,path,**kw):
    calls.append((path,kw.get("json")))
    tid=path.split("/")[2] if path.startswith("/threads/") else ""
    if kw.get("params")=={"format":"full"}: return {"messages":[{"payload":{"mimeType":"text/plain","body":{"data":"0J/RgNC40LLQtdGCINC40Lcg0L/QuNGB0YzQvNCw"}}}]}
    return {"messages":[{"payload":{"headers":[{"name":"Subject","value":f"Тема {tid}"},{"name":"From","value":f"S {tid} <a@b>"}]},"snippet":f"сниппет {tid}"}]}
mail.gmail=gm
async def tm(tid): return {"subject":f"Тема {tid}","sender":f"S {tid}","snippet":f"сниппет {tid}"}
mail.thread_meta=tm
sent=[]
async def tg(m,**p): sent.append((m,p)); return {"message_id":77}
mail.tg=tg
async def tr(ids): calls.append(("trash_many",ids)); return len(ids)
mail.trash_many=tr
def show(label):
    m,p=sent[-1]; print(f"--- {label} [{m}]"); print(p.get("text","").replace("\n"," / ")[:220])
    print("   ", [[b["text"] for b in row] for row in p["reply_markup"]["inline_keyboard"]])
async def cb(d): await mail.on_callback({"id":"x","data":d,"message":{"chat":{"id":1},"message_id":77}})
async def run():
    await mail.send_digest(header="🧹 Сегодня удалила 5.\n\n"); show("first card")
    await cb("c:f:t1"); show("full")
    await cb("c:k:t1"); show("after keep")
    await cb("c:l:t2"); show("after later")
    await cb("c:z"); show("after undo (t1 back)")
    await cb("c:d:t1"); show("after delete t1")
    await cb("c:all"); show("confirm all")
    await cb("c:allyes"); show("done")
    await cb("c:z"); 
    print("queue:", mail.queue(), "statuses:", mail.db.execute("select thread_id,status from gmail_items").fetchall())
    await mail.send_digest(); print("next digest:", sent[-1][1].get("text"))
asyncio.run(run())
print([c for c in calls if c[0]!="trash_many" and "modify" in c[0] or "trash" in str(c[0])])
