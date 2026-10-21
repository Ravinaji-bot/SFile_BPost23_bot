"""/autoforward - live copy of every NEW video of one private channel into two other private
channels, WITHOUT the "Forwarded from" tag:

    source channel  --movies-->  movie channel
                    --series-->  series channel

How it works
 * Every new video / video document that arrives in the SOURCE channel is looked at once.
   The file name (or, if the file has no name, the first line of the caption) is classified with
   the SAME rule the bot uses for its posts (`extract_media_info`): a season marker (S01, S01E02,
   Season 1 ...) = web series, everything else = movie.
 * The message is put in a queue kept in MongoDB (`autoforward_queue`) and a single worker copies
   it to the matching channel, one by one, `delay` seconds apart (FloodWait is handled).
 * Because the queue is in MongoDB, a restart / RAM-guard restart loses nothing: the worker starts
   again by itself and continues with whatever is still pending (see `resume_autoforward`).
 * Videos smaller than MIN_FILE_SIZE_MB (samples) are skipped, like everywhere else in the bot.
   Text, photos, stickers, audio ... in the source channel are ignored.
 * Only videos that arrive AFTER `/autoforward on` are copied. For old files use /ccopy.

Setup (admins only, `/autoforward`):
    /autoforward from   -100xxxxxxxxxx     source channel
    /autoforward movie  -100yyyyyyyyyy     movie channel   (only movies)
    /autoforward series -100zzzzzzzzzz     series channel  (only web series)
    /autoforward on
The bot must be ADMIN in all three channels (in the source it must be able to see the posts).
"""
import asyncio
import html
import logging
from datetime import datetime

from pymongo.errors import DuplicateKeyError
from pyrogram import Client, filters, enums
from pyrogram.errors import RPCError

from database.users_chats_db import db
from info import ADMINS, is_small_video, _VIDEO_EXT_RE
from dreamxbotz.util.jobtools import send_sticker, extract_markup
from dreamxbotz.util.style import head, row, block, tip, cmd, on_off, bar, LINE
from plugins.autopost import _flood_safe, _err_id, _say, _FATAL
from plugins.channel import extract_media_info
from plugins.file_caption import caption_for_copy

logger = logging.getLogger(__name__)

K_SRC, K_MOVIE, K_SERIES = "AF_SRC", "AF_MOVIE", "AF_SERIES"
K_ON, K_DELAY, K_CAPTION, K_NOTIFY = "AF_ON", "AF_DELAY", "AF_CAPTION", "AF_NOTIFY"

DEFAULT_DELAY = 2.0
DONE_TTL = 3 * 24 * 3600      # finished queue records are removed by MongoDB after 3 days
IDLE_FLUSH = 60              # seconds without a new file: the last movie / season is finished -> sticker
K_LAST = "AF_LAST"            # {"movie": {"dst":.., "mid":.., "fid":..}} the last video copied into each channel
K_OPEN = "AF_OPEN"            # {"movie": group key, "series": group key} of the group being copied now
NOTIFY_EVERY = 600            # at most one error message per kind of error every 10 minutes
FATAL = tuple(_FATAL) + ("CHAT_FORWARDS_RESTRICTED", "CHAT_ID_INVALID")

_cache = {}                   # {"cfg": {...}}  settings, refreshed by every setting change
_state = {"task": None, "indexed": False}
_wake = asyncio.Event()       # the listener sets it when a new item is queued
_last_notice = {}


def _queue():
    return db.db.autoforward_queue


def _running():
    task = _state.get("task")
    return bool(task and not task.done())


# --------------------------------------------------------------------------- settings
async def _get_cfg(bot, fresh=False):
    cfg = _cache.get("cfg")
    if cfg is not None and not fresh:
        return cfg
    bid = bot.me.id
    cfg = {
        "src": await db.get_bot_setting(bid, K_SRC, None),
        "movie": await db.get_bot_setting(bid, K_MOVIE, None),
        "series": await db.get_bot_setting(bid, K_SERIES, None),
        "on": bool(await db.get_bot_setting(bid, K_ON, False)),
        "delay": max(1.0, float(await db.get_bot_setting(bid, K_DELAY, DEFAULT_DELAY))),
        "caption": bool(await db.get_bot_setting(bid, K_CAPTION, True)),
        "notify": await db.get_bot_setting(bid, K_NOTIFY, None),
    }
    _cache["cfg"] = cfg
    return cfg


async def _set(bot, key, value):
    await db.update_bot_setting(bot.me.id, key, value)
    _cache.pop("cfg", None)


# --------------------------------------------------------------------------- classify
def _video_info(m):
    """(file_name, file_size, mime_type, file_type) if the message holds a video file, else None."""
    v = getattr(m, "video", None)
    if v:
        return (v.file_name or ""), (v.file_size or 0), (v.mime_type or "video/mp4"), "video"
    d = getattr(m, "document", None)
    if d:
        name = d.file_name or ""
        mime = str(d.mime_type or "")
        if mime.lower().startswith("video/") or _VIDEO_EXT_RE.search(name.strip()):
            return name, (d.file_size or 0), mime, "document"
    return None


def _media(file_name, caption):
    """(kind, group key) or None. kind: 'movie' / 'series' (a season marker = series, the rule of the bot's
    posts); group key: one movie / one season of a show, used to put the sticker between two groups."""
    text = (file_name or "").strip()
    if not text:
        text = next((ln.strip() for ln in (caption or "").splitlines() if ln.strip()), "")
    if not text:
        return None
    try:
        info = extract_media_info(text, caption or "")
    except Exception:
        logger.exception("autoforward: could not classify %r", text[:80])
        return None
    kind = "series" if info["tag"] == "#SERIES" else "movie"
    return kind, f"{kind}|{(info.get('base_name') or '').strip().lower()}|{info.get('season') or 0}"


def classify(file_name, caption):
    """'movie' / 'series', or None when there is no name and no caption to decide with."""
    res = _media(file_name, caption)
    return res[0] if res else None


# --------------------------------------------------------------------------- listener
async def _is_source(_, bot, m):
    try:
        cfg = await _get_cfg(bot)
    except Exception:
        return False
    return bool(cfg["on"] and cfg["src"] and m.chat and m.chat.id == cfg["src"])


_source = filters.create(_is_source, "AutoForwardSource")


async def _bump(field):
    try:
        await _queue().update_one({"_id": "stats"}, {"$inc": {field: 1}}, upsert=True)
    except Exception:
        logger.warning("autoforward: stats update failed", exc_info=True)


async def _ensure_indexes():
    if _state["indexed"]:
        return
    try:
        await _queue().create_index("done_at", expireAfterSeconds=DONE_TTL)
        await _queue().create_index([("status", 1), ("mid", 1)])
        _state["indexed"] = True
    except Exception:
        logger.warning("autoforward: could not create indexes", exc_info=True)


@Client.on_message(_source & (filters.video | filters.document), group=-30)
async def autoforward_listener(bot, message):
    try:
        info = _video_info(message)
        if not info:
            return
        name, size, mime, ftype = info
        if is_small_video(size, ftype, mime, name):
            return await _bump("skipped_small")
        res = _media(name, message.caption or "")
        if res is None:
            return await _bump("skipped_noname")
        kind, gkey = res
        await _ensure_indexes()
        try:
            await _queue().insert_one({
                "_id": f"{message.chat.id}:{message.id}", "src": message.chat.id, "mid": message.id,
                "kind": kind, "gkey": gkey, "name": (name or message.caption or "")[:200],
                "status": "pending", "created_at": datetime.utcnow(),
            })
        except DuplicateKeyError:
            return
        _wake.set()
        _kick(bot)
    except Exception:
        logger.exception("autoforward: listener failed")


# --------------------------------------------------------------------------- worker
async def _notice(bot, cfg, key, text):
    now = asyncio.get_running_loop().time()
    if now - _last_notice.get(key, -NOTIFY_EVERY) < NOTIFY_EVERY:
        return
    _last_notice[key] = now
    await _say(bot, cfg.get("notify"), text)


async def _finish_group(bot, dst, kind, last_sent):
    """A movie / season is finished: its LAST copied video gets the 'View Audio, Subs & Story' button
    (the group end is only known now, so the button is added to the already copied message), then the sticker."""
    info = (last_sent or {}).get(kind)
    if info and info.get("dst") == dst and info.get("mid"):
        try:
            markup = info.get("markup")
            if markup is None and info.get("fid"):
                from utils import extract_button
                from pyrogram.types import InlineKeyboardMarkup
                btn = extract_button(info["fid"])
                markup = InlineKeyboardMarkup([[btn]]) if btn else None
            if markup is not None:
                await _flood_safe(lambda: bot.edit_message_reply_markup(chat_id=dst, message_id=info["mid"], reply_markup=markup))
        except Exception as e:
            logger.warning("autoforward: button not added to message %s: %s", info.get("mid"), e)
        last_sent.pop(kind, None)
        try:
            await db.update_bot_setting(bot.me.id, K_LAST, {k: {x: y for x, y in v.items() if x != "markup"} for k, v in last_sent.items()})
        except Exception:
            pass
    await send_sticker(bot, dst)


async def _close_groups(bot, cfg, open_groups, last_sent=None):
    """The last movie / season is finished: sticker after it, then forget the open groups."""
    for kind in list(open_groups):
        await _finish_group(bot, cfg.get(kind), kind, last_sent)
    open_groups.clear()
    await db.update_bot_setting(bot.me.id, K_OPEN, {})


async def _worker(bot):
    col = _queue()
    loop = asyncio.get_running_loop()
    last_copy = 0.0
    try:
        open_groups = dict(await db.get_bot_setting(bot.me.id, K_OPEN, {}) or {})
    except Exception:
        open_groups = {}
    try:
        last_sent = dict(await db.get_bot_setting(bot.me.id, K_LAST, {}) or {})
    except Exception:
        last_sent = {}
    while True:
        try:
            cfg = await _get_cfg(bot)
            doc = await col.find_one({"status": "pending"}, sort=[("mid", 1)]) if cfg["on"] else None
            if not doc:
                if open_groups and loop.time() - last_copy >= IDLE_FLUSH:
                    await _close_groups(bot, cfg, open_groups, last_sent)
                try:
                    await asyncio.wait_for(_wake.wait(), timeout=30)
                except asyncio.TimeoutError:
                    pass
                _wake.clear()
                continue

            kind = doc.get("kind")
            dst = cfg.get(kind)
            if not dst:
                await col.update_one({"_id": doc["_id"]}, {"$set": {"status": "nodst"}})
                await _notice(bot, cfg, "nodst-" + str(kind),
                              f"⚠️ /autoforward: no {kind} channel is set, {kind} files are waiting. "
                              f"Set it with /autoforward {kind} [channel id], then /autoforward retry.")
                continue

            gkey = doc.get("gkey")
            if gkey and open_groups.get(kind) and open_groups[kind] != gkey:
                await _finish_group(bot, dst, kind, last_sent)     # the previous movie / season is finished: button + sticker
            kw = {} if cfg["caption"] else {"caption": ""}
            try:
                src_msg = await _flood_safe(lambda: bot.get_messages(doc["src"], doc["mid"]))
                std = await caption_for_copy(src_msg)     # every copied video gets the standard bot caption (/videocaption)
                if std:
                    kw = {"caption": std, "parse_mode": enums.ParseMode.HTML}
            except Exception:
                logger.debug("autoforward: standard caption not used for %s", doc.get("mid"), exc_info=True)
            try:
                copied = await _flood_safe(lambda: bot.copy_message(
                    chat_id=dst, from_chat_id=doc["src"], message_id=doc["mid"], **kw))
            except RPCError as e:
                err = _err_id(e)
                logger.warning("autoforward: message %s not copied: %s", doc["mid"], err)
                await col.update_one({"_id": doc["_id"]}, {"$set": {"status": "failed", "error": err}})
                if any(x in err for x in FATAL):
                    await _notice(bot, cfg, "fatal-" + str(dst),
                                  f"❌ /autoforward cannot post to the {kind} channel ({html.escape(err)}). "
                                  f"Make the bot ADMIN there, then send /autoforward retry.")
                    await asyncio.sleep(5)
                continue
            except Exception as e:
                logger.warning("autoforward: message %s not copied: %s", doc["mid"], e)
                await col.update_one({"_id": doc["_id"]}, {"$set": {"status": "failed", "error": str(e)[:200]}})
                await asyncio.sleep(1)
                continue

            await col.update_one({"_id": doc["_id"]},
                                 {"$set": {"status": "done", "done_at": datetime.utcnow()}})
            await _bump(kind)
            try:
                mk = extract_markup(copied)
                new_mid = getattr(copied, "id", None)
                if gkey and new_mid and mk is not None:
                    media = getattr(copied, "video", None) or getattr(copied, "document", None)
                    from database.ia_filterdb import unpack_new_file_id
                    last_sent[kind] = {"dst": dst, "mid": new_mid, "fid": unpack_new_file_id(media.file_id)[0]}
                    await db.update_bot_setting(bot.me.id, K_LAST, last_sent)
            except Exception:
                logger.debug("autoforward: last video not remembered", exc_info=True)
            if gkey:
                open_groups[kind] = gkey
                last_copy = loop.time()
                await db.update_bot_setting(bot.me.id, K_OPEN, open_groups)
            await asyncio.sleep(cfg["delay"])
        except asyncio.CancelledError:
            raise            # shutting down: pending items stay in MongoDB and continue after the restart
        except Exception:
            logger.exception("autoforward worker error")
            await asyncio.sleep(5)


def _kick(bot):
    """Makes sure the worker task exists (it normally starts once at bot start-up)."""
    if not _running():
        _state["task"] = asyncio.create_task(_worker(bot))


async def resume_autoforward(bot):
    """Called once from bot.py after start-up: start the worker, it continues with the pending items."""
    try:
        await asyncio.sleep(10)
        await _ensure_indexes()
        _kick(bot)
        pending = await _queue().count_documents({"status": "pending"})
        if pending:
            logger.info("autoforward resumed after restart: %s pending", pending)
    except Exception:
        logger.exception("autoforward: resume failed")


# --------------------------------------------------------------------------- commands
HELP = (
    "<b>/autoforward</b> - live copy of NEW videos (no \"Forwarded from\" tag)\n"
    "Source channel → movies go to the movie channel, web series to the series channel.\n\n"
    "1. Add the bot as <b>admin</b> in all 3 channels\n"
    "2. <code>/autoforward from -100xxxxxxxxxx</code> (source)\n"
    "3. <code>/autoforward movie -100yyyyyyyyyy</code> (only movies)\n"
    "4. <code>/autoforward series -100zzzzzzzzzz</code> (only series)\n"
    "5. <code>/autoforward on</code>\n\n"
    "Other: <code>off</code> | <code>status</code> | <code>delay 3</code> | <code>caption on|off</code> | "
    "<code>retry</code> (failed items again) | <code>clear confirm</code> (empty the queue) | "
    "<code>movie off</code> / <code>series off</code> / <code>from off</code> (unset a channel)\n\n"
    "Only videos that arrive after <code>on</code> are copied (old files: /ccopy). "
    "Samples smaller than MIN_FILE_SIZE_MB are skipped."
)


async def _check_chat(bot, cid):
    """(title, note, error). error is set when the chat can not be used at all."""
    try:
        chat = await bot.get_chat(cid)
    except Exception as e:
        return None, "", f"cannot open this chat ({html.escape(_err_id(e))}). Add the bot as admin there first."
    note = ""
    try:
        me = await bot.get_chat_member(cid, bot.me.id)
        if me.status not in (enums.ChatMemberStatus.ADMINISTRATOR, enums.ChatMemberStatus.OWNER):
            note = "\n⚠️ The bot is not admin there yet - make it admin."
    except Exception:
        note = "\n⚠️ Could not check the bot's admin rights there."
    return (chat.title or str(cid)), note, None


async def _status_text(bot):
    cfg = await _get_cfg(bot, fresh=True)
    col = _queue()
    counts = {r["_id"]: r["n"] for r in await col.aggregate(
        [{"$match": {"status": {"$exists": True}}}, {"$group": {"_id": "$status", "n": {"$sum": 1}}}]
    ).to_list(20)}
    stats = await col.find_one({"_id": "stats"}) or {}
    state = on_off(cfg["on"]) + ("  (worker running)" if _running() else "  (worker not running)")
    setup = [row("📥", "Source", cfg["src"] or "not set", code=bool(cfg["src"])),
             row("🎬", "Movie channel", cfg["movie"] or "not set", code=bool(cfg["movie"])),
             row("📺", "Series channel", cfg["series"] or "not set", code=bool(cfg["series"])),
             row("⏱", "Delay", f"{cfg['delay']:g}s") + "    " + row("📝", "Caption", "keep" if cfg["caption"] else "remove")]
    queue = [row("⏳", "Pending", counts.get("pending", 0)), row("✅", "Done", counts.get("done", 0)),
             row("❌", "Failed", counts.get("failed", 0)), row("📭", "No channel", counts.get("nodst", 0))]
    total = [row("🎬", "Copied movies", stats.get("movie", 0)), row("📺", "Copied series", stats.get("series", 0)),
             row("⏭", "Skipped samples", stats.get("skipped_small", 0)), row("⏭", "Skipped (no name)", stats.get("skipped_noname", 0))]
    return block([head("📡", "Auto Forward", state)], setup, [f"🗂 Queue (last 3 days)"] + queue, total)


def _parse_id(txt):
    try:
        v = int(txt)
    except (TypeError, ValueError):
        return None
    return v if v < 0 else None


@Client.on_message(filters.command("autoforward") & filters.user(ADMINS))
async def autoforward_cmd(bot, message):
    args = message.command[1:]
    sub = args[0].lower() if args else ""
    cfg = await _get_cfg(bot, fresh=True)
    if message.chat:
        await _set(bot, K_NOTIFY, message.chat.id)   # progress / error messages go to the admin who used it last

    if sub in ("", "help"):
        return await message.reply_text(HELP + "\n\n" + await _status_text(bot))

    if sub in ("from", "movie", "series"):
        key = {"from": K_SRC, "movie": K_MOVIE, "series": K_SERIES}[sub]
        if len(args) < 2:
            return await message.reply_text(f"Usage: <code>/autoforward {sub} -100xxxxxxxxxx</code>")
        if args[1].lower() == "off":
            await _set(bot, key, None)
            return await message.reply_text(f"✅ {sub} channel removed.")
        cid = _parse_id(args[1])
        if cid is None:
            return await message.reply_text("❌ Send the channel id, it starts with -100 (example: -1001234567890).")
        others = {"from": (cfg["movie"], cfg["series"]),
                  "movie": (cfg["src"], cfg["series"]),
                  "series": (cfg["src"], cfg["movie"])}[sub]
        if cid in others:
            return await message.reply_text("❌ The source, movie and series channels must be three different channels.")
        title, note, err = await _check_chat(bot, cid)
        if err:
            return await message.reply_text("❌ " + err)
        await _set(bot, key, cid)
        _wake.set()
        return await message.reply_text(
            f"✅ {'Source' if sub == 'from' else sub.capitalize() + ' channel'}: "
            f"<b>{html.escape(title)}</b> (<code>{cid}</code>){note}")

    if sub in ("on", "off"):
        if sub == "on":
            missing = [n for n, k in (("source", "src"), ("movie", "movie"), ("series", "series")) if not cfg[k]]
            if missing:
                return await message.reply_text("❌ First set the " + ", ".join(missing) + " channel.\n\n" + HELP)
        await _set(bot, K_ON, sub == "on")
        _wake.set()
        _kick(bot)
        return await message.reply_text(
            "▶️ Auto forward is ON. New videos of the source channel are copied now."
            if sub == "on" else "⏹ Auto forward is OFF. Items already in the queue wait until you turn it on again.")

    if sub == "delay":
        try:
            sec = float(args[1])
            if not 1 <= sec <= 120:
                raise ValueError
        except (IndexError, ValueError):
            return await message.reply_text("Usage: <code>/autoforward delay 3</code> (1 to 120 seconds)")
        await _set(bot, K_DELAY, sec)
        return await message.reply_text(f"✅ Delay between two copies: {sec:g}s")

    if sub == "caption":
        if len(args) < 2 or args[1].lower() not in ("on", "off"):
            return await message.reply_text("Usage: <code>/autoforward caption on</code> (keep) or <code>off</code> (remove)")
        await _set(bot, K_CAPTION, args[1].lower() == "on")
        return await message.reply_text("✅ Captions are " + ("kept." if args[1].lower() == "on" else "removed."))

    if sub == "status":
        return await message.reply_text(await _status_text(bot))

    if sub == "retry":
        res = await _queue().update_many({"status": {"$in": ["failed", "nodst"]}},
                                         {"$set": {"status": "pending"}, "$unset": {"error": ""}})
        _wake.set()
        _kick(bot)
        return await message.reply_text(f"🔁 {res.modified_count} items moved back to the queue.")

    if sub == "clear":
        if len(args) < 2 or args[1].lower() != "confirm":
            return await message.reply_text("This empties the queue (nothing already copied is touched). "
                                            "Send <code>/autoforward clear confirm</code> to proceed.")
        res = await _queue().delete_many({"status": {"$exists": True}})
        return await message.reply_text(f"🗑 {res.deleted_count} queue items deleted.")

    return await message.reply_text(HELP)
