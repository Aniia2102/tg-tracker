"""
tg-tracker — юзербот, который присылает через отдельного бота
удалённые и изменённые сообщения из личных чатов и групп.
Свои сообщения не отслеживаются. Фото, голосовые, кружки, видео,
аудио и файлы сохраняются и при удалении присылаются сами.

Переменные окружения:
  API_ID, API_HASH      — с my.telegram.org
  SESSION               — StringSession (получить через gen_session.py)
  BOT_TOKEN             — токен бота-уведомителя от @BotFather
  DB_PATH               — путь к базе (по умолчанию /data/tracker.db)
  RETENTION_DAYS        — сколько дней хранить текст (по умолчанию 30)
  MEDIA_RETENTION_DAYS  — сколько дней хранить медиа (по умолчанию 7)
  MEDIA_MAX_MB          — не скачивать файлы больше (по умолчанию 20)
  MEDIA_IN_GROUPS       — 1/0: сохранять медиа из групп (по умолчанию 1)
"""

import asyncio
import html
import json
import logging
import os
import sqlite3
import time
from pathlib import Path

import aiohttp
import mail
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
MEDIA_RETENTION_DAYS = int(os.environ.get("MEDIA_RETENTION_DAYS", "7"))
MEDIA_MAX_BYTES = int(float(os.environ.get("MEDIA_MAX_MB", "20")) * 1024 * 1024)
MEDIA_IN_GROUPS = os.environ.get("MEDIA_IN_GROUPS", "1") == "1"

MEDIA_DIR = Path(DB_PATH).parent / "media"
MAX_LEN = 4000  # лимит текста — 4096
MAX_CAPTION = 1000  # лимит подписи к файлу — 1024

# какой метод Bot API использовать для какого типа медиа
SEND_METHOD = {
    "photo": ("sendPhoto", "photo"),
    "voice": ("sendVoice", "voice"),
    "video_note": ("sendVideoNote", "video_note"),
    "video": ("sendVideo", "video"),
    "audio": ("sendAudio", "audio"),
    "gif": ("sendAnimation", "animation"),
    "document": ("sendDocument", "document"),
}

# ---------------------------------------------------------------- база


def open_db() -> sqlite3.Connection:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
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
            media_kind  TEXT,               -- photo / voice / video_note / ...
            media_path  TEXT,               -- путь к скачанному файлу
            PRIMARY KEY (chat_id, msg_id)
        )
        """
    )
    db.execute("CREATE INDEX IF NOT EXISTS idx_msg ON messages(msg_id)")
    db.commit()
    return db


db = open_db()

# ---------------------------------------------------------------- утилиты


def media_kind(msg) -> str | None:
    """Тип медиа, которое имеет смысл скачать и потом переслать."""
    m = msg.media
    if isinstance(m, MessageMediaPhoto):
        return "photo"
    if isinstance(m, MessageMediaDocument):
        if msg.sticker:
            return None
        if msg.voice:
            return "voice"
        if msg.video_note:
            return "video_note"
        if msg.gif:
            return "gif"
        if msg.video:
            return "video"
        if msg.audio:
            return "audio"
        return "document"
    return None


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


def media_size(msg) -> int:
    f = msg.file
    return (f.size or 0) if f else 0


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


def remove_file(path: str | None) -> None:
    if path:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass


# ---------------------------------------------------------------- отправка через бота

http: aiohttp.ClientSession | None = None
owner_id: int = 0


async def bot_call(method: str, *, json=None, file_field=None, file_path=None, fields=None) -> bool:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    for attempt in range(3):
        try:
            if file_path:
                form = aiohttp.FormData()
                for k, v in (fields or {}).items():
                    form.add_field(k, str(v))
                with open(file_path, "rb") as fh:
                    form.add_field(file_field, fh, filename=Path(file_path).name)
                    async with http.post(url, data=form) as r:
                        data = await r.json()
            else:
                async with http.post(url, json=json) as r:
                    data = await r.json()
            if data.get("ok"):
                return True
            retry = data.get("parameters", {}).get("retry_after")
            if retry:
                await asyncio.sleep(retry)
                continue
            log.error("Bot API %s: %s", method, data)
            return False
        except Exception as e:  # сеть
            log.warning("Ошибка %s (%s), попытка %d", method, e, attempt + 1)
            await asyncio.sleep(2)
    return False


def mute_kb(chat_id: int | None) -> dict | None:
    if chat_id is None:
        return None
    return {"inline_keyboard": [[{"text": "🔕 Не следить за этим чатом", "callback_data": f"mute:{chat_id}"}]]}


async def notify(text: str, chat_id: int | None = None) -> None:
    payload = {
        "chat_id": owner_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if (kb := mute_kb(chat_id)):
        payload["reply_markup"] = kb
    await bot_call("sendMessage", json=payload)


async def notify_media(kind: str, path: str, caption: str, chat_id: int | None = None) -> bool:
    """Отправить сохранённый файл. Подпись — если влезает в лимит."""
    method, field = SEND_METHOD.get(kind, SEND_METHOD["document"])
    fields = {"chat_id": owner_id}
    if (kb := mute_kb(chat_id)):
        fields["reply_markup"] = json.dumps(kb)
    long_caption = len(caption) > MAX_CAPTION
    # у кружков подписи не бывает
    if kind != "video_note" and not long_caption:
        fields.update(caption=caption, parse_mode="HTML")
    else:
        await notify(caption)
    ok = await bot_call(method, file_field=field, file_path=path, fields=fields)
    if not ok and kind == "video_note":
        # Telegram не принял как кружок — шлём обычным видео
        ok = await bot_call("sendVideo", file_field="video", file_path=path, fields=fields)
    if not ok and kind != "document":
        # например, голосовое в неподходящем формате — шлём как файл
        ok = await bot_call(
            "sendDocument", file_field="document", file_path=path, fields=fields
        )
    return ok


# ---------------------------------------------------------------- клиент

client = TelegramClient(StringSession(SESSION), API_ID, API_HASH)
bot_id = int(BOT_TOKEN.split(":")[0])


async def should_track(event) -> bool:
    """Личка с человеком (не бот) или группа. Свои сообщения — нет."""
    if event.out or event.sender_id == owner_id:
        return False
    if mail.is_muted(event.chat_id):
        return False
    if event.is_private:
        if event.chat_id == bot_id:
            return False
        sender = await event.get_sender()
        return isinstance(sender, User) and not sender.bot
    return event.is_group


async def download_media(event, kind: str) -> None:
    """Скачать медиа в фоне и записать путь в базу."""
    msg = event.message
    try:
        base = MEDIA_DIR / f"{event.chat_id}_{msg.id}"
        path = await client.download_media(msg, file=str(base))
        if not path:
            return
        cur = db.execute(
            "UPDATE messages SET media_path=? WHERE chat_id=? AND msg_id=?",
            (path, event.chat_id, msg.id),
        )
        db.commit()
        if cur.rowcount == 0:
            remove_file(path)  # сообщение уже удалили, пока качали
    except Exception:
        log.exception("download_media")
    finally:
        pending.pop((event.chat_id, msg.id), None)


# загрузки, которые ещё идут: (chat_id, msg_id) -> задача
pending: dict[tuple[int, int], asyncio.Task] = {}
DOWNLOAD_WAIT = 60  # сколько ждать загрузку, если сообщение удалили раньше


async def save(event, *, with_media: bool = True) -> None:
    msg = event.message
    sender = await event.get_sender()
    chat = await event.get_chat()
    is_channel = isinstance(chat, Channel)
    title = None if event.is_private else getattr(chat, "title", None)
    if isinstance(sender, User):
        name, username = display_name(sender), sender.username
    else:  # сообщение от имени канала/группы
        name = getattr(sender, "title", None) or "Неизвестный"
        username = getattr(sender, "username", None)

    kind = media_kind(msg) if with_media else None
    if kind and (
        media_size(msg) > MEDIA_MAX_BYTES or (event.is_group and not MEDIA_IN_GROUPS)
    ):
        kind = None

    db.execute(
        """
        INSERT INTO messages
            (chat_id, msg_id, is_channel, chat_title, sender_id, sender_name,
             username, text, created, media_kind, media_path)
        VALUES (?,?,?,?,?,?,?,?,?,?,NULL)
        ON CONFLICT(chat_id, msg_id) DO UPDATE SET
            text=excluded.text, sender_name=excluded.sender_name,
            username=excluded.username, chat_title=excluded.chat_title
        """,
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
            kind,
        ),
    )
    db.commit()
    if kind:
        pending[(event.chat_id, msg.id)] = asyncio.create_task(download_media(event, kind))


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
            + quote(clip(new_text, budget)),
            chat_id=event.chat_id,
        )
        await save(event, with_media=False)  # медиа уже сохранено
    except Exception:
        log.exception("on_edit")


@client.on(events.MessageDeleted())
async def on_delete(event):
    try:
        ids = list(event.deleted_ids)
        if not ids:
            return
        marks = ",".join("?" * len(ids))
        cols = (
            "chat_id, msg_id, sender_id, sender_name, username, chat_title, "
            "text, media_kind, media_path"
        )
        if event.chat_id is not None:
            # супергруппа: id уникальны только внутри чата
            rows = db.execute(
                f"SELECT {cols} FROM messages WHERE chat_id=? AND msg_id IN ({marks})",
                [event.chat_id, *ids],
            ).fetchall()
        else:
            # личка или обычная группа: Telegram не сообщает чат,
            # но id в них общие для аккаунта и не пересекаются
            rows = db.execute(
                f"SELECT {cols} FROM messages WHERE is_channel=0 AND msg_id IN ({marks})",
                ids,
            ).fetchall()
        for chat_id, msg_id, sender_id, name, username, title, text, kind, path in rows:
            task = pending.get((chat_id, msg_id))
            if task and sender_id != owner_id:
                # файл ещё качается — ждём, иначе пришлём только метку
                try:
                    await asyncio.wait_for(asyncio.shield(task), DOWNLOAD_WAIT)
                except asyncio.TimeoutError:
                    log.warning("Загрузка %s_%s не успела за %ss", chat_id, msg_id, DOWNLOAD_WAIT)
                row = db.execute(
                    "SELECT media_path FROM messages WHERE chat_id=? AND msg_id=?",
                    (chat_id, msg_id),
                ).fetchone()
                path = row[0] if row else path
            db.execute(
                "DELETE FROM messages WHERE chat_id=? AND msg_id=?", (chat_id, msg_id)
            )
            db.commit()
            if sender_id == owner_id or mail.is_muted(chat_id):
                remove_file(path)
                continue
            head = f"{who(name, username)} удалил(а) сообщение{where(title)}:\n\n"
            if path and os.path.exists(path):
                # сам файл уже показывает, что это — убираем метку вроде [фото]
                body = (text or "").split("] ", 1)[1] if (text or "").startswith("[") and "] " in text else ""
                caption = head.rstrip() if not body else head + quote(clip(body, MAX_LEN - len(head) - 40))
                if not await notify_media(kind, path, caption, chat_id=chat_id):
                    await notify(caption + "\n\n(файл не удалось отправить)", chat_id=chat_id)
                remove_file(path)
            else:
                await notify(head + quote(clip(text, MAX_LEN - len(head) - 40)), chat_id=chat_id)
    except Exception:
        log.exception("on_delete")


async def cleanup_loop():
    while True:
        now = int(time.time())
        # медиа удаляем раньше текста — оно занимает место на диске
        old_media = db.execute(
            "SELECT chat_id, msg_id, media_path FROM messages "
            "WHERE media_path IS NOT NULL AND created < ?",
            (now - MEDIA_RETENTION_DAYS * 86400,),
        ).fetchall()
        for chat_id, msg_id, path in old_media:
            remove_file(path)
            db.execute(
                "UPDATE messages SET media_path=NULL WHERE chat_id=? AND msg_id=?",
                (chat_id, msg_id),
            )
        n = db.execute(
            "DELETE FROM messages WHERE created < ?",
            (now - RETENTION_DAYS * 86400,),
        ).rowcount
        db.commit()
        # файлы, на которые в базе больше никто не ссылается
        known = {p for (p,) in db.execute(
            "SELECT media_path FROM messages WHERE media_path IS NOT NULL"
        )}
        stray = 0
        for f in MEDIA_DIR.iterdir():
            if str(f) not in known and now - f.stat().st_mtime > 3600:
                remove_file(str(f))
                stray += 1
        if n or old_media or stray:
            log.info(
                "Очистка: %d сообщений, %d медиа, %d лишних файлов",
                n, len(old_media), stray,
            )
        await asyncio.sleep(6 * 3600)


async def main():
    global http, owner_id
    http = aiohttp.ClientSession()
    await client.connect()
    if not await client.is_user_authorized():
        raise SystemExit("SESSION недействительна — сгенерируй новую через gen_session.py")
    me = await client.get_me()
    if me.bot:
        raise SystemExit(
            "SESSION принадлежит боту, а нужен личный аккаунт — "
            "сгенерируй её заново через gen_session_qr.py"
        )
    owner_id = me.id
    log.info("Запущен как %s (id %s), база %s", display_name(me), me.id, DB_PATH)
    asyncio.create_task(cleanup_loop())
    mail.start(http, db, owner_id, BOT_TOKEN, remove_file=remove_file)
    if not mail.ENABLED:
        log.info("Почта выключена: нет GMAIL_CLIENT_ID / GMAIL_CLIENT_SECRET / GMAIL_REFRESH_TOKEN")
    try:
        await client.run_until_disconnected()
    finally:
        await http.close()


if __name__ == "__main__":
    asyncio.run(main())
