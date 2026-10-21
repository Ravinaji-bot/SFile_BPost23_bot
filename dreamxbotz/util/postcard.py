"""Poster-style "post card" (same look as the channel auto-post) that can also be
sent right above the normal search result when a user searches in the bot PM."""
import asyncio
import logging
from dreamxbotz.util.autoclean import keep
from pyrogram import enums
from pyrogram.errors import FloodWait
from pyrogram.types import LinkPreviewOptions
from info import TMDB_POSTER, RESULT_ARCHIVE_CHANNEL, POST_ARCHIVE_BEFORE, POSTCARD_LANDSCAPE, POSTCARD_PREVIEW
from utils import find_poster, temp
from plugins.channel import extract_media_info, build_post_caption, build_post_buttons, get_post_format, preview_url

logger = logging.getLogger(__name__)


async def _send_post_poster_below(client, chat_id, poster, caption, post_btn, spoiler=False):
    """Send the post with the TEXT on top and the POSTER below it.
    Returns the sent message, or None if nothing could be sent with a poster."""
    if not poster:
        return None
    # Channel-post style: poster shown as a link preview BELOW the text
    if POSTCARD_PREVIEW and isinstance(poster, str) and poster.startswith("http"):
        try:
            return await client.send_message(
                chat_id=chat_id,
                text=f"<a href='{preview_url(poster)}'>&#8205;</a>{caption}",
                reply_markup=post_btn,
                parse_mode=enums.ParseMode.HTML,
                link_preview_options=LinkPreviewOptions(is_disabled=False, show_above_text=False),
            )
        except Exception as e:
            logger.warning("postcard: link-preview send failed, trying photo: %s", e)
    common = dict(chat_id=chat_id, photo=poster, caption=caption, reply_markup=post_btn,
                  parse_mode=enums.ParseMode.HTML, has_spoiler=spoiler)
    try:
        # caption above the photo == poster shown below the text
        return await client.send_photo(show_caption_above_media=True, **common)
    except TypeError:
        # library too old to know show_caption_above_media -> normal photo post
        try:
            return await client.send_photo(**common)
        except Exception as e:
            logger.warning("postcard: send_photo failed: %s", e)
    except Exception as e:
        logger.warning("postcard: send_photo(caption above) failed: %s", e)
    # Caption too long (>1024) etc.: text message with the poster as a preview shown BELOW the text
    if isinstance(poster, str) and poster.startswith("http"):
        try:
            return await client.send_message(
                chat_id=chat_id,
                text=f"<a href='{preview_url(poster)}'>&#8205;</a>{caption}",
                reply_markup=post_btn,
                parse_mode=enums.ParseMode.HTML,
                link_preview_options=LinkPreviewOptions(is_disabled=False, show_above_text=False),
            )
        except Exception as e:
            logger.warning("postcard: link-preview fallback failed: %s", e)
    return None


async def send_search_postcard(client, chat_id, files, tenbit=False):
    """Builds the post card (poster + title/audio/genres/quality box + download links)
    from the search result `files` and sends it to `chat_id`.
    Returns the sent message, or None when it could not be built/sent (never raises)."""
    try:
        movie_files, base_name = [], None
        for f in files:
            info = extract_media_info(f.file_name, getattr(f, "caption", None) or "")
            if base_name is None:
                base_name = info["base_name"]
            movie_files.append({
                "quality": info["quality"],
                "language": info["language"],
                "ott_platform": info["ott_platform"],
                "tag": info["tag"],
                "season": info["season"],
                "episode": info["episode"],
                "filename": f.file_name,
                "file_id": f.file_id,
                "file_size": f.file_size,
            })
        if not movie_files:
            return None
        base_name = base_name or "Unknown"
        imdb_data = None
        try:
            first_name = movie_files[0]["filename"]
            imdb_data = await find_poster(base_name, file=first_name)
        except Exception:
            imdb_data = None
        imdb_data = imdb_data or {}
        movie_doc = {
            "files": movie_files,
            "genres": imdb_data.get("genres") or "N/A",
            "year": imdb_data.get("year"),
        }
        fmt = await get_post_format(temp.ME)
        caption = build_post_caption(movie_doc, base_name, fmt, tenbit)
        post_btn = build_post_buttons(fmt, movie_doc, tenbit)
        sent = await _send_post_poster_below(
                        client, chat_id, (imdb_data.get("backdrop") if POSTCARD_LANDSCAPE else None) or imdb_data.get("poster"), caption, post_btn, fmt.get("spoiler", False))
        if sent is None:
            sent = await client.send_message(
                chat_id=chat_id, text=caption, reply_markup=post_btn,
                link_preview_options=LinkPreviewOptions(is_disabled=True),
                parse_mode=enums.ParseMode.HTML)
        return sent
    except Exception as e:
        logger.warning("send_search_postcard failed: %s", e)
        return None


_warned_no_db = False


async def archive_post(client, msg):
    """Saves a copy of the post (poster + caption + buttons) in the private RESULT_ARCHIVE_CHANNEL.
    Returns True when saved. Never raises."""
    global _warned_no_db
    if not msg:
        return False
    if RESULT_ARCHIVE_CHANNEL > -1000:  # unset (0 / default -100) or not a channel id
        if not _warned_no_db:
            _warned_no_db = True
            logger.warning("Post archive: RESULT_ARCHIVE_CHANNEL is not set, posts are NOT being saved")
        return False
    for attempt in range(2):
        try:
            await msg.copy(chat_id=RESULT_ARCHIVE_CHANNEL)
            return True
        except FloodWait as e:
            await asyncio.sleep(e.value + 1)
        except Exception as e:
            logger.warning("Post archive: could not save post to %s (%s). Is the bot admin there?",
                           RESULT_ARCHIVE_CHANNEL, e)
            return False
    return False


async def wait_then_archive(client, msg, delete_after, archive_before=None):
    """Sleeps until `archive_before` seconds before the deletion time, saves the post to the
    RESULT_ARCHIVE_CHANNEL, then sleeps the rest so the total wait is exactly `delete_after` seconds."""
    archive_before = POST_ARCHIVE_BEFORE if archive_before is None else archive_before
    keep(msg, delete_after)     # auto clean: a timer longer than 6 minutes stays as it is
    loop = asyncio.get_running_loop()
    start = loop.time()
    await asyncio.sleep(max(delete_after - archive_before, 0))
    await archive_post(client, msg)
    await asyncio.sleep(max(delete_after - (loop.time() - start), 0))


async def archive_and_delete_post(client, msg, delete_after, archive_before=None):
    """Post life cycle: save to RESULT_ARCHIVE_CHANNEL `archive_before` seconds before the end,
    then delete the post at `delete_after` seconds."""
    await wait_then_archive(client, msg, delete_after, archive_before)
    try:
        await msg.delete()
    except Exception:
        pass
