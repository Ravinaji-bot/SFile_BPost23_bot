"""/ram - owner only.
    /ram         memory status
    /ram clean   run a deep clean now and show before / after
"""
import asyncio
import threading
import time

from pyrogram import Client, filters

import memory_guard
from info import OWNER_IDS
from utils import temp


def _count(obj_path):
    try:
        mod, name = obj_path.rsplit(".", 1)
        return len(getattr(__import__(mod, fromlist=[name]), name))
    except Exception:
        return "?"


@Client.on_message(filters.command("ram") & filters.user(OWNER_IDS))
async def ram_cmd(client, message):
    note = ""
    if len(message.command) > 1 and message.command[1].lower() == "clean":
        before, after = memory_guard.cleanup(deep=True)
        note = f"🧹 Cleaned: <b>{before:.0f} MB → {after:.0f} MB</b>\n\n"
    rss = memory_guard.rss_mb()
    up = int(time.time() - memory_guard.STARTED_AT)
    try:
        from database.ia_filterdb import compile_regex
        rx = compile_regex.cache_info().currsize
    except Exception:
        rx = "?"
    text = (
        f"{note}<b>🧠 RAM</b>\n"
        f"Now: <b>{rss:.0f} MB</b> of {memory_guard.RAM_LIMIT_MB:.0f} MB "
        f"({(rss / memory_guard.RAM_LIMIT_MB * 100) if rss else 0:.0f}%)\n"
        f"Clean at: {memory_guard.SOFT_MB:.0f} MB · Auto-restart above: {memory_guard.HARD_MB:.0f} MB\n"
        f"Uptime: {up // 3600}h {up % 3600 // 60}m\n\n"
        f"<b>Caches</b>\n"
        f"results: {len(temp.GETALL)} · short: {len(temp.SHORT)} · imdb caps: {len(temp.IMDB_CAP)}\n"
        f"buttons: {_count('plugins.pmfilter.BUTTONS')} · queries: {_count('plugins.pmfilter.FRESH')}\n"
        f"regex: {rx} · title locks: {_count('plugins.channel.locks')}\n"
        f"asyncio tasks: {len(asyncio.all_tasks())} · threads: {threading.active_count()}"
    )
    await message.reply_text(text)
