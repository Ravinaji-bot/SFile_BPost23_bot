"""
Edit the caption of every delivered video/file DIRECTLY FROM THE BOT - no repo
edit, no redeploy.

Admin commands:
    /filecaption               - shows the caption currently in use
    /setfilecaption <text>     - sets a new caption (or reply to a message with it)
    /resetfilecaption          - goes back to the default (CUSTOM_FILE_CAPTION / Script.py)
    /langbox on|off            - the "🔊 #Hindi #English" language box under the caption (default OFF)
    /videocaption on|off       - /ccopy and /autoforward put this caption (name + VLC box + your text)
                                 under every video they copy (default ON)

Placeholders you can use in the caption:
    {file_name}     cleaned file name
    {file_size}     size, e.g. 1.4 GB
    {file_caption}  the file's original caption (may be empty)

You can type the caption with Telegram's own formatting (bold, italic, links,
quote) - it is saved as HTML. The value is stored in the existing
`bot_settings` collection, so it survives restarts and applies to:
  * files delivered in PM (single file, Send All, fallback)
  * files sent by the group settings path
  * autopost captions (unless AUTOPOST_CAPTION env is set)
"""
import html
import logging
from pyrogram import Client, filters, enums
from pyrogram.types import Message
from info import ADMINS, CUSTOM_FILE_CAPTION, strip_bad_prefixes
from database.users_chats_db import db
from utils import temp, get_size, get_languages_html, VLC_NOTE_HTML

logger = logging.getLogger(__name__)

_DB_KEY = "file_caption_override"
_CACHE = {"loaded": False, "value": ""}
_SAMPLE = {"file_name": "Movie Name 2024 1080p WEB-DL.mkv", "file_size": "1.4 GB", "file_caption": "sample"}
# a realistic LONG file name, used by /setfilecaption to check that the caption still fits 1024 characters
_LONG_NAME = ("Some Very Long Movie Title Part Two 2024 Season 01 Complete 1080p 10Bit WEB-DL x265 HEVC "
              "[Hindi ORG - Tamil - Telugu - English] ESub.mkv")
_LANG_BOX_MAX = "<blockquote>" + "🔊 #Hindi #English #Tamil #Telugu #Malayalam #Kannada" + "</blockquote>"


def _bot_id():
    return temp.ME if isinstance(temp.ME, int) else getattr(temp.ME, "id", temp.ME)


async def get_file_caption(default=None):
    """Returns the admin-set caption template, or `default` when none is set."""
    if not _CACHE["loaded"]:
        try:
            _CACHE["value"] = await db.get_bot_setting(_bot_id(), _DB_KEY, "") or ""
            _CACHE["loaded"] = True
        except Exception as e:
            logger.warning(f"file caption load failed: {e}")
            return default
    return _CACHE["value"] or default


def peek_file_caption(default=None):
    """Sync version for non-async code (uses the cache; filled after first delivery / command)."""
    return _CACHE["value"] or default


def render_caption(template, file_name, orig_caption, size_text, fill=None):
    """Fills a caption template. ONE place for every delivery path (PM, Send All, fallback, autopost, ccopy ...).
    * {file_name} is the pretty branded name, HTML-escaped. If the template already shows the brand tag itself
      (e.g. '{file_name}</a>➥ 𝑫𝒓𝒂𝒈𝒐𝒏𝑭𝒊𝒓𝒆𝑾𝒐𝒓𝒍𝒅') the bot does NOT add its own tag into the name -> the tag shows only once.
    * {file_caption} is HTML-escaped too.
    Returns None (never raises) when there is no template or it cannot be filled - the caller then sends the
    file with a plain caption, so a file is NEVER lost because of a caption."""
    if not template:
        return None
    from plugins.pretty_name import branded_name, clean_caption
    try:
        title = html.escape(branded_name(file_name or "", orig_caption, template, fill) or file_name or "")
        fcap = html.escape(clean_caption(strip_bad_prefixes(orig_caption or "") or "", template) or "")
        return template.format(file_name=title, file_size=size_text or "", file_caption=fcap)
    except Exception as e:
        logger.warning("caption template could not be filled (%s) - plain caption used", e)
        return None


async def _save(value: str):
    await db.update_bot_setting(_bot_id(), _DB_KEY, value)
    _CACHE["value"] = value
    _CACHE["loaded"] = True


@Client.on_message(filters.command("filecaption") & filters.user(ADMINS))
async def show_file_caption(bot: Client, message: Message):
    current = await get_file_caption()
    shown = current or CUSTOM_FILE_CAPTION or ""
    source = "✅ Custom (set from bot)" if current else "Default (info.py / Script.py)"
    await message.reply_text(
        f"<b>📝 File caption</b> — {source}\n\n"
        f"<code>{html.escape(shown)}</code>\n\n"
        "<b>Change:</b> <code>/setfilecaption your caption</code>\n"
        "(or reply to a message with <code>/setfilecaption</code>)\n"
        "<b>Placeholders:</b> <code>{file_name}</code> <code>{file_size}</code> <code>{file_caption}</code>\n"
        "<b>Reset:</b> <code>/resetfilecaption</code>",
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_message(filters.command("setfilecaption") & filters.user(ADMINS))
async def set_file_caption(bot: Client, message: Message):
    new = None
    if message.reply_to_message and (message.reply_to_message.text or message.reply_to_message.caption):
        r = message.reply_to_message
        new = (r.text.html if r.text else r.caption.html)
    elif len(message.command) > 1:
        parts = message.text.html.split(None, 1)
        new = parts[1] if len(parts) > 1 else None
    if not new or not new.strip():
        return await message.reply_text(
            "Usage: <code>/setfilecaption your caption</code>\n"
            "Example:\n<code>/setfilecaption {file_name}\n\n⚡ Join @YourChannel</code>\n\n"
            "Placeholders: <code>{file_name}</code> <code>{file_size}</code> <code>{file_caption}</code>",
            parse_mode=enums.ParseMode.HTML,
        )
    new = new.strip()
    from plugins.pretty_name import branded_name, template_has_brand
    try:
        # preview = exactly what a user gets (branded name, tag only once)
        title = html.escape(branded_name(_SAMPLE["file_name"], None, new))
        preview = new.format(file_name=title, file_size=_SAMPLE["file_size"], file_caption=_SAMPLE["file_caption"])
        # length check with a REALISTIC LONG name (not the short sample), because the box(es) are added under it
        long_title = html.escape(branded_name(_LONG_NAME, None, new))
        worst = new.format(file_name=long_title, file_size=_SAMPLE["file_size"], file_caption="")
    except Exception as e:
        return await message.reply_text(
            f"❌ Caption invalid: <code>{type(e).__name__}: {e}</code>\n"
            "Only use <code>{file_name}</code> <code>{file_size}</code> <code>{file_caption}</code> inside { }.",
            parse_mode=enums.ParseMode.HTML,
        )
    boxes = len(VLC_NOTE_HTML) + 2 + ((len(_LANG_BOX_MAX) + 2) if lang_box_on() else 0)
    if len(worst) + boxes > 1024:
        return await message.reply_text(
            f"❌ Caption too long: with a long file name ({len(_LONG_NAME)} characters) it becomes "
            f"{len(worst)} characters + {boxes} for the VLC / language box = {len(worst) + boxes} (Telegram max 1024). "
            "Please shorten your text."
        )
    await _save(new)
    note = ("\n\nℹ️ Your caption already shows the tag, so the bot does <b>not</b> add its own tag into the file name."
            if template_has_brand(new) else "")
    await message.reply_text(
        "✅ <b>File caption updated.</b> All new files will use it." + note + "\n\n<b>Preview:</b>\n\n" + preview,
        parse_mode=enums.ParseMode.HTML,
    )


@Client.on_message(filters.command("resetfilecaption") & filters.user(ADMINS))
async def reset_file_caption(bot: Client, message: Message):
    await _save("")
    await message.reply_text("✅ File caption reset to default.")


# --------------------------------------------------------------------------- switches + one caption for every video
# Under every video the bot sends / copies:  [file name + your /setfilecaption text]  ->  [⚠️ VLC box]
# and, only when /langbox is ON, the [🔊 #Hindi #English] box between them. The language box is
# OFF by default (it is not removed - /langbox on brings it back).
_K_LANG, _K_VCAP = "lang_box_on", "video_caption_on"
_SW = {"loaded": False, "lang": False, "vcap": True}


async def load_switches():
    if _SW["loaded"]:
        return
    try:
        _SW["lang"] = bool(await db.get_bot_setting(_bot_id(), _K_LANG, False))
        _SW["vcap"] = bool(await db.get_bot_setting(_bot_id(), _K_VCAP, True))
        _SW["loaded"] = True
    except Exception as e:
        logger.warning("caption switches load failed: %s", e)


def lang_box_on():
    """Sync (cached; filled by load_switches at start-up). Default OFF."""
    return _SW["lang"]


def video_caption_on():
    return _SW["vcap"]


def add_caption_boxes(caption, file_name, orig_caption, is_video=True):
    """Puts the [🔊 language] box (only when /langbox is ON) and the [⚠️ VLC] box under the first
    paragraph of `caption`. Returns `caption` unchanged when the result would pass Telegram's 1024 limit."""
    if not caption:
        return caption
    blocks = []
    if lang_box_on() and "🔊" not in caption:
        langs = get_languages_html(file_name, strip_bad_prefixes(orig_caption))
        if langs:
            blocks.append(langs)
    if is_video and "VLC" not in caption:
        blocks.append(VLC_NOTE_HTML)
    if not blocks:
        return caption
    extra = "\n\n".join(blocks)   # blank line keeps them as separate quote boxes
    first, sep, rest = caption.partition("\n\n")
    out = f"{first}\n\n{extra}\n\n{rest}" if sep else f"{first}\n\n{extra}"
    return out if len(out) <= 1024 else caption


def standard_caption(file_name, orig_caption, size_bytes, template=None, is_video=True, fill=None):
    """The caption every video gets: template ({file_name} pretty, {file_size}, {file_caption}) + boxes."""
    from plugins.pretty_name import branded_name
    name = file_name or ""
    tmpl = template or peek_file_caption(CUSTOM_FILE_CAPTION)
    cap = render_caption(tmpl, name, orig_caption, get_size(size_bytes or 0), fill)
    title = html.escape(branded_name(name, orig_caption, tmpl, fill) or name)
    if not cap:
        cap = f"<b>{title}</b>"
    cap = add_caption_boxes(cap, name, orig_caption, is_video)
    if len(cap) > 1024:
        cap = f"<b>{title[:900]}</b>"
    return cap


_VIDEO_EXT = (".mkv", ".mp4", ".avi", ".mov", ".webm", ".m4v", ".ts", ".wmv", ".flv")


async def caption_for_copy(message):
    """Caption for a video that /ccopy or /autoforward copies: the standard bot caption.
    None = leave the copy as it is (switch off, not a video, or anything went wrong)."""
    try:
        await load_switches()
        await get_file_caption()
        if not _SW["vcap"] or message is None:
            return None
        media = getattr(message, "video", None)
        if media is None:
            d = getattr(message, "document", None)
            if d is not None and (str(d.mime_type or "").lower().startswith("video/")
                                  or str(d.file_name or "").lower().endswith(_VIDEO_EXT)):
                media = d
        if media is None:
            return None
        name = media.file_name or ""
        orig = message.caption or ""
        if not name:   # no file name: the first line of the caption is the name
            name = next((ln.strip() for ln in orig.splitlines() if ln.strip()), "")
        if not name:
            return None
        return standard_caption(name, orig, media.file_size or 0)
    except Exception:
        logger.warning("standard video caption failed, the original caption stays", exc_info=True)
        return None


async def _switch_cmd(message, key, label, store, extra=""):
    on = None
    if len(message.command) > 1:
        a = message.command[1].lower()
        on = True if a in ("on", "yes", "true", "1") else False if a in ("off", "no", "false", "0") else None
    if on is None:
        return await message.reply_text(f"{label}: <b>{'ON' if _SW[store] else 'OFF'}</b>\nChange: <code>/{message.command[0]} on</code> or <code>off</code>{extra}",
                                        parse_mode=enums.ParseMode.HTML)
    await load_switches()
    await db.update_bot_setting(_bot_id(), key, on)
    _SW[store] = on
    await message.reply_text(f"✅ {label}: <b>{'ON' if on else 'OFF'}</b>", parse_mode=enums.ParseMode.HTML)


@Client.on_message(filters.command("langbox") & filters.user(ADMINS))
async def langbox_cmd(bot: Client, message: Message):
    await load_switches()
    await _switch_cmd(message, _K_LANG, "🔊 Language box", "lang")


@Client.on_message(filters.command("videocaption") & filters.user(ADMINS))
async def videocaption_cmd(bot: Client, message: Message):
    await load_switches()
    await _switch_cmd(message, _K_VCAP, "🎞 Standard video caption in /ccopy and /autoforward", "vcap",
                      "\nON = the copied videos get the bot caption (name + VLC box + your text). OFF = the old /ccopy caption on|off setting.")


@Client.on_message(filters.command("clearposter") & filters.user(ADMINS))
async def clear_poster_cmd(bot: Client, message: Message):
    """Wrong poster picked and remembered? This forgets all remembered poster lookups at once."""
    from utils import clear_poster_cache
    from dreamxbotz.util.master import clear_master_cache
    n = clear_poster_cache()
    m = clear_master_cache()
    await message.reply_text(f"✅ Poster cache cleared ({n} remembered lookups, {m} master files forgotten).")
