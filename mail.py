"""
Почта: чистка Gmail по правилам и вечерняя сводка в Telegram.

Включается, если заданы переменные:
  GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET, GMAIL_REFRESH_TOKEN
Необязательные:
  DIGEST_HOUR   — час сводки (по умолчанию 19)
  DIGEST_LIMIT  — сколько писем в одной сводке (по умолчанию 30)
  TZ_NAME       — часовой пояс (по умолчанию Asia/Nicosia)
  KEEP_FROM     — доп. отправители, которых не трогать никогда, через запятую

Как работает:
  • Правила удаления (RULES) — то, что точно не нужно: промо, соцсети,
    коды подтверждения, старые уведомления. Первый раз бот присылает
    сколько нашёл и ждёт подтверждения; дальше чистит сам каждый вечер.
  • Всё остальное, что пришло во «Входящие» и не отмечено звёздочкой,
    попадает в сводку: заголовок, отправитель, фрагмент и кнопки
    ⭐ оставить / 🗑 удалить / 📖 показать целиком.
  • Удаление = перемещение в Корзину (Gmail хранит её 30 дней).
"""

import asyncio
import base64
import datetime as dt
import html
import json
import logging
import os
import re
import sqlite3
import time
from zoneinfo import ZoneInfo

import aiohttp

log = logging.getLogger("mail")

CLIENT_ID = os.environ.get("GMAIL_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("GMAIL_CLIENT_SECRET", "")
REFRESH_TOKEN = os.environ.get("GMAIL_REFRESH_TOKEN", "")
DIGEST_HOUR = int(os.environ.get("DIGEST_HOUR", "19"))
DIGEST_LIMIT = int(os.environ.get("DIGEST_LIMIT", "30"))
TZ = ZoneInfo(os.environ.get("TZ_NAME", "Asia/Nicosia"))
PAGE = 10  # писем в одном сообщении сводки

ENABLED = bool(CLIENT_ID and CLIENT_SECRET and REFRESH_TOKEN)

API = "https://gmail.googleapis.com/gmail/v1/users/me"

# ---------------------------------------------------------------- правила

# что никогда не трогаем (ни удаление, ни сводка)
KEEP = ["from:cyber.org.il", "ממריאות"] + [
    f"from:{s.strip()}" for s in os.environ.get("KEEP_FROM", "").split(",") if s.strip()
]
# что не удаляем автоматически, но показываем в сводке (решаешь ты)
ASK = [
    "from:edu",
    "from:collegeboard.org",
    "category:purchases",
    "from:(moovit-pango.co.il OR ravkavonline.co.il)",
    'subject:(invoice OR receipt OR "order confirmation" OR booking OR reservation OR ticket '
    "OR boarding OR itinerary OR חשבון OR חשבונית OR קבלה OR כרטיס OR הזמנה OR счёт OR счет OR билет)",
]

SCOPE = "{in:inbox in:spam} -is:starred -in:sent"
NEVER_DELETE = " ".join(f"-{k}" for k in KEEP + ASK)

# (название для отчёта, запрос Gmail)
RULES = [
    ("промо и реклама", "category:promotions"),
    ("соцсети (Facebook, LinkedIn…)", "category:social"),
    (
        "рассылки сервисов",
        "from:(kaptest.com OR macaly.com OR news.railway.app OR m.ngrok.com "
        "OR webcasts@gitlab.com OR info@mail.gitlab.com OR jobalerts-noreply@linkedin.com "
        "OR facebookmail.com OR notices.dropbox.com OR wizznews.com)",
    ),
    (
        "коды, подтверждения, входы, сброс пароля",
        'subject:(verify OR verification OR "confirm your email" OR "verification code" '
        'OR "קוד אימות" OR "password reset" OR "reset your password" OR "secure link" '
        'OR "sign in to" OR activation OR "login by email" OR "קוד האימות") older_than:1d',
    ),
    ("старые оповещения безопасности Google", 'from:accounts.google.com subject:"security alert" older_than:7d'),
    ("статусы доставки AliExpress", "from:(notice.aliexpress.com OR cainiao.com) older_than:14d"),
    ("уведомления Railway", "from:notify.railway.app older_than:3d"),
    ("брони и билеты старше года", "category:reservations older_than:1y"),
]

DIGEST_QUERY = "in:inbox -is:starred " + " ".join(f"-{k}" for k in KEEP)


def rule_query(q: str) -> str:
    return f"{SCOPE} {NEVER_DELETE} ({q})"


# ---------------------------------------------------------------- состояние

db: sqlite3.Connection | None = None
http: aiohttp.ClientSession | None = None
owner_id = 0
bot_token = ""


def init_db() -> None:
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS gmail_items (
            thread_id TEXT PRIMARY KEY,
            status    TEXT NOT NULL,      -- sent / kept / trashed
            subject   TEXT,
            sender    TEXT,
            snippet   TEXT,
            created   INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS digest_msgs (
            tg_msg_id  INTEGER PRIMARY KEY,
            thread_ids TEXT NOT NULL       -- JSON-список в порядке номеров
        );
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


# ---------------------------------------------------------------- Gmail API

_token = {"value": "", "exp": 0.0}


class GmailAuthError(Exception):
    pass


async def access_token() -> str:
    if _token["value"] and time.time() < _token["exp"] - 60:
        return _token["value"]
    async with http.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "refresh_token": REFRESH_TOKEN,
            "grant_type": "refresh_token",
        },
    ) as r:
        data = await r.json()
    if "access_token" not in data:
        raise GmailAuthError(data.get("error_description") or data.get("error") or str(data))
    _token.update(value=data["access_token"], exp=time.time() + data.get("expires_in", 3600))
    return _token["value"]


def _rate_limited(status: int, data) -> bool:
    text = json.dumps(data) if not isinstance(data, str) else data
    return status == 429 or (status == 403 and ("rateLimitExceeded" in text or "Quota exceeded" in text))


async def gmail(method: str, path: str, **kw):
    for attempt in range(6):
        headers = {"Authorization": f"Bearer {await access_token()}"}
        async with http.request(method, API + path, headers=headers, **kw) as r:
            if r.status in (500, 502, 503):
                await asyncio.sleep(2 ** attempt)
                continue
            if r.status == 401:
                _token["value"] = ""
                continue
            data = await r.json(content_type=None) if r.content_length != 0 else {}
            if _rate_limited(r.status, data):
                # лимит Gmail в минуту — ждём и повторяем
                log.info("Лимит Gmail, жду %s c", 20 * (attempt + 1))
                await asyncio.sleep(20 * (attempt + 1))
                continue
            if r.status >= 400:
                raise RuntimeError(f"Gmail {r.status}: {data}")
            return data or {}
    raise RuntimeError("Gmail: слишком много повторов")


async def list_messages(query: str) -> list[str]:
    ids, page = [], None
    while True:
        params = {"q": query, "maxResults": 500, "includeSpamTrash": "true"}
        if page:
            params["pageToken"] = page
        data = await gmail("GET", "/messages", params=params)
        ids += [m["id"] for m in data.get("messages", [])]
        page = data.get("nextPageToken")
        if not page:
            return ids


async def trash_messages(ids: list[str], progress=None) -> int:
    """Массово в Корзину: пачками по 1000 писем за один запрос."""
    done = 0
    for i in range(0, len(ids), 1000):
        chunk = ids[i : i + 1000]
        try:
            await gmail(
                "POST",
                "/messages/batchModify",
                json={"ids": chunk, "addLabelIds": ["TRASH"], "removeLabelIds": ["INBOX", "SPAM"]},
            )
            done += len(chunk)
        except Exception as e:
            log.warning("batchModify: %s", e)
        if progress:
            await progress(done, len(ids))
    return done


async def list_threads(query: str, limit: int | None = None) -> list[str]:
    ids, page = [], None
    while True:
        params = {"q": query, "maxResults": 500, "includeSpamTrash": "true"}
        if page:
            params["pageToken"] = page
        data = await gmail("GET", "/threads", params=params)
        ids += [t["id"] for t in data.get("threads", [])]
        page = data.get("nextPageToken")
        if not page or (limit and len(ids) >= limit):
            return ids[:limit] if limit else ids


async def trash_many(ids: list[str]) -> int:
    sem = asyncio.Semaphore(8)
    done = 0

    async def one(tid):
        nonlocal done
        async with sem:
            try:
                await gmail("POST", f"/threads/{tid}/trash")
                done += 1
            except Exception as e:
                log.warning("trash %s: %s", tid, e)

    await asyncio.gather(*(one(t) for t in ids))
    return done


def _header(msg: dict, name: str) -> str:
    for h in msg.get("payload", {}).get("headers", []):
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def _clean_sender(raw: str) -> str:
    m = re.match(r'\s*"?([^"<]*?)"?\s*<([^>]+)>', raw)
    if m and m.group(1).strip():
        return m.group(1).strip()
    return (m.group(2) if m else raw).strip()


async def thread_meta(tid: str) -> dict:
    data = await gmail(
        "GET",
        f"/threads/{tid}",
        params=[("format", "metadata"), ("metadataHeaders", "Subject"), ("metadataHeaders", "From")],
    )
    msgs = data.get("messages", [])
    first, last = msgs[0], msgs[-1]
    return {
        "subject": _header(first, "Subject") or "(без темы)",
        "sender": _clean_sender(_header(last, "From")),
        "snippet": html.unescape(last.get("snippet", "")),
    }


def _decode(data: str) -> str:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")


def _body(payload: dict) -> str:
    """Текст письма: text/plain, иначе text/html без тегов."""
    plain, rich = [], []

    def walk(p):
        mime = p.get("mimeType", "")
        data = p.get("body", {}).get("data")
        if data and mime == "text/plain":
            plain.append(_decode(data))
        elif data and mime == "text/html":
            rich.append(_decode(data))
        for sub in p.get("parts", []) or []:
            walk(sub)

    walk(payload)
    if plain:
        text = "\n".join(plain)
    else:
        text = re.sub(r"(?is)<(script|style).*?</\1>", "", "\n".join(rich))
        text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", text)
        text = html.unescape(re.sub(r"<[^>]+>", "", text))
    text = re.sub(r"[ \t ͏‌﻿]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


async def thread_text(tid: str) -> str:
    data = await gmail("GET", f"/threads/{tid}", params={"format": "full"})
    last = data["messages"][-1]
    return _body(last.get("payload", {}))


# ---------------------------------------------------------------- Telegram

async def tg(method: str, **payload):
    async with http.post(f"https://api.telegram.org/bot{bot_token}/{method}", json=payload) as r:
        data = await r.json()
    if not data.get("ok"):
        log.warning("TG %s: %s", method, data)
    return data.get("result")


def gmail_link(tid: str) -> str:
    return f"https://mail.google.com/mail/u/0/#all/{tid}"


def digest_text(tids: list[str], start: int) -> str:
    lines = []
    for i, tid in enumerate(tids):
        row = db.execute(
            "SELECT subject, sender, snippet, status FROM gmail_items WHERE thread_id=?", (tid,)
        ).fetchone()
        subject, sender, snippet, status = row
        mark = {"kept": "⭐ ", "trashed": "🗑 "}.get(status, "")
        snip = snippet[:110] + ("…" if len(snippet) > 110 else "")
        lines.append(
            f"{mark}<b>{start + i}. {html.escape(subject[:90])}</b>\n"
            f"<i>{html.escape(sender[:40])}</i> — {html.escape(snip)}"
        )
    return "\n\n".join(lines)


def digest_keyboard(tids: list[str], start: int) -> dict:
    rows = []
    for i, tid in enumerate(tids):
        n = start + i
        status = db.execute("SELECT status FROM gmail_items WHERE thread_id=?", (tid,)).fetchone()[0]
        if status == "kept":
            rows.append([{"text": f"{n} ⭐ оставлено", "callback_data": f"f:{tid}"}])
        elif status == "trashed":
            rows.append([{"text": f"{n} 🗑 удалено · вернуть", "callback_data": f"u:{tid}"}])
        else:
            rows.append(
                [
                    {"text": f"{n} ⭐", "callback_data": f"k:{tid}"},
                    {"text": f"{n} 🗑", "callback_data": f"d:{tid}"},
                    {"text": f"{n} 📖", "callback_data": f"f:{tid}"},
                ]
            )
    if any(
        db.execute("SELECT status FROM gmail_items WHERE thread_id=?", (t,)).fetchone()[0] == "sent"
        for t in tids
    ):
        rows.append([{"text": "🗑 Удалить все неотмеченные", "callback_data": "rest"}])
    return {"inline_keyboard": rows}


async def send_digest(limit: int = DIGEST_LIMIT, header: str = "") -> int:
    """Подтянуть новые письма в очередь и показать карточку первого."""
    candidates = await list_threads(DIGEST_QUERY, limit=limit * 4 + 50)
    known = {r[0] for r in db.execute("SELECT thread_id FROM gmail_items")}
    fresh = [t for t in candidates if t not in known][:limit]
    now = int(time.time())
    for tid in fresh:
        meta = await thread_meta(tid)
        db.execute(
            "INSERT OR REPLACE INTO gmail_items VALUES (?,?,?,?,?,?)",
            (tid, "sent", meta["subject"], meta["sender"], meta["snippet"], now),
        )
    db.commit()
    if not queue():
        await tg("sendMessage", chat_id=owner_id, text=(header + "📭 Новых писем для разбора нет.").strip())
        return 0
    # у прошлой карточки убираем кнопки — работаем только с новой
    old = kv_get("card_msg")
    if old:
        await tg("editMessageReplyMarkup", chat_id=owner_id, message_id=int(old), reply_markup={"inline_keyboard": []})
    await render_card(header=header)
    return len(fresh)


# ---------------------------------------------------------------- карточка письма

def queue() -> list[str]:
    return [r[0] for r in db.execute(
        "SELECT thread_id FROM gmail_items WHERE status='sent' ORDER BY created, rowid"
    )]


def _item(tid: str):
    return db.execute("SELECT subject, sender, snippet FROM gmail_items WHERE thread_id=?", (tid,)).fetchone()


async def render_card(msg_id: int | None = None, header: str = "", full: bool = False, confirm_all: bool = False) -> None:
    q = queue()
    last = kv_get("last_action")
    undo = [{"text": "↩️ Отменить последнее", "callback_data": "c:z"}] if last else None

    if not q:
        kept, trashed, _ = (x or 0 for x in db.execute(
            "SELECT SUM(status='kept'), SUM(status='trashed'), 0 FROM gmail_items").fetchone())
        text = header + "✅ <b>Все письма разобраны!</b>\n\nНовые придут в сводке в " + f"{DIGEST_HOUR:02d}:00."
        kb = [[{"text": "📬 Проверить новые", "callback_data": "c:more"}]]
        if undo:
            kb.insert(0, undo)
    elif confirm_all:
        text = f"🗑 Удалить все оставшиеся письма ({len(q)})?\nОни попадут в Корзину Gmail, их можно будет вернуть 30 дней."
        kb = [[{"text": f"Да, удалить {len(q)}", "callback_data": "c:allyes"},
               {"text": "Нет, назад", "callback_data": "c:back"}]]
    else:
        tid = q[0]
        subject, sender, snippet = _item(tid)
        top = f"{header}📬 <b>Письмо на разбор</b> · осталось {len(q)}\n\n<b>{html.escape(subject)}</b>\n👤 {html.escape(sender)}\n\n"
        if full:
            try:
                body = await thread_text(tid)
            except Exception as e:
                body = f"(не удалось загрузить: {e})"
        else:
            body = snippet
        room = 3900 - len(top)
        text = top + html.escape(body[:room] + ("…" if len(body) > room else ""))
        kb = [
            [{"text": "⭐ Оставить", "callback_data": f"c:k:{tid}"},
             {"text": "🗑 Удалить", "callback_data": f"c:d:{tid}"}],
            [({"text": "⬆️ Свернуть", "callback_data": f"c:s:{tid}"} if full
              else {"text": "📖 Читать полностью", "callback_data": f"c:f:{tid}"}),
             {"text": "⏭ Позже", "callback_data": f"c:l:{tid}"}],
            [{"text": "🔗 Открыть в Gmail", "url": gmail_link(tid)}],
        ]
        if undo:
            kb.append(undo)
        if len(q) > 1:
            kb.append([{"text": f"🗑 Удалить все оставшиеся ({len(q)})", "callback_data": "c:all"}])

    markup = {"inline_keyboard": kb}
    if msg_id:
        await tg("editMessageText", chat_id=owner_id, message_id=msg_id, text=text,
                 parse_mode="HTML", disable_web_page_preview=True, reply_markup=markup)
    else:
        res = await tg("sendMessage", chat_id=owner_id, text=text,
                       parse_mode="HTML", disable_web_page_preview=True, reply_markup=markup)
        if res:
            kv_set("card_msg", res["message_id"])


async def on_card_callback(cb: dict, data: str, msg_id: int) -> None:
    parts = data.split(":", 2)
    act = parts[1]
    tid = parts[2] if len(parts) > 2 else ""

    if act == "k":
        await gmail("POST", f"/threads/{tid}/modify", json={"addLabelIds": ["STARRED"]})
        set_status(tid, "kept")
        kv_set("last_action", json.dumps({"t": tid, "a": "k"}))
        await answer(cb["id"], "⭐ Оставлено")
    elif act == "d":
        await gmail("POST", f"/threads/{tid}/trash")
        set_status(tid, "trashed")
        kv_set("last_action", json.dumps({"t": tid, "a": "d"}))
        await answer(cb["id"], "🗑 В корзине")
    elif act == "l":
        # в конец очереди
        db.execute("UPDATE gmail_items SET created=? WHERE thread_id=?", (int(time.time()) + 1, tid))
        db.commit()
        await answer(cb["id"], "⏭ Вернусь к нему позже")
    elif act == "f":
        await answer(cb["id"])
        await render_card(msg_id, full=True)
        return
    elif act in ("s", "back"):
        await answer(cb["id"])
    elif act == "z":
        last = kv_get("last_action")
        if last:
            info = json.loads(last)
            if info["a"] == "k":
                await gmail("POST", f"/threads/{info['t']}/modify", json={"removeLabelIds": ["STARRED"]})
            else:
                await gmail("POST", f"/threads/{info['t']}/untrash")
            first = db.execute("SELECT MIN(created) FROM gmail_items").fetchone()[0] or 0
            db.execute("UPDATE gmail_items SET status='sent', created=? WHERE thread_id=?", (first - 1, info["t"]))
            db.execute("DELETE FROM kv WHERE k='last_action'")
            db.commit()
        await answer(cb["id"], "↩️ Отменено")
    elif act == "all":
        await answer(cb["id"])
        await render_card(msg_id, confirm_all=True)
        return
    elif act == "allyes":
        q = queue()
        await answer(cb["id"], f"Удаляю {len(q)}…")
        await trash_many(q)
        for t in q:
            set_status(t, "trashed")
        db.execute("DELETE FROM kv WHERE k='last_action'")
        db.commit()
    elif act == "more":
        await answer(cb["id"], "Проверяю почту…")
        await tg("editMessageReplyMarkup", chat_id=owner_id, message_id=msg_id, reply_markup={"inline_keyboard": []})
        await send_digest()
        return
    else:
        await answer(cb["id"])
        return
    await render_card(msg_id)


# ---------------------------------------------------------------- чистка

async def find_junk() -> tuple[dict[str, int], list[str]]:
    """Письма (не цепочки) под удаление — по правилам, без повторов."""
    counts, all_ids = {}, []
    seen = set()
    for name, q in RULES:
        ids = [m for m in await list_messages(rule_query(q)) if m not in seen]
        seen.update(ids)
        counts[name] = len(ids)
        all_ids += ids
    return counts, all_ids


def counts_text(counts: dict[str, int]) -> str:
    return "\n".join(f"• {name}: {n}" for name, n in counts.items() if n)


async def cleanup_preview() -> None:
    counts, ids = await find_junk()
    if not ids:
        await tg("sendMessage", chat_id=owner_id, text="🧹 Нечего удалять — всё чисто.")
        return
    await tg(
        "sendMessage",
        chat_id=owner_id,
        text=(
            f"🧹 Чистка почты. Нашла к удалению {len(ids)} писем:\n\n"
            f"{counts_text(counts)}\n\n"
            "Не трогаю: ממריאות, отмеченные ⭐, письма от вузов и College Board, счета, чеки "
            "и билеты — они придут в сводку.\nУдалённое лежит в Корзине Gmail 30 дней."
        ),
        reply_markup={
            "inline_keyboard": [[{"text": f"🗑 Удалить {len(ids)}", "callback_data": "clean"}]]
        },
    )


async def run_cleanup(report: bool = False) -> tuple[int, int, dict[str, int]]:
    """Удалить мусор. report=True — показывать прогресс и итог в Telegram."""
    if _cleaning.locked():
        if report:
            await tg("sendMessage", chat_id=owner_id, text="🧹 Чистка уже идёт — пришлю итог, когда закончу.")
        return 0, 0, {}
    async with _cleaning:
        return await _run_cleanup(report)


async def _run_cleanup(report: bool) -> tuple[int, int, dict[str, int]]:
    status_msg = None
    if report:
        status_msg = await tg("sendMessage", chat_id=owner_id, text="🧹 Ищу письма для удаления…")
    counts, ids = await find_junk()

    async def progress(done, total):
        if status_msg:
            await tg(
                "editMessageText",
                chat_id=owner_id,
                message_id=status_msg["message_id"],
                text=f"🧹 Удаляю… {done} из {total}",
            )

    n = await trash_messages(ids, progress) if ids else 0
    failed = len(ids) - n
    if report:
        if not ids:
            text = "✅ Готово: удалять нечего, всё чисто."
        else:
            text = f"✅ Готово! Удалила {n} писем.\n\n{counts_text(counts)}"
            if failed:
                text += f"\n\n⚠️ Не удалось удалить {failed} — попробую снова при следующей чистке."
        await tg("editMessageText", chat_id=owner_id, message_id=status_msg["message_id"], text=text)
    return n, failed, counts


async def evening() -> None:
    """Ежедневный запуск: чистка (если уже разрешена) + сводка."""
    header = ""
    if kv_get("auto_cleanup") == "1":
        n, failed, counts = await run_cleanup()
        if n:
            header = f"🧹 Сегодня удалила {n} ненужных писем:\n{counts_text(counts)}\n\n"
        if failed:
            header += f"⚠️ Не удалось удалить {failed}.\n\n"
    await send_digest(header=header)


# ---------------------------------------------------------------- кнопки и команды

async def answer(cb_id: str, text: str = "") -> None:
    await tg("answerCallbackQuery", callback_query_id=cb_id, text=text)


async def refresh_digest_message(chat_id: int, msg_id: int) -> None:
    row = db.execute("SELECT thread_ids FROM digest_msgs WHERE tg_msg_id=?", (msg_id,)).fetchone()
    if not row:
        return
    info = json.loads(row[0])
    await tg(
        "editMessageText",
        chat_id=chat_id,
        message_id=msg_id,
        text=digest_text(info["ids"], info["start"]),
        parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=digest_keyboard(info["ids"], info["start"]),
    )


def set_status(tid: str, status: str) -> None:
    db.execute("UPDATE gmail_items SET status=? WHERE thread_id=?", (status, tid))
    db.commit()


async def on_callback(cb: dict) -> None:
    data = cb.get("data", "")
    msg = cb.get("message") or {}
    chat_id, msg_id = msg.get("chat", {}).get("id"), msg.get("message_id")

    if await on_chat_callback(cb, data, msg_id):
        return
    if not ENABLED:
        await answer(cb["id"], "Почта не подключена")
        return
    if data.startswith("c:"):
        await on_card_callback(cb, data, msg_id)
        return

    if data == "ar":
        on = kv_get("auto_read") != "1"
        kv_set("auto_read", "1" if on else "0")
        await answer(cb["id"], "Автопрочтение включено" if on else "Автопрочтение выключено")
        await show_settings(edit_msg_id=msg_id)
        if on:
            await mark_all_read(report=False)
        return
    if data == "dig":
        await answer(cb["id"])
        await send_digest()
        return
    if data == "cl":
        await answer(cb["id"])
        await do_cleanup()
        return

    if data == "clean":
        await answer(cb["id"], "Удаляю…")
        await tg("editMessageReplyMarkup", chat_id=chat_id, message_id=msg_id, reply_markup={"inline_keyboard": []})
        first = kv_get("auto_cleanup") != "1"
        await run_cleanup(report=True)
        kv_set("auto_cleanup", "1")
        if first:
            await tg(
                "sendMessage",
                chat_id=owner_id,
                text=(
                    f"Дальше буду чистить по этим правилам сама каждый вечер в {DIGEST_HOUR}:00 "
                    "и присылать сводку того, что под вопросом. Вот первая порция:"
                ),
            )
        await send_digest()
        return

    if data == "rest":
        row = db.execute("SELECT thread_ids FROM digest_msgs WHERE tg_msg_id=?", (msg_id,)).fetchone()
        ids = json.loads(row[0])["ids"] if row else []
        todo = [
            t for t in ids
            if db.execute("SELECT status FROM gmail_items WHERE thread_id=?", (t,)).fetchone()[0] == "sent"
        ]
        await answer(cb["id"], f"Удаляю {len(todo)}…")
        await trash_many(todo)
        for t in todo:
            set_status(t, "trashed")
        await refresh_digest_message(chat_id, msg_id)
        return

    kind, _, tid = data.partition(":")
    if not tid:
        await answer(cb["id"])
        return

    if kind == "k":
        await gmail("POST", f"/threads/{tid}/modify", json={"addLabelIds": ["STARRED"]})
        set_status(tid, "kept")
        await answer(cb["id"], "⭐ Оставлено")
        await refresh_digest_message(chat_id, msg_id)
    elif kind == "d":
        await gmail("POST", f"/threads/{tid}/trash")
        set_status(tid, "trashed")
        await answer(cb["id"], "🗑 В корзине")
        await refresh_digest_message(chat_id, msg_id)
    elif kind == "u":
        await gmail("POST", f"/threads/{tid}/untrash")
        set_status(tid, "sent")
        await answer(cb["id"], "↩️ Вернула во входящие")
        await refresh_digest_message(chat_id, msg_id)
    elif kind == "f":
        await answer(cb["id"])
        row = db.execute("SELECT subject, sender FROM gmail_items WHERE thread_id=?", (tid,)).fetchone()
        subject, sender = row if row else ("", "")
        try:
            body = await thread_text(tid)
        except Exception as e:
            body = f"(не удалось загрузить: {e})"
        head = f"<b>{html.escape(subject)}</b>\n<i>{html.escape(sender)}</i>\n\n"
        tail = f'\n\n<a href="{gmail_link(tid)}">Открыть в Gmail</a>'
        room = 4000 - len(head) - len(tail)
        text = html.escape(body[:room] + ("…" if len(body) > room else ""))
        await tg(
            "sendMessage",
            chat_id=owner_id,
            text=head + text + tail,
            parse_mode="HTML",
            disable_web_page_preview=True,
            reply_to_message_id=msg_id,
        )
    else:
        await answer(cb["id"])


# ---------------------------------------------------------------- меню и «прочитать всё»

BTN_DIGEST = "📬 Письма"
BTN_CLEAN = "🧹 Чистка"
BTN_READ = "📖 Прочитать"
BTN_AUTOREAD = "🔁 Автопрочтение"
BTN_STATS = "📊 Статистика"
BTN_HELP = "❓ Помощь"
BTN_CHATS = "🔕 Чаты"
BTN_SETTINGS = "⚙️ Настройки"

# старые подписи кнопок — чтобы нажатия с прежней клавиатуры тоже работали
OLD_LABELS = {
    "📬 Письма на разбор": BTN_DIGEST,
    "🧹 Почистить почту": BTN_CLEAN,
    "📖 Прочитать всё": BTN_READ,
}

MENU_KB = {
    "keyboard": [
        [{"text": BTN_DIGEST}, {"text": BTN_CLEAN}, {"text": BTN_READ}],
        [{"text": BTN_AUTOREAD}, {"text": BTN_STATS}, {"text": BTN_CHATS}],
        [{"text": BTN_SETTINGS}],
    ],
    "resize_keyboard": True,
    "is_persistent": True,
    "input_field_placeholder": "Выбери действие 👇",
}
MENU_VERSION = "3"

COMMANDS = [
    {"command": "menu", "description": "Показать меню"},
    {"command": "digest", "description": "Письма на разбор"},
    {"command": "cleanup", "description": "Почистить почту"},
    {"command": "readall", "description": "Отметить всё прочитанным"},
    {"command": "autoread", "description": "Вкл/выкл автопрочтение"},
    {"command": "stats", "description": "Статистика"},
    {"command": "chats", "description": "Отключённые чаты"},
    {"command": "settings", "description": "Настройки"},
    {"command": "help", "description": "Помощь"},
]

AUTO_READ_EVERY = 10 * 60  # секунд


async def mark_all_read(report: bool = True) -> int:
    ids = await list_messages("in:inbox is:unread")
    done = 0
    for i in range(0, len(ids), 1000):
        chunk = ids[i : i + 1000]
        try:
            await gmail("POST", "/messages/batchModify", json={"ids": chunk, "removeLabelIds": ["UNREAD"]})
            done += len(chunk)
        except Exception as e:
            log.warning("mark read: %s", e)
    if report:
        text = f"📖 Готово! Отметила прочитанными {done} писем." if ids else "📖 Непрочитанных писем нет."
        if len(ids) > done:
            text += f"\n⚠️ Не получилось с {len(ids) - done} — попробуй ещё раз позже."
        await tg("sendMessage", chat_id=owner_id, text=text, reply_markup=MENU_KB)
    return done


def stats_text() -> str:
    row = db.execute(
        "SELECT SUM(status='kept'), SUM(status='trashed'), SUM(status='sent') FROM gmail_items"
    ).fetchone()
    kept, trashed, waiting = (x or 0 for x in row)
    return f"⭐ оставлено: {kept}   🗑 удалено из сводки: {trashed}   ⏳ без ответа: {waiting}"


def settings_view() -> tuple[str, dict]:
    auto_read = kv_get("auto_read") == "1"
    auto_clean = kv_get("auto_cleanup") == "1"
    text = (
        "⚙️ <b>Настройки почты</b>\n\n"
        f"🕖 Сводка и чистка: каждый день в {DIGEST_HOUR:02d}:00\n"
        f"🧹 Автоматическая чистка: {'✅ включена' if auto_clean else '⏸ ждёт первого подтверждения'}\n"
        f"📖 Автопрочтение новых писем: {'✅ включено' if auto_read else '⏸ выключено'}\n"
        + ("   (каждые 10 минут всё во «Входящих» отмечается прочитанным)\n" if auto_read else "")
        + f"\n{stats_text()}"
    )
    kb = {
        "inline_keyboard": [
            [{"text": ("⏸ Выключить автопрочтение" if auto_read else "✅ Включить автопрочтение"), "callback_data": "ar"}],
            [{"text": "📬 Прислать письма сейчас", "callback_data": "dig"}, {"text": "🧹 Почистить", "callback_data": "cl"}],
        ]
    }
    return text, kb


async def show_settings(edit_msg_id: int | None = None) -> None:
    text, kb = settings_view()
    if edit_msg_id:
        await tg("editMessageText", chat_id=owner_id, message_id=edit_msg_id, text=text, parse_mode="HTML", reply_markup=kb)
    else:
        await tg("sendMessage", chat_id=owner_id, text=text, parse_mode="HTML", reply_markup=kb)


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


async def on_chat_callback(cb: dict, data: str, msg_id: int) -> bool:
    """Кнопки отключения чатов. True — если нажатие обработано."""
    if data == "pick":
        await answer(cb["id"])
        await show_pick(msg_id)
        return True
    if data == "chats":
        await answer(cb["id"])
        await show_chats(edit_msg_id=msg_id)
        return True
    kind, _, rest = data.partition(":")
    if kind not in ("mute", "unmute"):
        return False
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
    return True


async def show_menu() -> None:
    await tg(
        "sendMessage",
        chat_id=owner_id,
        text=(
            "👋 <b>Меню почты</b>\n\n"
            f"{BTN_DIGEST} — следующая порция писем с кнопками ⭐ / 🗑 / 📖\n"
            f"{BTN_CLEAN} — удалить промо, коды, рассылки и прочий мусор\n"
            f"{BTN_READ} — отметить все входящие прочитанными\n"
            f"{BTN_AUTOREAD} — включить / выключить автопрочтение\n"
            f"{BTN_STATS} — сколько оставлено и удалено\n"
            f"{BTN_CHATS} — чаты, из которых не присылать удалённые и изменённые\n"
            f"{BTN_SETTINGS} — всё вместе на одном экране\n\n"
            f"🕖 Каждый вечер в {DIGEST_HOUR:02d}:00 сводка приходит сама."
        ),
        parse_mode="HTML",
        reply_markup=MENU_KB,
    )


async def toggle_auto_read() -> None:
    on = kv_get("auto_read") != "1"
    kv_set("auto_read", "1" if on else "0")
    if on:
        n = await mark_all_read(report=False)
        text = (
            "🔁 Автопрочтение <b>включено</b> ✅\n"
            "Каждые 10 минут всё новое во «Входящих» будет отмечаться прочитанным."
            + (f"\nСразу отметила {n} писем." if n else "")
        )
    else:
        text = "🔁 Автопрочтение <b>выключено</b> ⏸\nНовые письма останутся непрочитанными."
    await tg("sendMessage", chat_id=owner_id, text=text, parse_mode="HTML", reply_markup=MENU_KB)


async def do_cleanup() -> None:
    if kv_get("auto_cleanup") == "1":
        await run_cleanup(report=True)
    else:
        await cleanup_preview()


async def on_message(m: dict) -> None:
    text = (m.get("text") or "").strip().split("@")[0]
    text = OLD_LABELS.get(text, text)
    if text in ("/chats", BTN_CHATS):
        await show_chats()
        return
    if text in ("/start", "/help", "/menu", BTN_HELP):
        await show_menu()
        return
    if not ENABLED:
        await tg("sendMessage", chat_id=owner_id, text="📭 Почта не подключена (нет переменных GMAIL_… на Railway).")
        return
    if text in ("/digest", BTN_DIGEST):
        await send_digest()
    elif text in ("/cleanup", BTN_CLEAN):
        await do_cleanup()
    elif text in ("/readall", BTN_READ):
        await mark_all_read()
    elif text in ("/autoread", BTN_AUTOREAD):
        await toggle_auto_read()
    elif text in ("/stats", BTN_STATS):
        await tg("sendMessage", chat_id=owner_id, text="📊 " + stats_text().replace("   ", "\n"), reply_markup=MENU_KB)
    elif text in ("/chats", BTN_CHATS):
        await show_chats()
    elif text in ("/settings", BTN_SETTINGS):
        await show_settings()
    elif text in ("/start", "/help", "/menu", BTN_HELP):
        await show_menu()


async def auto_read_loop() -> None:
    while True:
        await asyncio.sleep(AUTO_READ_EVERY)
        if kv_get("auto_read") == "1":
            try:
                n = await mark_all_read(report=False)
                if n:
                    log.info("Автопрочтение: %d писем", n)
            except GmailAuthError as e:
                await auth_problem(e)
            except Exception:
                log.exception("auto_read")


_bg: set[asyncio.Task] = set()
_cleaning = asyncio.Lock()


async def handle_update(upd: dict) -> None:
    try:
        if "callback_query" in upd:
            cb = upd["callback_query"]
            if cb["from"]["id"] == owner_id:
                await on_callback(cb)
        elif "message" in upd and upd["message"].get("from", {}).get("id") == owner_id:
            await on_message(upd["message"])
    except GmailAuthError as e:
        await auth_problem(e)
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
                # каждое нажатие — отдельной задачей, чтобы долгая чистка не блокировала кнопки
                _bg.add(t := asyncio.create_task(handle_update(upd)))
                t.add_done_callback(_bg.discard)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.warning("getUpdates: %s", e)
            await asyncio.sleep(5)


async def auth_problem(e: Exception) -> None:
    log.error("Gmail auth: %s", e)
    if kv_get("auth_warned") != time.strftime("%Y-%m-%d"):
        kv_set("auth_warned", time.strftime("%Y-%m-%d"))
        await tg(
            "sendMessage",
            chat_id=owner_id,
            text="⚠️ Потерян доступ к Gmail — нужно заново получить GMAIL_REFRESH_TOKEN (gen_gmail_token.py).",
        )


def next_run(now: dt.datetime) -> dt.datetime:
    run = now.replace(hour=DIGEST_HOUR, minute=0, second=0, microsecond=0)
    return run if run > now else run + dt.timedelta(days=1)


async def scheduler_loop() -> None:
    # самый первый запуск — предложить чистку
    if kv_get("welcomed") != "1":
        try:
            await cleanup_preview()
            kv_set("welcomed", "1")
        except GmailAuthError as e:
            await auth_problem(e)
        except Exception:
            log.exception("first run")
    while True:
        now = dt.datetime.now(TZ)
        wait = (next_run(now) - now).total_seconds()
        log.info("Следующая сводка почты через %.1f ч", wait / 3600)
        await asyncio.sleep(wait)
        try:
            await evening()
        except GmailAuthError as e:
            await auth_problem(e)
        except Exception:
            log.exception("evening")
        await asyncio.sleep(60)


def start(session: aiohttp.ClientSession, database: sqlite3.Connection, owner: int, token: str,
          remove_file=None) -> list[asyncio.Task]:
    global http, db, owner_id, bot_token, _remove_file
    http, db, owner_id, bot_token = session, database, owner, token
    if remove_file:
        _remove_file = remove_file
    init_db()
    tasks = [asyncio.create_task(updates_loop()), asyncio.create_task(setup_menu())]
    if ENABLED:
        log.info("Почта включена: сводка в %02d:00 (%s)", DIGEST_HOUR, TZ.key)
        tasks += [asyncio.create_task(scheduler_loop()), asyncio.create_task(auto_read_loop())]
    return tasks


async def setup_menu() -> None:
    """Команды в кнопке «Меню» Telegram; меню с кнопками — один раз после обновления."""
    try:
        await tg("setMyCommands", commands=COMMANDS)
        await tg("setChatMenuButton", chat_id=owner_id, menu_button={"type": "commands"})
        if kv_get("menu_shown") != MENU_VERSION:
            await show_menu()
            kv_set("menu_shown", MENU_VERSION)
        if ENABLED and kv_get("initial_read") != "1":
            kv_set("initial_read", "1")
            await mark_all_read()
    except Exception:
        log.exception("setup_menu")
