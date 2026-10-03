import os,sys,tempfile
_TMP=tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import os,asyncio,sqlite3,json
os.environ.update(API_ID="1",API_HASH="x",SESSION="",BOT_TOKEN="9:a",GMAIL_CLIENT_ID="a",GMAIL_CLIENT_SECRET="b",GMAIL_REFRESH_TOKEN="c")
import mail
mail.db=sqlite3.connect(":memory:"); mail.owner_id=1; mail.init_db()
async def lm(q):
    if "promotions" in q: return [f"p{i}" for i in range(2300)]
    if "social" in q: return ["s1","p5"]
    return []
mail.list_messages=lm
calls=[]; hits=[0]
async def gm(method,path,**kw):
    if path=="/messages/batchModify":
        hits[0]+=1
        if hits[0]==2: raise RuntimeError("boom")
        calls.append(len(kw["json"]["ids"])); return {}
    return {}
mail.gmail=gm
sent=[]
async def tg(m,**p): sent.append((m,p.get("text"))); return {"message_id":7}
mail.tg=tg
async def nd(**k): sent.append(("digest",None))
mail.send_digest=nd
async def run():
    await mail.on_callback({"id":"1","data":"clean","message":{"chat":{"id":1},"message_id":5}})
asyncio.run(run())
print("batches",calls)
for m,t in sent: print(m,"|",t)
