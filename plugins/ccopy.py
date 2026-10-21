"""/ccopy - copy the video files of one private channel into another private channel,
collection by collection, WITHOUT the "Forwarded from" tag.

How it works
 1. /ccopy scan   reads every message of the SOURCE channel, keeps only videos, and groups
                  them into collections (one movie = one collection, one web-series SEASON =
                  one collection) - the same grouping the channel auto-post uses.
                  Old and new copies of the same movie / season land in the same collection.
 2. From every collection ONLY the allowed qualities are kept, each at most once - the six
                  480p HEVC, 480p, 720p HEVC, 720p, 1080p HEVC, 1080p
                  PLUS the 10bit of each (480p 10bit, 720p 10bit, 1080p 10bit) = 9 slots in all
                  (10bit is a /ccopy + /bulkpost slot; the live post / PM card stay at six).
                  540p counts as 480p.
                  (same rule as the posts: more audio languages first, then the bigger file;
                  for web series it is decided per episode).
 3. /ccopy start  copies those files to the DESTINATION channel (copy = no forward tag), ordered
                  episode by episode / lowest quality first. When a collection is finished a
                  sticker is sent below it, then the next collection starts.

Two modes (`/ccopy mode files|card`):
  files (default)  the video files themselves are copied, then the sticker. The LAST video of a
                   collection (just above the sticker) carries the "View Audio, Subs & Story" button.
  card             ONE post card per collection is posted instead: poster + title / audio / genres box
                   + download links of the allowed qualities (max 9) - the same card the bot shows in PM
                   + the "View Audio, Subs & Story" button as its last row
                   search - then the sticker below it. The picked files are saved in the bot database
                   (if they are not there yet) so the links work.

The queue and the progress are saved in MongoDB (`ccopy_queue`), so stop / restart / redeploy
continue exactly where they stopped.
"""
import asyncio
import html
import logging
import re
import time
from datetime import datetime
from types import SimpleNamespace

from pyrogram import Client, filters, enums
from pyrogram.errors import RPCError
from pyrogram.types import Message, InlineKeyboardMarkup

from database.ia_filterdb import Media, Media2, save_file, unpack_new_file_id
from utils import extract_button
from dreamxbotz.util.jobtools import extract_markup
from dreamxbotz.util.style import head, row, block, tip, cmd, on_off, bar, LINE
from database.users_chats_db import db
from dreamxbotz.util.postcard import send_search_postcard
from info import ADMINS, MULTIPLE_DB, is_small_video
from plugins.channel import (
    extract_media_info, _series_group_key, _pick_per_slot, _SLOT_INDEX, _slot_label,
)
from plugins.autopost import (
    _flood_safe, _err_id, _say, _FATAL, _FatalChannelError, K_STICKER, wait_for_window, _get_window,
)

logger = logging.getLogger(__name__)

SCAN_BATCH = 200            # message ids per get_messages call (Telegram limit)
EMPTY_STOP = 30             # this many empty batches in a row after the last message = end of channel
SCAN_PROGRESS_EVERY = 25000  # progress message every N message ids
PROGRESS_EVERY = 25         # progress message every N finished collections
DEFAULT_DELAY = 2.0
from plugins.file_caption import caption_for_copy
from plugins.collection_order import GROUP_SORT, ensure_grouped, regroup, get_aliases

SORT = GROUP_SORT   # gyear DESC, gkey, ord, _id: all seasons of a show / all movies of a franchise together

K_SRC, K_DST, K_STICKER_OWN = "CC_SRC", "CC_DST", "CC_STICKER"
K_DELAY, K_CAPTION, K_RUNNING, K_NOTIFY = "CC_DELAY", "CC_CAPTION", "CC_RUNNING", "CC_NOTIFY"
K_LEFT = "CC_LEFT"          # collections still to do when `start N` was used (restart keeps the limit)
STICKER_OFF = "off"
STICKER_WAIT = 120          # seconds the bot waits for the next sticker after "/ccopy sticker"
K_MODE = "CC_MODE"          # "files" (copy the videos) or "card" (post card with links)

# errors that mean "this channel can not be used" -> stop the whole job
_FATAL_CC = tuple(_FATAL) + ("CHAT_FORWARDS_RESTRICTED", "CHANNEL_INVALID", "CHAT_ID_INVALID")

_state = {"scan": None, "post": None, "stop": False}
_wait_sticker = {}          # admin id -> time until which the next sticker he sends becomes the new sticker


def _queue():
    return db.db.ccopy_queue


def _running(key):
    task = _state.get(key)
    return bool(task and not task.done())


async def _cfg(bot):
    bid = bot.me.id
    own = await db.get_bot_setting(bid, K_STICKER_OWN, None)
    if own == STICKER_OFF:
        sticker = None                      # you switched the sticker off with /ccopy sticker off
    else:
        sticker = own or await db.get_bot_setting(bid, K_STICKER, None)   # fall back to the /autopost sticker
    return {
        "src": await db.get_bot_setting(bid, K_SRC, None),
        "dst": await db.get_bot_setting(bid, K_DST, None),
        "sticker": sticker,
        "keep_caption": bool(await db.get_bot_setting(bid, K_CAPTION, True)),
        "mode": "card" if await db.get_bot_setting(bid, K_MODE, "files") == "card" else "files",
        "delay": max(1.0, float(await db.get_bot_setting(bid, K_DELAY, DEFAULT_DELAY))),
    }


# --------------------------------------------------------------------------- scan
def _video_of(m):
    """(file_name, file_size) if the message holds a video file, else None."""
    v = getattr(m, "video", None)
    if v:
        return (v.file_name or ""), (v.file_size or 0)
    d = getattr(m, "document", None)
    if d and str(d.mime_type or "").startswith("video/"):
        return (d.file_name or ""), (d.file_size or 0)
    return None


def _ep_start(ep) -> int:
    m = re.search(r"\d+", str(ep or ""))
    return int(m.group(0)) if m else 0     # season pack / movie -> 0 (comes first)


def _to_year(y) -> int:
    try:
        y = int(y)
    except (TypeError, ValueError):
        return 0
    return y if 1900 <= y <= datetime.now().year + 1 else 0


async def _scan(bot, chat_id, src, last_id):
    q = _queue()
    groups = {}
    seen_ids = videos = unreadable = errors = 0
    cur = 0
    empty_run = 0
    next_progress = SCAN_PROGRESS_EVERY
    try:
        while True:
            if _state["stop"]:
                return await _say(bot, chat_id, "⏹ Scan stopped. Nothing was saved, run /ccopy scan again.")
            if last_id and cur >= last_id:
                break
            end = cur + SCAN_BATCH
            if last_id:
                end = min(end, last_id)
            ids = list(range(cur + 1, end + 1))
            try:
                msgs = await _flood_safe(lambda: bot.get_messages(src, ids))
                errors = 0
            except Exception as e:
                eid = _err_id(e)
                if any(x in eid for x in _FATAL_CC):
                    return await _say(bot, chat_id, f"❌ Cannot read the source channel ({html.escape(eid)}). "
                                                    "Make the bot ADMIN there.")
                errors += 1
                logger.warning("ccopy scan: get_messages failed (%s)", eid)
                if errors >= 5:
                    return await _say(bot, chat_id, f"❌ Scan stopped, Telegram keeps failing: {html.escape(eid)}")
                await asyncio.sleep(3)
                continue            # retry the same batch
            if not isinstance(msgs, list):
                msgs = [msgs]
            real = False
            for m in msgs:
                if not m or getattr(m, "empty", False):
                    continue
                real = True
                vi = _video_of(m)
                if not vi:
                    continue
                videos += 1
                name, size = vi
                try:
                    mi = extract_media_info(name or "", m.caption or "")
                    base = mi["base_name"]
                    if not base:
                        raise ValueError("empty base_name")
                except Exception:
                    unreadable += 1
                    continue
                is_series = mi["tag"] == "#SERIES"
                gkey = _series_group_key(base, mi["season"]) if is_series else base
                g = groups.get(gkey)
                if g is None:
                    g = groups[gkey] = {"title": base, "tag": mi["tag"], "season": mi["season"],
                                        "year": _to_year(mi.get("year")), "files": []}
                g["files"].append({
                    "m": m.id, "quality": mi["quality"], "language": mi["language"],
                    "file_size": size, "season": mi["season"], "episode": mi["episode"],
                })
            cur = end
            seen_ids = cur
            if real:
                empty_run = 0
            else:
                empty_run += 1
                if not last_id and empty_run >= EMPTY_STOP:
                    break           # no message for a long stretch -> end of the channel
            if cur >= next_progress:
                next_progress += SCAN_PROGRESS_EVERY
                await _say(bot, chat_id, f"🔎 Scan running... up to message {cur}, {videos} videos, "
                                         f"{len(groups)} collections so far")

        # ---- pick the allowed qualities (9 slots incl. 10bit) of every collection and save the queue
        docs, no_slot = [], 0
        for key, g in groups.items():
            is_series = g["tag"] == "#SERIES"
            picked = _pick_per_slot(g["files"], (lambda f: (f.get("season"), f.get("episode"))) if is_series
                                    else (lambda f: None), tenbit=True)
            if not picked:
                no_slot += 1        # none of the allowed qualities in this collection -> nothing to copy
                continue
            items = sorted(
                ((_ep_start(f.get("episode")) if is_series else 0, _SLOT_INDEX[slot], f["m"], _slot_label(slot))
                 for (slot, _k), f in picked.items()))
            docs.append({
                "_id": key, "title": g["title"], "tag": g["tag"], "season": g["season"], "year": g["year"],
                "status": "pending", "sent": 0, "sticker_done": False,
                "n": len(items), "picks": [{"m": mid, "q": label} for _e, _s, mid, label in items],
            })
        await q.create_index([("status", 1), ("year", -1), ("_id", 1)])
        for i in range(0, len(docs), 500):
            await q.insert_many(docs[i:i + 500], ordered=False)

        total_files = sum(d["n"] for d in docs)
        series = sum(1 for d in docs if d["tag"] == "#SERIES")
        await _say(
            bot, chat_id,
            block([head("✅", "Scan finished")],
                  [row("📄", "Messages read", f"up to id {seen_ids}"), row("🎞", "Videos found", videos),
                   row("⚠️", "Unreadable names", unreadable)],
                  [row("📦", "Collections to copy", len(docs)), row("🎬", "Movies", len(docs) - series),
                   row("📺", "Web series seasons", series)],
                  [row("📁", "Files to copy", f"{total_files} (max 9 qualities each)"),
                   row("⏭", "Skipped collections", f"{no_slot} (no allowed quality)")],
                  [tip(f"Next: {cmd('/ccopy test')} then {cmd('/ccopy start 5')}", "(try 5 collections first)")]))
    except Exception as e:
        logger.exception("ccopy scan failed")
        await _say(bot, chat_id, f"❌ Scan failed: {html.escape(str(e))}")
    finally:
        _state["stop"] = False


# --------------------------------------------------------------------------- copy
_extract_markup = extract_markup      # shared helper (dreamxbotz/util/jobtools.py)


async def _copy_one(bot, cfg, mid, last=False):
    """True = copied, False = this message could not be copied (skipped).
    last=True: this is the LAST video of the collection (the sticker follows it) -> it gets the
    'View Audio, Subs & Story' button."""
    kw = {} if cfg["keep_caption"] else {"caption": ""}
    try:
        src_msg = await _flood_safe(lambda: bot.get_messages(cfg["src"], mid))
        std = await caption_for_copy(src_msg)     # every copied video gets the standard bot caption (/videocaption)
        if std:
            kw = {"caption": std, "parse_mode": enums.ParseMode.HTML}
        if last:
            markup = _extract_markup(src_msg)
            if markup:
                kw["reply_markup"] = markup
    except Exception:
        logger.debug("ccopy: standard caption not used for %s", mid, exc_info=True)
    try:
        await _flood_safe(lambda: bot.copy_message(
            chat_id=cfg["dst"], from_chat_id=cfg["src"], message_id=mid, **kw))
        return True
    except RPCError as e:
        if any(x in _err_id(e) for x in _FATAL_CC):
            raise _FatalChannelError(_err_id(e))
        logger.warning("ccopy: skipped message %s: %s", mid, _err_id(e))
        return False
    except Exception as e:
        logger.warning("ccopy: skipped message %s: %s", mid, e)
        return False


async def _db_file_id(media):
    """Makes sure the file is in the bot database (so the card's download link works) and
    returns the id the database uses for it."""
    fid, _ref = unpack_new_file_id(media.file_id)
    try:
        await save_file(media, inbox=False)   # old files saved only for the card - not 'new' files
    except Exception as e:
        logger.warning("ccopy: save_file failed for %s: %s", getattr(media, "file_name", "?"), e)
    uid = getattr(media, "file_unique_id", None)
    flt = {"$or": [{"file_id": fid}] + ([{"file_unique_id": uid}] if uid else [])}
    try:
        for col in ([Media, Media2] if MULTIPLE_DB else [Media]):
            doc = await col.find_one(flt)
            if doc:
                return doc.file_id      # the same video may already be indexed under another id
    except Exception as e:
        logger.warning("ccopy: db lookup failed: %s", e)
    return fid


async def _post_card(bot, cfg, picks):
    """Builds and sends ONE post card for a collection. True = posted."""
    ids = [p["m"] for p in picks]
    msgs = await _flood_safe(lambda: bot.get_messages(cfg["src"], ids))
    if not isinstance(msgs, list):
        msgs = [msgs]
    files = []
    for m in msgs:
        if not m or getattr(m, "empty", False):
            continue
        kind = "video" if getattr(m, "video", None) else ("document" if getattr(m, "document", None) else None)
        if not kind:
            continue
        media = getattr(m, kind)
        media.file_type = kind
        media.caption = m.caption or ""
        if is_small_video(media.file_size, kind, getattr(media, "mime_type", None), media.file_name):
            continue    # sample / small video: never on a post card
        db_id = await _db_file_id(media)
        files.append(SimpleNamespace(file_name=media.file_name or "", caption=m.caption or "",
                                     file_id=db_id, file_size=media.file_size or 0))
    if not files:
        return False
    sent = await send_search_postcard(bot, cfg["dst"], files, tenbit=True)   # never raises, None = not sent
    return sent is not None


async def _send_group(bot, q, qdoc, cfg, stats):
    """Sends one collection (files or post card), then the sticker. Returns 'done', 'stopped' or 'failed'.
    Progress (`sent`) is saved after every file / the card, so a stop / restart continues where it left off."""
    key = qdoc["_id"]
    picks = qdoc.get("picks", [])
    bid = bot.me.id
    no_stop = lambda: _state["stop"]        # noqa: E731  (daily time window: /autopost schedule 10:00 22:00)
    if cfg["mode"] == "card":
        if int(qdoc.get("sent", 0)) < len(picks):
            if _state["stop"] or not await wait_for_window(bid, no_stop):
                return "stopped"
            if not await _post_card(bot, cfg, picks):
                return "failed"
            stats["files"] += len(picks)
            await q.update_one({"_id": key}, {"$set": {"sent": len(picks)}})
            await asyncio.sleep(cfg["delay"])
    else:
        for idx in range(int(qdoc.get("sent", 0)), len(picks)):
            if _state["stop"] or not await wait_for_window(bid, no_stop):
                return "stopped"
            ok = await _copy_one(bot, cfg, picks[idx]["m"], last=(idx == len(picks) - 1))
            stats["files" if ok else "skipped_files"] += 1
            await q.update_one({"_id": key}, {"$set": {"sent": idx + 1}})
            await asyncio.sleep(cfg["delay"])

    if cfg["sticker"] and not qdoc.get("sticker_done"):
        if not await wait_for_window(bid, no_stop):
            return "stopped"
        try:
            await _flood_safe(lambda: bot.send_sticker(chat_id=cfg["dst"], sticker=cfg["sticker"]))
        except RPCError as e:
            if any(x in _err_id(e) for x in _FATAL_CC):
                raise _FatalChannelError(_err_id(e))
            logger.warning("ccopy: sticker failed: %s", _err_id(e))
        except Exception as e:
            logger.warning("ccopy: sticker failed: %s", e)
        await q.update_one({"_id": key}, {"$set": {"sticker_done": True}})
        await asyncio.sleep(cfg["delay"])
    return "done"


async def _post_worker(bot, chat_id, limit):
    q = _queue()
    bid = bot.me.id
    await ensure_grouped(q, bid)     # show / franchise order (S01 -> S02 -> S03, Spider Man 1 -> 2 -> 3)
    stats = {"groups": 0, "files": 0, "skipped_files": 0, "failed": 0}
    reason = "queue finished"
    cancelled = False
    fails_in_row = 0
    try:
        while True:
            if _state["stop"]:
                reason = "stopped by you"
                break
            if fails_in_row >= 3:
                reason = ("3 collections failed in a row - check that the bot is ADMIN in both channels "
                          "(/ccopy test) and then use /ccopy retry")
                break
            if limit and stats["groups"] >= limit:
                reason = f"limit of {limit} collections reached"
                break
            qdoc = await q.find_one({"status": "pending"}, sort=SORT)
            if not qdoc:
                break
            cfg = await _cfg(bot)       # delay / caption / sticker changes apply live
            if not cfg["src"] or not cfg["dst"]:
                reason = "source or destination channel is not set"
                break
            try:
                res = await _send_group(bot, q, qdoc, cfg, stats)
            except _FatalChannelError as e:
                reason = (f"cannot use the channel ({html.escape(str(e))}). Bot must be ADMIN in both channels "
                          "and the source must allow forwarding / saving content.")
                break
            except Exception:
                logger.exception("ccopy: failed on %s", qdoc.get("_id"))
                res = "failed"
            if res == "stopped":
                reason = "stopped by you"
                break
            await q.update_one({"_id": qdoc["_id"]}, {"$set": {"status": res, "updated_at": datetime.now()}})
            fails_in_row = 0 if res == "done" else fails_in_row + 1
            if res == "done":
                stats["groups"] += 1
                if limit:     # remember how many are left, so a restart continues with the same limit
                    left = max(limit - stats["groups"], 0)
                    await db.update_bot_setting(bid, K_LEFT, left)
                    if left == 0:
                        await db.update_bot_setting(bid, K_RUNNING, False)
                if stats["groups"] % PROGRESS_EVERY == 0:
                    await _say(bot, chat_id,
                               f"📤 {stats['groups']} collections done ({stats['files']} files) | "
                               f"skipped files: {stats['skipped_files']} | failed: {stats['failed']}")
            else:
                stats["failed"] += 1
                await asyncio.sleep(1)
    except asyncio.CancelledError:
        cancelled = True            # bot is shutting down: keep the "running" flag so it resumes
        raise
    except Exception as e:
        logger.exception("ccopy worker crashed")
        reason = f"error: {html.escape(str(e))}"
    finally:
        _state["stop"] = False
        if not cancelled:
            try:
                await db.update_bot_setting(bid, K_RUNNING, False)
            except Exception:
                pass
            await _say(bot, chat_id, block(
                [head("🏁", "Copy ended"), f"📝 Reason : {reason}"],
                [row("📦", "Collections done", stats["groups"]), row("📤", "Files copied", stats["files"]),
                 row("⏭", "Files skipped", stats["skipped_files"]), row("❌", "Failed collections", stats["failed"])]))


async def resume_ccopy(bot):
    """Called once from bot.py after start-up: continue a copy job that was running before a restart."""
    try:
        await asyncio.sleep(20)
        bid = bot.me.id
        if not await db.get_bot_setting(bid, K_RUNNING, False) or _running("post"):
            return
        chat_id = await db.get_bot_setting(bid, K_NOTIFY, None)
        left = int(await db.get_bot_setting(bid, K_LEFT, 0) or 0)   # 0 = no limit
        _state["stop"] = False
        _state["post"] = asyncio.create_task(_post_worker(bot, chat_id, left))
        await _say(bot, chat_id, head("♻️", "Bot restarted") + "\nCopy Channel continues where it stopped.")
        logger.info("ccopy resumed after restart")
    except Exception:
        logger.exception("ccopy: resume failed")


# --------------------------------------------------------------------------- commands
async def _status_text(bot):
    q = _queue()
    await ensure_grouped(q, bot.me.id)
    cfg = await _cfg(bot)
    counts = {st: await q.count_documents({"status": st}) for st in ("pending", "done", "failed")}
    agg = await q.aggregate([{"$group": {"_id": None, "n": {"$sum": "$n"}, "sent": {"$sum": "$sent"}}}]).to_list(1)
    state = "🔎 scanning" if _running("scan") else ("▶️ copying" if _running("post") else "⏸ idle")
    top = [row("⏳", "Pending", counts["pending"]), row("✅", "Done", counts["done"]), row("❌", "Failed", counts["failed"])]
    if agg:
        top.append(row("📁", "Files", f"{agg[0]['sent']} of {agg[0]['n']} copied"))
    nxt = await q.find_one({"status": "pending"}, sort=SORT, projection={"tag": 1, "sent": 1, "n": 1})
    nxt_lines = []
    if nxt:
        kind = "📺" if nxt.get("tag") == "#SERIES" else "🎬"
        nxt_lines.append(f"▶️ Next : {kind} <b>{html.escape(str(nxt['_id']))}</b> ({nxt.get('sent', 0)}/{nxt.get('n', '?')} files)")
    win = await _get_window(bot.me.id, fresh=True)
    sched = (f"{win[0] // 60:02d}:{win[0] % 60:02d} - {win[1] // 60:02d}:{win[1] % 60:02d} (India time)") if win else "OFF (any time)"
    setup = [row("📥", "Source channel", cfg["src"] or "NOT SET", code=bool(cfg["src"])),
             row("📤", "Destination", cfg["dst"] or "NOT SET", code=bool(cfg["dst"])),
             row("🧩", "Mode", "post card (poster + links)" if cfg["mode"] == "card" else "copy video files"),
             row("🎭", "Sticker", "set" if cfg["sticker"] else "OFF / NOT SET"),
             row("📝", "Caption", "original kept" if cfg["keep_caption"] else "removed") + "    " + row("⏱", "Delay", f"{cfg['delay']}s"),
             row("🕒", "Schedule", sched)]
    return block([head("📊", "Copy Channel", state)], top, nxt_lines, setup,
                 [tip(f"Sticker: {cmd('/ccopy sticker')}", f"Time window: {cmd('/autopost schedule 10:00 22:00')}")])


async def _set_channel(bot, message, key, label, args):
    if len(args) < 2:
        return await message.reply_text(f"Usage: /ccopy {args[0]} -100xxxxxxxxxx")
    try:
        ch_id = int(args[1])
    except ValueError:
        return await message.reply_text("Channel id must be a number like -1001234567890")
    try:
        chat = await bot.get_chat(ch_id)
    except Exception as e:
        return await message.reply_text(f"❌ Bot can not open this channel: {html.escape(str(e))}\n"
                                        "Add the bot as ADMIN first.")
    await db.update_bot_setting(bot.me.id, key, ch_id)
    await message.reply_text(f"✅ {label} channel saved: {html.escape(str(chat.title))} ({ch_id})")


# --------------------------------------------------------------------------- sticker setup
async def _save_sticker(bot, message, sticker):
    await db.update_bot_setting(bot.me.id, K_STICKER_OWN, sticker.file_id)
    _wait_sticker.pop(message.from_user.id, None)
    await message.reply_text("✅ New sticker saved. It is sent below every finished collection.\n"
                             "Change it any time with /ccopy sticker, see it with /ccopy sticker show.")


async def _cmd_sticker(bot, message, args):
    """/ccopy sticker            -> then send ANY sticker, the bot detects it and uses it from now on
       /ccopy sticker (reply)    -> the replied sticker becomes the new one
       /ccopy sticker show       -> shows the current sticker
       /ccopy sticker off        -> no sticker after the collections"""
    bid = bot.me.id
    arg = args[1].lower() if len(args) > 1 else ""
    rep = message.reply_to_message
    if arg == "off":
        await db.update_bot_setting(bid, K_STICKER_OWN, STICKER_OFF)
        _wait_sticker.pop(message.from_user.id, None)
        return await message.reply_text("✅ Sticker switched OFF. Set a new one any time with /ccopy sticker")
    if arg == "show":
        cfg = await _cfg(bot)
        if not cfg["sticker"]:
            return await message.reply_text("No sticker is set. Use /ccopy sticker to set one.")
        try:
            await message.reply_sticker(cfg["sticker"])
            return await message.reply_text("☝️ This is the current sticker. Change it with /ccopy sticker")
        except Exception as e:
            return await message.reply_text(f"❌ The saved sticker can not be sent ({html.escape(str(e))}). "
                                            "Set a new one with /ccopy sticker")
    if rep and rep.sticker:
        return await _save_sticker(bot, message, rep.sticker)
    # no reply: wait for the next sticker this admin sends
    _wait_sticker[message.from_user.id] = time.time() + STICKER_WAIT
    await message.reply_text(f"🎭 Now send me the sticker you like (within {STICKER_WAIT // 60} minutes) - "
                             "I will use it from now on.\n\n"
                             "Other options: reply to a sticker with /ccopy sticker, /ccopy sticker show, "
                             "/ccopy sticker off")


def _waiting_for_sticker(_, __, m):
    u = getattr(m, "from_user", None)
    return bool(u and _wait_sticker.get(u.id, 0) > time.time())


@Client.on_message(filters.sticker & filters.user(ADMINS) & filters.create(_waiting_for_sticker))
async def ccopy_sticker_listener(bot: Client, message: Message):
    await _save_sticker(bot, message, message.sticker)


@Client.on_message(filters.command("ccopy") & filters.user(ADMINS))
async def ccopy_cmd(bot: Client, message: Message):
    args = message.command[1:]
    sub = args[0].lower() if args else ""
    chat_id = message.chat.id
    q = _queue()
    bid = bot.me.id

    if sub == "from":
        await _set_channel(bot, message, K_SRC, "📥 Source", args)

    elif sub == "to":
        await _set_channel(bot, message, K_DST, "📤 Destination", args)

    elif sub == "sticker":
        await _cmd_sticker(bot, message, args)

    elif sub == "test":
        cfg = await _cfg(bot)
        out = []
        if not cfg["src"]:
            out.append("📥 Source: channel not set")
        else:
            try:
                await bot.get_chat(cfg["src"])
                out.append("📥 Source: OK (bot can open it)")
            except Exception as e:
                out.append(f"📥 Source: ❌ {html.escape(str(e))}")
        if not cfg["dst"]:
            out.append("📤 Destination: channel not set")
        else:
            try:
                await bot.send_message(cfg["dst"], "✅ /ccopy test")
                if cfg["sticker"]:
                    await bot.send_sticker(cfg["dst"], cfg["sticker"])
                out.append("📤 Destination: OK")
            except Exception as e:
                out.append(f"📤 Destination: ❌ {html.escape(str(e))}")
        await message.reply_text("\n".join(out))

    elif sub == "scan":
        if _running("scan") or _running("post"):
            return await message.reply_text("Scan or copying is already running.")
        cfg = await _cfg(bot)
        if not cfg["src"]:
            return await message.reply_text("Set the source first: /ccopy from -100...")
        if await q.count_documents({}) > 0:
            return await message.reply_text(
                "A queue already exists. /ccopy status shows it, or /ccopy clear confirm and then scan again.")
        last_id = int(args[1]) if len(args) > 1 and args[1].isdigit() else 0
        await message.reply_text(
            "🔎 Scan started. Reading all messages of the source channel"
            + (f" up to id {last_id}" if last_id else " (end is detected automatically)")
            + ", this can take a while...")
        _state["stop"] = False
        _state["scan"] = asyncio.create_task(_scan(bot, chat_id, cfg["src"], last_id))

    elif sub == "start":
        if _running("scan"):
            return await message.reply_text("Scan is still running, wait for it to finish.")
        if _running("post"):
            return await message.reply_text("Copying is already running. /ccopy stop to stop it.")
        cfg = await _cfg(bot)
        if not cfg["src"] or not cfg["dst"]:
            return await message.reply_text("Set both channels first:\n/ccopy from -100...\n/ccopy to -100...")
        if not cfg["sticker"]:
            await message.reply_text("⚠️ No sticker set - collections will follow each other without a sticker. "
                                     "(Reply to a sticker with /ccopy sticker)")
        if await q.count_documents({"status": "pending"}) == 0:
            return await message.reply_text("Nothing pending. Run /ccopy scan first (or /ccopy retry).")
        nums = [int(a) for a in args[1:] if a.isdigit()]
        limit = nums[0] if nums else 0
        await db.update_bot_setting(bid, K_RUNNING, True)
        await db.update_bot_setting(bid, K_NOTIFY, chat_id)
        await db.update_bot_setting(bid, K_LEFT, limit)
        _state["stop"] = False
        _state["post"] = asyncio.create_task(_post_worker(bot, chat_id, limit))
        await message.reply_text(
            f"▶️ Copy started: {limit or 'all'} collections: one show / franchise after the other (S01, S02, S03 ...), newest show first.\n"
            "/ccopy stop to stop, /ccopy status to check.")

    elif sub == "stop":
        if _running("scan"):
            _state["stop"] = True
            return await message.reply_text("⏹ Stopping the scan...")
        if not _running("post"):
            await db.update_bot_setting(bid, K_RUNNING, False)
            return await message.reply_text("Copying is not running.")
        _state["stop"] = True
        await message.reply_text("⏹ Stopping after the current file... (progress is saved)")

    elif sub == "status":
        await message.reply_text(await _status_text(bot))

    elif sub == "delay":
        try:
            sec = max(1.0, float(args[1]))
        except (IndexError, ValueError):
            return await message.reply_text("Usage: /ccopy delay 2   (seconds between two files, minimum 1)")
        await db.update_bot_setting(bid, K_DELAY, sec)
        await message.reply_text(f"✅ Delay set to {sec}s (applies from the next collection).")

    elif sub == "caption":
        if len(args) < 2 or args[1].lower() not in ("on", "off"):
            return await message.reply_text("Usage: /ccopy caption on   (keep original caption)\n"
                                            "or   /ccopy caption off   (copy without caption)")
        on = args[1].lower() == "on"
        await db.update_bot_setting(bid, K_CAPTION, on)
        await message.reply_text("✅ Original caption is kept." if on else "✅ Files are copied without caption.")

    elif sub == "mode":
        if len(args) < 2 or args[1].lower() not in ("files", "card"):
            return await message.reply_text(
                "Usage: /ccopy mode files   (copy the video files)\n"
                "or   /ccopy mode card   (post ONE card per collection: poster + quality links)")
        mode = args[1].lower()
        await db.update_bot_setting(bid, K_MODE, mode)
        await message.reply_text("✅ Mode: post card (poster + quality links), sticker below it."
                                 if mode == "card" else "✅ Mode: copy the video files, sticker below them.")

    elif sub == "regroup":
        if _running("post"):
            return await message.reply_text("Stop copying first, then send /ccopy regroup.")
        n = await regroup(q, await get_aliases(bid))
        await message.reply_text(f"✅ {n} collections grouped again (all seasons of a show and all movies of a franchise together). "
                                 "Use /franchise to join movie names that do not start the same.")

    elif sub == "retry":
        if _running("post"):
            return await message.reply_text("Stop copying first.")
        res = await q.update_many({"status": "failed"}, {"$set": {"status": "pending"}})
        await message.reply_text(f"🔁 {res.modified_count} collections moved back to pending.")

    elif sub == "clear":
        if _running("scan") or _running("post"):
            return await message.reply_text("Stop scan/copying first.")
        if len(args) < 2 or args[1].lower() != "confirm":
            return await message.reply_text("This deletes the whole queue. Send /ccopy clear confirm to proceed.")
        await q.drop()
        await db.update_bot_setting(bid, K_RUNNING, False)
        await message.reply_text("🗑 Queue deleted. Messages already copied are untouched.")

    else:
        await message.reply_text(
            "Copy old videos from one private channel to another (no forward tag)\n\n"
            "1) /ccopy from -100...   (source channel)\n"
            "2) /ccopy to -100...   (destination channel)\n"
            "3) /ccopy sticker   (then send the sticker you like)\n"
            "4) /ccopy test\n"
            "5) /ccopy scan   (or /ccopy scan [last message id])\n"
            "6) /ccopy start 5   then   /ccopy start\n\n"
            "/ccopy stop | status | mode files|card | delay [seconds] | caption on|off | retry | clear confirm\n\n"
            + await _status_text(bot))
