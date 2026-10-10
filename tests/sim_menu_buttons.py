import os,sys,tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import asyncio,sqlite3
import bot
bot.db=sqlite3.connect(":memory:"); bot.owner_id=1
bot.db.execute("CREATE TABLE messages (chat_id INTEGER, chat_title TEXT, sender_name TEXT, media_path TEXT, created INTEGER)")
bot.init_db()
sent=[]
async def tg(m,**p): sent.append((m,p)); return {"message_id":3}
bot.tg=tg
async def run():
    await bot.setup_menu()
    assert bot.kv_get("menu_shown")==bot.MENU_VERSION
    shown=[p for m,p in sent if m=="sendMessage"]
    assert len(shown)==1 and "Почта теперь в отдельном боте" in shown[0]["text"]
    sent.clear(); await bot.setup_menu()
    assert not [1 for m,_ in sent if m=="sendMessage"], "меню показано повторно"
    sent.clear(); await bot.on_message({"text":"📬 Письма"})
    assert "Слежу" in sent[-1][1]["text"]
    sent.clear(); await bot.on_callback({"id":"1","data":"c:k","message":{"message_id":5}})
    assert sent[-1][1]["text"]=="Почта теперь в отдельном боте"
asyncio.run(run())
print([r["text"] for row in bot.MENU_KB["keyboard"] for r in row])
