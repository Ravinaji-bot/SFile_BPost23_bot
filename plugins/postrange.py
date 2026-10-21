"""
Copy every post between a START number and an END number from one channel to another.

The "number" of a post is the last number of its link:
    https://t.me/c/1234567890/456   ->  post number 456   (channel id = -100 + 1234567890)
    https://t.me/mychannel/456      ->  post number 456

Commands (admin only):
    /postrange <from_channel_id> <to_channel_id> <start> <end> [delay]
                          - copy posts start..end (both included) one by one.
                            Example: /postrange -1001111111111 -1002222222222 100 450
    /postrange status     - progress
    /postrange stop       - stop (progress is saved)
    /postrange resume     - continue a stopped job
    /postrange clear confirm - forget the job (nothing is deleted in the channels)

Notes:
    * The bot must be ADMIN in both channels.
    * Posts are COPIED (no "Forwarded from" tag). Deleted numbers / service messages are skipped.
    * The daily time window of  /autopost schedule 10:00 22:00  (India time) is respected here
      too: outside the window the job waits and continues by itself.
    * Progress is saved after every post and a bot restart continues automatically.
    * Telegram allows roughly 20 posts a minute per channel (default delay = 3 seconds).
"""
import asyncio
import html
import logging
from datetime import datetime

from pyrogram import Client, filters
from pyrogram.errors import RPCError
from pyrogram.types import Message

from database.users_chats_db import db
from info import ADMINS
from dreamxbotz.util.style import head, row, block, tip, cmd, on_off, bar, LINE
from plugins.autopost import (
    _FATAL, _FatalChannelError, _err_id, _flood_safe, _get_window, _say, wait_for_window,
)

logger = logging.getLogger(__name__)

BATCH = 100                  # message numbers fetched per request
MAX_EMPTY_BATCHES = 10       # 10 x 100 empty numbers in a row = we are past the last post
DEFAULT_DELAY = 3.0
PROGRESS_EVERY = 100         # progress message every N copied posts

_state = {"task": None, "stop": False}


def _jobs():
    return db.db.postrange_job


def _running():
    t = _state.get("task")
    return bool(t and not t.done())


async def _finish_text(job, reason):
    return block([head("🏁", "Post Range ended"), f"📝 Reason : {reason}"],
                 [row("✅", "Copied", job.get("copied", 0)), row("⏭", "Skipped (deleted / service)", job.get("skipped", 0)),
                  row("❌", "Failed", job.get("failed", 0))],
                 [row("▶️", "Next number", f"{job.get('next')} of {job.get('end')}")])


# --------------------------------------------------------------------------- worker
async def _worker(bot):
    col = _jobs()
    bid = bot.me.id
    job = await col.find_one({"_id": "job"})
    if not job:
        return
    chat_id = job.get("notify")
    src, dst, end, delay = job["src"], job["dst"], job["end"], max(1.0, float(job.get("delay", DEFAULT_DELAY)))
    reason, status, cancelled = "finished", "finished", False
    empty_batches = 0
    last_real = None
    stopped = False
    try:
        while not stopped:
            job = await col.find_one({"_id": "job"})
            nxt = job["next"]
            if nxt > end:
                break
            ids = list(range(nxt, min(nxt + BATCH, end + 1)))
            try:
                msgs = await _flood_safe(lambda: bot.get_messages(src, ids))
            except RPCError as e:
                if any(x in _err_id(e) for x in _FATAL):
                    raise _FatalChannelError(_err_id(e))
                raise
            if not isinstance(msgs, list):
                msgs = [msgs]

            real = [(mid, m) for mid, m in zip(ids, msgs) if m is not None and not getattr(m, "empty", False)]
            empty_batches = 0 if real else empty_batches + 1
            if real:
                last_real = real[-1][0]

            for mid, m in zip(ids, msgs):
                if _state["stop"] or not await wait_for_window(bid, lambda: _state["stop"]):
                    stopped = True
                    break
                inc = {}
                skip = m is None or getattr(m, "empty", False) or getattr(m, "service", None)
                if skip:
                    inc["skipped"] = 1
                else:
                    try:
                        await _flood_safe(lambda: bot.copy_message(chat_id=dst, from_chat_id=src, message_id=mid))
                        inc["copied"] = 1
                    except RPCError as e:
                        if any(x in _err_id(e) for x in _FATAL):
                            raise _FatalChannelError(_err_id(e))
                        logger.warning("postrange: post %s not copied: %s", mid, _err_id(e))
                        inc["failed"] = 1
                    except Exception as e:
                        logger.warning("postrange: post %s not copied: %s", mid, e)
                        inc["failed"] = 1
                await col.update_one({"_id": "job"}, {"$set": {"next": mid + 1}, "$inc": inc})
                if inc.get("copied"):
                    cur = await col.find_one({"_id": "job"}, {"copied": 1})
                    if cur and cur.get("copied", 0) % PROGRESS_EVERY == 0:
                        await _say(bot, chat_id, f"📤 {cur['copied']} posts copied (now at number {mid})")
                    await asyncio.sleep(delay)

            if stopped:
                break
            if empty_batches >= MAX_EMPTY_BATCHES:
                reason = f"no more posts after number {last_real if last_real else job['start']}"
                await col.update_one({"_id": "job"}, {"$set": {"next": end + 1}})
                break
        if stopped:
            reason, status = "stopped by you", "stopped"
    except _FatalChannelError as e:
        reason, status = f"cannot use the channel ({html.escape(str(e))}). Make the bot ADMIN in both channels.", "error"
    except asyncio.CancelledError:
        cancelled = True          # bot shutting down: keep status "running" so it resumes
        raise
    except Exception as e:
        logger.exception("postrange worker crashed")
        reason, status = f"error: {html.escape(str(e))}", "error"
    finally:
        _state["stop"] = False
        if not cancelled:
            await col.update_one({"_id": "job"}, {"$set": {"status": status, "updated_at": datetime.now()}})
            final = await col.find_one({"_id": "job"}) or {}
            await _say(bot, chat_id, await _finish_text(final, reason))


async def resume_postrange(bot):
    """Called once from bot.py after start-up: continue a job that was running before a restart."""
    try:
        await asyncio.sleep(20)
        job = await _jobs().find_one({"_id": "job"})
        if not job or job.get("status") != "running" or _running():
            return
        _state["stop"] = False
        _state["task"] = asyncio.create_task(_worker(bot))
        await _say(bot, job.get("notify"), head("♻️", "Bot restarted") + "\nPost Range continues where it stopped.")
        logger.info("postrange resumed after restart")
    except Exception:
        logger.exception("postrange: resume failed")


# --------------------------------------------------------------------------- command
async def _status_text(bot):
    job = await _jobs().find_one({"_id": "job"})
    if not job:
        return head("📊", "Post Range") + "\nNo post-range job yet."
    total = job["end"] - job["start"] + 1
    done = min(max(job["next"] - job["start"], 0), total)
    win = await _get_window(bot.me.id, fresh=True)
    sched = (f"{win[0] // 60:02d}:{win[0] % 60:02d} - {win[1] // 60:02d}:{win[1] % 60:02d} (India time)") if win else "OFF (any time)"
    state = ("▶️ running" if _running() else "⏸ " + str(job["status"]))
    return block(
        [head("📊", "Post Range", state)],
        [row("📥", "From", job["src"], code=True), row("📤", "To", job["dst"], code=True),
         row("🔢", "Numbers", f"{job['start']} to {job['end']}"), row("▶️", "Next", job["next"])],
        [f"{bar(done, total)}   {done}/{total}"],
        [row("✅", "Copied", job.get("copied", 0)), row("⏭", "Skipped", job.get("skipped", 0)), row("❌", "Failed", job.get("failed", 0))],
        [row("⏱", "Delay", f"{job.get('delay', DEFAULT_DELAY)}s") + "    " + row("🕒", "Schedule", sched)])


HELP = ("Copy posts start..end from one channel to another\n\n"
        "/postrange [from_channel_id] [to_channel_id] [start] [end] [delay_seconds]\n"
        "Example: /postrange -1001111111111 -1002222222222 100 450\n\n"
        "The number of a post = last number of its link (t.me/c/123456/450 -> 450).\n"
        "/postrange status | stop | resume | clear confirm\n"
        "Time window: /autopost schedule 10:00 22:00  (India time, off = any time)")


@Client.on_message(filters.command("postrange") & filters.user(ADMINS))
async def postrange_cmd(bot: Client, message: Message):
    args = message.command[1:]
    sub = args[0].lower() if args else ""
    chat_id = message.chat.id
    col = _jobs()

    if not args:
        return await message.reply_text(HELP + "\n\n" + await _status_text(bot))

    if sub == "status":
        return await message.reply_text(await _status_text(bot))

    if sub == "stop":
        if not _running():
            return await message.reply_text("No post-range job is running.")
        _state["stop"] = True
        return await message.reply_text("⏹ Stopping after the current post... (progress is saved)")

    if sub == "resume":
        job = await col.find_one({"_id": "job"})
        if not job:
            return await message.reply_text("No job to resume.")
        if _running():
            return await message.reply_text("The job is already running.")
        if job["next"] > job["end"]:
            return await message.reply_text("That job is already finished.")
        await col.update_one({"_id": "job"}, {"$set": {"status": "running", "notify": chat_id}})
        _state["stop"] = False
        _state["task"] = asyncio.create_task(_worker(bot))
        return await message.reply_text(f"▶️ Continuing from number {job['next']}.")

    if sub == "clear":
        if _running():
            return await message.reply_text("Stop the job first.")
        if len(args) < 2 or args[1].lower() != "confirm":
            return await message.reply_text("Send /postrange clear confirm to forget the job.")
        await col.delete_one({"_id": "job"})
        return await message.reply_text("🗑 Job forgotten. Nothing was deleted in the channels.")

    # ---- new job:  /postrange <from> <to> <start> <end> [delay]
    if len(args) < 4:
        return await message.reply_text(HELP)
    try:
        src, dst, start, end = (int(a) for a in args[:4])
    except ValueError:
        return await message.reply_text("All four values must be numbers.\n\n" + HELP)
    if start < 1 or end < start:
        return await message.reply_text("START must be 1 or more and END must not be smaller than START.")
    if src == dst:
        return await message.reply_text("From-channel and to-channel must be different.")
    if _running():
        return await message.reply_text("A post-range job is already running. /postrange stop first.")
    try:
        delay = max(1.0, float(args[4])) if len(args) > 4 else DEFAULT_DELAY
    except ValueError:
        delay = DEFAULT_DELAY

    titles = []
    for label, ch in (("From", src), ("To", dst)):
        try:
            chat = await bot.get_chat(ch)
            titles.append(f"{label}: {html.escape(str(chat.title))}")
        except Exception as e:
            return await message.reply_text(f"❌ Bot can not open the {label.lower()}-channel {ch}: {html.escape(str(e))}\n"
                                            "Add the bot as ADMIN there first.")

    await col.replace_one({"_id": "job"}, {
        "_id": "job", "src": src, "dst": dst, "start": start, "end": end, "next": start,
        "copied": 0, "skipped": 0, "failed": 0, "delay": delay, "status": "running",
        "notify": chat_id, "created": datetime.now(),
    }, upsert=True)
    _state["stop"] = False
    _state["task"] = asyncio.create_task(_worker(bot))
    win = await _get_window(bot.me.id, fresh=True)
    await message.reply_text(
        f"▶️ Post range started\n{titles[0]}\n{titles[1]}\nNumbers {start} to {end} ({end - start + 1} numbers), "
        f"{delay}s gap.\n" + ("🕒 A time window is set, posts go out only inside it.\n" if win else "")
        + "/postrange status  |  /postrange stop")
