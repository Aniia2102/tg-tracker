"""
Бот-уведомитель: всё, что идёт через Bot API.

  • цикл getUpdates (кнопки и сообщения Анны);
  • отключённые чаты — «🔕 Не следить за этим чатом» под уведомлением и экран «🔕 Чаты»;
  • меню-клавиатура и команды.

Почта отсюда вынесена в отдельный бот (life-bots/mail).
"""

import asyncio
import html
import json
import logging
import sqlite3
import time

import aiohttp

log = logging.getLogger("bot")

# ---------------------------------------------------------------- состояние

db: sqlite3.Connection | None = None
http: aiohttp.ClientSession | None = None
owner_id = 0
bot_token = ""


def init_db() -> None:
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
        CREATE TABLE IF NOT EXISTS muted_chats (
            chat_id INTEGER PRIMARY KEY,
            title   TEXT,
            created INTEGER NOT NULL
        );
        """
    )
    db.commit()
    _muted.clear()
    _muted.update(r[0] for r in db.execute("SELECT chat_id FROM muted_chats"))


def kv_get(k: str, default=None):
    row = db.execute("SELECT v FROM kv WHERE k=?", (k,)).fetchone()
    return row[0] if row else default


def kv_set(k: str, v) -> None:
    db.execute("INSERT OR REPLACE INTO kv VALUES (?,?)", (k, str(v)))
    db.commit()


async def tg(method: str, **payload):
    async with http.post(f"https://api.telegram.org/bot{bot_token}/{method}", json=payload) as r:
        data = await r.json()
    if not data.get("ok"):
        log.warning("TG %s: %s", method, data)
    return data.get("result")


async def answer(cb_id: str, text: str = "", show_alert: bool = False) -> None:
    await tg("answerCallbackQuery", callback_query_id=cb_id, text=text, show_alert=show_alert)


# ---------------------------------------------------------------- отключённые чаты

_muted: set[int] = set()
_remove_file = lambda path: None  # main.py подставляет свою функцию


def is_muted(chat_id) -> bool:
    return chat_id in _muted


def chat_title(chat_id: int) -> str:
    row = db.execute(
        "SELECT MAX(chat_title), MAX(sender_name) FROM messages WHERE chat_id=?", (chat_id,)
    ).fetchone()
    if row and (row[0] or row[1]):
        return row[0] or row[1]
    row = db.execute("SELECT title FROM muted_chats WHERE chat_id=?", (chat_id,)).fetchone()
    return (row[0] if row and row[0] else None) or f"чат {chat_id}"


def mute(chat_id: int) -> str:
    title = chat_title(chat_id)
    db.execute("INSERT OR REPLACE INTO muted_chats VALUES (?,?,?)", (chat_id, title, int(time.time())))
    # сохранённые сообщения этого чата больше не нужны
    for (path,) in db.execute(
        "SELECT media_path FROM messages WHERE chat_id=? AND media_path IS NOT NULL", (chat_id,)
    ).fetchall():
        _remove_file(path)
    db.execute("DELETE FROM messages WHERE chat_id=?", (chat_id,))
    db.commit()
    _muted.add(chat_id)
    return title


def unmute(chat_id: int) -> str:
    title = chat_title(chat_id)
    db.execute("DELETE FROM muted_chats WHERE chat_id=?", (chat_id,))
    db.commit()
    _muted.discard(chat_id)
    return title


def chats_view() -> tuple[str, dict]:
    rows = db.execute("SELECT chat_id, title FROM muted_chats ORDER BY created DESC").fetchall()
    if rows:
        text = (
            "🔕 <b>Отключённые чаты</b>\n"
            "Из них не присылаю удалённые и изменённые сообщения.\n\n"
            + "\n".join(f"• {html.escape(t or str(c))}" for c, t in rows)
        )
    else:
        text = (
            "🔕 <b>Отключённых чатов нет</b>\n\n"
            "Отключить чат можно кнопкой «🔕 Не следить за этим чатом» под любым уведомлением "
            "или здесь — «➕ Отключить чат»."
        )
    kb = [[{"text": f"🔔 {(t or str(c))[:40]}", "callback_data": f"unmute:{c}:v"}] for c, t in rows]
    kb.append([{"text": "➕ Отключить чат", "callback_data": "pick"}])
    return text, {"inline_keyboard": kb}


async def show_chats(edit_msg_id: int | None = None) -> None:
    text, kb = chats_view()
    if edit_msg_id:
        await tg("editMessageText", chat_id=owner_id, message_id=edit_msg_id, text=text, parse_mode="HTML", reply_markup=kb)
    else:
        await tg("sendMessage", chat_id=owner_id, text=text, parse_mode="HTML", reply_markup=kb)


async def show_pick(msg_id: int) -> None:
    """Недавние чаты, из которых приходили сообщения, — выбрать, какой отключить."""
    rows = db.execute(
        "SELECT chat_id, COALESCE(MAX(chat_title), MAX(sender_name)), MAX(created) AS last "
        "FROM messages GROUP BY chat_id ORDER BY last DESC LIMIT 20"
    ).fetchall()
    rows = [(c, t) for c, t, _ in rows if c not in _muted][:12]
    if not rows:
        text = "Пока не из чего выбрать — бот ещё не видел сообщений из других чатов."
    else:
        text = "Какой чат отключить? Недавние:"
    kb = [[{"text": (t or str(c))[:45], "callback_data": f"mute:{c}:v"}] for c, t in rows]
    kb.append([{"text": "← Назад", "callback_data": "chats"}])
    await tg("editMessageText", chat_id=owner_id, message_id=msg_id, text=text, reply_markup={"inline_keyboard": kb})


async def on_callback(cb: dict) -> None:
    data = cb.get("data", "")
    msg_id = (cb.get("message") or {}).get("message_id")
    if data == "pick":
        await answer(cb["id"])
        await show_pick(msg_id)
        return
    if data == "chats":
        await answer(cb["id"])
        await show_chats(edit_msg_id=msg_id)
        return
    kind, _, rest = data.partition(":")
    if kind not in ("mute", "unmute"):
        # старые кнопки почты и прочее — больше не работают
        await answer(cb["id"], "Почта теперь в отдельном боте")
        return
    chat_s, _, from_view = rest.partition(":")
    chat_id = int(chat_s)
    if kind == "mute":
        title = mute(chat_id)
        await answer(cb["id"], f"🔕 Больше не слежу за «{title[:40]}»")
        flip = {"text": "🔔 Снова следить за этим чатом", "callback_data": f"unmute:{chat_id}"}
    else:
        title = unmute(chat_id)
        await answer(cb["id"], f"🔔 Снова слежу за «{title[:40]}»")
        flip = {"text": "🔕 Не следить за этим чатом", "callback_data": f"mute:{chat_id}"}
    if from_view:
        await show_chats(edit_msg_id=msg_id)
    else:
        # нажали под уведомлением — меняем кнопку на противоположную
        await tg("editMessageReplyMarkup", chat_id=owner_id, message_id=msg_id,
                 reply_markup={"inline_keyboard": [[flip]]})


# ---------------------------------------------------------------- меню

BTN_CHATS = "🔕 Чаты"
BTN_HELP = "❓ Помощь"

MENU_KB = {
    "keyboard": [[{"text": BTN_CHATS}, {"text": BTN_HELP}]],
    "resize_keyboard": True,
    "is_persistent": True,
    "input_field_placeholder": "Выбери действие 👇",
}
MENU_VERSION = "5"

COMMANDS = [
    {"command": "chats", "description": "Отключённые чаты"},
    {"command": "help", "description": "Помощь"},
]


async def show_menu() -> None:
    await tg(
        "sendMessage",
        chat_id=owner_id,
        text=(
            "👋 <b>Слежу за удалёнными и изменёнными сообщениями</b>\n\n"
            "Когда кто-то удалит или изменит сообщение в личке или группе, пришлю его сюда — "
            "вместе с фото, голосовыми и кружками.\n\n"
            f"{BTN_CHATS} — чаты, из которых не присылать уведомления\n\n"
            "📬 Почта теперь в отдельном боте."
        ),
        parse_mode="HTML",
        reply_markup=MENU_KB,
    )


async def on_message(m: dict) -> None:
    text = (m.get("text") or "").strip().split("@")[0]
    if text in ("/chats", BTN_CHATS):
        await show_chats()
    else:
        await show_menu()


# ---------------------------------------------------------------- цикл обновлений

_bg: set[asyncio.Task] = set()


async def handle_update(upd: dict) -> None:
    try:
        if "callback_query" in upd:
            cb = upd["callback_query"]
            if cb["from"]["id"] == owner_id:
                await on_callback(cb)
        elif "message" in upd and upd["message"].get("from", {}).get("id") == owner_id:
            await on_message(upd["message"])
    except Exception:
        log.exception("update")


async def updates_loop() -> None:
    await tg("deleteWebhook")
    offset = int(kv_get("tg_offset", "0"))
    while True:
        try:
            async with http.get(
                f"https://api.telegram.org/bot{bot_token}/getUpdates",
                params={"offset": offset, "timeout": 50, "allowed_updates": json.dumps(["message", "callback_query"])},
                timeout=aiohttp.ClientTimeout(total=70),
            ) as r:
                data = await r.json()
            for upd in data.get("result", []):
                offset = upd["update_id"] + 1
                kv_set("tg_offset", offset)
                _bg.add(t := asyncio.create_task(handle_update(upd)))
                t.add_done_callback(_bg.discard)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("getUpdates: %s", e)
            await asyncio.sleep(5)


async def setup_menu() -> None:
    """Команды в кнопке «Меню» Telegram; новое меню — один раз после обновления."""
    try:
        await tg("setMyCommands", commands=COMMANDS)
        await tg("setChatMenuButton", chat_id=owner_id, menu_button={"type": "commands"})
        if kv_get("menu_shown") != MENU_VERSION:
            await show_menu()
            kv_set("menu_shown", MENU_VERSION)
    except Exception:
        log.exception("setup_menu")


def start(session: aiohttp.ClientSession, database: sqlite3.Connection, owner: int, token: str,
          remove_file=None) -> list[asyncio.Task]:
    global http, db, owner_id, bot_token, _remove_file
    http, db, owner_id, bot_token = session, database, owner, token
    if remove_file:
        _remove_file = remove_file
    init_db()
    return [asyncio.create_task(updates_loop()), asyncio.create_task(setup_menu())]
