"""/bcdelete - the bot owner sets how long a BROADCAST stays in the users' PMs / groups.

Everything else the bot sends in a PM or a group is cleaned after AUTO_CLEAN_TIME (6 minutes,
see dreamxbotz/util/autoclean.py). Broadcasts are the only messages that stay, and they stay exactly
as long as the owner says here:

    /bcdelete            show the current time
    /bcdelete 30m        broadcasts are deleted 30 minutes after they were sent
    /bcdelete 2h | 1d    hours / days   (a plain number = minutes)
    /bcdelete off        broadcasts are never deleted

It counts from the moment each user / group received the message, and it applies to the
broadcasts sent AFTER the change.
"""
import re

from pyrogram import Client, filters

from dreamxbotz.util import autoclean
from info import OWNER_IDS

_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
MAX_SECONDS = 30 * 86400


def human(seconds):
    seconds = int(seconds or 0)
    if seconds <= 0:
        return "never (broadcasts stay)"
    for unit, size in (("day", 86400), ("hour", 3600), ("minute", 60)):
        if seconds >= size and seconds % size == 0:
            n = seconds // size
            return f"{n} {unit}{'s' if n != 1 else ''}"
    return f"{seconds} seconds"


def parse_time(text):
    """'30m' / '2h' / '1d' / '90s' / '45' (minutes) / 'off' -> seconds, None when it can not be read."""
    t = (text or "").strip().lower()
    if t in ("off", "0", "never", "no"):
        return 0
    m = re.fullmatch(r"(\d+)\s*([smhd]?)", t)
    if not m:
        return None
    return int(m.group(1)) * _UNITS[m.group(2) or "m"]


@Client.on_message(filters.private & filters.incoming, group=-100)
async def clean_user_pm(bot, message):
    """Runs first and lets every other handler continue: only notes the user's message for the clean-up."""
    try:
        autoclean.note_user_message(message)
    except Exception:
        pass


@Client.on_message(filters.command("bcdelete") & filters.user(OWNER_IDS))
async def bcdelete_cmd(bot, message):
    arg = message.command[1] if len(message.command) > 1 else ""
    if not arg:
        return await message.reply_text(
            f"📢 Broadcast auto delete: <b>{human(autoclean.broadcast_seconds())}</b>\n"
            "Other messages of the bot are deleted after 6 minutes.\n\n"
            "Change: <code>/bcdelete 30m</code>, <code>/bcdelete 2h</code>, <code>/bcdelete 1d</code> or "
            "<code>/bcdelete off</code>")
    sec = parse_time(arg)
    if sec is None or sec > MAX_SECONDS:
        return await message.reply_text("❌ Use for example <code>/bcdelete 30m</code>, <code>2h</code>, "
                                        "<code>1d</code> (up to 30 days) or <code>off</code>.")
    await autoclean.set_broadcast_seconds(bot.me.id, sec)
    await message.reply_text(f"✅ Broadcasts are now deleted after <b>{human(sec)}</b> (counted from the moment "
                             "each user / group receives it). It applies to the next broadcasts.")
