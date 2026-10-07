import os,sys,tempfile
_TMP=tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import asyncio,types
os.environ.update(API_ID="1",API_HASH="x",SESSION="",BOT_TOKEN="999:abc",DB_PATH=os.path.join(_TMP,"t.db"))
import main
from telethon.tl.types import User, MessageMediaDocument, Chat
main.BOT_UPLOAD_LIMIT = 1000  # в тесте «большой» = больше 1000 байт
calls=[]
async def fake_call(method,**kw): calls.append(("bot",method)); return True
main.bot_call=fake_call; main.owner_id=1
async def fake_dl(msg,file):
    p=file+".mp4"; open(p,"wb").write(b"x"*(5000 if msg.id==40 else 10)); return p
main.client.download_media=fake_dl
async def fake_send_file(to,path,**kw): calls.append(("userbot",to,os.path.getsize(path)))
main.client.send_file=fake_send_file
u=User(id=5,first_name="Геля")
grp=Chat(id=77,title="Класс",photo=None,participants_count=3,date=None,version=1)
def ev(mid,size,chat=None):
    m=types.SimpleNamespace(id=mid,message="",media=MessageMediaDocument(),sticker=False,voice=False,
        video_note=False,gif=False,video=True,audio=False,file=types.SimpleNamespace(size=size))
    async def gs(): return u
    async def gc(): return chat or u
    return types.SimpleNamespace(message=m,out=False,sender_id=5,is_private=chat is None,is_group=chat is not None,
        chat_id=(-77 if chat else 5),get_sender=gs,get_chat=gc)
async def run():
    await main.on_new(ev(40, 120*2**20))          # 120 МБ видео в личке — сохраняем
    await main.on_new(ev(41, 120*2**20, grp))     # 120 МБ в группе — не сохраняем
    await main.on_new(ev(42, 30*2**20))           # 30 МБ — обычная отправка ботом
    await asyncio.sleep(0.1)
    await main.on_delete(types.SimpleNamespace(deleted_ids=[40,42],chat_id=None))
    await main.on_delete(types.SimpleNamespace(deleted_ids=[41],chat_id=-77))
asyncio.run(run())
print(calls)
assert ("userbot","me",5000) in calls, "большое видео должно уйти в Избранное"
assert ("bot","sendVideo") in calls, "среднее видео отправляет бот"
print("big video ok")
