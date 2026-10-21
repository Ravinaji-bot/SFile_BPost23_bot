"""Auto clean - everything the bot sends in a PM or a group disappears after AUTO_CLEAN_TIME seconds
(default 360 = 6 minutes): command replies, search results / filters, errors, status messages.

 * Channels are never touched (log / bin / archive / update channels, the autopost / ccopy /
   autoforward targets ...): only private chats, groups and supergroups are cleaned.
 * The messages the USERS type in the bot's PM are cleaned after the same time (PM only, not groups), so the
   PM stays empty; CLEAN_USER_PM_MESSAGES=False turns this off.
 * Broadcasts are the one thing that stays - and they carry the auto delete time the BOT OWNER set
   with /bcdelete (default BROADCAST_DELETE_TIME, 24 h; /bcdelete off = never delete).
 * The delete times the bot already had (search results DELETE_TIME, delivered files FILE_DELETE_TIME,
   /start messages, post cards, welcome card ...) are NOT changed. A message that already has an own
   timer shorter than 6 minutes is simply gone before the clean-up runs; one with a LONGER timer
   (welcome card 10 min, DELETE_TIME / START_HOME_DELETE_TIME set above 360 in the env) is registered
   with `keep()` so the clean-up waits for that longer time instead of cutting it short.

How it works: `install()` wraps the send methods of the pyrogram Client (send_message, send_photo,
send_video, send_document, send_cached_media, copy_message, Message.copy ... - every `message.reply_*`
goes through them). Each message of a private chat / group gets a job (chat id, message id, due time),
saved in MongoDB (`autoclean_jobs`) so a restart or a RAM-guard restart loses nothing. One sweeper
checks the jobs every 10 seconds and deletes the due messages in batches of up to 100 per chat.
"""
import asyncio
import contextlib
import contextvars
import functools
import logging
import time

from pyrogram import Client, enums, types
from pyrogram.errors import FloodWait, RPCError

logger = logging.getLogger(__name__)

COLLECTION = "autoclean_jobs"
SWEEP_EVERY = 10
K_BC_DELETE = "BROADCAST_DELETE_TIME"     # bot setting: seconds, 0 = never delete broadcasts
_SEND_METHODS = (
    "send_message", "send_photo", "send_video", "send_document", "send_cached_media", "send_animation",
    "send_audio", "send_voice", "send_sticker", "send_video_note", "send_media_group",
    "copy_message", "copy_media_group", "forward_messages",
)
# bot settings that hold a channel / group the bot posts into on its own (never cleaned)
_TARGET_KEYS = ("AP_MOVIE_CH", "AP_SERIES_CH", "CC_DST", "CC_SRC", "AF_SRC", "AF_MOVIE", "AF_SERIES", "STATUS_CHANNEL")

_scope = contextvars.ContextVar("autoclean_scope", default=None)   # None = normal rule, 0 = skip, >0 = seconds
_buf = {}          # (chat, mid) -> due       new jobs, written to MongoDB by the sweeper
_ext = {}          # (chat, mid) -> due       "keep longer" requests for jobs that exist already
_protected = set()
_state = {"task": None, "bc": None, "installed": False, "clean": 360, "users": True}


# --------------------------------------------------------------------------- settings
def _clean_seconds():
    return int(_state["clean"] or 0)


def broadcast_seconds():
    """Auto delete time of broadcasts in seconds (0 = never). Set by the owner with /bcdelete."""
    return int(_state["bc"] if _state["bc"] is not None else 0)


async def set_broadcast_seconds(bot_id, seconds):
    from database.users_chats_db import db
    await db.update_bot_setting(bot_id, K_BC_DELETE, int(seconds))
    _state["bc"] = int(seconds)


def _static_protected():
    import info as I
    ids = set()
    for name in ("LOG_CHANNEL", "BIN_CHANNEL", "LOG_VR_CHANNEL", "PREMIUM_LOGS", "RESULT_ARCHIVE_CHANNEL",
                 "MOVIE_UPDATE_CHANNEL", "POSTCARD_CHANNEL", "REQST_CHANNEL"):
        v = getattr(I, name, None)
        if isinstance(v, int):
            ids.add(v)
    for name in ("CHANNELS", "AUTH_CHANNELS", "AUTH_REQ_CHANNELS"):
        for v in getattr(I, name, None) or []:
            if isinstance(v, int):
                ids.add(v)
    return ids


async def _refresh_protected(bot_id):
    from database.users_chats_db import db
    ids = _static_protected()
    for key in _TARGET_KEYS:
        try:
            v = await db.get_bot_setting(bot_id, key, None)
        except Exception:
            v = None
        if isinstance(v, int):
            ids.add(v)
    _protected.clear()
    _protected.update(ids)


# --------------------------------------------------------------------------- scheduling
@contextlib.contextmanager
def scope(seconds):
    """with scope(n): messages sent inside get an auto delete time of n seconds instead of the normal
    rule (n = 0: no auto delete at all)."""
    token = _scope.set(seconds)
    try:
        yield
    finally:
        _scope.reset(token)


def broadcast_scope():
    """with broadcast_scope(): ...  -> the messages sent inside use the owner's broadcast delete time."""
    return scope(broadcast_seconds())


def _messages(res):
    if isinstance(res, (list, tuple)):
        return [m for m in res if isinstance(m, types.Message)]
    return [res] if isinstance(res, types.Message) else []


def _key(m):
    chat = getattr(m, "chat", None)
    cid = getattr(chat, "id", None)
    mid = getattr(m, "id", None)
    return (cid, mid) if cid is not None and mid is not None else None


def _note(res):
    ctx = _scope.get()
    delay = _clean_seconds() if ctx is None else ctx
    if not delay or delay < 0:
        return
    now = time.time()
    for m in _messages(res):
        chat = getattr(m, "chat", None)
        if chat is None or chat.type not in (enums.ChatType.PRIVATE, enums.ChatType.GROUP, enums.ChatType.SUPERGROUP):
            continue                      # channels (and anything unknown) are never cleaned
        if chat.id in _protected:
            continue
        key = _key(m)
        if key:
            _buf[key] = max(_buf.get(key, 0), now + delay)


def note_user_message(m):
    """A message a USER typed in the bot's PM: deleted after the same time as the bot's own messages,
    so the PM stays completely empty (CLEAN_USER_PM_MESSAGES=False switches this off)."""
    delay = _clean_seconds()
    if not delay or not _state["users"]:
        return
    chat = getattr(m, "chat", None)
    if chat is None or chat.type != enums.ChatType.PRIVATE:
        return
    key = _key(m)
    if key:
        _buf[key] = max(_buf.get(key, 0), time.time() + delay)


def keep(msgs, delay):
    """Called by the code that already deletes a message after its OWN `delay` seconds. When that delay
    is longer than the clean-up time, the clean-up waits for it, so the old timer stays as it was."""
    try:
        clean = _clean_seconds()
        if not clean or not delay or delay <= clean:
            return
        due = time.time() + delay + 15
        for m in (msgs if isinstance(msgs, (list, tuple)) else [msgs]):
            key = _key(m) if m is not None else None
            if not key:
                continue
            if key in _buf:
                _buf[key] = max(_buf[key], due)
            else:
                _ext[key] = max(_ext.get(key, 0), due)
    except Exception:
        logger.debug("autoclean.keep failed", exc_info=True)


def _wrap(orig):
    @functools.wraps(orig)
    async def wrapper(*args, **kwargs):
        res = await orig(*args, **kwargs)
        try:
            _note(res)
        except Exception:
            logger.debug("autoclean: could not schedule a message", exc_info=True)
        return res
    return wrapper


# --------------------------------------------------------------------------- sweeper
def _col():
    from database.users_chats_db import db
    return db.db[COLLECTION]


async def _flush():
    from pymongo import UpdateOne
    if not _buf and not _ext:
        return
    new, ext = dict(_buf), dict(_ext)
    _buf.clear()
    _ext.clear()
    col = _col()
    try:
        ops = [UpdateOne({"_id": f"{c}:{m}"}, {"$max": {"due": due}, "$setOnInsert": {"chat": c, "mid": m}}, upsert=True)
               for (c, m), due in new.items()]
        for i in range(0, len(ops), 1000):
            await col.bulk_write(ops[i:i + 1000], ordered=False)
        ops = [UpdateOne({"_id": f"{c}:{m}"}, {"$max": {"due": due}}) for (c, m), due in ext.items()]
        for i in range(0, len(ops), 1000):
            await col.bulk_write(ops[i:i + 1000], ordered=False)
    except Exception:
        for k, v in new.items():                 # database hiccup: try again at the next round
            _buf[k] = max(_buf.get(k, 0), v)
        for k, v in ext.items():
            _ext[k] = max(_ext.get(k, 0), v)
        raise


async def _run_due(bot):
    col = _col()
    docs = await col.find({"due": {"$lte": time.time()}}).limit(500).to_list(500)
    if not docs:
        return
    by_chat = {}
    for d in docs:
        by_chat.setdefault(d["chat"], []).append(d)
    for chat, items in by_chat.items():
        for i in range(0, len(items), 100):
            part = items[i:i + 100]
            try:
                await bot.delete_messages(chat, [d["mid"] for d in part])
            except FloodWait as e:
                await asyncio.sleep(e.value + 1)
                return                           # jobs stay, the next round continues
            except RPCError as e:
                logger.debug("autoclean: chat %s not cleaned (%s) - jobs dropped", chat, getattr(e, "ID", e))
            await col.delete_many({"_id": {"$in": [d["_id"] for d in part]}})
            await asyncio.sleep(0.3)


async def _sweeper(bot):
    bid = bot.me.id
    last_refresh = 0
    while True:
        try:
            if time.time() - last_refresh > 60:
                await _refresh_protected(bid)
                last_refresh = time.time()
            await _flush()
            await _run_due(bot)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("autoclean sweeper error")
        await asyncio.sleep(SWEEP_EVERY)


# --------------------------------------------------------------------------- start-up
async def install(bot):
    """Call once after the client started (bot.py): wraps the send methods and starts the sweeper."""
    import info as I
    from database.users_chats_db import db
    _state["clean"] = int(getattr(I, "AUTO_CLEAN_TIME", 360) or 0)
    _state["users"] = bool(getattr(I, "CLEAN_USER_PM_MESSAGES", True))
    saved = await db.get_bot_setting(bot.me.id, K_BC_DELETE, None)
    _state["bc"] = int(saved) if saved is not None else int(getattr(I, "BROADCAST_DELETE_TIME", 86400) or 0)
    await _refresh_protected(bot.me.id)
    try:
        await _col().create_index("due")
    except Exception:
        logger.warning("autoclean: could not create the index", exc_info=True)
    if not _state["installed"]:
        for name in _SEND_METHODS:
            orig = getattr(Client, name, None)
            if orig is not None:
                setattr(Client, name, _wrap(orig))
        types.Message.copy = _wrap(types.Message.copy)
        _state["installed"] = True
    if not _clean_seconds():
        logger.info("autoclean: AUTO_CLEAN_TIME=0 - bot messages are not cleaned (broadcast timer still works)")
    if not (_state["task"] and not _state["task"].done()):
        _state["task"] = asyncio.create_task(_sweeper(bot))
    logger.info("autoclean on: bot messages in PMs / groups are deleted after %ss, broadcasts after %ss",
                _clean_seconds(), broadcast_seconds())
