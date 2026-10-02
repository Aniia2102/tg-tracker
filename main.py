"""
tg-tracker — юзербот, который присылает через отдельного бота
удалённые и изменённые сообщения из личных чатов и групп.
Свои сообщения не отслеживаются.

Переменные окружения:
  API_ID, API_HASH   — с my.telegram.org
  SESSION            — StringSession (получить через gen_session.py)
  BOT_TOKEN          — токен бота-уведомителя от @BotFather
  DB_PATH            — путь к базе (по умолчанию /data/tracker.db)
  RETENTION_DAYS     — сколько дней хранить сообщения (по умолчанию 30)
"""

import asyncio
import html
import logging
import os
import sqlite3
import time
from pathlib import Path

import aiohttp
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.types import (
    Channel,
    MessageMediaContact,
    MessageMediaDocument,
    MessageMediaGeo,
    MessageMediaPhoto,
    MessageMediaPoll,
    User,
)

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
)
log = logging.getLogger("tracker")

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
SESSION = os.environ["SESSION"]
BOT_TOKEN = os.environ["BOT_TOKEN"]
DB_PATH = os.environ.get("DB_PATH", "/data/tracker.db")
RETENTION_DAYS = int(os.environ.get("RETENTION_DAYS", "30"))

MAX_LEN = 4000  # лимит Telegram — 4096 символов

# ---------------------------------------------------------------- база


def open_db() -> sqlite3.Connection:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS messages (
            chat_id     INTEGER NOT NULL,
            msg_id      INTEGER NOT NULL,
            is_channel  INTEGER NOT NULL,   -- 1 для супергрупп (своя нумерация id)
            chat_title  TEXT,               -- название группы, NULL для лички
            sender_id   INTEGER,
            sender_name TEXT,
            username    TEXT,
            text        TEXT,
            created     INTEGER NOT NULL,
            PRIMARY KEY (chat_id, msg_id)
        )
        """
    )
    db.execute("CREATE INDEX IF NOT EXISTS idx_msg ON messages(msg_id)")
    db.commit()
    return db


db = open_db()

# ---------------------------------------------------------------- утилиты


def media_label(msg) -> str:
    m = msg.media
    if m is None:
        return ""
    if isinstance(m, MessageMediaPhoto):
        return "[фото]"
    if isinstance(m, MessageMediaDocument):
        if msg.voice:
            return "[голосовое]"
        if msg.video_note:
            return "[кружок]"
        if msg.video:
            return "[видео]"
        if msg.sticker:
            return "[стикер]"
        if msg.gif:
            return "[GIF]"
        if msg.audio:
            return "[аудио]"
        return "[файл]"
    if isinstance(m, MessageMediaGeo):
        return "[геолокация]"
    if isinstance(m, MessageMediaContact):
        return "[контакт]"
    if isinstance(m, MessageMediaPoll):
        return "[опрос]"
    return "[вложение]"


def message_text(msg) -> str:
    label = media_label(msg)
    text = msg.message or ""
    return f"{label} {text}".strip() if label else text


def display_name(user) -> str:
    if user is None:
        return "Неизвестный"
    name = " ".join(filter(None, [user.first_name, user.last_name])).strip()
    return name or "Без имени"


def quote(text: str) -> str:
    return f"<blockquote>{html.escape(text or '(пусто)')}</blockquote>"


def who(name: str, username: str | None) -> str:
    s = html.escape(name)
    if username:
        s += f" (@{html.escape(username)})"
    return s


def where(chat_title: str | None) -> str:
    return f" в «{html.escape(chat_title)}»" if chat_title else ""


def clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


# ---------------------------------------------------------------- отправка через бота

http: aiohttp.ClientSession | None = None
owner_id: int = 0


async def notify(text: str) -> None:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": owner_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    for attempt in range(3):
        try:
            async with http.post(url, json=payload) as r:
                data = await r.json()
                if data.get("ok"):
                    return
                retry = data.get("parameters", {}).get("retry_after")
                if retry:
                    await asyncio.sleep(retry)
                    continue
                log.error("Bot API: %s", data)
                return
        except Exception as e:  # сеть
            log.warning("Ошибка отправки (%s), попытка %d", e, attempt + 1)
            await asyncio.sleep(2)


# ---------------------------------------------------------------- клиент

client = TelegramClient(StringSession(SESSION), API_ID, API_HASH)
bot_id = int(BOT_TOKEN.split(":")[0])


async def should_track(event) -> bool:
    """Личка с человеком (не бот) или группа. Свои сообщения — нет."""
    if event.out or event.sender_id == owner_id:
        return False
    if event.is_private:
        if event.chat_id == bot_id:
            return False
        sender = await event.get_sender()
        return isinstance(sender, User) and not sender.bot
    return event.is_group


async def save(event) -> None:
    msg = event.message
    sender = await event.get_sender()
    chat = await event.get_chat()
    is_channel = isinstance(chat, Channel)
    title = None if event.is_private else getattr(chat, "title", None)
    if isinstance(sender, User):
        name, username = display_name(sender), sender.username
    else:  # сообщение от имени канала/группы
        name, username = getattr(sender, "title", "Неизвестный"), getattr(
            sender, "username", None
        )
    db.execute(
        "INSERT OR REPLACE INTO messages VALUES (?,?,?,?,?,?,?,?,?)",
        (
            event.chat_id,
            msg.id,
            int(is_channel),
            title,
            event.sender_id,
            name,
            username,
            message_text(msg),
            int(time.time()),
        ),
    )
    db.commit()


@client.on(events.NewMessage(incoming=True))
async def on_new(event):
    try:
        if await should_track(event):
            await save(event)
    except Exception:
        log.exception("on_new")


@client.on(events.MessageEdited(incoming=True))
async def on_edit(event):
    try:
        if not await should_track(event):
            return
        row = db.execute(
            "SELECT text, sender_name, username, chat_title FROM messages "
            "WHERE chat_id=? AND msg_id=?",
            (event.chat_id, event.message.id),
        ).fetchone()
        new_text = message_text(event.message)
        if row is None:
            # сообщение пришло до запуска бота — просто запоминаем
            await save(event)
            return
        old_text, name, username, title = row
        if old_text == new_text:
            return  # реакции и прочие «правки» без изменения текста
        head = f"{who(name, username)} изменил(а) сообщение{where(title)}:\n\n"
        budget = (MAX_LEN - len(head) - 60) // 2
        await notify(
            head
            + "Old:\n"
            + quote(clip(old_text, budget))
            + "\n\nNew:\n"
            + quote(clip(new_text, budget))
        )
        await save(event)
    except Exception:
        log.exception("on_edit")


@client.on(events.MessageDeleted())
async def on_delete(event):
    try:
        ids = list(event.deleted_ids)
        if not ids:
            return
        marks = ",".join("?" * len(ids))
        if event.chat_id is not None:
            # супергруппа: id уникальны только внутри чата
            rows = db.execute(
                f"SELECT chat_id, msg_id, sender_id, sender_name, username, chat_title, text "
                f"FROM messages WHERE chat_id=? AND msg_id IN ({marks})",
                [event.chat_id, *ids],
            ).fetchall()
        else:
            # личка или обычная группа: Telegram не сообщает чат,
            # но id в них общие для аккаунта и не пересекаются
            rows = db.execute(
                f"SELECT chat_id, msg_id, sender_id, sender_name, username, chat_title, text "
                f"FROM messages WHERE is_channel=0 AND msg_id IN ({marks})",
                ids,
            ).fetchall()
        for chat_id, msg_id, sender_id, name, username, title, text in rows:
            if sender_id == owner_id:
                continue
            head = f"{who(name, username)} удалил(а) сообщение{where(title)}:\n\n"
            await notify(head + quote(clip(text, MAX_LEN - len(head) - 40)))
            db.execute(
                "DELETE FROM messages WHERE chat_id=? AND msg_id=?", (chat_id, msg_id)
            )
        db.commit()
    except Exception:
        log.exception("on_delete")


async def cleanup_loop():
    while True:
        cutoff = int(time.time()) - RETENTION_DAYS * 86400
        n = db.execute("DELETE FROM messages WHERE created < ?", (cutoff,)).rowcount
        db.commit()
        if n:
            log.info("Очистка: удалено %d старых сообщений", n)
        await asyncio.sleep(6 * 3600)


async def main():
    global http, owner_id
    http = aiohttp.ClientSession()
    await client.connect()
    if not await client.is_user_authorized():
        raise SystemExit("SESSION недействительна — сгенерируй новую через gen_session.py")
    me = await client.get_me()
    owner_id = me.id
    log.info("Запущен как %s (id %s), база %s", display_name(me), me.id, DB_PATH)
    asyncio.create_task(cleanup_loop())
    try:
        await client.run_until_disconnected()
    finally:
        await http.close()


if __name__ == "__main__":
    asyncio.run(main())
