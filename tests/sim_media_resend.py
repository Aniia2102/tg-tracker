import os,sys,tempfile
_TMP=tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import os,asyncio,types,shutil
os.environ.update(API_ID="1",API_HASH="x",SESSION="",BOT_TOKEN="999:abc",DB_PATH=os.path.join(_TMP,"t.db"))
import main
from telethon.tl.types import User, MessageMediaPhoto, MessageMediaDocument
calls=[]
async def fake_call(method,**kw):
    calls.append((method,kw.get("fields") or kw.get("json"),kw.get("file_path"))); return True
main.bot_call=fake_call; main.owner_id=1
async def fake_dl(msg,file): p=file+".ogg"; open(p,"wb").write(b"x"); return p
main.client.download_media=fake_dl
u=User(id=5,first_name="🥥")
def ev(text,mid,media=None,voice=False):
    m=types.SimpleNamespace(id=mid,message=text,media=media,sticker=False,voice=voice,video_note=False,gif=False,video=False,audio=False,file=types.SimpleNamespace(size=1000))
    async def g(): return u
    return types.SimpleNamespace(message=m,out=False,sender_id=5,is_private=True,is_group=False,chat_id=5,get_sender=g,get_chat=g)
async def run():
    await main.on_new(ev("",20,MessageMediaDocument(),voice=True))
    await main.on_new(ev("смотри",21,MessageMediaPhoto()))
    await main.on_new(ev("[тест] привет",22))
    await asyncio.sleep(0.1)
    await main.on_delete(types.SimpleNamespace(deleted_ids=[20,21,22],chat_id=None))
asyncio.run(run())
for c in calls: print(c)
print("файлов осталось:",os.listdir(os.path.join(_TMP,"media")))
