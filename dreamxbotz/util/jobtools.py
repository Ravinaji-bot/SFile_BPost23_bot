"""Helpers shared by the background jobs (/autopost, /bulkpost, /ccopy, /postrange, /autoforward).

 * send_notice(): the progress / finished / warning messages of the jobs go to the STATUS CHANNEL
   (private channel set with /statuschannel) instead of the bot PM. Without a status channel they
   come to the admin PM as before.
 * send_sticker(): the sticker that closes a finished post / collection. One sticker for all jobs:
   the /ccopy sticker if there is one (/ccopy sticker off = none), else the /autopost sticker,
   else AUTOPOST_STICKER from the env.
"""
import asyncio
import logging
import os

from pyrogram.errors import FloodWait
from pyrogram.types import InlineKeyboardMarkup

from database.users_chats_db import db

logger = logging.getLogger(__name__)

K_STATUS = "STATUS_CHANNEL"
_cache = {}


async def status_channel(bot_id):
    if "sc" not in _cache:
        _cache["sc"] = await db.get_bot_setting(bot_id, K_STATUS, None)
    return _cache["sc"]


async def set_status_channel(bot_id, chat_id):
    await db.update_bot_setting(bot_id, K_STATUS, chat_id)
    _cache["sc"] = chat_id


async def send_notice(bot, chat_id, text):
    """Job message -> status channel when one is set (falls back to `chat_id`, the admin PM, if the
    channel can not be reached), else -> `chat_id`."""
    try:
        target = await status_channel(bot.me.id)
    except Exception:
        target = None
    for dest in dict.fromkeys(d for d in (target, chat_id) if d):
        try:
            await bot.send_message(dest, text)
            return True
        except Exception:
            logger.exception("job message could not be sent to %s", dest)
    return False


async def get_sticker(bot_id):
    own = await db.get_bot_setting(bot_id, "CC_STICKER", None)
    if own == "off":
        return None
    return own or await db.get_bot_setting(bot_id, "AP_STICKER", None) or os.environ.get("AUTOPOST_STICKER") or None


async def send_sticker(bot, chat_id):
    """Sends the shared sticker to `chat_id`. Never raises; returns True when it was sent."""
    if not chat_id:
        return False
    try:
        sticker = await get_sticker(bot.me.id)
    except Exception:
        return False
    if not sticker:
        return False
    for _ in range(3):
        try:
            await bot.send_sticker(chat_id=chat_id, sticker=sticker)
            return True
        except FloodWait as e:
            await asyncio.sleep(e.value + 1)
        except Exception as e:
            logger.warning("sticker not sent to %s: %s", chat_id, e)
            return False
    return False


def extract_markup(msg):
    """InlineKeyboardMarkup with the 'View Audio, Subs & Story' button for a video / document message
    (the last file of a collection, just above the sticker). None when it cannot be made."""
    try:
        from database.ia_filterdb import unpack_new_file_id
        from utils import extract_button
        media = getattr(msg, "video", None) or getattr(msg, "document", None)
        if media is None:
            return None
        fid, _ref = unpack_new_file_id(media.file_id)
        btn = extract_button(fid)
        return InlineKeyboardMarkup([[btn]]) if btn else None
    except Exception:
        return None

