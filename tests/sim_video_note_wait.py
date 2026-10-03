import os,sys,tempfile
_TMP=tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import os,asyncio,types,shutil
os.environ.update(API_ID="1",API_HASH="x",SESSION="",BOT_TOKEN="999:abc",DB_PATH=os.path.join(_TMP,"t.db"))
import main
from telethon.tl.types import User, MessageMediaDocument
calls=[]
async def fake_call(method,**kw):
    calls.append((method,(kw.get("fields") or kw.get("json") or {}).get("caption") or (kw.get("json") or {}).get("text"),kw.get("file_path")))
    return method!="sendVideoNote"   # имитируем отказ Telegram для кружка
main.bot_call=fake_call; main.owner_id=1
async def slow_dl(msg,file):
    await asyncio.sleep(2); p=file+".mp4"; open(p,"wb").write(b"x"); return p
main.client.download_media=slow_dl
u=User(id=5,first_name="🥥")
m=types.SimpleNamespace(id=30,message="",media=MessageMediaDocument(),sticker=False,voice=False,video_note=True,gif=False,video=True,audio=False,file=types.SimpleNamespace(size=1000))
async def g(): return u
ev=types.SimpleNamespace(message=m,out=False,sender_id=5,is_private=True,is_group=False,chat_id=5,get_sender=g,get_chat=g)
async def run():
    await main.on_new(ev)
    await asyncio.sleep(0.1)
    await main.on_delete(types.SimpleNamespace(deleted_ids=[30],chat_id=None))
asyncio.run(run())
for c in calls: print(c)
print("файлов:",os.listdir(os.path.join(_TMP,"media")), "pending:",main.pending)
