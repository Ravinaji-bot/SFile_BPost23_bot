import asyncio
import os
import re
import logging
import aiofiles
import tempfile
import uuid
import requests
import html as _html
import aiohttp

from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from telegraph import Telegraph
from pymediainfo import MediaInfo

from database.ia_filterdb import get_file_details
from database.users_chats_db import db
from info import BIN_CHANNEL
from dreamxbotz.util.file_properties import get_name
from plugins.channel import extract_media_info
from plugins.Dreamxfutures.Imdbposter import _search_media_id, _tmdb_get, TMDB_BEARER_TOKEN
from info import TMDB_API_KEY

logger = logging.getLogger(__name__)

# Telegraph page settings (change here or via env)
TELEGRAPH_AUTHOR = os.environ.get("TELEGRAPH_AUTHOR", "@DragonFireWorld")
TELEGRAPH_AUTHOR_URL = os.environ.get("TELEGRAPH_AUTHOR_URL", "https://t.me/DragonFireWorld")
MAIN_GROUP_URL = os.environ.get("MAIN_GROUP_URL", "https://t.me/DragonFirePrime_bot")

# Telegraph init
TELEGRAPH_ACCESS_TOKEN = os.environ.get("TELEGRAPH_ACCESS_TOKEN")   # optional; without it a new Telegraph account is created automatically
if TELEGRAPH_ACCESS_TOKEN:
    telegraph = Telegraph(access_token=TELEGRAPH_ACCESS_TOKEN)
else:
    telegraph = Telegraph()
    try:
        telegraph.create_account(short_name="DreamxBotz")
        # Shown only when TELEGRAPH_ACCESS_TOKEN is NOT set. Copy it into the env var, then clear old logs.
        logger.warning("NEW TELEGRAPH TOKEN (put in env TELEGRAPH_ACCESS_TOKEN, keep private): %s", telegraph.get_access_token())
    except Exception:
        logger.exception("Failed to create Telegraph account")


def format_track(lang: str | None, title: str | None) -> str:
    lang = (lang or "").strip()
    title = (title or "").strip()

    if lang and lang.lower() != "und":
        return lang

    if title:
        return title

    return "und"


async def _translate_to_hindi(text: str) -> str | None:
    """English -> Hindi (free Google translate endpoint, no key). Returns None on failure."""
    if not text:
        return None
    try:
        timeout = aiohttp.ClientTimeout(total=8)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(
                "https://translate.googleapis.com/translate_a/single",
                params={"client": "gtx", "sl": "en", "tl": "hi", "dt": "t", "q": text[:4000]},
            ) as resp:
                resp.raise_for_status()
                data = await resp.json(content_type=None)
        out = "".join(part[0] for part in data[0] if part and part[0])
        return out.strip() or None
    except Exception as e:
        logger.info("Hindi translate failed: %s", e)
        return None


async def get_story_hindi(file_name: str, caption: str = "") -> dict | None:
    """Find the movie / series on TMDB and return its story in Hindi.
    Order: TMDB Hindi overview -> English overview translated to Hindi -> English overview.
    Returns {"title", "story", "is_hindi"} or None when nothing is found."""
    key = TMDB_API_KEY or None
    if not key and not TMDB_BEARER_TOKEN:
        return None
    try:
        info = extract_media_info(file_name, caption or "")
        is_series = info.get("tag") == "#SERIES"
        season = info.get("season")
        media_type, media_id = await _search_media_id(
            info["base_name"], api_key=key, file=file_name,
            is_series=is_series, season=season,
        )
        if not media_id:
            return None

        async def overview(lang):
            text = ""
            if media_type == "tv" and season:
                try:
                    sd = await _tmdb_get(f"tv/{media_id}/season/{season}", params={"language": lang}, api_key=key)
                    text = (sd.get("overview") or "").strip()
                except Exception:
                    text = ""
            main = await _tmdb_get(f"{media_type}/{media_id}", params={"language": lang}, api_key=key)
            return (text or (main.get("overview") or "").strip()), (main.get("title") or main.get("name") or "")

        story, title = await overview("hi-IN")
        if story:
            return {"title": title, "story": story, "is_hindi": True}

        story_en, title_en = await overview("en-US")
        if not story_en:
            return {"title": title_en, "story": "", "is_hindi": False}
        hindi = await _translate_to_hindi(story_en)
        if hindi:
            return {"title": title_en, "story": hindi, "is_hindi": True}
        return {"title": title_en, "story": story_en, "is_hindi": False}
    except Exception as e:
        logger.info("get_story_hindi failed for %r: %s", file_name, e)
        return None


_INFLIGHT = set()      # file_ids whose details are being made right now (a 2nd press just waits)
_DONE_TEXT = "📝 ᴠɪᴇᴡ ᴛʀᴀᴄᴋꜱ & ꜱᴛᴏʀʏ 📝"


def _swap_button(markup, data, new_button):
    """The same keyboard, with the button whose callback_data == data replaced by new_button."""
    rows = []
    for row in (getattr(markup, "inline_keyboard", None) or []):
        rows.append([new_button if b.callback_data == data else b for b in row])
    return InlineKeyboardMarkup(rows) if rows else None


async def _set_markup(query, markup):
    if markup is None:
        return
    try:
        await query.edit_message_reply_markup(reply_markup=markup)
    except Exception:
        pass


async def _restore(query, markup, text):
    """Error: a pop-up for the person who pressed (never a message in the chat / channel) and the
    original button comes back, so it can be pressed again."""
    try:
        await query.answer(text[:190], show_alert=True)
    except Exception:
        pass
    await _set_markup(query, markup)


@Client.on_callback_query(filters.regex(r"^wait_data$"), group=2)
async def wait_data_handler(client: Client, query: CallbackQuery):
    try:
        await query.answer("⏳ Details are being prepared, please wait a few seconds...", show_alert=False)
    except Exception:
        pass


@Client.on_callback_query(filters.regex(r"^extract_data"), group=2)
async def extract_data_handler(client: Client, query: CallbackQuery):
    data = query.data
    file_id = data.split(":", 1)[1] if ":" in data else ""
    original_markup = query.message.reply_markup if query.message else None
    if not file_id:
        return await query.answer("❌ Invalid button.", show_alert=True)

    # already made once (by anybody)? -> the link at once, nothing is built again
    cached = await db.get_extract_url(file_id)
    if cached:
        try:
            await query.answer("✅ Ready", show_alert=False)
        except Exception:
            pass
        await _set_markup(query, _swap_button(original_markup, data, InlineKeyboardButton(_DONE_TEXT, url=cached)))
        return

    if file_id in _INFLIGHT:
        try:
            await query.answer("⏳ Details are being prepared, please wait a few seconds...", show_alert=False)
        except Exception:
            pass
        return

    try:
        await query.answer("Fetching Details...", show_alert=False)
    except Exception:
        pass
    _INFLIGHT.add(file_id)

    await _set_markup(query, _swap_button(original_markup, data,
                                          InlineKeyboardButton("ᴘʟᴇᴀꜱᴇ ᴡᴀɪᴛ... ⏳", callback_data="wait_data")))

    temp_path = os.path.join(
        tempfile.gettempdir(),
        f"acc_{query.from_user.id}_{query.message.id}_{uuid.uuid4().hex}.tmp"
    )

    try:
        files_ = await get_file_details(file_id)
        m = query.message
        if m is not None and (m.video or m.document):
            log_msg = m                                    # the file itself carries the button (PM file, /ccopy file)
        elif files_:
            log_msg = await client.send_cached_media(chat_id=BIN_CHANNEL, file_id=file_id)   # a post card: read the file via BIN
        else:
            await _restore(query, original_markup, "❌ File not found.")
            return
        orig_caption = (getattr(files_[0], "caption", None) if files_ else None) or str(getattr(m, "caption", None) or "")

        file_name = get_name(log_msg) or next((ln.strip() for ln in orig_caption.splitlines() if ln.strip()), "") or "Movie"
        # Strip any "@ChannelName" tag from the start/anywhere in the filename before
        # it becomes the Telegraph page title (e.g. "@MoviesFlickerPathaan..." -> "Pathaan...").
        file_name_clean = re.sub(r'@\S+', '', file_name).strip()
        safe_title = (
            file_name_clean.replace(".", " ")
            .replace("_", " ")
            .replace("-", " ")
            .replace("[", "")
            .replace("]", "")
            .replace("(", "")
            .replace(")", "")
            .replace("mkv", "")
            .replace("mp4", "")
        )

        # story lookup runs in parallel with the mediainfo download below
        story_task = asyncio.create_task(
            get_story_hindi(file_name_clean, orig_caption)
        )

        media = getattr(log_msg, log_msg.media.value) if log_msg.media else None
        file_size = getattr(media, "file_size", 0) or 0
        chunk_limit = 5 if file_size > 200 * 1024 * 1024 else 4

        async with aiofiles.open(temp_path, "wb") as f:
            async for chunk in client.stream_media(log_msg, limit=chunk_limit):
                await f.write(chunk)

        lib_path = os.path.abspath("MediaInfo.dll") if os.path.exists("MediaInfo.dll") else None

        media_info = await asyncio.wait_for(
            asyncio.to_thread(MediaInfo.parse, temp_path, library_file=lib_path),
            timeout=6
        )

        audio_tracks = []
        subtitle_tracks = []
        video_info = []

        seen_audio = set()
        seen_subs = set()

        for track in media_info.tracks:
            ttype = (track.track_type or "").lower()

            if ttype == "video":
                codec = track.format or track.codec_id or "Unknown"
                width = track.width or "?"
                height = track.height or "?"
                video_info.append(f"Video: {codec} {width}x{height}")

            elif ttype == "audio":
                lang = (
                    track.other_language[0]
                    if getattr(track, "other_language", None)
                    else track.language or "und"
                )
                key = (lang, track.title)
                if key not in seen_audio:
                    seen_audio.add(key)
                    audio_tracks.append({
                        "language": lang,
                        "title": track.title
                    })

            elif ttype in ("text", "subtitle"):
                lang = (
                    track.other_language[0]
                    if getattr(track, "other_language", None)
                    else track.language or "und"
                )
                key = (lang, track.title)
                if key not in seen_subs:
                    seen_subs.add(key)
                    subtitle_tracks.append({
                        "language": lang,
                        "title": track.title
                    })

        try:
            story_data = await asyncio.wait_for(story_task, timeout=12)
        except Exception:
            story_data = None

        esc = _html.escape
        footer = f'<p><i><a href="{MAIN_GROUP_URL}">Join Main Group</a></i></p>'

        # ---- page 1: Audio track (video line first) ----
        audio_parts = ["<h3>🔊 Audio Track</h3>"]
        for v in video_info:
            audio_parts.append(f"<blockquote>• {esc(v)}</blockquote>")
        if audio_tracks:
            audio_parts.append(f"<p><b>Audio Tracks ({len(audio_tracks)}):</b></p>")
            for a in audio_tracks:
                audio_parts.append(f"<blockquote>• {esc(format_track(a['language'], a['title']))}</blockquote>")
        else:
            audio_parts.append("<p><b>Audio Tracks:</b> None</p>")
        audio_parts.append(footer)

        # ---- page 2: Subtitles track ----
        sub_parts = ["<h3>💬 Subtitles Track</h3>"]
        if subtitle_tracks:
            sub_parts.append(f"<p><b>Subtitle Tracks ({len(subtitle_tracks)}):</b></p>")
            for s_ in subtitle_tracks:
                sub_parts.append(f"<blockquote>• {esc(format_track(s_['language'], s_['title']))}</blockquote>")
        else:
            sub_parts.append("<p><b>Subtitle Tracks:</b> None</p>")
        sub_parts.append(footer)

        # ---- page 3: Story explain script (Hindi) ----
        story_parts = ["<h3>📖 Story Explain Script</h3>"]
        if story_data and story_data.get("story"):
            story_parts.append(f"<p>{esc(story_data['story'])}</p>")
            if not story_data.get("is_hindi"):
                story_parts.append("<p><i>(Hindi story available nahi thi, English story di gayi hai)</i></p>")
        else:
            story_parts.append("<p>इस फ़िल्म / सीरीज़ की कहानी अभी उपलब्ध नहीं है।</p>")
        story_parts.append(footer)

        async def _make_page(title, parts):
            if not telegraph.get_access_token():      # account creation failed at start-up -> try again now
                await asyncio.to_thread(telegraph.create_account, short_name="DreamxBotz")
            return await asyncio.to_thread(
                telegraph.create_page,
                title=title[:200],
                html_content="".join(parts),
                author_name=TELEGRAPH_AUTHOR,
                author_url=TELEGRAPH_AUTHOR_URL,
            )

        try:
            audio_res, sub_res, story_res = await asyncio.gather(
                _make_page(f"Audio Track - {safe_title}", audio_parts),
                _make_page(f"Subtitles Track - {safe_title}", sub_parts),
                _make_page(f"Story - {safe_title}", story_parts),
            )

            # ---- main page: index with 3 clickable lines + Join Main Group ----
            index_html = (
                "<p>👇 Available Tracks 👇</p>"
                f'<p>1 <a href="{audio_res["url"]}">Audio Track</a></p>'
                f'<p>2 <a href="{sub_res["url"]}">Subtitles Track</a></p>'
                f'<p>3 <a href="{story_res["url"]}">Story Explain Script</a></p>'
                f'<p><a href="{MAIN_GROUP_URL}">Join Main Group</a></p>'
            )
            response = await _make_page(safe_title, [index_html])
        except (requests.exceptions.ConnectionError, requests.exceptions.ReadTimeout):
            await _restore(query, original_markup, "⚠️ Telegraph is busy. Try again later.")
            return

        telegraph_url = response["url"]

        await db.save_extract_url(file_id, telegraph_url)      # next time (any user, any message): instant
        await _set_markup(query, _swap_button(original_markup, data, InlineKeyboardButton(_DONE_TEXT, url=telegraph_url)))

    except Exception as e:
        logger.exception(e)
        await _restore(query, original_markup, "❌ Could not read the details of this file. Please try again later.")

    finally:
        _INFLIGHT.discard(file_id)
        try:
            if not story_task.done():
                story_task.cancel()
        except NameError:
            pass
        if os.path.exists(temp_path):
            os.remove(temp_path)
