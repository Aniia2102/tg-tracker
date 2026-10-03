# tg-tracker — карта проекта для Claude

Владелец: Anna (Aniia2102). Общение — на русском. Код правит и деплоит Claude,
Anna только описывает, что нужно. В боте нет ИИ — токены тратятся только на разработку.

## Что делает
1. **Юзербот (Telethon, от аккаунта Анны)**: ловит удалённые и изменённые сообщения
   в личках и группах и шлёт их через бота-уведомителя. Свои сообщения не шлёт.
   Фото/голосовые/кружки/видео/файлы скачиваются заранее и пересылаются сами.
   Под каждым уведомлением — «🔕 Не следить за этим чатом».
2. **Почта (Gmail API)**: чистка по правилам + карточка «одно письмо и кнопки»
   (⭐ оставить / 🗑 удалить / 📖 полностью / ⏭ позже / ↩️ отменить / 🚫 всегда удалять
   от отправителя). Каждый день в 19:00 (Asia/Nicosia): чистка + сводка.

## Файлы
| Файл | Что внутри |
| --- | --- |
| `main.py` | Telethon-клиент, кэш сообщений (SQLite), уведомления, скачивание медиа, проверка `mail.is_muted` |
| `mail.py` | Всё, что идёт через Bot API: цикл `getUpdates`, меню-клавиатура, кнопки, отключённые чаты, Gmail (правила, карточка, автопрочтение, планировщик) |
| `gen_session_qr.py` | Получить `SESSION` входом по QR (запускает Anna на Mac) |
| `gen_gmail_token.py` | Получить `GMAIL_REFRESH_TOKEN` (берёт `client_secret*.json` из «Загрузок») |
| `gen_session.py` | Старый вход по коду (не нужен, оставлен на всякий) |
| `tests/` | Симуляции без сети: `bash tests/run.sh` — гонять перед каждым пушем |
| `PRIVACY.md` | Политика для экрана согласия Google |

## Хранилище (`/data`, Railway Volume)
`tracker.db` (SQLite), таблицы: `messages` (кэш сообщений + путь к медиа),
`gmail_items` (очередь карточек: status sent/kept/trashed, email отправителя),
`custom_rules` (свои правила удаления), `muted_chats`, `digest_msgs` (старый формат),
`kv` (настройки: auto_cleanup, auto_read, disabled_rules, card_msg, last_action, awaiting, tg_offset…).
Медиа — `/data/media`, чистится через 7 дней.

## Переменные Railway
`API_ID`, `API_HASH`, `SESSION`, `BOT_TOKEN`, `DB_PATH=/data/tracker.db`,
`GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`, `GMAIL_REFRESH_TOKEN`.
Необязательные: `DIGEST_HOUR`, `DIGEST_LIMIT`, `TZ_NAME`, `KEEP_FROM`, `RETENTION_DAYS`,
`MEDIA_RETENTION_DAYS`, `MEDIA_MAX_MB`, `MEDIA_IN_GROUPS`.
Секреты вписывает только Anna — никогда не просить их в чат.

## Деплой
- Railway: проект `tg-tracker` (id `c28edd3a-d46d-48d1-9511-8e048d5e2492`),
  сервис `tg-tracker` (id `f65bab0a-e960-48e8-9d2c-48cc75fb343e`),
  environment `production` (id `0e73ce19-ec0f-40e4-9069-49165a07c794`).
- Поток: `bash tests/run.sh` → commit → `git push origin main` → Railway собирает сам.
  Если через ~2 минуты нового деплоя нет — Railway-коннектор `connect-service-source`
  (repo `Aniia2102/tg-tracker`, branch `main`) запускает сборку.
- Проверка: `list-deployments` (limit 1) → `get-logs` по deploymentId, limit ≤ 10.
  Успешный старт: «Запущен как anéchka», «Почта включена». Не тянуть логи без limit — они огромные.

## Правила почты (`mail.py`)
- `RULES` — встроенные; Anna включает/выключает их в «📋 Правила» (`kv.disabled_rules`).
- Свои правила — `custom_rules` (kind `from` | `subject`).
- Защита (`KEEP` + `ASK`) подмешивается во все запросы удаления: ממריאות / cyber.org.il,
  ⭐, `from:edu`, College Board, `category:purchases`, Moovit/Rav-Kav, темы со счетами/билетами.
  Gmail иногда кладёт счета в «промо» — поэтому защита обязательна.

## Грабли (уже решены — не повторять)
- Gmail: удаление по одной цепочке упирается в лимит в минуту (403 Quota exceeded).
  Массово — `messages/batchModify` по 1000; `gmail()` сам ждёт и повторяет при лимите.
- Telegram-кнопки: `callback_data` ≤ 64 байт. Префиксы: `c:` карточка, `r:` правила,
  `mute:`/`unmute:`/`pick`/`chats` — чаты, `ar`/`dig`/`cl`/`clean` — настройки/чистка.
- Долгие действия (чистка) идут отдельными задачами, чтобы кнопки не зависали.
- Удаление в обычных группах/личках приходит без chat_id — ищем по msg_id с `is_channel=0`.
- Кружки качаются из другого DC несколько секунд — удаление ждёт загрузку до 60 с.
- Меню обновляется у Анны, только если поднять `MENU_VERSION`.
