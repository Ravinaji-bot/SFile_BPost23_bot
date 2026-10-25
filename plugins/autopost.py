"""
Auto-post the files that are ALREADY saved in the database into private channels.

    * every movie / every web series (one season) becomes ONE "collection":
      all of its files (every quality) are sent one after another,
    * when a collection is finished a STICKER is sent, then the next collection starts,
      (the LAST file of a collection carries the "View Audio, Subs & Story" button, just above the sticker)
    * movies go to the MOVIE channel, web series go to the SERIES channel.

Each file is sent with the same caption the bot gives to users (title + language boxes +
size), and (optionally) with the poster cover: watermark + 1080p HEVC / S01E01-04 labels.

Commands (admin only):
    /autopost                    - help + current settings
    /autopost movie <channel_id> - private channel for MOVIES      (bot must be admin there)
    /autopost series <channel_id>- private channel for WEB SERIES  (bot must be admin there)
    /autopost sticker            - reply to a sticker with this command to save it
    /autopost test               - send a test message + the sticker to both channels
    /autopost scan [show]        - step 1: read ALL saved files and build the collections.
                                   "show" = one collection per series (all seasons together)
                                   default = one collection per season
    /autopost start [count]      - step 2: start posting (count = how many collections, none = all)
    /autopost stop               - stop (progress is saved, /autopost start continues)
    /autopost status             - numbers and what is next
    /autopost delay <seconds>    - pause between two files (default 2, minimum 1)
    /autopost cover on|off       - poster cover on the videos (default ON)
    /autopost schedule 10:00 22:00 - post only inside this daily time window (India time);
                                   /autopost schedule off = post any time
    /autopost retry              - put failed collections back in the queue
    /autopost new <channel_id> [confirm] - send to ANY channel every file it has not got yet (see plugins/autocycle.py)
    /autopost clear confirm      - delete the queue (messages already sent are NOT touched)

Notes:
    * The queue lives in the `autopost_queue` collection, so it survives restarts. If the bot
      restarts while posting, it continues by itself (the running flag is saved).
    * Optional environment variables (the commands above override them):
      AUTOPOST_MOVIE_CHANNEL, AUTOPOST_SERIES_CHANNEL, AUTOPOST_STICKER, AUTOPOST_DELAY,
      AUTOPOST_CAPTION (caption template with {file_name} {file_size} {file_caption}).
    * Telegram limits bots to roughly 20 messages a minute per channel. FloodWait is handled
      automatically, but 250 000 files still take several days - that is normal.
"""
import asyncio
import html
import logging
import os
import re
from datetime import datetime
from io import BytesIO

import pytz

from pymongo import UpdateOne
from pyrogram import Client, filters
from pyrogram.errors import FloodWait, RPCError
from pyrogram.types import Message, InlineKeyboardMarkup

from database.ia_filterdb import Media, Media2
from database.users_chats_db import db
from info import ADMINS, MULTIPLE_DB, TMDB_POSTER, CUSTOM_FILE_CAPTION, NOT_SMALL_VIDEO, ALLOW_PORTRAIT_POSTER, norm_res
from plugins.file_caption import peek_file_caption, get_file_caption, standard_caption, load_switches
from plugins.pretty_name import display_name, load_pretty_settings, clean_caption
from plugins.channel import extract_media_info, _series_group_key
from plugins.Dreamxfutures.Imdbposter import fetch_image
from utils import find_poster, clean_filename, get_size, get_languages_html, add_episode_label, get_episode_label, extract_button

logger = logging.getLogger(__name__)

YEAR_TAIL = re.compile(r"(?<!\d)((?:19|20)\d{2})\s*$")
RES_RE = re.compile(r"(?<![a-z0-9])(2160|1440|1080|720|576|540|480|360|240)p?(?![a-z0-9])", re.IGNORECASE)
BATCH = 1000
PROGRESS_EVERY = 25            # progress message every N finished collections
SCAN_PROGRESS_EVERY = 25000    # ... and every N files while scanning
DEFAULT_DELAY = 2.0
from dreamxbotz.util.jobtools import send_notice
from plugins.collection_order import GROUP_SORT, ensure_grouped, regroup, get_aliases
from dreamxbotz.util.style import head, row, block, tip, cmd, on_off, LINE

SORT = GROUP_SORT   # gyear DESC, gkey, ord, _id: all seasons of a show / all movies of a franchise together

K_MOVIE, K_SERIES, K_STICKER = "AP_MOVIE_CH", "AP_SERIES_CH", "AP_STICKER"
K_COVER, K_DELAY, K_RUNNING, K_NOTIFY = "AP_COVER", "AP_DELAY", "AP_RUNNING", "AP_NOTIFY"
K_LEFT = "AP_LEFT"          # collections still to do when `start N` was used (restart keeps the limit)
K_WINDOW = "AP_WINDOW"        # daily posting window "HH:MM-HH:MM" (India time), shared with /postrange
TZ = pytz.timezone("Asia/Kolkata")

# Telegram errors that mean "this channel can not be used" -> stop the whole job
_FATAL = ("CHAT_WRITE_FORBIDDEN", "CHAT_ADMIN_REQUIRED", "CHANNEL_INVALID", "CHANNEL_PRIVATE",
          "PEER_ID_INVALID", "USER_BANNED_IN_CHANNEL", "CHAT_SEND_MEDIA_FORBIDDEN",
          "CHAT_SEND_VIDEOS_FORBIDDEN", "CHAT_SEND_DOCS_FORBIDDEN", "CHAT_SEND_STICKERS_FORBIDDEN")

_state = {"scan": None, "post": None, "stop": False, "waiting": False}


class _FatalChannelError(Exception):
    pass


def _queue():
    return db.db.autopost_queue


def _running(key):
    task = _state.get(key)
    return bool(task and not task.done())


async def _say(bot, chat_id, text):
    """Job message: goes to the status channel (/statuschannel) when one is set, else to `chat_id`."""
    await send_notice(bot, chat_id, text)


def _err_id(e) -> str:
    return str(getattr(e, "ID", None) or getattr(e, "MESSAGE", None) or type(e).__name__).upper()


def _env_int(name):
    try:
        return int(os.environ.get(name, "").strip())
    except ValueError:
        return None


# --------------------------------------------------------------------------- settings
async def _cfg(bot):
    bid = bot.me.id
    movie = await db.get_bot_setting(bid, K_MOVIE, None) or _env_int("AUTOPOST_MOVIE_CHANNEL")
    series = await db.get_bot_setting(bid, K_SERIES, None) or _env_int("AUTOPOST_SERIES_CHANNEL")
    sticker = await db.get_bot_setting(bid, K_STICKER, None) or os.environ.get("AUTOPOST_STICKER") or None
    cover = bool(await db.get_bot_setting(bid, K_COVER, True))
    try:
        env_delay = float(os.environ.get("AUTOPOST_DELAY", DEFAULT_DELAY))
    except ValueError:
        env_delay = DEFAULT_DELAY
    delay = max(1.0, float(await db.get_bot_setting(bid, K_DELAY, env_delay)))
    return {"movie_ch": movie, "series_ch": series, "sticker": sticker, "cover": cover, "delay": delay}


# --------------------------------------------------------------------------- schedule
def _now_ist():
    return datetime.now(TZ)


def _parse_hm(text):
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", text or "")
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    return h * 60 + mi if h < 24 and mi < 60 else None


def _parse_window(value):
    """'10:00-22:00' -> (600, 1320); None / invalid -> None (= always allowed)."""
    if not value or "-" not in str(value):
        return None
    a, b = str(value).split("-", 1)
    a, b = _parse_hm(a), _parse_hm(b)
    return (a, b) if a is not None and b is not None else None


def _in_window(win, now):
    if not win or win[0] == win[1]:
        return True
    minute = now.hour * 60 + now.minute
    a, b = win
    return a <= minute < b if a < b else (minute >= a or minute < b)


def _secs_until_open(win, now):
    minute = now.hour * 60 + now.minute
    return max(1, ((win[0] - minute) % 1440) * 60 - now.second)


_win_cache = {"t": 0.0, "v": None}


async def _get_window(bid, fresh=False):
    loop_time = asyncio.get_event_loop().time()
    if fresh or loop_time - _win_cache["t"] > 20:
        _win_cache["v"] = _parse_window(await db.get_bot_setting(bid, K_WINDOW, None))
        _win_cache["t"] = loop_time
    return _win_cache["v"]


async def wait_for_window(bid, stop_fn):
    """Blocks while we are OUTSIDE the daily posting window. Returns False if the job was
    stopped meanwhile (stop_fn() is True), True when it is fine to post."""
    while True:
        win = await _get_window(bid)
        now = _now_ist()
        if _in_window(win, now):
            _state["waiting"] = False
            return not stop_fn()
        _state["waiting"] = True
        if stop_fn():
            _state["waiting"] = False
            return False
        await asyncio.sleep(min(60, _secs_until_open(win, now)))


# --------------------------------------------------------------------------- scan
async def _scan(bot, chat_id, per_show):
    q = _queue()
    scan_start = datetime.utcnow()      # files added after this moment are NOT in the scan -> they stay in the /autocycle inbox
    seen = errors = skipped_audio = 0
    ops = []
    max_year = datetime.now().year + 1
    try:
        await q.create_index([("status", 1), ("year", -1), ("_id", 1)])
        sources = [Media.collection]
        if MULTIPLE_DB:
            sources.append(Media2.collection)

        for col in sources:
            cursor = col.find(NOT_SMALL_VIDEO, {"file_name": 1, "caption": 1, "file_size": 1, "file_type": 1,
                                   "mime_type": 1, "cover": 1})
            async for doc in cursor:
                seen += 1
                if doc.get("file_type") == "audio":
                    skipped_audio += 1
                    continue
                try:
                    info = extract_media_info(doc.get("file_name") or "", doc.get("caption") or "")
                    base = info["base_name"]
                    if not base:
                        raise ValueError("empty base_name")
                except Exception:
                    errors += 1
                    continue

                is_series = info["tag"] == "#SERIES"
                if is_series and not per_show:
                    group_key = _series_group_key(base, info["season"])
                else:
                    group_key = base

                m = YEAR_TAIL.search(base)
                year = int(m.group(1)) if m else 0
                if year > max_year:
                    year = 0

                file_data = {
                    "id": doc["_id"],
                    "n": doc.get("file_name") or "",
                    "s": doc.get("file_size") or 0,
                    "t": doc.get("file_type"),
                    "m": doc.get("mime_type"),
                    "c": (doc.get("caption") or "")[:500],
                    "cv": doc.get("cover"),
                }
                ops.append(UpdateOne(
                    {"_id": group_key},
                    {
                        "$setOnInsert": {
                            "title": base, "year": year, "tag": info["tag"],
                            "season": info["season"], "status": "pending",
                            "sent": 0, "sticker_done": False,
                        },
                        "$push": {"files": file_data},
                        "$inc": {"n": 1},
                    },
                    upsert=True,
                ))
                if len(ops) >= BATCH:
                    await q.bulk_write(ops, ordered=True)
                    ops = []
                if seen % SCAN_PROGRESS_EVERY == 0:
                    await _say(bot, chat_id, f"🔎 Scan running... {seen} files read")

        if ops:
            await q.bulk_write(ops, ordered=True)

        total = await q.count_documents({})
        series = await q.count_documents({"tag": "#SERIES"})
        await _say(
            bot, chat_id,
            block([head("✅", "Scan finished")],
                  [row("📄", "Files read", seen), row("🎵", "Skipped (audio)", skipped_audio), row("⚠️", "Unreadable", errors)],
                  [row("📦", "Collections", total), row("🎬", "Movies", total - series), row("📺", "Web series", series)],
                  [tip(f"Next: {cmd('/autopost test')} then {cmd('/autopost start 5')}", "(try 5 collections first)")])
        )
        cleared = await db.inbox_clear_before(scan_start)     # the scan has them already - /autocycle must not send them again
        if cleared:
            logger.info("autopost scan: %s inbox entries cleared (already in the scan)", cleared)
    except Exception as e:
        logger.exception("autopost scan failed")
        await _say(bot, chat_id, f"❌ Scan failed: {html.escape(str(e))}")


# --------------------------------------------------------------------------- helpers
def _sort_key(f):
    name = f.get("n") or ""
    # same episode detection as the cover label: "S01E05-08" -> 5, season pack -> 0
    m_ep = re.search(r"E(\d+)", get_episode_label(name) or "")
    ep_start = int(m_ep.group(1)) if m_ep else 0
    m = RES_RE.search(name)
    res = int(norm_res(m.group(1) + "p")[:-1]) if m else (2160 if re.search(r"(?<![a-z0-9])(4k|uhd)(?![a-z0-9])", name, re.I) else 0)
    return (ep_start, res, f.get("s") or 0, name)


def _prepare_files(files):
    """Remove duplicates (same file_id) and order: episodes first, then lowest quality first."""
    unique = {}
    for f in files:
        if f.get("id") and f["id"] not in unique:
            unique[f["id"]] = f
    return sorted(unique.values(), key=_sort_key)


def _build_caption(f):
    name = f.get("n") or ""
    # same caption as every other video: name + VLC box (+ language box only when /langbox is ON)
    return standard_caption(name, f.get("c"), f.get("s") or 0,
                            template=os.environ.get("AUTOPOST_CAPTION") or None)


async def _group_cover_base(qdoc):
    """One TMDB/IMDb lookup + one image download for the whole collection.
    Returns the JPEG bytes (already watermarked) or None.
    Uses utils.find_poster - the SAME lookup as the PM video cover and the PM post card - so the three routes
    pick the same film (language of the file name counts, IMDb fallback gets a TMDB backdrop, results are cached)."""
    title = qdoc.get("title")
    is_series = qdoc.get("tag") == "#SERIES"
    try:
        files = qdoc.get("files") or []
        first_name = (files[0].get("n") if files and isinstance(files[0], dict) else None) or None
        details = await find_poster(title, file=first_name, season=qdoc.get("season"), is_series=is_series) or {}
        # same rule as the PM cover (utils.get_landscape_thumb): a portrait poster only when ALLOW_PORTRAIT_POSTER is on
        url = details.get("backdrop") or (details.get("poster") if ALLOW_PORTRAIT_POSTER else None)
        if not url:
            return None
        buf = await fetch_image(url, (1280, 720))
        if not buf or isinstance(buf, str):
            return None
        return buf.getvalue()
    except Exception as e:
        logger.warning("autopost: cover lookup failed for %s: %s", title, e)
        return None


async def _flood_safe(coro_factory):
    """Run a send; on FloodWait sleep and retry (max 6 times)."""
    for _ in range(6):
        try:
            return await coro_factory()
        except FloodWait as e:
            wait = int(getattr(e, "value", None) or getattr(e, "x", None) or 30)
            logger.warning("autopost: FloodWait %ss", wait)
            await asyncio.sleep(wait + 1)
    raise RuntimeError("too many FloodWaits")


async def _send_file(bot, channel, f, cover_base, last=False):
    """_send_file_raw + the log 'this file was sent to this channel' (so /autocycle and /autopost new never repeat it)."""
    ok = await _send_file_raw(bot, channel, f, cover_base, last)
    if ok:
        await db.posted_add(channel, f["id"])
    return ok


async def _send_file_raw(bot, channel, f, cover_base, last=False):
    """True = sent, False = this file could not be sent (skipped).
    last=True: the LAST file of the collection (the sticker follows) -> 'View Audio, Subs & Story' button."""
    file_id = f["id"]
    markup = None
    if last:
        btn = extract_button(file_id)
        markup = InlineKeyboardMarkup([[btn]]) if btn else None
    await get_file_caption()  # loads the in-bot caption into cache
    await load_switches()
    await load_pretty_settings()
    caption = _build_caption(f)
    is_video = f.get("t") == "video" or str(f.get("m") or "").startswith("video/")
    cover = f.get("cv")
    if is_video and cover_base:
        try:
            cover = add_episode_label(BytesIO(cover_base), f.get("n") or "")
        except Exception:
            cover = f.get("cv")

    def rewind():
        if hasattr(cover, "seek"):
            cover.seek(0)

    try:
        if is_video:
            try:
                def _try_video():
                    rewind()
                    return bot.send_video(chat_id=channel, video=file_id, supports_streaming=True,
                                          caption=caption, cover=cover, reply_markup=markup)
                await _flood_safe(_try_video)
                return True
            except RPCError as e:
                if any(x in _err_id(e) for x in _FATAL):
                    raise _FatalChannelError(_err_id(e))
                logger.warning("autopost: send_video failed (%s), trying send_cached_media", _err_id(e))
            except Exception as e:
                logger.warning("autopost: send_video failed (%s), trying send_cached_media", e)
            rewind()
        def _try_cached():
            rewind()
            return bot.send_cached_media(chat_id=channel, file_id=file_id, cover=cover, caption=caption, reply_markup=markup)
        await _flood_safe(_try_cached)
        return True
    except _FatalChannelError:
        raise
    except RPCError as e:
        if any(x in _err_id(e) for x in _FATAL):
            raise _FatalChannelError(_err_id(e))
        logger.warning("autopost: skipped %s: %s", f.get("n"), _err_id(e))
        return False
    except Exception as e:
        logger.warning("autopost: skipped %s: %s", f.get("n"), e)
        return False
    finally:
        if cover is not f.get("cv") and hasattr(cover, "close"):
            try:
                cover.close()      # free the cover image now, not "some time later"
            except Exception:
                pass


async def _send_group(bot, q, qdoc, cfg, stats):
    """Sends one collection. Returns 'done' or 'stopped'. Progress (`sent`) is saved after
    every file, so a restart / stop continues exactly where it left off."""
    key = qdoc["_id"]
    channel = cfg["series_ch"] if qdoc.get("tag") == "#SERIES" else cfg["movie_ch"]
    files = _prepare_files(qdoc.get("files", []))
    start = int(qdoc.get("sent", 0))

    cover_base = None
    if cfg["cover"] and start < len(files) and any(
            f.get("t") == "video" or str(f.get("m") or "").startswith("video/") for f in files[start:]):
        cover_base = await _group_cover_base(qdoc)

    bid = bot.me.id
    for idx in range(start, len(files)):
        if _state["stop"] or not await wait_for_window(bid, lambda: _state["stop"]):
            return "stopped"
        ok = await _send_file(bot, channel, files[idx], cover_base, last=(idx == len(files) - 1))
        stats["files" if ok else "skipped_files"] += 1
        await q.update_one({"_id": key}, {"$set": {"sent": idx + 1}})
        await asyncio.sleep(cfg["delay"])

    if cfg["sticker"] and not qdoc.get("sticker_done"):
        if not await wait_for_window(bid, lambda: _state["stop"]):
            return "stopped"
        try:
            await _flood_safe(lambda: bot.send_sticker(chat_id=channel, sticker=cfg["sticker"]))
        except RPCError as e:
            if any(x in _err_id(e) for x in _FATAL):
                raise _FatalChannelError(_err_id(e))
            logger.warning("autopost: sticker failed: %s", _err_id(e))
        except Exception as e:
            logger.warning("autopost: sticker failed: %s", e)
        await q.update_one({"_id": key}, {"$set": {"sticker_done": True}})
        await asyncio.sleep(cfg["delay"])
    return "done"


# --------------------------------------------------------------------------- worker
async def _post_worker(bot, chat_id, limit):
    q = _queue()
    bid = bot.me.id
    await ensure_grouped(q, bid)     # show / franchise order (S01 -> S02 -> S03, Spider Man 1 -> 2 -> 3)
    cfg = await _cfg(bot)
    stats = {"groups": 0, "files": 0, "skipped_files": 0, "failed": 0}
    reason = "queue finished"
    cancelled = False
    try:
        while True:
            if _state["stop"]:
                reason = "stopped by you"
                break
            if limit and stats["groups"] >= limit:
                reason = f"limit of {limit} collections reached"
                break

            qdoc = await q.find_one({"status": "pending"}, sort=SORT)
            if not qdoc:
                break
            cfg = await _cfg(bot)   # pick up /autopost delay, cover, channel changes live

            channel = cfg["series_ch"] if qdoc.get("tag") == "#SERIES" else cfg["movie_ch"]
            if not channel:
                reason = "channel not set for " + ("web series" if qdoc.get("tag") == "#SERIES" else "movies")
                break

            try:
                res = await _send_group(bot, q, qdoc, cfg, stats)
            except _FatalChannelError as e:
                reason = f"cannot post to the channel ({html.escape(str(e))}). Make the bot ADMIN there."
                break
            except Exception:
                logger.exception("autopost: failed on %s", qdoc.get("_id"))
                res = "failed"

            if res == "stopped":
                reason = "stopped by you"
                break
            await q.update_one({"_id": qdoc["_id"]},
                               {"$set": {"status": res, "updated_at": datetime.now()}})
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
        cancelled = True          # bot is shutting down: keep the "running" flag so it resumes
        raise
    except Exception as e:
        logger.exception("autopost worker crashed")
        reason = f"error: {html.escape(str(e))}"
    finally:
        _state["stop"] = False
        if not cancelled:
            try:
                await db.update_bot_setting(bid, K_RUNNING, False)
            except Exception:
                pass
            await _say(bot, chat_id, block(
                [head("🏁", "Auto Post ended"), f"📝 Reason : {reason}"],
                [row("📦", "Collections done", stats["groups"]), row("📤", "Files sent", stats["files"]),
                 row("⏭", "Files skipped", stats["skipped_files"]), row("❌", "Failed collections", stats["failed"])]))


async def resume_autopost(bot):
    """Called once from bot.py after start-up: continue a job that was running before a restart."""
    try:
        await asyncio.sleep(15)
        bid = bot.me.id
        if not await db.get_bot_setting(bid, K_RUNNING, False) or _running("post"):
            return
        chat_id = await db.get_bot_setting(bid, K_NOTIFY, None)
        left = int(await db.get_bot_setting(bid, K_LEFT, 0) or 0)   # 0 = no limit
        _state["stop"] = False
        _state["post"] = asyncio.create_task(_post_worker(bot, chat_id, left))
        await _say(bot, chat_id, head("♻️", "Bot restarted") + "\nAuto Post continues where it stopped.")
        logger.info("autopost resumed after restart")
    except Exception:
        logger.exception("autopost: resume failed")


# --------------------------------------------------------------------------- commands
async def _status_text(bot):
    q = _queue()
    await ensure_grouped(q, bot.me.id)
    cfg = await _cfg(bot)
    counts = {st: await q.count_documents({"status": st}) for st in ("pending", "done", "failed")}
    agg = await q.aggregate([{"$group": {"_id": None, "n": {"$sum": "$n"}, "sent": {"$sum": "$sent"}}}]).to_list(1)
    state = "🔎 scanning" if _running("scan") else ("▶️ posting" if _running("post") else "⏸ idle")
    top = [row("⏳", "Pending", counts["pending"]), row("✅", "Done", counts["done"]), row("❌", "Failed", counts["failed"])]
    if agg:
        top.append(row("📁", "Files", f"{agg[0]['sent']} of {agg[0]['n']} sent"))
    nxt = await q.find_one({"status": "pending"}, sort=SORT, projection={"year": 1, "tag": 1, "sent": 1, "n": 1})
    nxt_lines = []
    if nxt:
        kind = "📺" if nxt.get("tag") == "#SERIES" else "🎬"
        nxt_lines.append(f"▶️ Next : {kind} <b>{html.escape(str(nxt['_id']))}</b> ({nxt.get('sent', 0)}/{nxt.get('n', '?')} files)")
    win = await _get_window(bot.me.id, fresh=True)
    sched = (f"{win[0] // 60:02d}:{win[0] % 60:02d} - {win[1] // 60:02d}:{win[1] % 60:02d} (India time)"
             + (" - waiting" if _state["waiting"] else "")) if win else "OFF (any time)"
    setup = [row("🎬", "Movie channel", cfg["movie_ch"] or "NOT SET", code=bool(cfg["movie_ch"])),
             row("📺", "Series channel", cfg["series_ch"] or "NOT SET", code=bool(cfg["series_ch"])),
             row("🎭", "Sticker", "set" if cfg["sticker"] else "NOT SET"),
             row("🖼", "Cover", on_off(cfg["cover"])) + "    " + row("⏱", "Delay", f"{cfg['delay']}s"),
             row("🕒", "Schedule", sched)]
    return block([head("📊", "Auto Post", state)], top, nxt_lines, setup)


async def _set_channel(bot, message, key, label, args):
    if len(args) < 2:
        return await message.reply_text(f"Usage: /autopost {args[0]} -100xxxxxxxxxx")
    try:
        ch_id = int(args[1])
    except ValueError:
        return await message.reply_text("Channel id must be a number like -1001234567890")
    try:
        chat = await bot.get_chat(ch_id)
    except Exception as e:
        return await message.reply_text(f"❌ Bot can not open this channel: {html.escape(str(e))}\nAdd the bot as ADMIN first.")
    await db.update_bot_setting(bot.me.id, key, ch_id)
    await message.reply_text(f"✅ {label} channel saved: {html.escape(str(chat.title))} ({ch_id})")


@Client.on_message(filters.command("autopost") & filters.user(ADMINS))
async def autopost_cmd(bot: Client, message: Message):
    args = message.command[1:]
    sub = args[0].lower() if args else ""
    chat_id = message.chat.id
    q = _queue()
    bid = bot.me.id

    if sub == "movie":
        await _set_channel(bot, message, K_MOVIE, "🎬 Movie", args)

    elif sub == "series":
        await _set_channel(bot, message, K_SERIES, "📺 Web series", args)

    elif sub == "sticker":
        rep = message.reply_to_message
        if not rep or not rep.sticker:
            return await message.reply_text("Reply to a STICKER with /autopost sticker")
        await db.update_bot_setting(bid, K_STICKER, rep.sticker.file_id)
        await message.reply_text("✅ Sticker saved. It is sent after every finished collection.")

    elif sub == "test":
        cfg = await _cfg(bot)
        out = []
        for label, ch in (("🎬 Movie", cfg["movie_ch"]), ("📺 Series", cfg["series_ch"])):
            if not ch:
                out.append(f"{label}: channel not set")
                continue
            try:
                await bot.send_message(ch, f"✅ Auto-post test ({label})")
                if cfg["sticker"]:
                    await bot.send_sticker(ch, cfg["sticker"])
                out.append(f"{label}: OK")
            except Exception as e:
                out.append(f"{label}: ❌ {html.escape(str(e))}")
        await message.reply_text("\n".join(out))

    elif sub == "scan":
        if _running("scan") or _running("post"):
            return await message.reply_text("Scan or posting is already running.")
        if await q.count_documents({}) > 0:
            return await message.reply_text(
                "A queue already exists. /autopost status shows it, or /autopost clear confirm "
                "and then scan again.")
        per_show = len(args) > 1 and args[1].lower() == "show"
        await message.reply_text(
            "🔎 Scan started (" + ("one collection per SERIES" if per_show else "one collection per SEASON")
            + "). Reading all saved files, this can take a while...")
        _state["scan"] = asyncio.create_task(_scan(bot, chat_id, per_show))

    elif sub == "start":
        if _running("scan"):
            return await message.reply_text("Scan is still running, wait for it to finish.")
        if _running("post"):
            return await message.reply_text("Posting is already running. /autopost stop to stop it.")
        cfg = await _cfg(bot)
        if not cfg["movie_ch"] or not cfg["series_ch"]:
            return await message.reply_text("Set both channels first:\n/autopost movie -100...\n/autopost series -100...")
        if not cfg["sticker"]:
            await message.reply_text("⚠️ No sticker set - collections will follow each other without a sticker. "
                                     "(Reply to a sticker with /autopost sticker)")
        if await q.count_documents({"status": "pending"}) == 0:
            return await message.reply_text("Nothing pending. Run /autopost scan first (or /autopost retry).")
        nums = [int(a) for a in args[1:] if a.isdigit()]
        limit = nums[0] if nums else 0
        await db.update_bot_setting(bid, K_RUNNING, True)
        await db.update_bot_setting(bid, K_NOTIFY, chat_id)
        await db.update_bot_setting(bid, K_LEFT, limit)
        _state["stop"] = False
        _state["post"] = asyncio.create_task(_post_worker(bot, chat_id, limit))
        await message.reply_text(
            f"▶️ Auto-post started: {limit or 'all'} collections: one show / franchise after the other (S01, S02, S03 ...), newest show first.\n"
            "/autopost stop to stop, /autopost status to check.")

    elif sub == "stop":
        if not _running("post"):
            await db.update_bot_setting(bid, K_RUNNING, False)
            return await message.reply_text("Posting is not running.")
        _state["stop"] = True
        await message.reply_text("⏹ Stopping after the current file... (progress is saved)")

    elif sub == "status":
        await message.reply_text(await _status_text(bot))

    elif sub == "delay":
        try:
            sec = max(1.0, float(args[1]))
        except (IndexError, ValueError):
            return await message.reply_text("Usage: /autopost delay 2   (seconds between two files, minimum 1)")
        await db.update_bot_setting(bid, K_DELAY, sec)
        await message.reply_text(f"✅ Delay set to {sec}s (applies from the next collection).")

    elif sub == "cover":
        if len(args) < 2 or args[1].lower() not in ("on", "off"):
            return await message.reply_text("Usage: /autopost cover on   or   /autopost cover off")
        on = args[1].lower() == "on"
        await db.update_bot_setting(bid, K_COVER, on)
        await message.reply_text("✅ Poster cover ON." if on else "✅ Poster cover OFF (files keep their own cover).")

    elif sub == "schedule":
        if len(args) >= 2 and args[1].lower() == "off":
            await db.update_bot_setting(bid, K_WINDOW, None)
            await _get_window(bid, fresh=True)
            return await message.reply_text("✅ Schedule OFF - posting runs any time.")
        if len(args) < 3 or _parse_hm(args[1]) is None or _parse_hm(args[2]) is None:
            return await message.reply_text(
                "Usage: /autopost schedule 10:00 22:00   (post only between these times, India time)\n"
                "Overnight works too: /autopost schedule 22:00 06:00\n"
                "/autopost schedule off")
        await db.update_bot_setting(bid, K_WINDOW, f"{args[1]}-{args[2]}")
        await _get_window(bid, fresh=True)
        await message.reply_text(
            f"✅ Schedule saved: posts go out only between {args[1]} and {args[2]} (India time).\n"
            "Outside this time the bot waits and continues by itself. It also applies to /postrange.")

    elif sub == "regroup":
        if _running("post"):
            return await message.reply_text("Stop posting first, then send /autopost regroup.")
        n = await regroup(q, await get_aliases(bid))
        await message.reply_text(f"✅ {n} collections grouped again (all seasons of a show and all movies of a franchise together). "
                                 "Use /franchise to join movie names that do not start the same.")

    elif sub == "retry":
        if _running("post"):
            return await message.reply_text("Stop posting first.")
        res = await q.update_many({"status": "failed"}, {"$set": {"status": "pending"}})
        await message.reply_text(f"🔁 {res.modified_count} collections moved back to pending.")

    elif sub == "new":
        from plugins.autocycle import postnew_command     # /autopost new <channel> [confirm]
        await postnew_command(bot, message, args[1:])
    elif sub == "clear":
        if _running("scan") or _running("post"):
            return await message.reply_text("Stop scan/posting first.")
        if len(args) < 2 or args[1].lower() != "confirm":
            return await message.reply_text("This deletes the whole queue. Send /autopost clear confirm to proceed.")
        await q.drop()
        await db.update_bot_setting(bid, K_RUNNING, False)
        await message.reply_text("🗑 Queue deleted. Messages already sent are untouched.")

    else:
        await message.reply_text(
            "Auto-post old files into private channels\n\n"
            "1) /autopost movie -100...\n"
            "2) /autopost series -100...\n"
            "3) reply to a sticker: /autopost sticker\n"
            "4) /autopost test\n"
            "5) /autopost scan   (or /autopost scan show)\n"
            "6) /autopost start 5   then   /autopost start\n\n"
            "/autopost schedule 10:00 22:00   (post only in this time window, India time; off = any time)\n"
            "/autopost stop | status | delay [seconds] | cover on|off | retry | clear confirm\n\n"
            + await _status_text(bot))
