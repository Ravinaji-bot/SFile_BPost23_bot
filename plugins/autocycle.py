"""/autocycle - the new files of the bot are posted by themselves, every few hours, without any command.
/sendto and /autopost new - post to ANY channel when YOU want.

How it works
  * Every NEW file saved by the bot (index channel, /index, request index) is put in an INBOX
    (collection `new_files`, filled by database.ia_filterdb.save_file). Old files are never in it.
  * Every N hours (default 4) one ROUND runs. It takes the oldest files of the inbox (default 200),
    groups them by movie / season and, for every group, in this order:
        1. the files -> the MOVIE channel / SERIES channel of /autopost (button + sticker, same caption),
        2. the card  -> RESULT_ARCHIVE_CHANNEL (a card that already exists is EDITED: the new quality / episode
                        is added to it, nothing is reposted; a new season is a new card).
    The public live post card (MOVIE_UPDATE_CHANNEL) is not touched - it keeps working by itself.
  * A file is sent to a channel only once (collection `posted_log`), so nothing is ever repeated.

Commands (admins):
    /autocycle on | off          - start / stop the automatic rounds (survives restarts)
    /autocycle every <hours>     - time between two rounds (1 - 48, default 4)
    /autocycle limit <files>     - max files per round (10 - 2000, default 200)
    /autocycle now               - run a round right now
    /autocycle stop              - stop the round that is running
    /autocycle files on|off      - send the files (default ON)
    /autocycle cards on|off      - post / edit the cards (default ON)
    /autocycle inbox             - how many new files wait;  /autocycle inbox clear confirm
    /autocycle status
    The daily time window of /autopost (/autopost schedule 10:00 22:00) is used by the rounds too.

    /sendto <channel_id> <name> [files|card|both]
                                 - post ONE movie / season (files + sticker, and/or its card) to any channel now,
                                   e.g.  /sendto -1001234567890 Jailer 2023
    /autopost new <channel_id> [confirm]
                                 - send to that channel every file that was not sent to it before
                                   (without `confirm` it only counts; run it again with `confirm`)

Old files (before /autocycle): send them ONCE with /autopost scan + /autopost start (a scan empties the inbox
of the files it already has, so nothing goes twice); switch /autocycle on when that backfill is finished.
"""
import asyncio
import html
import logging
import re
import time
from collections import OrderedDict
from datetime import datetime

from pymongo import UpdateOne
from pyrogram import Client, filters
from pyrogram.types import Message

from database.ia_filterdb import Media, Media2
from database.users_chats_db import db
from info import ADMINS, MULTIPLE_DB, NOT_SMALL_VIDEO
from dreamxbotz.util.jobtools import send_notice
from dreamxbotz.util.style import head, row, block, tip, cmd, on_off
from plugins import autopost as ap
from plugins import bulk_post as bp
from plugins.channel import extract_media_info, _series_group_key

logger = logging.getLogger(__name__)

K_ON, K_EVERY, K_LIMIT, K_NEXT = "AC_ON", "AC_EVERY", "AC_LIMIT", "AC_NEXT"
K_NOTIFY, K_FILES, K_CARDS, K_LAST = "AC_NOTIFY", "AC_FILES", "AC_CARDS", "AC_LAST"
DEFAULT_EVERY_H, DEFAULT_LIMIT = 4.0, 200
BATCH = 1000

_state = {"loop": None, "round": None, "stop": False, "job": None}
_PROJ = {"file_name": 1, "caption": 1, "file_size": 1, "file_type": 1, "mime_type": 1, "cover": 1}


class _NoQueue:
    """autopost._send_group saves its progress in a queue document - a round has no queue, nothing to save."""
    async def update_one(self, *a, **k):
        return None


def _running(key):
    t = _state.get(key)
    return bool(t and not t.done())


def _stop_flag(v):
    _state["stop"] = v
    # autopost._send_group / wait_for_window read ap's flag, so autocycle's rounds / jobs need it too.
    # But while the /autopost backfill itself is running the flag is HIS: /autocycle off|stop must not
    # stop it, and a new round must not cancel a /autopost stop that was just sent.
    if ap._running("post"):
        return
    if v and not (_running("round") or _running("job")):
        return                     # nothing of autocycle is running: nothing to stop, no stale flag left behind
    ap._state["stop"] = v


# --------------------------------------------------------------------------- grouping
async def _load_docs(ids):
    out = {}
    cols = [Media.collection] + ([Media2.collection] if MULTIPLE_DB else [])
    for col in cols:
        async for d in col.find({"_id": {"$in": list(ids)}}, _PROJ):
            out[d["_id"]] = d
    return out


def _group_docs(docs):
    """Media documents -> (groups, bad_ids). Same grouping as /autopost and /bulkpost:
    a movie = its title, a series = one season. A group carries the files in BOTH formats
    (`files` for autopost._send_group, `bfiles` for bulk_post._post_one)."""
    groups = OrderedDict()
    bad = []
    max_year = datetime.now().year + 1
    for doc in docs:
        fid = doc["_id"]
        if doc.get("file_type") == "audio":
            bad.append(fid)
            continue
        try:
            info = extract_media_info(doc.get("file_name") or "", doc.get("caption") or "")
            base = info["base_name"]
            if not base:
                raise ValueError("empty base_name")
        except Exception:
            bad.append(fid)
            continue
        is_series = info["tag"] == "#SERIES"
        key = _series_group_key(base, info["season"]) if is_series else base
        m = bp.YEAR_TAIL.search(base)
        year = int(m.group(1)) if m else 0
        if year > max_year:
            year = 0
        g = groups.get(key)
        if g is None:
            g = groups[key] = {"_id": key, "title": base, "year": year, "tag": info["tag"], "season": info["season"],
                               "ott_platform": info["ott_platform"], "files": [], "bfiles": [], "ids": []}
        g["ids"].append(fid)
        g["files"].append({
            "id": fid, "n": doc.get("file_name") or "", "s": doc.get("file_size") or 0, "t": doc.get("file_type"),
            "m": doc.get("mime_type"), "c": (doc.get("caption") or "")[:500], "cv": doc.get("cover"),
        })
        g["bfiles"].append({
            "filename": doc.get("file_name"), "processed": info["processed"], "quality": info["quality"],
            "language": info["language"], "ott_platform": info["ott_platform"], "timestamp": datetime.now(),
            "tag": info["tag"], "season": info["season"], "episode": info["episode"], "file_id": fid,
            "file_size": doc.get("file_size") or 0, "cover": doc.get("cover"),
        })
    return groups, bad


async def _archive_key(g):
    """A movie whose files carry no year ('RRR 1080p') joins the card of 'RRR 2022' when that is unambiguous
    (same rule as /bulkpost), so a new quality is added to the existing card instead of making a second one."""
    if g["tag"] == "#SERIES" or bp.YEAR_TAIL.search(g["title"]):
        return g["_id"], g["title"]
    try:
        rx = f"^{re.escape(g['title'])} (19|20)\\d{{2}}$"
        found = await bp._posts().find({"tag": {"$ne": "#SERIES"}, "_id": {"$regex": rx, "$options": "i"}},
                                       {"_id": 1, "title": 1}).to_list(3)
        if len(found) == 1:
            return found[0]["_id"], found[0].get("title") or found[0]["_id"]
    except Exception:
        logger.debug("autocycle: archive key lookup failed", exc_info=True)
    return g["_id"], g["title"]


# --------------------------------------------------------------------------- one group
async def _do_group(bot, cfg, g, stats, *, channel=None, files=True, cards=True,
                    card_chat=None, card_coll=None, force=False):
    """Files (+ sticker) and card of ONE movie / season. Returns 'done' or 'stopped'.
    channel=None: the MOVIE / SERIES channel of /autopost; card_chat=None: the archive channel."""
    if files:
        target = channel or (cfg["series_ch"] if g["tag"] == "#SERIES" else cfg["movie_ch"])
        if not target:
            raise RuntimeError("the MOVIE / SERIES channel is not set (/autopost movie <id>, /autopost series <id>)")
        todo = g["files"]
        if not force:
            fresh = set(await db.posted_new(target, [f["id"] for f in todo]))
            todo = [f for f in todo if f["id"] in fresh]
        if todo:
            qdoc = {"_id": g["_id"], "title": g["title"], "tag": g["tag"], "season": g["season"],
                    "files": todo, "sent": 0, "sticker_done": False}
            local = dict(cfg, movie_ch=target, series_ch=target)
            res = await ap._send_group(bot, _NoQueue(), qdoc, local, stats)
            if res == "stopped":
                return "stopped"
    if cards and (card_chat or bp._archive_ready()):
        if card_chat:
            key, title = g["_id"], g["title"]
        else:
            key, title = await _archive_key(g)
        bq = {"_id": key, "title": title, "year": g["year"], "tag": g["tag"],
              "ott_platform": g["ott_platform"], "files": g["bfiles"]}
        try:
            r = await bp._post_one(bot, bq, True, chat_id=card_chat, coll=card_coll)
        except Exception:
            logger.exception("autocycle: card failed for %s", key)
            r = "failed"
        stats["card_" + r] = stats.get("card_" + r, 0) + 1
    return "done"


# --------------------------------------------------------------------------- round
def _blank_stats():
    return {"files": 0, "skipped_files": 0, "groups": 0, "failed_groups": 0, "dropped": 0}


async def _run_round(bot, notify_chat, manual=False):
    """One round over the inbox. Returns the stats dict (or None when it could not start)."""
    bid = bot.me.id
    me = asyncio.current_task()
    other = _state.get("round")
    if other is not None and other is not me and not other.done():
        return None                                   # another round is running
    _stop_flag(False)
    stats = _blank_stats()
    _state["round"] = me
    try:
        cfg = await ap._cfg(bot)
        files_on = bool(await db.get_bot_setting(bid, K_FILES, True))
        cards_on = bool(await db.get_bot_setting(bid, K_CARDS, True))
        limit = int(await db.get_bot_setting(bid, K_LIMIT, DEFAULT_LIMIT) or DEFAULT_LIMIT)
        if files_on and not (cfg["movie_ch"] and cfg["series_ch"]):
            await send_notice(bot, notify_chat, block([head("⚠️", "Auto Cycle can not start")], [tip(f"Set the channels first:", f"{cmd('/autopost movie -100xxxxxxxxxx')}", f"{cmd('/autopost series -100xxxxxxxxxx')}")]))
            return None
        ids = await db.inbox_take(limit)
        if not ids:
            if manual:
                await send_notice(bot, notify_chat, head("📭", "Auto Cycle") + "\nThe inbox is empty - no new files.")
            return stats
        docs = await _load_docs(ids)
        await db.inbox_remove([i for i in ids if i not in docs])         # deleted from the database meanwhile
        groups, bad = _group_docs(list(docs.values()))
        await db.inbox_remove(bad)                                       # audio / unreadable: nothing to post
        for key, g in groups.items():
            if _state["stop"]:
                break
            if not await ap.wait_for_window(bid, lambda: _state["stop"]):
                break
            try:
                res = await _do_group(bot, cfg, g, stats, files=files_on, cards=cards_on)
            except ap._FatalChannelError as e:
                await send_notice(bot, notify_chat, block([head("❌", "Auto Cycle stopped"), f"A channel can not be used ({html.escape(str(e))})"],
                                                    [tip(f"Make the bot ADMIN there, then {cmd('/autocycle now')}")]))
                break
            except Exception as e:
                logger.exception("autocycle: group %s failed", key)
                stats["failed_groups"] += 1
                stats["dropped"] += await db.inbox_fail(g["ids"])
                continue
            if res == "stopped":
                break
            stats["groups"] += 1
            await db.inbox_remove(g["ids"])
        return stats
    except Exception as e:
        logger.exception("autocycle: round failed")
        await send_notice(bot, notify_chat, head("❌", "Auto Cycle round failed") + f"\n{html.escape(str(e))}")
        return None
    finally:
        if stats and (stats["groups"] or stats["failed_groups"] or stats["files"]):
            await db.update_bot_setting(bid, K_LAST, {"t": time.time(), **stats})
            try:
                left = await db.inbox_count()
                cards = " | ".join(f"{k[5:]} {v}" for k, v in stats.items() if k.startswith("card_"))
                await send_notice(bot, notify_chat, block(
                    [head("🔄", "Round finished")],
                    [row("📦", "Collections", stats["groups"]), row("📤", "Files sent", f"{stats['files']} (skipped {stats['skipped_files']})"),
                     row("🧾", "Cards", cards or "-")],
                    [row("❌", "Failed collections", f"{stats['failed_groups']} (dropped after 3 tries: {stats['dropped']})"),
                     row("📥", "Still waiting", left)]))
            except Exception:
                logger.debug("autocycle: summary not sent", exc_info=True)
        _state["round"] = None


async def _loop(bot):
    bid = bot.me.id
    try:
        while True:
            if not await db.get_bot_setting(bid, K_ON, False):
                return
            nxt = float(await db.get_bot_setting(bid, K_NEXT, 0) or 0)
            now = time.time()
            if now < nxt:
                await asyncio.sleep(min(30, nxt - now))
                continue
            if ap._running("post") or _running("job"):
                await asyncio.sleep(300)       # the old-files backfill (/autopost) or /autopost new is running: wait
                continue
            await _run_round(bot, await db.get_bot_setting(bid, K_NOTIFY, None))
            every = float(await db.get_bot_setting(bid, K_EVERY, DEFAULT_EVERY_H) or DEFAULT_EVERY_H)
            await db.update_bot_setting(bid, K_NEXT, time.time() + every * 3600)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("autocycle loop crashed")


def _ensure_loop(bot):
    if not _running("loop"):
        _state["loop"] = asyncio.create_task(_loop(bot))


async def resume_autocycle(bot):
    """Called once from bot.py after start-up: the automatic rounds continue after a restart."""
    try:
        await asyncio.sleep(25)
        if await db.get_bot_setting(bot.me.id, K_ON, False):
            _ensure_loop(bot)
            logger.info("autocycle resumed after restart")
    except Exception:
        logger.exception("autocycle: resume failed")


# --------------------------------------------------------------------------- /autocycle
def _fmt_ago(ts):
    secs = max(0, int(time.time() - ts))
    return f"{secs // 3600}h {(secs % 3600) // 60}m ago"


async def _status_text(bot):
    bid = bot.me.id
    on = bool(await db.get_bot_setting(bid, K_ON, False))
    every = float(await db.get_bot_setting(bid, K_EVERY, DEFAULT_EVERY_H) or DEFAULT_EVERY_H)
    limit = int(await db.get_bot_setting(bid, K_LIMIT, DEFAULT_LIMIT) or DEFAULT_LIMIT)
    files_on = bool(await db.get_bot_setting(bid, K_FILES, True))
    cards_on = bool(await db.get_bot_setting(bid, K_CARDS, True))
    nxt = float(await db.get_bot_setting(bid, K_NEXT, 0) or 0)
    last = await db.get_bot_setting(bid, K_LAST, None)
    cfg = await ap._cfg(bot)
    win = await db.get_bot_setting(bid, ap.K_WINDOW, None)
    left = await db.inbox_count()
    state = on_off(on) + ("   ▶️ round running" if _running("round") else "")
    setup = [row("⏱", "Every", f"{every:g} h"), row("📦", "Limit", f"{limit} files per round"),
             row("🕒", "Window", win or "any time"),
             row("📂", "Files", on_off(files_on)) + "    " + row("🧾", "Cards", on_off(cards_on))]
    channels = [row("🎬", "Movie channel", cfg["movie_ch"] or "NOT SET", code=bool(cfg["movie_ch"])),
                row("📺", "Series channel", cfg["series_ch"] or "NOT SET", code=bool(cfg["series_ch"])),
                row("📢", "Archive channel", bp.RESULT_ARCHIVE_CHANNEL if bp._archive_ready() else "NOT SET", code=bp._archive_ready())]
    now = [row("📥", "New files waiting", left)]
    if on:
        now.append(row("▶️", "Next round", "due now" if nxt <= time.time() else f"in {int((nxt - time.time()) // 60)} min"))
    if last:
        now.append(row("🕘", "Last round", f"{_fmt_ago(last['t'])} ({last.get('groups', 0)} collections, {last.get('files', 0)} files)"))
    warn = [tip("⚠️ Auto Post (old files) is running - rounds wait until it is finished.")] if ap._running("post") else []
    return block([head("🔄", "Auto Cycle", state)], setup, channels, now, warn)


HELP = block(
    [head("🔄", "Auto Cycle")],
    [f"{cmd('/autocycle on')}  |  {cmd('off')}", f"{cmd('/autocycle every 4')}  (hours)", f"{cmd('/autocycle limit 200')}  (files per round)",
     f"{cmd('/autocycle now')}  |  {cmd('stop')}  |  {cmd('status')}", f"{cmd('/autocycle files on')}  |  {cmd('cards off')}",
     f"{cmd('/autocycle inbox')}  |  {cmd('inbox clear confirm')}"],
    [head("📤", "Post to any channel"), f"{cmd('/sendto -100xxxxxxxxxx Jailer 2023')}", "(add files / card / both at the end)",
     f"{cmd('/autopost new -100xxxxxxxxxx')}", "(counts first, then add confirm)"])


@Client.on_message(filters.command("autocycle") & filters.user(ADMINS))
async def autocycle_cmd(bot: Client, message: Message):
    args = message.command[1:]
    sub = args[0].lower() if args else ""
    bid = bot.me.id
    if sub in ("", "help"):
        return await message.reply_text(HELP + "\n\n" + await _status_text(bot))
    if sub == "status":
        return await message.reply_text(await _status_text(bot))
    if sub == "on":
        await db.update_bot_setting(bid, K_ON, True)
        await db.update_bot_setting(bid, K_NOTIFY, message.chat.id)
        if not float(await db.get_bot_setting(bid, K_NEXT, 0) or 0):
            await db.update_bot_setting(bid, K_NEXT, 0)           # first round right away
        _ensure_loop(bot)
        return await message.reply_text("✅ /autocycle is ON. The first round starts in a moment, then every "
                                        f"{float(await db.get_bot_setting(bid, K_EVERY, DEFAULT_EVERY_H) or DEFAULT_EVERY_H):g} h.\n\n"
                                        + await _status_text(bot))
    if sub == "off":
        await db.update_bot_setting(bid, K_ON, False)
        _stop_flag(True)
        return await message.reply_text("⏸ /autocycle is OFF (the round that is running stops after its current file).")
    if sub == "stop":
        _stop_flag(True)
        return await message.reply_text("🛑 The running round stops after its current file. (/autocycle off = no more rounds)")
    if sub == "now":
        if _running("round"):
            return await message.reply_text("A round is already running.")
        await db.update_bot_setting(bid, K_NOTIFY, message.chat.id)
        if await db.get_bot_setting(bid, K_ON, False):
            await db.update_bot_setting(bid, K_NEXT, 0)
            _ensure_loop(bot)
        else:
            _state["round"] = asyncio.create_task(_run_round(bot, message.chat.id, manual=True))
        return await message.reply_text("▶️ Round started. You get a message when it is finished.")
    if sub in ("every", "limit") and len(args) > 1:
        try:
            val = float(args[1])
        except ValueError:
            return await message.reply_text("Please send a number.")
        if sub == "every":
            if not 1 <= val <= 48:
                return await message.reply_text("Hours: 1 to 48.")
            await db.update_bot_setting(bid, K_EVERY, val)
            await db.update_bot_setting(bid, K_NEXT, time.time() + val * 3600)
            return await message.reply_text(f"✅ A round every {val:g} h (next one in {val:g} h).")
        if not 10 <= val <= 2000:
            return await message.reply_text("Files per round: 10 to 2000.")
        await db.update_bot_setting(bid, K_LIMIT, int(val))
        return await message.reply_text(f"✅ Max {int(val)} files per round.")
    if sub in ("files", "cards") and len(args) > 1 and args[1].lower() in ("on", "off"):
        on = args[1].lower() == "on"
        await db.update_bot_setting(bid, K_FILES if sub == "files" else K_CARDS, on)
        return await message.reply_text(f"✅ {sub.capitalize()}: {'ON' if on else 'OFF'}")
    if sub == "inbox":
        if len(args) > 2 and args[1].lower() == "clear" and args[2].lower() == "confirm":
            return await message.reply_text(f"🗑 {await db.inbox_clear()} entries removed from the inbox (the files stay in the database).")
        n = await db.inbox_count()
        old = await db.inbox_oldest()
        return await message.reply_text(f"📥 {n} new files wait for the next round." + (f"\nOldest: {old:%Y-%m-%d %H:%M} UTC" if old else ""))
    await message.reply_text(HELP)


# --------------------------------------------------------------------------- /sendto
_CHANNEL_RE = re.compile(r"-100\d{5,}")


@Client.on_message(filters.command("sendto") & filters.user(ADMINS))
async def sendto_cmd(bot: Client, message: Message):
    args = message.command[1:]
    if len(args) < 2 or not _CHANNEL_RE.fullmatch(args[0]):
        return await message.reply_text("Usage: <code>/sendto -100xxxxxxxxxx Jailer 2023</code>\n"
                                        "Add <code>files</code>, <code>card</code> or <code>both</code> (default) at the end.")
    channel = int(args[0])
    mode = "both"
    rest = args[1:]
    if rest[-1].lower() in ("files", "card", "both") and len(rest) > 1:
        mode = rest[-1].lower()
        rest = rest[:-1]
    words = [w for w in re.split(r"\s+", " ".join(rest).strip()) if w]
    if not words:
        return await message.reply_text("Send the movie / series name too.")
    if _running("job"):
        return await message.reply_text("Another /sendto or /autopost new job is running. Wait for it, or /autocycle stop.")
    # a character that is not a letter / digit can be a space, dot or dash in the stored file name -> any character
    flt = {"$and": [{"file_name": {"$regex": "".join(c if c.isalnum() else "." for c in w), "$options": "i"}} for w in words]}
    docs = []
    cols = [Media.collection] + ([Media2.collection] if MULTIPLE_DB else [])
    for col in cols:
        async for d in col.find({"$and": [flt, NOT_SMALL_VIDEO]}, _PROJ).limit(3000):
            docs.append(d)
    groups, _bad = _group_docs(docs)
    if not groups:
        return await message.reply_text("❌ Nothing found for that name.")
    query = " ".join(words).lower()
    pick = next((g for k, g in groups.items() if k.lower() == query), None)
    if pick is None and len(groups) == 1:
        pick = next(iter(groups.values()))
    if pick is None:
        lines = [f"• {html.escape(k)} ({len(g['files'])} files)" for k, g in list(groups.items())[:12]]
        return await message.reply_text("More than one match - send the exact title:\n" + "\n".join(lines))
    cfg = await ap._cfg(bot)
    await message.reply_text(f"▶️ Sending <b>{html.escape(pick['title'])}</b> ({len(pick['files'])} files, {mode}) to <code>{channel}</code> ...")
    _stop_flag(False)
    _state["job"] = asyncio.create_task(_sendto_job(bot, message.chat.id, cfg, pick, channel, mode))


async def _sendto_job(bot, chat_id, cfg, g, channel, mode):
    stats = _blank_stats()
    try:
        res = await _do_group(bot, cfg, g, stats, channel=channel, files=(mode != "card"), cards=(mode != "files"),
                              card_chat=channel, card_coll=db.db[f"sendto_cards_{abs(channel)}"], force=True)
        cards = ", ".join(f"{k[5:]} {v}" for k, v in stats.items() if k.startswith("card_"))
        await send_notice(bot, chat_id, block(
            [head("🛑" if res == "stopped" else "✅", "Send finished" if res != "stopped" else "Send stopped")],
            [row("🎬", "Title", html.escape(g["title"])), row("📤", "To", channel, code=True)],
            [row("📤", "Files sent", f"{stats['files']} (skipped {stats['skipped_files']})"), row("🧾", "Card", cards or "-")]))
    except ap._FatalChannelError as e:
        await send_notice(bot, chat_id, block([head("❌", "Channel can not be used"), row("📤", "Channel", channel, code=True), f"Reason : {html.escape(str(e))}"],
                                        [tip("Make the bot ADMIN there and try again")]))
    except Exception as e:
        logger.exception("sendto failed")
        await send_notice(bot, chat_id, head("❌", "Send failed") + f"\n{html.escape(str(e))}")


# --------------------------------------------------------------------------- /autopost new
def _newq(channel):
    return db.db[f"postnew_queue_{abs(channel)}"]


async def postnew_command(bot, message, args):
    if not args or not _CHANNEL_RE.fullmatch(args[0]):
        return await message.reply_text("Usage: <code>/autopost new -100xxxxxxxxxx</code> (counts) and "
                                        "<code>/autopost new -100xxxxxxxxxx confirm</code> (sends).\n"
                                        "It sends every file that was not sent to that channel before.")
    channel = int(args[0])
    confirm = len(args) > 1 and args[1].lower() == "confirm"
    if _running("job"):
        return await message.reply_text("Another job is running. Wait for it, or /autocycle stop.")
    _stop_flag(False)
    await message.reply_text("🔎 Looking for files that were not sent to this channel yet ..."
                             if not confirm else "▶️ Scanning and sending ... (progress messages follow)")
    _state["job"] = asyncio.create_task(_postnew_job(bot, message.chat.id, channel, confirm))


async def _scan_new(channel, q):
    """Every file that is NOT in the sent-log of `channel`, grouped like /autopost. Returns (files, groups)."""
    files = 0
    ops = []
    max_year = datetime.now().year + 1
    cols = [Media.collection] + ([Media2.collection] if MULTIPLE_DB else [])
    for col in cols:
        batch = []

        async def flush(batch):
            nonlocal files, ops
            fresh = set(await db.posted_new(channel, [d["_id"] for d in batch]))
            docs = [d for d in batch if d["_id"] in fresh]
            groups, _bad = _group_docs(docs)
            for key, g in groups.items():
                files += len(g["files"])
                ops.append(UpdateOne({"_id": key}, {
                    "$setOnInsert": {"title": g["title"], "year": g["year"], "tag": g["tag"], "season": g["season"],
                                     "status": "pending", "sent": 0, "sticker_done": False},
                    "$push": {"files": {"$each": g["files"]}}, "$inc": {"n": len(g["files"])}}, upsert=True))
            if ops:
                await q.bulk_write(ops, ordered=True)
                ops = []

        async for doc in col.find(NOT_SMALL_VIDEO, _PROJ):
            batch.append(doc)
            if len(batch) >= BATCH:
                await flush(batch)
                batch = []
        if batch:
            await flush(batch)
    return files, await q.count_documents({})


async def _postnew_job(bot, chat_id, channel, confirm):
    q = _newq(channel)
    try:
        history = await db.posted_count(channel)
        if not confirm:
            await q.delete_many({})
            files, groups = await _scan_new(channel, q)
            await q.delete_many({})
            await send_notice(bot, chat_id, block(
                [head("📊", "Not sent yet"), row("📤", "Channel", channel, code=True)],
                [row("📄", "Files", files), row("📦", "Collections", groups), row("✅", "Already sent before", history)],
                ([tip("⚠️ This channel has NO history, so everything counts as new.")] if not history else []),
                [tip(f"To send: {cmd(f'/autopost new {channel} confirm')}")]))
            return
        if not await q.find_one({"status": "pending"}):          # a pending queue = continue after a stop / restart
            await q.delete_many({})
            files, groups = await _scan_new(channel, q)
            await send_notice(bot, chat_id, block([head("📦", "Queue made"), row("📤", "Channel", channel, code=True)], [row("📄", "Files", files), row("📦", "Collections", groups)], ["▶️ Sending ..."]))
        cfg = dict(await ap._cfg(bot), movie_ch=channel, series_ch=channel)
        stats = _blank_stats()
        n = 0
        while not _state["stop"]:
            qdoc = await q.find_one({"status": "pending"}, sort=[("year", -1), ("_id", 1)])
            if not qdoc:
                break
            todo = ap._prepare_files(qdoc.get("files", []))
            fresh = set(await db.posted_new(channel, [f["id"] for f in todo]))
            qdoc["files"] = [f for f in todo if f["id"] in fresh]
            qdoc["sent"] = 0
            qdoc["sticker_done"] = False
            if qdoc["files"]:
                res = await ap._send_group(bot, _NoQueue(), qdoc, cfg, stats)
                if res == "stopped":
                    break
            await q.update_one({"_id": qdoc["_id"]}, {"$set": {"status": "done"}})
            n += 1
            if n % 25 == 0:
                await send_notice(bot, chat_id, block([head("⏳", "Sending to a channel")], [row("📦", "Collections sent", n), row("⏳", "Left", await q.count_documents({'status': 'pending'}))]))
        left = await q.count_documents({"status": "pending"})
        await send_notice(bot, chat_id, block(
            [head("✅" if not left else "🛑", "Send finished" if not left else "Send stopped"), row("📤", "Channel", channel, code=True)],
            [row("📦", "Collections", n), row("📤", "Files sent", f"{stats['files']} (skipped {stats['skipped_files']})"), row("⏳", "Left", left)],
            ([tip(f"Continue: {cmd(f'/autopost new {channel} confirm')}")] if left else [])))
        if not left:
            await q.drop()
    except ap._FatalChannelError as e:
        await send_notice(bot, chat_id, block([head("❌", "Channel can not be used"), row("📤", "Channel", channel, code=True), f"Reason : {html.escape(str(e))}"],
                                        [tip("Make the bot ADMIN there and try again")]))
    except Exception as e:
        logger.exception("autopost new failed")
        await send_notice(bot, chat_id, head("❌", "Send failed") + f"\n{html.escape(str(e))}")
