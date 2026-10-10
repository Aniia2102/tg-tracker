import os,sys,tempfile
_TMP=tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import os,asyncio,types,shutil,json
os.environ.update(API_ID="1",API_HASH="x",SESSION="",BOT_TOKEN="999:abc",DB_PATH=os.path.join(_TMP,"t.db"))
import main, bot
from telethon.tl.types import User, Chat
sent=[]
async def fake_call(method,**kw): sent.append(("bot",method,kw.get("json") or kw.get("fields"))); return True
main.bot_call=fake_call; main.owner_id=1
async def tg(m,**p): sent.append(("tg",m,p)); return {"message_id":50}
bot.tg=tg; bot.db=main.db; bot.owner_id=1; bot._remove_file=main.remove_file; bot.init_db()
geli=User(id=5,first_name="геля",username="afgdhsi")
grp=Chat(id=77,title="Класс",photo=None,participants_count=3,date=None,version=1)
def ev(sender,text,mid,chat=None):
    m=types.SimpleNamespace(id=mid,message=text,media=None)
    async def gs(): return sender
    async def gc(): return chat or sender
    return types.SimpleNamespace(message=m,out=False,sender_id=sender.id,is_private=chat is None,is_group=chat is not None,
        chat_id=(-77 if chat else 5),get_sender=gs,get_chat=gc)
async def run():
    await main.on_new(ev(geli,"привет",10))
    await main.on_new(ev(geli,"в группе",11,grp))
    await main.on_edit(ev(geli,"в группе!",11,grp))
    edit=[x for x in sent if x[1]=="sendMessage"][-1][2]
    print("EDIT NOTIF KB:", edit["reply_markup"])
    sent.clear()
    # mute group from notification
    await bot.on_callback({"id":"1","data":"mute:-77","message":{"chat":{"id":1},"message_id":9}})
    for x in sent: print(x[1], {k:v for k,v in x[2].items() if k in("text","reply_markup")})
    sent.clear()
    await main.on_new(ev(geli,"ещё",12,grp)); await main.on_delete(types.SimpleNamespace(deleted_ids=[11,12],chat_id=None))
    assert not [x for x in sent if x[0]=="bot"], "из отключённого чата пришло уведомление"
    print("cached group msgs:", main.db.execute("select count(*) from messages where chat_id=-77").fetchone())
    sent.clear()
    await bot.on_message({"text":"🔕 Чаты"})
    print("CHATS:", sent[-1][2]["text"]); print(json.dumps(sent[-1][2]["reply_markup"],ensure_ascii=False))
    await bot.on_callback({"id":"2","data":"pick","message":{"chat":{"id":1},"message_id":50}})
    print("PICK:", sent[-1][2]["text"], json.dumps(sent[-1][2]["reply_markup"],ensure_ascii=False))
    await bot.on_callback({"id":"3","data":"unmute:-77:v","message":{"chat":{"id":1},"message_id":50}})
    print("AFTER UNMUTE:", sent[-1][2]["text"][:80])
    print("muted now:", bot._muted)
    assert not bot._muted
asyncio.run(run())
