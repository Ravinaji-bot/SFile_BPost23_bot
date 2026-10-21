"""
Bulk-post OLD files (already saved in the database) - ONE post per movie with ALL its
qualities - into the private channel set in RESULT_ARCHIVE_CHANNEL,
one franchise after the other (Spider Man 1 -> 2 -> 3), newest franchise first - see plugins/collection_order.py.

It reuses the exact same title parsing (extract_media_info), poster fetch
(TMDB -> IMDb fallback), caption builder and sender that plugins/channel.py uses
for live posts, so the bulk posts look identical to the normal auto-posts.

Commands (admin only):
    /bulkpost scan                 - step 1: read ALL saved files and build the queue (run once)
    /bulkpost start [count] [gap]  - step 2: start posting. count = how many posts (0/none = all),
                                     gap = seconds between posts (default 4, minimum 2)
    /bulkpost stop                 - stop after the current post
    /bulkpost status               - queue numbers, running state, next year to be posted
    /bulkpost noposter on|off      - allow/skip movies for which no poster was found (default: skip)
    /bulkpost retry                - put failed + no-poster items back in the queue
    /bulkpost clear confirm        - delete the queue (the posts already sent are NOT touched)

Notes:
    * The queue lives in the `bulk_post_queue` collection, one document per movie/series,
      so it survives restarts. After a restart just send /bulkpost start again.
    * Posts go to RESULT_ARCHIVE_CHANNEL (private channel, bot must be admin) and are
      recorded in their own `bulk_posts` collection, so the live auto-posts in
      MOVIE_UPDATE_CHANNEL are never touched.
    * A movie is never posted twice; files of a movie that are missing from its post are
      merged into it instead.
    * After the scan, files of the same movie that only differ by a missing year
      ("RRR 1080p" / "RRR 2022 720p") are merged into ONE movie when there is exactly one
      matching year.
"""
import asyncio
import html
import logging
import re
from collections import defaultdict
from datetime import datetime

from pymongo import UpdateOne
from pymongo.errors import DuplicateKeyError
from pyrogram import Client, filters
from pyrogram.types import Message

from database.ia_filterdb import Media, Media2
from database.users_chats_db import db
from info import ALLOW_PORTRAIT_POSTER, LINK_PREVIEW, ADMINS, MULTIPLE_DB, TMDB_POSTER, RESULT_ARCHIVE_CHANNEL, NOT_SMALL_VIDEO
from plugins.channel import (
    extract_media_info,
    send_movie_update,
    update_movie_message,
    STANDARD_GENRES,
    _series_group_key,
)
from plugins.Dreamxfutures.Imdbposter import get_movie_detailsx, get_movie_details

logger = logging.getLogger(__name__)

YEAR_TAIL = re.compile(r"(?<!\d)((?:19|20)\d{2})\s*$")
BATCH = 1000
DEFAULT_GAP = 4
NOPOSTER_KEY = "BULK_ALLOW_NOPOSTER"
K_RUNNING, K_NOTIFY, K_LEFT, K_GAP = "BULK_RUNNING", "BULK_NOTIFY", "BULK_LEFT", "BULK_GAP"   # restart-proof job state
PROGRESS_EVERY = 100          # send a progress message every N posts
SCAN_PROGRESS_EVERY = 25000   # ... and every N files while scanning
from dreamxbotz.util.jobtools import send_notice, send_sticker
from dreamxbotz.util.style import head, row, block, tip, cmd, on_off, bar, LINE
from plugins.collection_order import GROUP_SORT, ensure_grouped, regroup, get_aliases

SORT = GROUP_SORT   # gyear DESC, gkey, ord, _id: all seasons of a show / all movies of a franchise together

_state = {"scan": None, "post": None, "stop": False}


def _queue():
    return db.db.bulk_post_queue


def _posts():
    """Records of the posts sent by /bulkpost (separate from the live `movie_updates`)."""
    return db.db.bulk_posts


def _archive_ready():
    return RESULT_ARCHIVE_CHANNEL <= -1000      # unset = 0 / default -100


async def _merge_yearless(q):
    """Movies whose files have no year in the name ('RRR 1080p') are merged into the movie
    that has the same title WITH a year ('RRR 2022'), but only when that is unambiguous
    (exactly one year for that title), so remakes are never mixed up."""
    with_year, yearless = defaultdict(list), []
    async for d in q.find({"tag": {"$ne": "#SERIES"}}, {"_id": 1}):
        key = d["_id"]
        m = YEAR_TAIL.search(key)
        if m:
            title = key[:m.start()].strip().lower()
            if title:
                with_year[title].append(key)
        else:
            yearless.append(key)
    merged = 0
    for key in yearless:
        cands = with_year.get(key.strip().lower())
        if cands and len(cands) == 1:
            src = await q.find_one({"_id": key})
            if src and src.get("files"):
                await q.update_one({"_id": cands[0]}, {"$push": {"files": {"$each": src["files"]}}})
                await q.delete_one({"_id": key})
                merged += 1
    return merged


def _running(key):
    task = _state.get(key)
    return bool(task and not task.done())


async def _say(bot, chat_id, text):
    """Job message: goes to the status channel (/statuschannel) when one is set, else to `chat_id`."""
    await send_notice(bot, chat_id, text)


# --------------------------------------------------------------------------- scan
async def _scan(bot, chat_id):
    q = _queue()
    seen = errors = 0
    ops = []
    max_year = datetime.now().year + 1
    try:
        await q.create_index([("status", 1), ("year", -1), ("_id", 1)])
        sources = [Media.collection]
        if MULTIPLE_DB:
            sources.append(Media2.collection)

        for col in sources:
            cursor = col.find(NOT_SMALL_VIDEO, {"file_name": 1, "caption": 1, "file_size": 1, "cover": 1})
            async for doc in cursor:
                seen += 1
                try:
                    info = extract_media_info(doc.get("file_name") or "", doc.get("caption") or "")
                    base = info["base_name"]
                    if not base:
                        raise ValueError("empty base_name")
                except Exception:
                    errors += 1
                    continue

                # For series, queue each season separately so it later becomes
                # its own post instead of every season being merged into one.
                group_key = _series_group_key(base, info["season"]) if info["tag"] == "#SERIES" else base

                m = YEAR_TAIL.search(base)
                year = int(m.group(1)) if m else 0
                if year > max_year:      # e.g. "Blade Runner 2049" is not a 2049 release
                    year = 0

                file_data = {
                    "filename": doc.get("file_name"),
                    "processed": info["processed"],
                    "quality": info["quality"],
                    "language": info["language"],
                    "ott_platform": info["ott_platform"],
                    "timestamp": datetime.now(),
                    "tag": info["tag"],
                    "season": info["season"],
                    "episode": info["episode"],
                    "file_id": doc["_id"],
                    "file_size": doc.get("file_size") or 0,
                    # This file's own Telegram cover/thumbnail, already saved at
                    # index time (COVERX) - reused as a last-resort poster if
                    # TMDB/IMDb have nothing for this title at all.
                    "cover": doc.get("cover"),
                }
                ops.append(UpdateOne(
                    {"_id": group_key},
                    {
                        "$setOnInsert": {
                            "title": base,
                            "year": year,
                            "status": "pending",
                            "tag": info["tag"],
                            "ott_platform": info["ott_platform"],
                        },
                        "$push": {"files": file_data},
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

        merged = await _merge_yearless(q)
        movies = await q.count_documents({})
        years = await q.aggregate([
            {"$group": {"_id": "$year", "n": {"$sum": 1}}},
            {"$sort": {"_id": -1}},
            {"$limit": 8},
        ]).to_list(8)
        year_lines = "\n".join(
            f"{y['_id'] if y['_id'] else 'No year'} : {y['n']}" for y in years
        )
        await _say(
            bot, chat_id,
            block([head("✅", "Scan finished")],
                  [row("📄", "Files read", seen), row("⚠️", "Unreadable", errors)],
                  [row("📦", "Movies / series queued", movies), row("🔗", "Merged (no-year duplicates)", merged)],
                  [f"📅 Top of the queue :\n{year_lines}"],
                  [tip(f"Test first: {cmd('/bulkpost start 20')}")]))
    except Exception as e:
        logger.exception("bulkpost scan failed")
        await _say(bot, chat_id, f"❌ Scan failed: {e}")


# --------------------------------------------------------------------------- post
async def _post_one(bot, qdoc, allow_noposter, chat_id=None, coll=None):
    """Returns one of: done, skipped, noposter, failed.
    chat_id / coll: another channel and its own record collection (used by /sendto and /autocycle);
    default = the archive channel and the /bulkpost records."""
    chat_id = chat_id or RESULT_ARCHIVE_CHANNEL
    group_key = qdoc["_id"]
    # Old queue entries (scanned before this change) won't have a "title"
    # field - fall back to the key itself, which was the plain title back then.
    title_base = qdoc.get("title") or group_key
    files = qdoc.get("files", [])
    mu = coll if coll is not None else _posts()

    existing = await mu.find_one({"_id": group_key})
    if existing and not existing.get("files"):
        await mu.delete_one({"_id": group_key})      # empty stub, replace it
        existing = None

    if existing:
        have = {f.get("file_id") for f in existing["files"]}
        new_files = [f for f in files if f["file_id"] not in have]
        if new_files:
            await mu.update_one({"_id": group_key}, {"$push": {"files": {"$each": new_files}}})
        if existing.get("message_id"):
            if new_files:
                await update_movie_message(bot, group_key, chat_id=chat_id, coll=mu, tenbit=True)
            return "skipped"
        msg = await send_movie_update(bot, group_key, chat_id=chat_id, coll=mu, tenbit=True)
        return "done" if msg else "failed"

    error_tmdb = False
    details = {}
    # First file's season tells us definitively whether this is a series
    # (matches the same detection channel.py already uses for live uploads) -
    # restricts TMDB search to the right media type and fetches the right
    # season's own poster/year instead of the show's overall one.
    season_num = next((f.get("season") for f in files if f.get("season") is not None), None)
    is_series_hint = (qdoc.get("tag") == "#SERIES") or (season_num is not None)
    try:
        if TMDB_POSTER:
            details = await get_movie_detailsx(title_base, season=season_num, is_series=is_series_hint)
            if not details or details.get("error") or (
                not details.get("poster_url") and not details.get("backdrop_url")
            ):
                error_tmdb = True
                details = await get_movie_details(title_base) or {}
        else:
            details = await get_movie_details(title_base) or {}
    except Exception:
        logger.exception("bulkpost: poster lookup failed for %s", title_base)
        error_tmdb = True
        details = {}

    # Posters always come out landscape now (fetch_image letterboxes a
    # portrait source onto a landscape canvas - see Imdbposter.py), so prefer
    # a real backdrop when TMDB has one since it's naturally landscape,
    # falling back to the portrait poster only if there's no backdrop at all.
    poster = (details.get("backdrop_url") if TMDB_POSTER and not error_tmdb else None) or details.get("poster_url")
    # No poster/backdrop anywhere -> fall back to one of these old files' own
    # saved cover/thumbnail instead of skipping/posting with no image.
    _own_cover = next((f.get("cover") for f in files if f.get("cover")), None)
    if (not ALLOW_PORTRAIT_POSTER and not LINK_PREVIEW and _own_cover
            and not (details.get("backdrop_url") and TMDB_POSTER and not error_tmdb)):
        poster = None   # portrait poster only -> use the old file's own landscape cover
    fallback_thumb = None if poster else _own_cover
    if not poster and not fallback_thumb and not allow_noposter:
        return "noposter"

    raw_genres = details.get("genres", "N/A")
    if isinstance(raw_genres, str):
        raw_genres = [g.strip() for g in raw_genres.split(",")]
    genres = ", ".join(g for g in raw_genres if g in STANDARD_GENRES) or "N/A"

    movie_doc = {
        "_id": group_key,
        "title": title_base,
        "files": files,
        "poster_url": poster,
        "genres": genres,
        "rating": details.get("rating", "N/A"),
        "imdb_url": details.get("url", "") if not TMDB_POSTER or error_tmdb else details.get("tmdb_url"),
        "year": details.get("year") or (str(qdoc["year"]) if qdoc.get("year") else None),
        "tag": qdoc.get("tag", "#MOVIE"),
        "ott_platform": qdoc.get("ott_platform", "N/A"),
        "message_id": None,
        "is_photo": False,
        "error_tmdb": error_tmdb,
        "is_backdrop": bool(details.get("backdrop_url")),
        "fallback_thumb_file_id": fallback_thumb,
    }
    try:
        await mu.insert_one(movie_doc)
    except DuplicateKeyError:
        return "skipped"

    msg = await send_movie_update(bot, group_key, chat_id=chat_id, coll=mu, tenbit=True)
    return "done" if msg else "failed"


async def _post_worker(bot, chat_id, limit, gap):
    q = _queue()
    counts = {"done": 0, "skipped": 0, "noposter": 0, "failed": 0}
    bid = bot.me.id
    await ensure_grouped(q, bid)     # show / franchise order (S01 -> S02 -> S03, Spider Man 1 -> 2 -> 3)
    allow_noposter = bool(await db.get_bot_setting(bid, NOPOSTER_KEY, False))
    reason = "queue finished"
    cancelled = False
    try:
        while True:
            if _state["stop"]:
                reason = "stopped by you"
                break
            if limit and counts["done"] >= limit:
                reason = f"limit of {limit} posts reached"
                break

            qdoc = await q.find_one({"status": "pending"}, sort=SORT)
            if not qdoc:
                break

            try:
                status = await _post_one(bot, qdoc, allow_noposter)
            except Exception:
                logger.exception("bulkpost: failed on %s", qdoc.get("_id"))
                status = "failed"

            await q.update_one(
                {"_id": qdoc["_id"]},
                {"$set": {"status": status, "updated_at": datetime.now()}},
            )
            counts[status] += 1

            if status == "done":
                if limit:     # remember how many are left, so a restart continues with the same limit
                    left = max(limit - counts["done"], 0)
                    await db.update_bot_setting(bid, K_LEFT, left)
                    if left == 0:
                        await db.update_bot_setting(bid, K_RUNNING, False)
                if counts["done"] % PROGRESS_EVERY == 0:
                    await _say(
                        bot, chat_id,
                        f"📤 {counts['done']} posted (now at year {qdoc.get('year') or 'No year'}) | "
                        f"no poster: {counts['noposter']} | failed: {counts['failed']}"
                    )
                await send_sticker(bot, RESULT_ARCHIVE_CHANNEL)   # sticker closes every finished post
                await asyncio.sleep(gap)
            elif status == "failed":
                await asyncio.sleep(1)
    except asyncio.CancelledError:
        cancelled = True          # bot is shutting down: keep the "running" flag so it resumes
        raise
    except Exception as e:
        logger.exception("bulkpost worker crashed")
        reason = f"error: {e}"
    finally:
        _state["stop"] = False
        if not cancelled:
            try:
                await db.update_bot_setting(bid, K_RUNNING, False)
            except Exception:
                pass
            await _say(bot, chat_id, block(
                [head("🏁", "Bulk Post ended"), f"📝 Reason : {reason}"],
                [row("✅", "Posted", counts["done"]), row("🔁", "Already posted / merged", counts["skipped"]),
                 row("🖼", "No poster (skipped)", counts["noposter"]), row("❌", "Failed", counts["failed"])]))


async def resume_bulkpost(bot):
    """Called once from bot.py after start-up: continue a /bulkpost job that was running before a restart.
    Posts that are already done are never posted again; the queue continues with the pending ones."""
    try:
        await asyncio.sleep(25)
        bid = bot.me.id
        if not await db.get_bot_setting(bid, K_RUNNING, False) or _running("post") or _running("scan"):
            return
        chat_id = await db.get_bot_setting(bid, K_NOTIFY, None)
        if not _archive_ready():
            return await _say(bot, chat_id, "⚠️ /bulkpost could not continue after the restart: RESULT_ARCHIVE_CHANNEL is not set.")
        try:
            await bot.get_chat(RESULT_ARCHIVE_CHANNEL)
        except Exception as e:
            return await _say(bot, chat_id, f"⚠️ /bulkpost could not continue after the restart: cannot reach the channel ({e}). "
                                            "Fix it and send /bulkpost start.")
        left = int(await db.get_bot_setting(bid, K_LEFT, 0) or 0)   # 0 = no limit
        gap = max(2, int(await db.get_bot_setting(bid, K_GAP, DEFAULT_GAP) or DEFAULT_GAP))
        _state["stop"] = False
        _state["post"] = asyncio.create_task(_post_worker(bot, chat_id, left, gap))
        await _say(bot, chat_id, head("♻️", "Bot restarted") + "\nBulk Post continues where it stopped.")
        logger.info("bulkpost resumed after restart")
    except Exception:
        logger.exception("bulkpost: resume failed")


# --------------------------------------------------------------------------- command
async def _status_text(bot=None):
    q = _queue()
    if bot is not None:
        await ensure_grouped(q, bot.me.id)
    c = {st: await q.count_documents({"status": st}) for st in ("pending", "done", "skipped", "noposter", "failed")}
    state = "🔎 scanning" if _running("scan") else ("▶️ posting" if _running("post") else "⏸ idle")
    top = [row("⏳", "Pending", c["pending"]), row("✅", "Done", c["done"]), row("🔁", "Skipped (already posted)", c["skipped"]),
           row("🖼", "No poster", c["noposter"]), row("❌", "Failed", c["failed"])]
    nxt = await q.find_one({"status": "pending"}, sort=SORT, projection={"year": 1})
    nxt_lines = [f"▶️ Next : <b>{html.escape(str(nxt['_id']))}</b> (year {nxt.get('year') or 'No year'})"] if nxt else []
    chan = [row("📢", "Archive channel", RESULT_ARCHIVE_CHANNEL if _archive_ready() else "NOT SET", code=_archive_ready())]
    return block([head("📊", "Bulk Post", state)], top, nxt_lines, chan)


@Client.on_message(filters.command("bulkpost") & filters.user(ADMINS))
async def bulkpost_cmd(bot: Client, message: Message):
    args = message.command[1:]
    sub = args[0].lower() if args else ""
    chat_id = message.chat.id
    q = _queue()
    bid = bot.me.id

    if sub == "scan":
        if _running("scan") or _running("post"):
            return await message.reply_text("Scan or posting is already running.")
        if await q.count_documents({}) > 0:
            return await message.reply_text(
                "A queue already exists. Use /bulkpost status to see it, or "
                "/bulkpost clear confirm and then scan again."
            )
        await message.reply_text("🔎 Scan started. Reading all saved files, this can take a few minutes...")
        _state["scan"] = asyncio.create_task(_scan(bot, chat_id))

    elif sub == "start":
        if _running("scan"):
            return await message.reply_text("Scan is still running, wait for it to finish.")
        if _running("post"):
            return await message.reply_text("Posting is already running. /bulkpost stop to stop it.")
        if not _archive_ready():
            return await message.reply_text(
                "❌ RESULT_ARCHIVE_CHANNEL is not set. Put your private channel id "
                "(like -1001234567890) in the environment variables and restart the bot.")
        try:
            chat = await bot.get_chat(RESULT_ARCHIVE_CHANNEL)
        except Exception as e:
            return await message.reply_text(
                f"❌ Cannot reach RESULT_ARCHIVE_CHANNEL ({RESULT_ARCHIVE_CHANNEL}): {e}\n"
                "Make the bot admin there (post permission) and send any message in that channel once.")
        if await q.count_documents({"status": "pending"}) == 0:
            return await message.reply_text("Nothing pending. Run /bulkpost scan first (or /bulkpost retry).")
        nums = [int(a) for a in args[1:] if a.isdigit()]
        limit = nums[0] if nums else 0
        gap = max(2, nums[1]) if len(nums) > 1 else DEFAULT_GAP
        _state["stop"] = False
        bid = bot.me.id
        await db.update_bot_setting(bid, K_RUNNING, True)
        await db.update_bot_setting(bid, K_NOTIFY, chat_id)
        await db.update_bot_setting(bid, K_LEFT, limit)
        await db.update_bot_setting(bid, K_GAP, gap)
        _state["post"] = asyncio.create_task(_post_worker(bot, chat_id, limit, gap))
        await message.reply_text(
            f"▶️ Posting started: {limit or 'all'} posts, {gap}s gap, one show / franchise after the other, newest first.\n"
            f"📂 Channel: {html.escape(str(chat.title or RESULT_ARCHIVE_CHANNEL))}\n"
            "/bulkpost stop to stop, /bulkpost status to check."
        )

    elif sub == "stop":
        if not _running("post"):
            return await message.reply_text("Posting is not running.")
        _state["stop"] = True
        await message.reply_text("⏹ Stopping after the current post...")

    elif sub == "status":
        await message.reply_text(await _status_text(bot))

    elif sub == "noposter":
        if len(args) < 2 or args[1].lower() not in ("on", "off"):
            cur = await db.get_bot_setting(bot.me.id, NOPOSTER_KEY, False)
            return await message.reply_text(
                f"Posts without poster: {'ALLOWED' if cur else 'SKIPPED'}\n"
                "Use /bulkpost noposter on  or  /bulkpost noposter off"
            )
        on = args[1].lower() == "on"
        await db.update_bot_setting(bot.me.id, NOPOSTER_KEY, on)
        await message.reply_text(
            "✅ Movies with no poster will now be posted as text." if on
            else "✅ Movies with no poster will be skipped."
        )

    elif sub == "regroup":
        if _running("post"):
            return await message.reply_text("Stop posting first, then send /bulkpost regroup.")
        n = await regroup(q, await get_aliases(bid))
        await message.reply_text(f"✅ {n} collections grouped again (all seasons of a show and all movies of a franchise together). "
                                 "Use /franchise to join movie names that do not start the same.")

    elif sub == "retry":
        if _running("post"):
            return await message.reply_text("Stop posting first.")
        res = await q.update_many(
            {"status": {"$in": ["failed", "noposter"]}},
            {"$set": {"status": "pending"}},
        )
        await message.reply_text(f"🔁 {res.modified_count} items moved back to pending.")

    elif sub == "clear":
        if _running("scan") or _running("post"):
            return await message.reply_text("Stop scan/posting first.")
        if len(args) < 2 or args[1].lower() != "confirm":
            return await message.reply_text("This deletes the whole queue. Send /bulkpost clear confirm to proceed.")
        await q.drop()
        await message.reply_text("🗑 Queue deleted. Already sent posts are untouched.")

    else:
        await message.reply_text(
            "Bulk post (old files -> one post per movie, all qualities, to RESULT_ARCHIVE_CHANNEL)\n\n"
            "/bulkpost scan\n"
            "/bulkpost start [count] [gap_seconds]\n"
            "/bulkpost stop\n"
            "/bulkpost status\n"
            "/bulkpost noposter on|off\n"
            "/bulkpost retry\n"
            "/bulkpost regroup\n"
            "/bulkpost clear confirm"
)
