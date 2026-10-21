"""Helper for the 2nd (post-card) bot.

A Telegram file_id works only for the bot it came from, so the helper bot cannot send the file_ids that are
saved in the database (they belong to the main bot). Fix: the main bot puts the file in BIN_CHANNEL once, the
helper bot reads that message (it must be admin there) and gets ITS OWN file_id. The result is cached
(memory + MongoDB), so every file goes through BIN_CHANNEL only once."""
import logging
from info import BIN_CHANNEL
from database.users_chats_db import db

logger = logging.getLogger(__name__)
_CACHE = {}


def is_post_bot(client) -> bool:
    from dreamxbotz.Bot import post_bot
    return post_bot is not None and client is post_bot


async def bot_file_id(client, file_id):
    """file_id valid for `client`. The main bot (or any other client) gets the same id back."""
    if not is_post_bot(client):
        return file_id
    if file_id in _CACHE:
        return _CACHE[file_id]
    col = db.db.posthelper_fileids
    try:
        doc = await col.find_one({"_id": file_id})
        if doc and doc.get("fid"):
            _CACHE[file_id] = doc["fid"]
            return doc["fid"]
    except Exception:
        logger.debug("posthelper cache read failed", exc_info=True)
    from dreamxbotz.Bot import dreamxbotz
    log_msg = await dreamxbotz.send_cached_media(chat_id=BIN_CHANNEL, file_id=file_id)
    msg = await client.get_messages(BIN_CHANNEL, log_msg.id)
    media = getattr(msg, msg.media.value) if msg and msg.media else None
    new_id = getattr(media, "file_id", None)
    if not new_id:
        raise RuntimeError("helper bot could not read the file from BIN_CHANNEL (is it admin there?)")
    _CACHE[file_id] = new_id
    try:
        await col.update_one({"_id": file_id}, {"$set": {"fid": new_id}}, upsert=True)
    except Exception:
        logger.debug("posthelper cache write failed", exc_info=True)
    return new_id
