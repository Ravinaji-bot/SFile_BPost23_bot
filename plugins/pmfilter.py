import html as _html
import logging
import string
from utils import spawn_delete
from dreamxbotz.util.autoclean import keep
from plugins.pretty_name import display_name
from dreamxbotz.util.postcard import send_search_postcard, wait_then_archive
from utils import get_random_mix_id, get_size, is_subscribed, is_req_subscribed, group_setting_buttons, get_poster, get_posterx, temp, get_settings, save_group_settings, get_cap, imdb, is_check_admin, extract_request_content, log_error, clean_filename, generate_season_variations, clean_search_text, get_settings_text, fsub_gate, get_fsub_buttons
from utils import fsub_gate, get_fsub_buttons
from rapidfuzz import process
from utils import strip_cap_header, BoundedDict
from dreamxbotz.util.file_properties import get_name, get_hash
from urllib.parse import quote_plus

from database.ia_filterdb import Media, Media2, get_search_results, get_bad_files
from database.config_db import mdb
from pyrogram.errors import MessageIdInvalid, UserIsBlocked, MessageNotModified, PeerIdInvalid, MessageDeleteForbidden
from pyrogram import Client, filters, enums
from pyrogram.types import LinkPreviewOptions, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, InputMediaPhoto
from info import (
    PM_POST_FIRST, OWNER_IDS,
    ADMINS, AUTH_CHANNELS, AUTH_REQ_CHANNELS, BIN_CHANNEL, DELETE_TIME, RESULT_ARCHIVE_CHANNEL,
    EMOJI_MODE, GRP_LNK, LANDSCAPE_POSTER, LANGUAGES, LOG_CHANNEL, MAX_B_TN, MSG_ALRT,
    MULTIPLE_DB, NO_RESULTS_MSG, OWNER_LNK, OWNER_UPI_ID, PICS, PICS_URL, QR_CODE, QUALITIES,
    REACTIONS, REQST_CHANNEL, SEASONS, SUBSCRIPTION, SUPPORT_CHAT_ID,
    TMDB_ON_SEARCH, TMDB_POSTER, ULTRA_FAST_MODE, UPDATE_CHNL_LNK, URL, USER_MSG_DELETE_TIME
)
from Script import script
from pyrogram.errors.exceptions.bad_request_400 import MediaEmpty, PhotoInvalidDimensions, WebpageMediaEmpty
from database.refer import referdb
from database.users_chats_db import db
import asyncio
import re
import math
import random
import pytz
from datetime import datetime, timedelta
lock = asyncio.Lock()

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------------------------
# SAFE IMDb TEMPLATE RENDERING
# The group template (/set_template) is written by ANY group admin. It used to be filled with
# TEMPLATE.format(..., **locals()), so a template like "{client.bot_token}" or
# "{message._client.api_hash}" printed the bot's secrets into the group. Now only plain fields
# (no attribute / index access) are allowed, plus the one default-template field
# {message.from_user.mention}.
# --------------------------------------------------------------------------------------------
_ALLOWED_DOTTED_FIELDS = {"message.from_user.mention"}


class _SafeTemplateFormatter(string.Formatter):
    def __init__(self, user_mention):
        super().__init__()
        self._user_mention = user_mention

    def get_field(self, field_name, args, kwargs):
        if field_name == "message.from_user.mention":
            return self._user_mention, field_name
        if "." in field_name or "[" in field_name:
            raise ValueError(f"field {field_name!r} is not allowed in a template")
        return super().get_field(field_name, args, kwargs)


def find_unsafe_template_field(template):
    """First forbidden / malformed field of a template, or None when it is fine."""
    try:
        for _lit, field, spec, _conv in string.Formatter().parse(template or ""):
            if field is None:
                continue
            if field not in _ALLOWED_DOTTED_FIELDS and ("." in field or "[" in field):
                return field
            if spec:
                bad = find_unsafe_template_field(spec)
                if bad:
                    return bad
    except ValueError as e:
        return f"invalid braces ({e})"
    return None


def render_imdb_template(template, user_mention, **fields):
    """Fills the IMDb template with plain values only. A bad / unsafe group template falls back to the default one."""
    fields.setdefault("user_mention", user_mention)   # {user_mention} stays usable in custom templates
    last_err = None
    for tpl in (template, script.IMDB_TEMPLATE_TXT):
        try:
            return _SafeTemplateFormatter(user_mention).vformat(tpl or "", (), fields)
        except Exception as e:
            last_err = e
            logger.warning("IMDb template could not be filled (%s) - default template used", e)
    raise last_err


TIMEZONE = "Asia/Kolkata"
BUTTON = {}
BUTTONS = BoundedDict(2000)
FRESH = BoundedDict(2000)
# True = group me koi bhi user kisi ke bhi search result ke buttons/links khol sakta hai ("not your request" alert band)
ANYONE_CAN_CLICK = True

BUTTONS0 = {}
BUTTONS1 = {}
BUTTONS2 = {}
SPELL_CHECK = {}

import os as _os
CARD_SEARCH_LIMIT = int(_os.environ.get("CARD_SEARCH_LIMIT", "400"))    # how many matching files are looked at when the result card groups them by movie
CARD_MAX_MOVIE_BTNS = min(int(_os.environ.get("CARD_MAX_MOVIE_BTNS", "50")), 90)  # at most this many movie buttons on one card (Telegram allows 100 buttons per message)


_BG_TASKS = set()   # keeps background tasks alive until they finish


def _title_key(t):
    """Same movie, different spelling -> same key: 'Spider-Man' = 'Spiderman' = 'Spider Man', 'The Amazing X' = 'Amazing X'."""
    t = re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()
    t = re.sub(r"^the\s+", "", t)
    return t.replace(" ", "")


def _group_files_by_movie(files):
    """Groups search-result files into movies: same title + year (+ season for series).
    Inside one title+year, files whose audio languages never overlap are split into separate movies
    (e.g. Drishyam 2021 Malayalam vs Drishyam 2021 Telugu). Returns a list of dicts:
    {"title", "year", "season", "langs", "files"} sorted by year (oldest first)."""
    from plugins.channel import extract_media_info
    by_base = {}
    for f in files:
        try:
            info = extract_media_info(f.file_name, getattr(f, "caption", None) or "")
        except Exception:
            info = None
        if not info:
            title, year, season, langs = display_name(f.file_name, getattr(f, "caption", None)), None, None, set()
        else:
            title = (info.get("base_name") or "").strip()
            m = re.search(r"\b((?:19|20)\d{2})$", title)
            year = m.group(1) if m else None
            if m:
                title = title[:m.start()].strip()
            season = info.get("season") if info.get("tag") == "#SERIES" else None
            langs = {x.strip() for x in str(info.get("language") or "").split(",") if x.strip() and x.strip() != "N/A"}
        k = (_title_key(title), year, season)
        by_base.setdefault(k, {"title": title or "Unknown", "year": year, "season": season, "items": []})["items"].append((f, langs))

    # files whose name has NO year join the title when exactly ONE year exists for it
    # (Dhoom 720p + Dhoom 2004 1080p -> one movie). With 2+ years it is unclear which one they are, so they stay separate.
    for k in [k for k in by_base if k[1] is None]:
        years = [k2 for k2 in by_base if k2[0] == k[0] and k2[2] == k[2] and k2[1] is not None]
        if len(years) == 1:
            by_base[years[0]]["items"].extend(by_base.pop(k)["items"])

    movies = []
    for g in by_base.values():
        clusters = []   # [langset, [files]]
        no_lang = []
        for f, langs in g["items"]:
            if g["season"] is not None:
                # a series season is ONE button, whatever the language (no language shown for series)
                if clusters:
                    clusters[0][0] |= langs
                    clusters[0][1].append(f)
                else:
                    clusters.append([set(langs), [f]])
                continue
            if not langs:
                no_lang.append(f)
                continue
            for c in clusters:
                if c[0] & langs:
                    c[0] |= langs
                    c[1].append(f)
                    break
            else:
                clusters.append([set(langs), [f]])
        # clusters that became connected through a later file are merged
        merged = True
        while merged:
            merged = False
            for i in range(len(clusters)):
                for j in range(i + 1, len(clusters)):
                    if clusters[i][0] & clusters[j][0]:
                        clusters[i][0] |= clusters[j][0]
                        clusters[i][1] += clusters[j][1]
                        del clusters[j]
                        merged = True
                        break
                if merged:
                    break
        if not clusters:
            clusters = [[set(), []]]
        biggest = max(clusters, key=lambda c: len(c[1]))
        biggest[1].extend(no_lang)   # files with no language in the name go to the biggest group
        for langs, fl in clusters:
            if fl:
                movies.append({"title": g["title"], "year": g["year"], "season": g["season"], "langs": sorted(langs), "files": fl})
    # series: no year in the button. A title that has season groups (S01, S02 ...) is a series, so its
    # year-only pack (e.g. "Game Of Thrones 2011 Complete") also loses the year in the label.
    norm = _title_key
    series_titles = {norm(m["title"]) for m in movies if m["season"] is not None}
    title_year = {}
    for m in movies:
        if m["year"]:
            t = norm(m["title"])
            title_year[t] = min(title_year.get(t, m["year"]), m["year"])
    packs = {}
    rest = []
    for m in movies:
        if m["season"] is None and norm(m["title"]) in series_titles:
            t = norm(m["title"])
            if t in packs:
                packs[t]["files"].extend(m["files"])
                continue
            packs[t] = m
        rest.append(m)
    movies = rest
    for m in movies:
        m["show_year"] = m["season"] is None and norm(m["title"]) not in series_titles   # series: no year, no language
    # language is shown ONLY when two movie buttons would otherwise look the same (same title + same year)
    same = {}
    for m in movies:
        if m["show_year"]:
            same[(norm(m["title"]), m["year"])] = same.get((norm(m["title"]), m["year"]), 0) + 1
    for m in movies:
        m["show_lang"] = bool(m["show_year"] and same.get((norm(m["title"]), m["year"]), 0) > 1)

    def _sort_key(m):
        t = norm(m["title"])
        ty = title_year.get(t)
        sn = int(m["season"]) if str(m["season"]).isdigit() else 0
        return (ty is None, ty or "", t, m["season"] is not None, sn, m["year"] or "")
    movies.sort(key=_sort_key)
    return movies


def _movie_button_label(mv, limit=60):
    # suffix (season / year / language / bullet) is always kept; only a long TITLE is shortened with "…"
    suffix = ""
    if mv["season"] is not None:
        suffix += f" S{str(mv['season']).zfill(2)}" if str(mv["season"]).isdigit() else f" {mv['season']}"
    if mv["year"] and mv.get("show_year", True):
        suffix += f" ({mv['year']})"
    if mv["langs"] and mv.get("show_lang", False):
        suffix += " • " + "+".join(sorted(mv["langs"], key=lambda x: x.lower() == "english")[:2])   # English goes last
    if not mv.get("show_year", True):
        suffix += " •"   # series button: "🎬 Game Of Thrones S01 •"
    title = mv["title"]
    room = max(limit - len("🎬 ") - len(suffix), 8)
    if len(title) > room:
        title = title[:room - 1].rstrip() + "…"
    return "🎬 " + title + suffix



def _season_only_query(q):
    """'game of thrones s07e07' / 'season 7 episode 7' / 's07 e07' -> 'game of thrones s07' (the whole season).
    Returns None when the query has no season+episode tag."""
    from database.ia_filterdb import _normalize_season_query
    n = _normalize_season_query((q or "").lower())
    pat = r"\bs0*(\d{1,3})e0*\d{1,4}\b"
    if not re.search(pat, n):
        return None
    n = re.sub(pat, lambda x: f"s{int(x.group(1)):02d}", n)
    return re.sub(r"\s+", " ", n).strip()


async def _delete_user_msg(msg, delay):
    await asyncio.sleep(delay)
    try:
        await msg.delete()
    except Exception:
        pass
        
@Client.on_message(filters.group & filters.text & filters.incoming & ~filters.regex(r"^/") )
async def give_filter(client, message):
    if not message.from_user:
        return
    if EMOJI_MODE:
        try:
            await message.react(emoji=random.choice(REACTIONS), big=True)
        except Exception:
            pass
    await mdb.update_top_messages(message.from_user.id, message.text)
    if message.chat.id != SUPPORT_CHAT_ID:
        settings = await get_settings(message.chat.id)
        if settings.get('auto_ffilter') is None:
            # old group record without this setting -> default is ON
            await save_group_settings(message.chat.id, 'auto_ffilter', True)
            settings = await get_settings(message.chat.id)
        try:
            if settings.get('auto_ffilter', True):
                if re.search(r'https?://\S+|www\.\S+|t\.me/\S+', message.text):
                    if await is_check_admin(client, message.chat.id, message.from_user.id):
                        return
                    return await message.delete()
                await auto_filter(client, message)
        except Exception as e:
            # a bug inside auto_filter must not re-run it (that sent duplicate results)
            logger.exception("Error in auto filter: %s", e)
    else:
        search = message.text
        _, _, total_results = await get_search_results(chat_id=message.chat.id, query=search.lower(), offset=0, filter=True)
        if total_results == 0:
            return
        await message.reply_text(
            script.ALREADY_AVAILABLE_TXT.format(message.from_user.mention, total_results, search),
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔍 ᴊᴏɪɴ ᴀɴᴅ ꜱᴇᴀʀᴄʜ ʜᴇʀᴇ 🔎", url=GRP_LNK)]])
        )


@Client.on_message(filters.private & filters.text & filters.incoming & ~filters.regex(r"^/") & ~filters.regex(r"(https?://)?(t\.me|telegram\.me|telegram\.dog)/"))
async def pm_text(bot, message):
    bot_id = bot.me.id
    content = message.text
    user = message.from_user.first_name
    user_id = message.from_user.id
    if EMOJI_MODE:
        try:
            await message.react(emoji=random.choice(REACTIONS), big=True)
        except Exception:
            pass
    if content.startswith(("#")):
        return
    if not await fsub_gate(bot, message):
        return
    try:
        await mdb.update_top_messages(user_id, content)
        pm_search = await db.pm_search_status(bot_id)
        if pm_search:
            await auto_filter(bot, message)
        else:
            await message.reply_text(
                text=script.PM_SEARCH_DISABLED_TXT.format(user),
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📝 ʀᴇǫᴜᴇsᴛ ʜᴇʀᴇ ", url=GRP_LNK)]])
            )
            await bot.send_message(
                chat_id=LOG_CHANNEL,
                text=script.PM_LOG_TXT.format(user, user_id, content)
            )
    except Exception:
        logger.exception("pm_text failed for user %s", user_id)


@Client.on_callback_query(filters.regex(r"^reffff"))
async def refercall(bot, query):
    ref_points = await referdb.get_refer_points(query.from_user.id)
    btn = [[
        InlineKeyboardButton(
            'invite link', url=f'https://telegram.me/share/url?url=https://t.me/{bot.me.username}?start=reff_{query.from_user.id}&text=Hello%21%20Experience%20a%20bot%20that%20offers%20a%20vast%20library%20of%20unlimited%20movies%20and%20series.%20%F0%9F%98%83'),
        InlineKeyboardButton(
            f'⏳ {ref_points}', callback_data='ref_point'),
        InlineKeyboardButton('Back', callback_data='premium_info')
    ]]
    reply_markup = InlineKeyboardMarkup(btn)
    try:
        await bot.edit_message_media(
            query.message.chat.id,
            query.message.id,
            InputMediaPhoto("https://graph.org/file/1a2e64aee3d4d10edd930.jpg")
        )
    except Exception:    
        pass
    await query.message.edit_text(
        text=script.REFER_TXT.format(bot.me.username, query.from_user.id),
        reply_markup=reply_markup,
        parse_mode=enums.ParseMode.HTML
    )
    await query.answer()

@Client.on_callback_query(filters.regex(r"^next"))
async def next_page(bot, query):
    try:
        await query.answer()
    except Exception:
        pass
    ident, req, key, offset = query.data.split("_")
    curr_time = datetime.now(pytz.timezone('Asia/Kolkata')).time()
    if ANYONE_CAN_CLICK is False and int(req) not in [query.from_user.id, 0]:
        return await query.answer(script.ALRT_TXT.format(query.from_user.first_name), show_alert=True)
    try:
        offset = int(offset)
    except Exception:
        offset = 0
    if BUTTONS.get(key) is not None:
        search = BUTTONS.get(key)
    else:
        search = FRESH.get(key)
    if not search:
        await query.answer(script.OLD_ALRT_TXT.format(query.from_user.first_name), show_alert=True)
        return
    files, n_offset, total = await get_search_results(query.message.chat.id, search, offset=offset, filter=True)
    try:
        n_offset = int(n_offset)
    except Exception:
        n_offset = 0

    if not files:
        return
    temp.GETALL[key] = files
    temp.SHORT[query.from_user.id] = query.message.chat.id
    settings = await get_settings(query.message.chat.id)
    if settings.get('button'):
        btn = [
            [
                InlineKeyboardButton(text=f"🔗 {get_size(file.file_size)} ≽ " + display_name(file.file_name, file.caption), callback_data=f'file#{file.file_id}'),
            ]
            for file in files
        ]
        btn.insert(0,
                   [
                       InlineKeyboardButton(
                           'Qᴜᴀʟɪᴛʏ', callback_data=f"qualities#{req}#{key}"),
                       InlineKeyboardButton(
                           "Lᴀɴɢᴜᴀɢᴇ", callback_data=f"languages#{req}#{key}"),
                       InlineKeyboardButton(
                           "Sᴇᴀsᴏɴ",  callback_data=f"seasons#{req}#{key}")
                   ]
                   )
        btn.insert(0,
                   [
                       InlineKeyboardButton(
                           "ʀᴇᴍᴏᴠᴇ ᴀᴅs", url=f"https://t.me/{temp.U_NAME}?start=premium", style=enums.ButtonStyle.PRIMARY),
                       InlineKeyboardButton(
                           "Sᴇɴᴅ Aʟʟ", callback_data=f"sendfiles#{key}", style=enums.ButtonStyle.SUCCESS)

                   ]
                   )

    else:
        btn = []
        btn.insert(0,
                   [
                       InlineKeyboardButton(
                           'Qᴜᴀʟɪᴛʏ', callback_data=f"qualities#{req}#{key}"),
                       InlineKeyboardButton(
                           "Lᴀɴɢᴜᴀɢᴇ", callback_data=f"languages#{req}#{key}"),
                       InlineKeyboardButton(
                           "Sᴇᴀsᴏɴ",  callback_data=f"seasons#{req}#{key}")
                   ]
                   )
        btn.insert(0, [
            InlineKeyboardButton(
                "ʀᴇᴍᴏᴠᴇ ᴀᴅs", url=f"https://t.me/{temp.U_NAME}?start=premium", style=enums.ButtonStyle.PRIMARY),
            InlineKeyboardButton("Sᴇɴᴅ Aʟʟ", callback_data=f"sendfiles#{key}", style=enums.ButtonStyle.SUCCESS)
        ])
    if ULTRA_FAST_MODE:
        if 0 < offset <= 10:
            off_set = 0
        elif offset == 0:
            off_set = None
        else:
            off_set = offset - 10
        if n_offset == 0:
            btn.append(
                [InlineKeyboardButton("⋞ ʙᴀᴄᴋ", callback_data=f"next_{req}_{key}_{off_set}"), InlineKeyboardButton(f"{math.ceil(int(offset)/10)+1}", callback_data="pages")]
            )
        elif off_set is None:
            btn.append([InlineKeyboardButton("ᴘᴀɢᴇ", callback_data="pages"), InlineKeyboardButton(f"{math.ceil(int(offset)/10)+1}", callback_data="pages"), InlineKeyboardButton("ɴᴇxᴛ ⋟", callback_data=f"next_{req}_{key}_{n_offset}")])
        else:
            btn.append(
                [
                    InlineKeyboardButton("⋞ ʙᴀᴄᴋ", callback_data=f"next_{req}_{key}_{off_set}"),
                    InlineKeyboardButton(f"{math.ceil(int(offset)/10)+1}", callback_data="pages"),
                    InlineKeyboardButton("ɴᴇxᴛ ⋟", callback_data=f"next_{req}_{key}_{n_offset}")
                ],
            )
    else:
        try:
            if settings['max_btn']:
                if 0 < offset <= 10:
                    off_set = 0
                elif offset == 0:
                    off_set = None
                else:
                    off_set = offset - 10
                if n_offset == 0:
                    btn.append([InlineKeyboardButton("⋞ ʙᴀᴄᴋ", callback_data=f"next_{req}_{key}_{off_set}"), InlineKeyboardButton(
                        f"{math.ceil(int(offset)/10)+1} / {math.ceil(total/10)}", callback_data="pages")])
                elif off_set is None:
                    btn.append([InlineKeyboardButton("ᴘᴀɢᴇ", callback_data="pages"), InlineKeyboardButton(
                        f"{math.ceil(int(offset)/10)+1} / {math.ceil(total/10)}", callback_data="pages"), InlineKeyboardButton("ɴᴇxᴛ ⋟", callback_data=f"next_{req}_{key}_{n_offset}")])
                else:
                    btn.append(
                        [
                            InlineKeyboardButton(
                                "⋞ ʙᴀᴄᴋ", callback_data=f"next_{req}_{key}_{off_set}"),
                            InlineKeyboardButton(
                                f"{math.ceil(int(offset)/10)+1} / {math.ceil(total/10)}", callback_data="pages"),
                            InlineKeyboardButton(
                                "ɴᴇxᴛ ⋟", callback_data=f"next_{req}_{key}_{n_offset}")
                        ],
                    )
            else:
                if 0 < offset <= int(MAX_B_TN):
                    off_set = 0
                elif offset == 0:
                    off_set = None
                else:
                    off_set = offset - int(MAX_B_TN)
                if n_offset == 0:
                    btn.append([InlineKeyboardButton("⋞ ʙᴀᴄᴋ", callback_data=f"next_{req}_{key}_{off_set}"), InlineKeyboardButton(
                        f"{math.ceil(int(offset)/int(MAX_B_TN))+1} / {math.ceil(total/int(MAX_B_TN))}", callback_data="pages")])
                elif off_set is None:
                    btn.append([InlineKeyboardButton("ᴘᴀɢᴇ", callback_data="pages"), InlineKeyboardButton(
                        f"{math.ceil(int(offset)/int(MAX_B_TN))+1} / {math.ceil(total/int(MAX_B_TN))}", callback_data="pages"), InlineKeyboardButton("ɴᴇxᴛ ⋟", callback_data=f"next_{req}_{key}_{n_offset}")])
                else:
                    btn.append(
                        [
                            InlineKeyboardButton(
                                "⋞ ʙᴀᴄᴋ", callback_data=f"next_{req}_{key}_{off_set}"),
                            InlineKeyboardButton(
                                f"{math.ceil(int(offset)/int(MAX_B_TN))+1} / {math.ceil(total/int(MAX_B_TN))}", callback_data="pages"),
                            InlineKeyboardButton(
                                "ɴᴇxᴛ ⋟", callback_data=f"next_{req}_{key}_{n_offset}")
                        ],
                    )
        except KeyError:
            await save_group_settings(query.message.chat.id, 'max_btn', True)
            if 0 < offset <= 10:
                off_set = 0
            elif offset == 0:
                off_set = None
            else:
                off_set = offset - 10
            if n_offset == 0:
                btn.append(
                    [InlineKeyboardButton("⋞ ʙᴀᴄᴋ", callback_data=f"next_{req}_{key}_{off_set}"), InlineKeyboardButton(
                        f"{math.ceil(int(offset)/10)+1} / {math.ceil(total/10)}", callback_data="pages")]
                )
            elif off_set is None:
                btn.append([InlineKeyboardButton("ᴘᴀɢᴇ", callback_data="pages"), InlineKeyboardButton(
                    f"{math.ceil(int(offset)/10)+1} / {math.ceil(total/10)}", callback_data="pages"), InlineKeyboardButton("ɴᴇxᴛ ⋟", callback_data=f"next_{req}_{key}_{n_offset}")])
            else:
                btn.append(
                    [
                        InlineKeyboardButton(
                            "⋞ ʙᴀᴄᴋ", callback_data=f"next_{req}_{key}_{off_set}"),
                        InlineKeyboardButton(
                            f"{math.ceil(int(offset)/10)+1} / {math.ceil(total/10)}", callback_data="pages"),
                        InlineKeyboardButton(
                            "ɴᴇxᴛ ⋟", callback_data=f"next_{req}_{key}_{n_offset}")
                    ],
                )
    if not settings["button"]:
        cur_time = datetime.now(pytz.timezone('Asia/Kolkata')).time()
        time_difference = timedelta(hours=cur_time.hour, minutes=cur_time.minute, seconds=(cur_time.second+(cur_time.microsecond/1000000))) - \
            timedelta(hours=curr_time.hour, minutes=curr_time.minute, seconds=(
                curr_time.second+(curr_time.microsecond/1000000)))
        remaining_seconds = "{:.2f}".format(time_difference.total_seconds())
        dreamx_title = clean_search_text(search)
        cap = None
        try:
            if settings['imdb']:
                cap = await get_cap(settings, remaining_seconds, files, query, total, dreamx_title, offset)
                if query.message.caption:
                    try:
                        await query.message.edit_caption(caption=cap, reply_markup=InlineKeyboardMarkup(btn), parse_mode=enums.ParseMode.HTML)
                    except (MessageNotModified, MessageIdInvalid):
                        pass
                    except Exception as e:
                        logger.exception(e)
                else:
                    try:
                        await query.message.edit_text(text=cap, reply_markup=InlineKeyboardMarkup(btn), link_preview_options=LinkPreviewOptions(is_disabled=True), parse_mode=enums.ParseMode.HTML)
                    except (MessageNotModified, MessageIdInvalid):
                        pass
            else:
                cap = await get_cap(settings, remaining_seconds, files, query, total, dreamx_title, offset+1)
                await query.message.edit_text(text=cap, reply_markup=InlineKeyboardMarkup(btn), link_preview_options=LinkPreviewOptions(is_disabled=True), parse_mode=enums.ParseMode.HTML)
        except (MessageNotModified, MessageIdInvalid):
            pass
        except Exception as e:
            logger.exception("Failed to send result: %s", e)
    else:
        try:
            await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(btn))
        except (MessageNotModified, MessageIdInvalid):
            pass
    await query.answer()


@Client.on_callback_query(filters.regex(r"^spol"))
async def advantage_spoll_choker(bot, query):
    _, id, user = query.data.split('#')
    if ANYONE_CAN_CLICK is False and int(user) != 0 and query.from_user.id != int(user):
        return await query.answer(script.ALRT_TXT.format(query.from_user.first_name), show_alert=True)
    movies = await get_posterx(id, id=True) if TMDB_ON_SEARCH else await get_poster(id, id=True)
    if not movies or not movies.get('title'):
        return await query.answer("⚠️ Could not load this title, please search again.", show_alert=True)
    movie = movies.get('title')
    movie = re.sub(r"[:-]", " ", movie)
    movie = re.sub(r"\s+", " ", movie).strip()
    await query.answer(script.TOP_ALRT_MSG)
    files, offset, total_results = await get_search_results(query.message.chat.id, movie, offset=0, filter=True)
    if files:
        k = (movie, files, offset, total_results)
        await auto_filter(bot, query, k)
    else:
        reqstr1 = query.from_user.id if query.from_user else 0
        reqstr = await bot.get_users(reqstr1)
        if NO_RESULTS_MSG:
            try:
                await bot.send_message(chat_id=BIN_CHANNEL, text=script.NORSLTS.format(reqstr.id, reqstr.mention, movie))
            except Exception as e:
                logger.error("Error In Spol: %s — Make Sure Bot Admin BIN CHANNEL", e)
        btn = InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔰Cʟɪᴄᴋ ʜᴇʀᴇ & ʀᴇǫᴜᴇsᴛ ᴛᴏ ᴀᴅᴍɪɴ🔰", url=OWNER_LNK)]])
        k = await query.message.edit(script.MVE_NT_FND, reply_markup=btn)
        await asyncio.sleep(10)
        await k.delete()

# Qualities
@Client.on_callback_query(filters.regex(r"^qualities#"))
async def qualities_cb_handler(client: Client, query: CallbackQuery):
    _, req, key = query.data.split("#")
    try:
        if ANYONE_CAN_CLICK is False and int(req) not in [query.from_user.id, 0]:
            return await query.answer(
                f"⚠️ ʜᴇʟʟᴏ {query.from_user.first_name},\n"
                f"ᴛʜɪꜱ ɪꜱ ɴᴏᴛ ʏᴏᴜʀ ᴍᴏᴠɪᴇ ʀᴇǫᴜᴇꜱᴛ,\nʀᴇǫᴜᴇꜱᴛ ʏᴏᴜʀ'ꜱ...",
                show_alert=True,
            )
    except Exception:
        pass

    search = FRESH.get(key)
    if not search:
        return await query.answer(script.OLD_ALRT_TXT.format(query.from_user.first_name), show_alert=True)
    search = search.replace(' ', '_')

    btn = []
    for i in range(0, len(QUALITIES), 2):
        q1 = QUALITIES[i]
        row = [InlineKeyboardButton(
            text=q1, callback_data=f"fq#{q1.lower()}#{req}#{key}")]
        if i + 1 < len(QUALITIES):
            q2 = QUALITIES[i + 1]
            row.append(InlineKeyboardButton(
                text=q2, callback_data=f"fq#{q2.lower()}#{req}#{key}"))
        btn.append(row)

    btn.insert(0, [
        InlineKeyboardButton(text="⇊ ꜱᴇʟᴇᴄᴛ ǫᴜᴀʟɪᴛʏ ⇊", callback_data="ident")
    ])
    btn.append([
        InlineKeyboardButton(text="↭ ʙᴀᴄᴋ ᴛᴏ ꜰɪʟᴇs ↭",
                             callback_data=f"fq#homepage#{req}#{key}")
    ])

    await query.edit_message_reply_markup(InlineKeyboardMarkup(btn))


@Client.on_callback_query(filters.regex(r"^fq#"))
async def filter_qualities_cb_handler(client: Client, query: CallbackQuery):
    _, qual, req, key = query.data.split("#")
    curr_time = datetime.now(pytz.timezone('Asia/Kolkata')).time()
    search = FRESH.get(key)
    if not search:
        return await query.answer(script.OLD_ALRT_TXT.format(query.from_user.first_name), show_alert=True)
    search = search.replace("_", " ")
    # remove the previously chosen tag as a WHOLE WORD only ("tel" must not cut "Hotel", "mal" must not cut "Animal")
    search = re.sub(r"(?<![0-9A-Za-z])" + re.escape(qual) + r"(?![0-9A-Za-z])", "", search, flags=re.IGNORECASE)
    search = re.sub(r"\s+", " ", search).strip()
    chat_id = query.message.chat.id
    message = query.message
    try:
        if ANYONE_CAN_CLICK is False and int(req) not in [query.from_user.id, 0]:
            return await query.answer(f"⚠️ ʜᴇʟʟᴏ {query.from_user.first_name},\nᴛʜɪꜱ ɪꜱ ɴᴏᴛ ʏᴏᴜʀ ᴍᴏᴠɪᴇ ʀᴇǫᴜᴇꜱᴛ,\nʀᴇǫᴜᴇꜱᴛ ʏᴏᴜʀ'ꜱ...", show_alert=True,)
    except Exception:
        pass
    if qual != "homepage":
        search = f"{search} {qual}"
    BUTTONS[key] = search
    files, offset, total_results = await get_search_results(chat_id, search, offset=0, filter=True)
    if not files:
        await query.answer("🚫 ɴᴏ ꜰɪʟᴇꜱ ᴡᴇʀᴇ ꜰᴏᴜɴᴅ 🚫", show_alert=1)
        return
    temp.GETALL[key] = files
    settings = await get_settings(message.chat.id)
    if settings.get('button'):
        btn = [
            [
                InlineKeyboardButton(text=f"🔗 {get_size(file.file_size)} ≽ " + display_name(file.file_name, file.caption), callback_data=f'file#{file.file_id}'),
            ]
            for file in files
        ]
        btn.insert(0,
                   [
                       InlineKeyboardButton(
                           'Qᴜᴀʟɪᴛʏ', callback_data=f"qualities#{req}#{key}"),
                       InlineKeyboardButton(
                           "Lᴀɴɢᴜᴀɢᴇ", callback_data=f"languages#{req}#{key}"),
                       InlineKeyboardButton(
                           "Sᴇᴀsᴏɴ",  callback_data=f"seasons#{req}#{key}")
                   ]
                   )
        btn.insert(0,
                   [
                       InlineKeyboardButton(
                           "ʀᴇᴍᴏᴠᴇ ᴀᴅs", url=f"https://t.me/{temp.U_NAME}?start=premium", style=enums.ButtonStyle.PRIMARY),
                       InlineKeyboardButton(
                           "Sᴇɴᴅ Aʟʟ", callback_data=f"sendfiles#{key}", style=enums.ButtonStyle.SUCCESS)
                   ])
    else:
        btn = []
        btn.insert(0,
                   [
                       InlineKeyboardButton(
                           'Qᴜᴀʟɪᴛʏ', callback_data=f"qualities#{req}#{key}"),
                       InlineKeyboardButton(
                           "Lᴀɴɢᴜᴀɢᴇ", callback_data=f"languages#{req}#{key}"),
                       InlineKeyboardButton(
                           "Sᴇᴀsᴏɴ",  callback_data=f"seasons#{req}#{key}")
                   ]
                   )
        btn.insert(0,
                   [
                       InlineKeyboardButton(
                           "ʀᴇᴍᴏᴠᴇ ᴀᴅs", url=f"https://t.me/{temp.U_NAME}?start=premium", style=enums.ButtonStyle.PRIMARY),
                       InlineKeyboardButton(
                           "Sᴇɴᴅ Aʟʟ", callback_data=f"sendfiles#{key}", style=enums.ButtonStyle.SUCCESS)

                   ])
    if offset != "":
        try:
            if settings['max_btn']:
                btn.append(

                    [InlineKeyboardButton("ᴘᴀɢᴇ", callback_data="pages"), InlineKeyboardButton(
                        text=f"1/{math.ceil(int(total_results)/10)}", callback_data="pages"), InlineKeyboardButton(text="ɴᴇxᴛ ⋟", callback_data=f"next_{req}_{key}_{offset}")]
                )
            else:
                btn.append(

                    [InlineKeyboardButton("ᴘᴀɢᴇ", callback_data="pages"), InlineKeyboardButton(
                        text=f"1/{math.ceil(int(total_results)/int(MAX_B_TN))}", callback_data="pages"), InlineKeyboardButton(text="ɴᴇxᴛ ⋟", callback_data=f"next_{req}_{key}_{offset}")]
                )
        except KeyError:
            await save_group_settings(query.message.chat.id, 'max_btn', True)
            btn.append(

                [InlineKeyboardButton("ᴘᴀɢᴇ", callback_data="pages"), InlineKeyboardButton(
                    text=f"1/{math.ceil(int(total_results)/10)}", callback_data="pages"), InlineKeyboardButton(text="ɴᴇxᴛ ⋟", callback_data=f"next_{req}_{key}_{offset}")]
            )
    else:
        btn.append(

            [InlineKeyboardButton(
                text="↭ ɴᴏ ᴍᴏʀᴇ ᴘᴀɢᴇꜱ ᴀᴠᴀɪʟᴀʙʟᴇ ↭", callback_data="pages")]
        )
    if not settings["button"]:
        cur_time = datetime.now(pytz.timezone('Asia/Kolkata')).time()
        time_difference = timedelta(hours=cur_time.hour, minutes=cur_time.minute, seconds=(cur_time.second+(cur_time.microsecond/1000000))) - \
            timedelta(hours=curr_time.hour, minutes=curr_time.minute, seconds=(
                curr_time.second+(curr_time.microsecond/1000000)))
        remaining_seconds = "{:.2f}".format(time_difference.total_seconds())
        dreamx_title = clean_search_text(search)
        cap = await get_cap(settings, remaining_seconds, files, query, total_results, dreamx_title, offset=1)
        try:
            await query.message.edit_text(text=cap, reply_markup=InlineKeyboardMarkup(btn), link_preview_options=LinkPreviewOptions(is_disabled=True), parse_mode=enums.ParseMode.HTML)
        except (MessageNotModified, MessageIdInvalid):
            pass
    else:
        try:
            await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(btn))
        except (MessageNotModified, MessageIdInvalid):
            pass
    await query.answer()

# languages


@Client.on_callback_query(filters.regex(r"^languages#"))
async def languages_cb_handler(client: Client, query: CallbackQuery):
    _, req, key = query.data.split("#")
    try:
        if ANYONE_CAN_CLICK is False and int(req) not in [query.from_user.id, 0]:
            return await query.answer(
                f"⚠️ ʜᴇʟʟᴏ {query.from_user.first_name},\n"
                f"ᴛʜɪꜱ ɪꜱ ɴᴏᴛ ʏᴏᴜʀ ᴍᴏᴠɪᴇ ʀᴇǫᴜᴇꜱᴛ,\nʀᴇǫᴜᴇꜱᴛ ʏᴏᴜʀ'ꜱ...",
                show_alert=True,
            )
    except Exception:
        pass

    search = FRESH.get(key)
    if not search:
        return await query.answer(script.OLD_ALRT_TXT.format(query.from_user.first_name), show_alert=True)
    search = search.replace(' ', '_')

    items = list(LANGUAGES.items())
    btn = []

    for i in range(0, len(items), 2):
        name1, code1 = items[i]
        row = [InlineKeyboardButton(
            text=name1, callback_data=f"fl#{code1}#{req}#{key}")]
        if i + 1 < len(items):
            name2, code2 = items[i + 1]
            row.append(InlineKeyboardButton(
                text=name2, callback_data=f"fl#{code2}#{req}#{key}"))
        btn.append(row)

    btn.insert(0, [InlineKeyboardButton(
        text="⇊ ꜱᴇʟᴇᴄᴛ ʟᴀɴɢᴜᴀɢᴇ ⇊", callback_data="ident")])
    btn.append([InlineKeyboardButton(text="↭ ʙᴀᴄᴋ ᴛᴏ ꜰɪʟᴇs ↭",
               callback_data=f"fl#homepage#{req}#{key}")])

    await query.edit_message_reply_markup(InlineKeyboardMarkup(btn))


@Client.on_callback_query(filters.regex(r"^fl#"))
async def filter_languages_cb_handler(client: Client, query: CallbackQuery):
    _, lang, req, key = query.data.split("#")
    curr_time = datetime.now(pytz.timezone('Asia/Kolkata')).time()
    search = FRESH.get(key)
    if not search:
        return await query.answer(script.OLD_ALRT_TXT.format(query.from_user.first_name), show_alert=True)
    search = search.replace("_", " ")
    # remove the previously chosen tag as a WHOLE WORD only ("tel" must not cut "Hotel", "mal" must not cut "Animal")
    search = re.sub(r"(?<![0-9A-Za-z])" + re.escape(lang) + r"(?![0-9A-Za-z])", "", search, flags=re.IGNORECASE)
    search = re.sub(r"\s+", " ", search).strip()
    chat_id = query.message.chat.id
    message = query.message
    try:
        if ANYONE_CAN_CLICK is False and int(req) not in [query.from_user.id, 0]:
            return await query.answer(f"⚠️ ʜᴇʟʟᴏ {query.from_user.first_name},\nᴛʜɪꜱ ɪꜱ ɴᴏᴛ ʏᴏᴜʀ ᴍᴏᴠɪᴇ ʀᴇǫᴜᴇꜱᴛ,\nʀᴇǫᴜᴇꜱᴛ ʏᴏᴜʀ'ꜱ...", show_alert=True,)
    except Exception:
        pass
    if lang != "homepage":
        search = f"{search} {lang}"
    BUTTONS[key] = search
    files, offset, total_results = await get_search_results(chat_id, search, offset=0, filter=True)
    if not files:
        await query.answer("🚫 ɴᴏ ꜰɪʟᴇꜱ ᴡᴇʀᴇ ꜰᴏᴜɴᴅ 🚫", show_alert=1)
        return
    temp.GETALL[key] = files
    settings = await get_settings(message.chat.id)
    if settings.get('button'):
        btn = [
            [
                InlineKeyboardButton(text=f"🔗 {get_size(file.file_size)} ≽ " + display_name(file.file_name, file.caption), callback_data=f'file#{file.file_id}'),
            ]
            for file in files
        ]
        btn.insert(0,
                   [
                       InlineKeyboardButton(
                           'Qᴜᴀʟɪᴛʏ', callback_data=f"qualities#{req}#{key}"),
                       InlineKeyboardButton(
                           "Lᴀɴɢᴜᴀɢᴇ", callback_data=f"languages#{req}#{key}"),
                       InlineKeyboardButton(
                           "Sᴇᴀsᴏɴ",  callback_data=f"seasons#{req}#{key}")
                   ]
                   )
        btn.insert(0,
                   [
                       InlineKeyboardButton(
                           "ʀᴇᴍᴏᴠᴇ ᴀᴅs", url=f"https://t.me/{temp.U_NAME}?start=premium", style=enums.ButtonStyle.PRIMARY),
                       InlineKeyboardButton(
                           "Sᴇɴᴅ Aʟʟ", callback_data=f"sendfiles#{key}", style=enums.ButtonStyle.SUCCESS)
                   ]
                   )
    else:
        btn = []
        btn.insert(0,
                   [
                       InlineKeyboardButton(
                           'Qᴜᴀʟɪᴛʏ', callback_data=f"qualities#{req}#{key}"),
                       InlineKeyboardButton(
                           "Lᴀɴɢᴜᴀɢᴇ", callback_data=f"languages#{req}#{key}"),
                       InlineKeyboardButton(
                           "Sᴇᴀsᴏɴ",  callback_data=f"seasons#{req}#{key}")
                   ])
        btn.insert(0,
                   [
                       InlineKeyboardButton(
                           "ʀᴇᴍᴏᴠᴇ ᴀᴅs", url=f"https://t.me/{temp.U_NAME}?start=premium", style=enums.ButtonStyle.PRIMARY),
                       InlineKeyboardButton(
                           "Sᴇɴᴅ Aʟʟ", callback_data=f"sendfiles#{key}", style=enums.ButtonStyle.SUCCESS)
                   ])
    if offset != "":
        try:
            if settings['max_btn']:
                btn.append(
                    [
                        InlineKeyboardButton("ᴘᴀɢᴇ", callback_data="pages"), InlineKeyboardButton(
                            text=f"1/{math.ceil(int(total_results)/10)}", callback_data="pages"), InlineKeyboardButton(text="ɴᴇxᴛ ⋟", callback_data=f"next_{req}_{key}_{offset}")
                    ])
            else:
                btn.append(
                    [
                        InlineKeyboardButton("ᴘᴀɢᴇ", callback_data="pages"), InlineKeyboardButton(
                            text=f"1/{math.ceil(int(total_results)/int(MAX_B_TN))}", callback_data="pages"), InlineKeyboardButton(text="ɴᴇxᴛ ⋟", callback_data=f"next_{req}_{key}_{offset}")
                    ])
        except KeyError:
            await save_group_settings(query.message.chat.id, 'max_btn', True)
            btn.append(
                [
                    InlineKeyboardButton("ᴘᴀɢᴇ", callback_data="pages"), InlineKeyboardButton(
                        text=f"1/{math.ceil(int(total_results)/10)}", callback_data="pages"), InlineKeyboardButton(text="ɴᴇxᴛ ⋟", callback_data=f"next_{req}_{key}_{offset}")
                ])
    else:
        btn.append([InlineKeyboardButton(
            text="↭ ɴᴏ ᴍᴏʀᴇ ᴘᴀɢᴇꜱ ᴀᴠᴀɪʟᴀʙʟᴇ ↭", callback_data="pages")])
    if not settings["button"]:
        cur_time = datetime.now(pytz.timezone('Asia/Kolkata')).time()
        time_difference = timedelta(hours=cur_time.hour, minutes=cur_time.minute, seconds=(cur_time.second+(cur_time.microsecond/1000000))) - \
            timedelta(hours=curr_time.hour, minutes=curr_time.minute, seconds=(
                curr_time.second+(curr_time.microsecond/1000000)))
        remaining_seconds = "{:.2f}".format(time_difference.total_seconds())
        dreamx_title = clean_search_text(search)
        cap = await get_cap(settings, remaining_seconds, files, query, total_results, dreamx_title, offset=1)
        try:
            await query.message.edit_text(text=cap, reply_markup=InlineKeyboardMarkup(btn), link_preview_options=LinkPreviewOptions(is_disabled=True), parse_mode=enums.ParseMode.HTML)
        except (MessageNotModified, MessageIdInvalid):
            pass
    else:
        try:
            await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(btn))
        except (MessageNotModified, MessageIdInvalid):
            pass
    await query.answer()


@Client.on_callback_query(filters.regex(r"^seasons#"))
async def seasons_cb_handler(client: Client, query: CallbackQuery):
    _, req, key = query.data.split("#")
    try:
        if ANYONE_CAN_CLICK is False and int(req) not in [query.from_user.id, 0]:
            return await query.answer(
                f"⚠️ ʜᴇʟʟᴏ {query.from_user.first_name},\nᴛʜɪꜱ ɪꜱ ɴᴏᴛ ʏᴏᴜʀ ᴍᴏᴠɪᴇ ʀᴇǫᴜᴇꜱᴛ,\nʀᴇǫᴜᴇꜱᴛ ʏᴏᴜʀ'ꜱ…",
                show_alert=True,
            )
    except Exception:
        pass
    offset = 0
    btn: list[list[InlineKeyboardButton]] = []
    for i in range(0, len(SEASONS) - 1, 2):
        btn.append([
            InlineKeyboardButton(
                f"Sᴇᴀꜱᴏɴ {SEASONS[i][1:]}", callback_data=f"fs#{SEASONS[i].lower()}#{req}#{key}"),
            InlineKeyboardButton(
                f"Sᴇᴀꜱᴏɴ {SEASONS[i+1][1:]}", callback_data=f"fs#{SEASONS[i+1].lower()}#{req}#{key}")
        ])

    btn.insert(
        0,
        [InlineKeyboardButton("⇊ ꜱᴇʟᴇᴄᴛ ꜱᴇᴀꜱᴏɴ ⇊", callback_data="ident")],
    )
    btn.append([InlineKeyboardButton(text="↭ ʙᴀᴄᴋ ᴛᴏ ꜰɪʟᴇs ​↭",
               callback_data=f"next_{req}_{key}_{offset}")])
    await query.edit_message_reply_markup(InlineKeyboardMarkup(btn))
    await query.answer()


@Client.on_callback_query(filters.regex(r"^fs#"))
async def filter_seasons_cb_handler(client: Client, query: CallbackQuery):
    _, season_tag, req, key = query.data.split("#")
    search = FRESH.get(key)
    if not search:
        return await query.answer(script.OLD_ALRT_TXT.format(query.from_user.first_name), show_alert=True)
    search = search.replace("_", " ")
    season_tag = season_tag.lower()
    if season_tag == "homepage":
        search_final = search
        query_input = search_final
    else:
        season_number = int(season_tag[1:])
        query_input = generate_season_variations(search, season_number)
        search_final = query_input[0] if query_input else search

    BUTTONS[key] = search_final
    try:
        if ANYONE_CAN_CLICK is False and int(req) not in [query.from_user.id, 0]:
            return await query.answer("⚠️ Not your request", show_alert=True)
    except Exception:
        pass

    chat_id = query.message.chat.id
    files, n_offset, total_results = await get_search_results(chat_id, query_input, offset=0, filter=True)
    if not files:
        BUTTONS[key] = None
        return await query.answer("🚫 ɴᴏ ꜰɪʟᴇꜱ ꜰᴏᴜɴᴅ 🚫", show_alert=True)

    temp.GETALL[key] = files
    settings = await get_settings(chat_id)
    btn: list[list[InlineKeyboardButton]] = []
    if settings.get("button"):
        btn.extend(
            [
                [
                    InlineKeyboardButton(
                        f"🔗 {get_size(f.file_size)} ≽ " +
                        display_name(f.file_name, f.caption),
                        callback_data=f"file#{f.file_id}",
                    )
                ]
                for f in files
            ]
        )
    btn.insert(
        0,
        [
            InlineKeyboardButton("Qᴜᴀʟɪᴛʏ", callback_data=f"qualities#{req}#{key}"),
            InlineKeyboardButton("Lᴀɴɢᴜᴀɢᴇ", callback_data=f"languages#{req}#{key}"),
            InlineKeyboardButton("Sᴇᴀꜱᴏɴ", callback_data=f"seasons#{req}#{key}"),
        ],
    )
    btn.insert(
        0,
        [
            InlineKeyboardButton(
                "ʀᴇᴍᴏᴠᴇ ᴀᴅs", url=f"https://t.me/{temp.U_NAME}?start=premium", style=enums.ButtonStyle.PRIMARY),
            InlineKeyboardButton("Sᴇɴᴅ Aʟʟ", callback_data=f"sendfiles#{key}", style=enums.ButtonStyle.SUCCESS),
        ],
    )
    if n_offset != "":
        try:
            if settings['max_btn']:
                btn.append(
                    [InlineKeyboardButton("ᴘᴀɢᴇ", callback_data="pages"), InlineKeyboardButton(
                        text=f"1/{math.ceil(int(total_results)/10)}", callback_data="pages"), InlineKeyboardButton(text="ɴᴇxᴛ ⋟", callback_data=f"next_{req}_{key}_{n_offset}")]
                )

            else:
                btn.append(
                    [InlineKeyboardButton("ᴘᴀɢᴇ", callback_data="pages"), InlineKeyboardButton(
                        text=f"1/{math.ceil(int(total_results)/int(MAX_B_TN))}", callback_data="pages"), InlineKeyboardButton(text="ɴᴇxᴛ ⋟", callback_data=f"next_{req}_{key}_{n_offset}")]
                )
        except KeyError:
            await save_group_settings(query.message.chat.id, 'max_btn', True)
            btn.append(
                [InlineKeyboardButton("ᴘᴀɢᴇ", callback_data="pages"), InlineKeyboardButton(
                    text=f"1/{math.ceil(int(total_results)/10)}", callback_data="pages"), InlineKeyboardButton(text="ɴᴇxᴛ ⋟", callback_data=f"next_{req}_{key}_{n_offset}")]
            )
    else:
        n_offset = 0
        btn.append(
            [InlineKeyboardButton(
                "↭  ɴᴏ ᴍᴏʀᴇ ᴘᴀɢᴇꜱ ᴀᴠᴀɪʟᴀʙʟᴇ ↭", callback_data="pages")]
        )
    if not settings.get("button"):
        curr_time = datetime.now(pytz.timezone("Asia/Kolkata")).time()
        time_difference = timedelta(
            hours=curr_time.hour,
            minutes=curr_time.minute,
            seconds=curr_time.second + curr_time.microsecond / 1_000_000,
        )
        remaining_seconds = f"{time_difference.total_seconds():.2f}"
        dreamx_title = clean_search_text(search_final)
        cap = await get_cap(settings, remaining_seconds, files, query, total_results, dreamx_title, offset=1)
        try:
            await query.message.edit_text(
                text=cap,
                reply_markup=InlineKeyboardMarkup(btn),
                link_preview_options=LinkPreviewOptions(is_disabled=True),
            )
        except (MessageNotModified, MessageIdInvalid):
            pass
    else:
        try:
            await query.edit_message_reply_markup(InlineKeyboardMarkup(btn))
        except (MessageNotModified, MessageIdInvalid):
            pass
    await query.answer()


@Client.on_callback_query(group=10)
async def cb_handler(client: Client, query: CallbackQuery):
    DreamxData = query.data
    if query.data == "close_data":
        try:
            user = query.message.reply_to_message.from_user.id
        except Exception:
            user = query.from_user.id
        if int(user) != 0 and query.from_user.id != int(user):
            return await query.answer(script.NT_ALRT_TXT, show_alert=True)
        await query.answer("ᴛʜᴀɴᴋs ꜰᴏʀ ᴄʟᴏsᴇ 🙈")
        await query.message.delete()
        try:
            await query.message.reply_to_message.delete()
        except Exception:
            pass

    elif query.data == "pages":
        await query.answer("ᴛʜɪs ɪs ᴘᴀɢᴇs ʙᴜᴛᴛᴏɴ 😅")



    if query.data.startswith("file"):
        ident, file_id = query.data.split("#")
        user = query.message.reply_to_message.from_user.id if query.message.reply_to_message else query.from_user.id
        if ANYONE_CAN_CLICK is False and int(user) != 0 and query.from_user.id != int(user):
            return await query.answer(script.ALRT_TXT.format(query.from_user.first_name), show_alert=True)
        await query.answer(url=f"https://t.me/{temp.U_NAME}?start=file_{query.message.chat.id}_{file_id}")

    elif query.data.startswith("cardsend"):
        ident, key = query.data.split("#")
        try:
            await query.answer(url=f"https://telegram.me/{temp.U_NAME}?start=postcard_{query.message.chat.id}_{key}")
            return
        except UserIsBlocked:
            await query.answer('Uɴʙʟᴏᴄᴋ ᴛʜᴇ ʙᴏᴛ ᴍᴀʜɴ !', show_alert=True)
        except PeerIdInvalid:
            await query.answer(url=f"https://telegram.me/{temp.U_NAME}?start=cardsend3_{key}")
        except Exception as e:
            logger.exception(e)
            await query.answer(url=f"https://telegram.me/{temp.U_NAME}?start=cardsend4_{key}")

    elif query.data.startswith("sendfiles"):
        ident, key = query.data.split("#")
        settings = await get_settings(query.message.chat.id)
        try:
            await query.answer(url=f"https://telegram.me/{temp.U_NAME}?start=allfiles_{query.message.chat.id}_{key}")
            return
        except UserIsBlocked:
            await query.answer('Uɴʙʟᴏᴄᴋ ᴛʜᴇ ʙᴏᴛ ᴍᴀʜɴ !', show_alert=True)
        except PeerIdInvalid:
            await query.answer(url=f"https://telegram.me/{temp.U_NAME}?start=sendfiles3_{key}")
        except Exception as e:
            logger.exception(e)
            await query.answer(url=f"https://telegram.me/{temp.U_NAME}?start=sendfiles4_{key}")



    elif query.data.startswith("autofilter_delete"):
        if not query.from_user or query.from_user.id not in OWNER_IDS:
            return await query.answer("Only the bot owner can do this.", show_alert=True)
        await Media.collection.drop()
        if MULTIPLE_DB:    
            await Media2.collection.drop()
        await query.answer("Eᴠᴇʀʏᴛʜɪɴɢ's Gᴏɴᴇ")
        await query.message.edit('ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ ᴅᴇʟᴇᴛᴇᴅ ᴀʟʟ ɪɴᴅᴇxᴇᴅ ꜰɪʟᴇꜱ ✅')
    elif query.data == "fsubgate":
        btn = await get_fsub_buttons(client, query.from_user.id)
        if btn:
            btn.append([InlineKeyboardButton("♻️ ᴛʀʏ ᴀɢᴀɪɴ ♻️", callback_data="fsubgate")])
            try:
                await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(btn))
            except (MessageNotModified, MessageIdInvalid):
                pass
            return await query.answer("🛑 Pehle sabhi channel join kijiye, phir Try Again dabaiye.", show_alert=True)
        await query.answer("✅ Thank you! Ab /start bhejiye ya movie ka naam likhiye.", show_alert=True)
        try:
            await query.message.delete()
        except Exception:
            pass
    elif query.data.startswith("checksub"):
        try:
            ident, kk, file_id = query.data.split("#")
            btn = []
            chat = file_id.split("_")[0]
            settings = await get_settings(chat)
            fsub_channels = list(dict.fromkeys((settings.get('fsub', []) if settings else [])+ AUTH_CHANNELS)) 
            btn += await is_subscribed(client, query.from_user.id, fsub_channels)
            btn += await is_req_subscribed(client, query.from_user.id, AUTH_REQ_CHANNELS)
            if btn:
                btn.append([InlineKeyboardButton("♻️ ᴛʀʏ ᴀɢᴀɪɴ ♻️", callback_data=f"checksub#{kk}#{file_id}")])
                try:
                    await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(btn))
                except (MessageNotModified, MessageIdInvalid):
                    pass
                await query.answer(
                    f"👋 Hello {query.from_user.first_name},\n\n"
                    "🛑 Yᴏᴜ ʜᴀᴠᴇ ɴᴏᴛ ᴊᴏɪɴᴇᴅ ᴀʟʟ ʀᴇǫᴜɪʀᴇᴅ ᴜᴘᴅᴀᴛᴇ Cʜᴀɴɴᴇʟs.\n"
                    "👉 Pʟᴇᴀsᴇ ᴊᴏɪɴ ᴇᴀᴄʜ ᴏɴᴇ ᴀɴᴅ ᴛʀʏ ᴀɢᴀɪɴ.\n",
                    show_alert=True
                )
                return
            await query.answer(url=f"https://t.me/{temp.U_NAME}?start={kk}_{file_id}")
            if query.message.chat.type == enums.ChatType.PRIVATE:
                try:
                    await query.message.delete()
                except MessageDeleteForbidden:
                    pass
        except Exception as e:
            await log_error(client, f"❌ Error in checksub callback:\n\n{repr(e)}")
            logger.error(f"❌ Error in checksub callback:\n\n{repr(e)}")


    elif query.data.startswith("killfilesdq"):
        if not query.from_user or query.from_user.id not in OWNER_IDS:
            return await query.answer("Only the bot owner can do this.", show_alert=True)
        ident, keyword = query.data.split("#")
        await query.message.edit_text(f"<b>Fetching Files for your query {keyword} on DB... Please wait...</b>")
        files, total = await get_bad_files(keyword)
        await query.message.edit_text("<b>ꜰɪʟᴇ ᴅᴇʟᴇᴛɪᴏɴ ᴘʀᴏᴄᴇꜱꜱ ᴡɪʟʟ ꜱᴛᴀʀᴛ ɪɴ 5 ꜱᴇᴄᴏɴᴅꜱ !</b>")
        await asyncio.sleep(5)
        deleted = 0
        async with lock:
            try:
                for file in files:
                    file_ids = file.file_id
                    file_name = file.file_name
                    result = await Media.collection.delete_one({
                        '_id': file_ids,
                    })
                    if not result.deleted_count and MULTIPLE_DB:
                        result = await Media2.collection.delete_one({
                            '_id': file_ids,
                        })
                    if result.deleted_count:
                        logger.info(
                            f'ꜰɪʟᴇ ꜰᴏᴜɴᴅ ꜰᴏʀ ʏᴏᴜʀ ǫᴜᴇʀʏ {keyword}! ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ ᴅᴇʟᴇᴛᴇᴅ {file_name} ꜰʀᴏᴍ ᴅᴀᴛᴀʙᴀꜱᴇ.')
                    deleted += 1
                    if deleted % 20 == 0:
                        await query.message.edit_text(f"<b>ᴘʀᴏᴄᴇꜱꜱ ꜱᴛᴀʀᴛᴇᴅ ꜰᴏʀ ᴅᴇʟᴇᴛɪɴɢ ꜰɪʟᴇꜱ ꜰʀᴏᴍ ᴅʙ. ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ ᴅᴇʟᴇᴛᴇᴅ {str(deleted)} ꜰɪʟᴇꜱ ꜰʀᴏᴍ ᴅʙ ꜰᴏʀ ʏᴏᴜʀ ǫᴜᴇʀʏ {keyword} !\n\nᴘʟᴇᴀꜱᴇ ᴡᴀɪᴛ...</b>")
            except Exception as e:
                logger.error("Error In killfiledq: %s", e)
                await query.message.edit_text(f'Error: {e}')
            else:
                await query.message.edit_text(f"<b>ᴘʀᴏᴄᴇꜱꜱ ᴄᴏᴍᴘʟᴇᴛᴇᴅ ꜰᴏʀ ꜰɪʟᴇ ᴅᴇʟᴇᴛᴀᴛɪᴏɴ !\n\nꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ ᴅᴇʟᴇᴛᴇᴅ {str(deleted)} ꜰɪʟᴇꜱ ꜰʀᴏᴍ ᴅʙ ꜰᴏʀ ʏᴏᴜʀ ǫᴜᴇʀʏ {keyword}.</b>")

    elif query.data.startswith("opnsetgrp"):
        ident, grp_id = query.data.split("#")
        userid = query.from_user.id if query.from_user else None
        try:
            st_status = (await client.get_chat_member(int(grp_id), userid)).status
        except Exception:
            st_status = None
        if (
                st_status != enums.ChatMemberStatus.ADMINISTRATOR
                and st_status != enums.ChatMemberStatus.OWNER
                and userid not in ADMINS
        ):
            await query.answer("ʏᴏᴜ ᴅᴏɴ'ᴛ ʜᴀᴠᴇ ʀɪɢʜᴛꜱ ᴛᴏ ᴅᴏ ᴛʜɪꜱ !", show_alert=True)
            return
        title = query.message.chat.title
        settings = await get_settings(grp_id)
        if settings is not None:
            btn = await group_setting_buttons(int(grp_id))
            reply_markup = InlineKeyboardMarkup(btn)
            await query.message.edit_text(
                text=await get_settings_text(grp_id, title),
                link_preview_options=LinkPreviewOptions(is_disabled=True),
                parse_mode=enums.ParseMode.HTML
            )
            await query.message.edit_reply_markup(reply_markup)

    elif query.data.startswith("opnsetpm"):
        ident, grp_id = query.data.split("#")
        userid = query.from_user.id if query.from_user else None
        try:
            st_status = (await client.get_chat_member(int(grp_id), userid)).status
        except Exception:
            st_status = None
        if (
                st_status != enums.ChatMemberStatus.ADMINISTRATOR
                and st_status != enums.ChatMemberStatus.OWNER
                and userid not in ADMINS
        ):
            await query.answer("Yᴏᴜ ᴅᴏɴ'ᴛ ʜᴀᴠᴇ sᴜғғɪᴄɪᴀɴᴛ ʀɪɢʜᴛs ᴛᴏ ᴅᴏ ᴛʜɪs !", show_alert=True)
            return
        title = query.message.chat.title
        settings = await get_settings(grp_id)
        btn2 = [[
            InlineKeyboardButton(
                "ᴄʜᴇᴄᴋ ᴍʏ ᴅᴍ 🗳️", url=f"telegram.me/{temp.U_NAME}")
        ]]
        reply_markup = InlineKeyboardMarkup(btn2)
        await query.message.edit_text(f"<b>ʏᴏᴜʀ sᴇᴛᴛɪɴɢs ᴍᴇɴᴜ ғᴏʀ {title} ʜᴀs ʙᴇᴇɴ sᴇɴᴛ ᴛᴏ ʏᴏᴜ ʙʏ ᴅᴍ.</b>")
        await query.message.edit_reply_markup(reply_markup)
        if settings is not None:
            btn = await group_setting_buttons(int(grp_id))
            reply_markup = InlineKeyboardMarkup(btn)
            await client.send_message(
                chat_id=userid,
                text=await get_settings_text(grp_id, title),
                reply_markup=reply_markup,
                link_preview_options=LinkPreviewOptions(is_disabled=True),
                parse_mode=enums.ParseMode.HTML
            )

    elif query.data.startswith("show_option"):
        ident, from_user = query.data.split("#")
        btn = [[
            InlineKeyboardButton("⚠️ ᴜɴᴀᴠᴀɪʟᴀʙʟᴇ ⚠️", callback_data=f"unavailable#{from_user}"),
            InlineKeyboardButton("🟢 ᴜᴘʟᴏᴀᴅᴇᴅ 🟢", callback_data=f"uploaded#{from_user}")
        ], [
            InlineKeyboardButton("♻️ ᴀʟʀᴇᴀᴅʏ ᴀᴠᴀɪʟᴀʙʟᴇ ♻️", callback_data=f"already_available#{from_user}")
        ], [
            InlineKeyboardButton("📌 Not Released 📌", callback_data=f"Not_Released#{from_user}"),
            InlineKeyboardButton("♨️Type Correct Spelling♨️", callback_data=f"Type_Correct_Spelling#{from_user}")
        ], [
            InlineKeyboardButton("⚜️ Not Available In The Hindi ⚜️", callback_data=f"Not_Available_In_The_Hindi#{from_user}")
        ]]
        if query.from_user.id in ADMINS:
            reply_markup = InlineKeyboardMarkup(btn)
            await query.message.edit_reply_markup(reply_markup)
            await query.answer("Hᴇʀᴇ ᴀʀᴇ ᴛʜᴇ ᴏᴘᴛɪᴏɴs !")
        else:
            await query.answer("Yᴏᴜ ᴅᴏɴ'ᴛ ʜᴀᴠᴇ sᴜғғɪᴄɪᴀɴᴛ ʀɪɢʜᴛs ᴛᴏ ᴅᴏ ᴛʜɪs !", show_alert=True)

    elif query.data.split("#")[0] in ["unavailable", "Not_Released", "Type_Correct_Spelling", "Not_Available_In_The_Hindi", "uploaded", "already_available"]:
        key = query.data.split("#")[0]
        ident, from_user = query.data.split("#")
        if query.from_user.id not in ADMINS:
            return await query.answer("Yᴏᴜ ᴅᴏɴ'ᴛ ʜᴀᴠᴇ sᴜғғɪᴄɪᴀɴᴛ ʀɪɢʜᴛs ᴛᴏ ᴅᴏ ᴛʜɪs !", show_alert=True)
            
        status_configs = {
            "unavailable": {
                "btn_text": "⚠️ ᴜɴᴀᴠᴀɪʟᴀʙʟᴇ ⚠️", "alert_key": "unalert", "answer": "Sᴇᴛ ᴛᴏ Uɴᴀᴠᴀɪʟᴀʙʟᴇ !",
                "pm": "<b>Hᴇʏ {mention},</b>\n\n<u>{content}</u> Hᴀs Bᴇᴇɴ Mᴀʀᴋᴇᴅ Aᴅ ᴜɴᴀᴠᴀɪʟᴀʙʟᴇ...💔\n\n#Uɴᴀᴠᴀɪʟᴀʙʟᴇ ⚠️",
                "sup": "<b>Hᴇʏ {mention},</b>\n\n<u>{content}</u> Hᴀs Bᴇᴇɴ Mᴀʀᴋᴇᴅ Aᴅ ᴜɴᴀᴠᴀɪʟᴀʙʟᴇ...💔\n\n#Uɴᴀᴠᴀɪʟᴀʙʟᴇ ⚠️\n\n<small>Bʟᴏᴄᴋᴇᴅ? Uɴʙʟᴏᴄᴋ ᴛʜᴇ ʙᴏᴛ ᴛᴏ ʀᴇᴄᴇɪᴠᴇ ᴍᴇꜱꜱᴀɢᴇꜱ.</small></b>",
                "search": False
            },
            "Not_Released": {
                "btn_text": "📌 Not Released 📌", "alert_key": "nralert", "answer": "Sᴇᴛ ᴛᴏ Nᴏᴛ Rᴇʟᴇᴀꜱᴇᴅ !",
                "pm": "<b>Hᴇʏ {mention}\n\n<code>{content}</code>, ʏᴏᴜʀ ʀᴇǫᴜᴇꜱᴛ ʜᴀꜱ ɴᴏᴛ ʙᴇᴇɴ ʀᴇʟᴇᴀꜱᴇᴅ ʏᴇᴛ\n\n#CᴏᴍɪɴɢSᴏᴏɴ...🕊️✌️</b>",
                "sup": "<u>Hᴇʏ {mention}</u>\n\n<b><code>{content}</code>, ʏᴏᴜʀ ʀᴇǫᴜᴇꜱᴛ ʜᴀꜱ ɴᴏᴛ ʙᴇᴇɴ ʀᴇʟᴇᴀꜱᴇᴅ ʏᴇᴛ\n\n#CᴏᴍɪɴɢSᴏᴏɴ...🕊️✌️\n\n<small>Bʟᴏᴄᴋᴇᴅ? Uɴʙʟᴏᴄᴋ ᴛʜᴇ ʙᴏᴛ ᴛᴏ ʀᴇᴄᴇɪᴠᴇ ᴍᴇꜱꜱᴀɢᴇꜱ.</small></b>",
                "search": False
            },
            "Type_Correct_Spelling": {
                "btn_text": "♨️ Type Correct Spelling ♨️", "alert_key": "wsalert", "answer": "Sᴇᴛ ᴛᴏ Cᴏʀʀᴇᴄᴛ Sᴘᴇʟʟɪɴɢ !",
                "pm": "<b>Hᴇʏ {mention}\n\nWᴇ Dᴇᴄʟɪɴᴇᴅ Yᴏᴜʀ Rᴇǫᴜᴇsᴛ <code>{content}</code>, Bᴇᴄᴀᴜsᴇ Yᴏᴜʀ Sᴘᴇʟʟɪɴɢ Wᴀs Wʀᴏɴɢ 😢\n\n#Wʀᴏɴɢ_Sᴘᴇʟʟɪɴɢ 😑</b>",
                "sup": "<u>Hᴇʏ {mention}</u>\n\n<b><code>{content}</code>, Bᴇᴄᴀᴜsᴇ Yᴏᴜʀ Sᴘᴇʟʟɪɴɢ Wᴀs Wʀᴏɴɢ 😢\n\n#Wʀᴏɴɢ_Sᴘᴇʟʟɪɴɢ 😑\n\n<small>Bʟᴏᴄᴋᴇᴅ? Uɴʙʟᴏᴄᴋ ᴛʜᴇ ʙᴏᴛ ᴛᴏ ʀᴇᴄᴇɪᴠᴇ ᴍᴇꜱꜱᴀɢᴇꜱ.</small></b>",
                "search": False
            },
            "Not_Available_In_The_Hindi": {
                "btn_text": "⚜️ Not Available In The Hindi ⚜️", "alert_key": "hnalert", "answer": "Sᴇᴛ ᴛᴏ Nᴏᴛ Aᴠᴀɪʟᴀʙʟᴇ Iɴ Hɪɴᴅɪ !",
                "pm": "<b>Hᴇʏ {mention}\n\nYᴏᴜʀ Rᴇǫᴜᴇsᴛ <code>{content}</code> ɪs Nᴏᴛ Aᴠᴀɪʟᴀʙʟᴇ ɪɴ Hɪɴᴅɪ ʀɪɢʜᴛ ɴᴏᴡ. Sᴏ ᴏᴜʀ ᴍᴏᴅᴇʀᴀᴛᴏʀs ᴄᴀɴ'ᴛ ᴜᴘʟᴏᴀᴅ ɪᴛ\n\n#Hɪɴᴅɪ_ɴᴏᴛ_ᴀᴠᴀɪʟᴀʙʟᴇ ❌</b>",
                "sup": "<u>Hᴇʏ {mention}</u>\n\n<b><code>{content}</code> ɪs Nᴏᴛ Aᴠᴀɪʟᴀʙʟᴇ ɪɴ Hɪɴᴅɪ ʀɪɢʜᴛ ɴᴏᴡ. Sᴏ ᴏᴜʀ ᴍᴏᴅᴇʀᴀᴛᴏʀs ᴄᴀɴ'ᴛ ᴜᴘʟᴏᴀᴅ ɪᴛ\n\n#Hɪɴᴅɪ_ɴᴏᴛ_ᴀᴠᴀɪʟᴀʙʟᴇ ❌\n\n<small>Bʟᴏᴄᴋᴇᴅ? Uɴʙʟᴏᴄᴋ ᴛʜᴇ ʙᴏᴛ ᴛᴏ ʀᴇᴄᴇɪᴠᴇ ᴍᴇꜱꜱᴀɢᴇꜱ.</small></b>",
                "search": False
            },
            "uploaded": {
                "btn_text": "🟢 ᴜᴘʟᴏᴀᴅᴇᴅ 🟢", "alert_key": "upalert", "answer": "Sᴇᴛ ᴛᴏ Uᴘʟᴏᴀᴅᴇᴅ !",
                "pm": "<b>Hᴇʏ {mention},\n\n<u>{content}</u> Yᴏᴜʀ ʀᴇǫᴜᴇꜱᴛ ʜᴀꜱ ʙᴇᴇɴ ᴜᴘʟᴏᴀᴅᴇᴅ ʙʏ ᴏᴜʀ ᴍᴏᴅᴇʀᴀᴛᴏʀs.\nKɪɴᴅʟʏ sᴇᴀʀᴄʜ ɪɴ ᴏᴜʀ Gʀᴏᴜᴘ.</b>\n\n#Uᴘʟᴏᴀᴅᴇᴅ✅",
                "sup": "<u>{content}</u>\n\n<b>Hᴇʏ {mention}, Yᴏᴜʀ ʀᴇǫᴜᴇꜱᴛ ʜᴀꜱ ʙᴇᴇɴ ᴜᴘʟᴏᴀᴅᴇᴅ ʙʏ ᴏᴜʀ ᴍᴏᴅᴇʀᴀᴛᴏʀs.Kɪɴᴅʟʏ sᴇᴀʀᴄʜ ɪɴ ᴏᴜʀ Gʀᴏᴜᴘ.</b>\n\n#Uᴘʟᴏᴀᴅᴇᴅ✅\n\n<small>Bʟᴏᴄᴋᴇᴅ? Uɴʙʟᴏᴄᴋ ᴛʜᴇ ʙᴏᴛ ᴛᴏ ʀᴇᴄᴇɪᴠᴇ ᴍᴇꜱꜱᴀɢᴇꜱ.</small>",
                "search": True
            },
            "already_available": {
                "btn_text": "♻️ ᴀʟʀᴇᴀᴅʏ ᴀᴠᴀɪʟᴀʙʟᴇ ♻️", "alert_key": "alalert", "answer": "Sᴇᴛ ᴛᴏ Aʟʀᴇᴀᴅʏ Aᴠᴀɪʟᴀʙʟᴇ !",
                "pm": "<b>Hᴇʏ {mention},\n\n<u>{content}</u> Yᴏᴜʀ ʀᴇǫᴜᴇꜱᴛ ɪꜱ ᴀʟʀᴇᴀᴅʏ ᴀᴠᴀɪʟᴀʙʟᴇ ɪɴ ᴏᴜʀ ʙᴏᴛ'ꜱ ᴅᴀᴛᴀʙᴀꜱᴇ.\nKɪɴᴅʟʏ sᴇᴀʀᴄʜ ɪɴ ᴏᴜʀ Gʀᴏᴜᴘ.</b>\n\n#Aᴠᴀɪʟᴀʙʟᴇ 💗",
                "sup": "<b>Hᴇʏ {mention},\n\n<u>{content}</u> Yᴏᴜʀ ʀᴇǫᴜᴇꜱᴛ ɪꜱ ᴀʟʀᴇᴀᴅʏ ᴀᴠᴀɪʟᴀʙʟᴇ ɪɴ ᴏᴜʀ ʙᴏᴛ'ꜱ ᴅᴀᴛᴀʙᴀꜱᴇ.\nKɪɴᴅʟʏ sᴇᴀʀᴄʜ ɪɴ ᴏᴜʀ Gʀᴏᴜᴘ.</b>\n\n#Aᴠᴀɪʟᴀʙʟᴇ 💗\n<small>Bʟᴏᴄᴋᴇᴅ? Uɴʙʟᴏᴄᴋ ᴛʜᴇ ʙᴏᴛ ᴛᴏ ʀᴇᴄᴇɪᴠᴇ ᴍᴇꜱꜱᴀɢES.</small></i>",
                "search": True
            }
        }
        cfg = status_configs[key]
        user = await client.get_users(int(from_user))
        btn = [[InlineKeyboardButton(cfg["btn_text"], callback_data=f'{cfg["alert_key"]}#{from_user}')]]
        btn2 = [[
            InlineKeyboardButton('ᴊᴏɪɴ ᴄʜᴀɴɴᴇʟ', url=UPDATE_CHNL_LNK),
            InlineKeyboardButton("ᴠɪᴇᴡ ꜱᴛᴀᴛᴜꜱ", url=f"{query.message.link}")
        ]]
        if cfg["search"]:
            btn2.append([InlineKeyboardButton("🔍 ꜱᴇᴀʀᴄʜ ʜᴇʀᴇ 🔎", url=GRP_LNK)])
            
        content = query.message.text
        await query.message.edit_text(f"<b><strike>{content}</strike></b>")
        await query.message.edit_reply_markup(InlineKeyboardMarkup(btn))
        await query.answer(cfg["answer"])
        
        req_content = extract_request_content(query.message.text)
        try:
            await client.send_message(
                chat_id=int(from_user),
                text=cfg["pm"].format(mention=user.mention, content=req_content),
                reply_markup=InlineKeyboardMarkup(btn2)
            )
        except UserIsBlocked:
            await client.send_message(
                chat_id=int(SUPPORT_CHAT_ID),
                text=cfg["sup"].format(mention=user.mention, content=req_content),
                reply_markup=InlineKeyboardMarkup(btn2)
            )

    elif query.data.split("#")[0] in ["alalert", "upalert", "unalert", "hnalert", "nralert", "wsalert"]:
        ident, from_user = query.data.split("#")
        alerts = {
            "alalert": "Hᴇʏ {name}, Yᴏᴜʀ ʀᴇǫᴜᴇꜱᴛ ɪꜱ Aʟʀᴇᴀᴅʏ Aᴠᴀɪʟᴀʙʟᴇ ✅",
            "upalert": "Hᴇʏ {name}, Yᴏᴜʀ ʀᴇǫᴜESꜱᴛ ɪꜱ Uᴘʟᴏᴀᴅᴇᴅ 🔼",
            "unalert": "Hᴇʏ {name}, Yᴏᴜʀ ʀᴇǫᴜᴇꜱᴛ ɪꜱ Uɴᴀᴠᴀɪʟᴀʙʟᴇ ⚠️",
            "hnalert": "Hᴇʏ {name}, Tʜɪꜱ ɪꜱ Nᴏᴛ Aᴠᴀɪʟᴀʙʟᴇ ɪɴ Hɪɴᴅɪ ❌",
            "nralert": "Hᴇʏ {name}, Tʜᴇ Mᴏᴠɪᴇ/ꜱʜᴏᴡ ɪꜱ Nᴏᴛ Rᴇʟᴇᴀꜱᴇᴅ Yᴇᴛ 🆕",
            "wsalert": "Hᴇʏ {name}, Yᴏᴜʀ Rᴇǫᴜᴇꜱᴛ ᴡᴀꜱ ʀᴇᴊᴇᴄᴛᴇᴅ ᴅᴜᴇ ᴛᴏ ᴡʀᴏɴɢ sᴘᴇʟʟɪɴɢ ❗"
        }
        if int(query.from_user.id) == int(from_user):
            user = await client.get_users(from_user)
            msg_text = alerts[ident].format(name=user.first_name)
            await query.answer(msg_text, show_alert=True)
        else:
            await query.answer("Yᴏᴜ ᴅᴏɴ'ᴛ ʜᴀᴠᴇ ᴘᴇʀᴍɪssɪᴏɴ ᴛᴏ sᴇᴇ ᴛʜɪꜱ ❌", show_alert=True)

    elif DreamxData.startswith("generate_stream_link"):
        _, file_id = DreamxData.split(":")
        try:
            user_id = query.from_user.id
            username = query.from_user.mention
            log_msg = await client.send_cached_media(chat_id=BIN_CHANNEL, file_id=file_id,)
            fileName = quote_plus(get_name(log_msg))
            dreamx_stream = f"{URL}watch/{str(log_msg.id)}/{quote_plus(get_name(log_msg))}?hash={get_hash(log_msg)}"
            dreamx_download = f"{URL}{str(log_msg.id)}/{quote_plus(get_name(log_msg))}?hash={get_hash(log_msg)}"
            await query.answer(MSG_ALRT)
            await asyncio.sleep(1)
            await log_msg.reply_text(
                text=f"•• ʟɪɴᴋ ɢᴇɴᴇʀᴀᴛᴇᴅ ꜰᴏʀ ɪᴅ #{user_id} \n•• ᴜꜱᴇʀɴᴀᴍᴇ : {username} \n\n•• ᖴᎥᒪᗴ Nᗩᗰᗴ : {fileName}",
                link_preview_options=LinkPreviewOptions(is_disabled=True),
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🚀 ꜰᴀꜱᴛ ᴅᴏᴡɴʟᴏᴀᴅ 🚀", url=dreamx_download),  # we download Link
                                                    InlineKeyboardButton('🖥️ ᴡᴀᴛᴄʜ ᴏɴʟɪɴᴇ 🖥️', url=dreamx_stream)]])  # web stream Link
            )
            dreamcinezone = await query.edit_message_reply_markup(
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("🚀 ꜰᴀꜱᴛ ᴅᴏᴡɴʟᴏᴀᴅ 🚀", url=dreamx_download),
                        InlineKeyboardButton('🖥️ ᴡᴀᴛᴄʜ ᴏɴʟɪɴᴇ 🖥️', url=dreamx_stream)
                    ]
                ])
        )
            spawn_delete(DELETE_TIME, dreamcinezone)
            return
        except Exception as e:
            logger.error("Callback error: %s", e)
            await query.answer(f"⚠️ SOMETHING WENT WRONG STREAM LINK  \n\n{e}", show_alert=True)
            return


    elif query.data == "prestream":
        await query.answer(text=script.PRE_STREAM_ALERT, show_alert=True)
        dreamcinezone = await client.send_photo(
            chat_id=query.message.chat.id,
            photo="https://i.ibb.co/whf8xF7j/photo-2025-07-26-10-42-46-7531339305176793100.jpg",
            caption=script.PRE_STREAM,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🚀 Buy Premium 🚀", callback_data="premium_info")]
            ])
        )
        spawn_delete(DELETE_TIME, dreamcinezone)




    elif query.data == "start":
        buttons = [[
                    InlineKeyboardButton('✧ 𝐀𝐝𝐝 𝐌𝐞 𝐓𝐨 𝐘𝐨𝐮𝐫 𝐆𝐫𝐨𝐮𝐩 ✧', url=f'http://telegram.me/{temp.U_NAME}?startgroup=true')
                ],[
                    InlineKeyboardButton(' ✧ 𝐇𝐞𝐥𝐩 ✧', callback_data='help')
                    
                ],[
                    InlineKeyboardButton('🔍 𝐓𝐨𝐩 𝐒𝐞𝐚𝐫𝐜𝐡𝐢𝐧𝐠 🔎', callback_data="topsearch"),
                     InlineKeyboardButton('⏣ 𝐔𝐩𝐠𝐫𝐚𝐝𝐞 ⏣', callback_data="premium_info"),
                ]]
        reply_markup = InlineKeyboardMarkup(buttons)
        current_time = datetime.now(pytz.timezone(TIMEZONE))
        curr_time = current_time.hour
        if curr_time < 12:
            gtxt = "ɢᴏᴏᴅ ᴍᴏʀɴɪɴɢ 🌞"
        elif curr_time < 17:
            gtxt = "ɢᴏᴏᴅ ᴀғᴛᴇʀɴᴏᴏɴ 🌓"
        elif curr_time < 21:
            gtxt = "ɢᴏᴏᴅ ᴇᴠᴇɴɪɴɢ 🌘"
        else:
            gtxt = "ɢᴏᴏᴅ ɴɪɢʜᴛ 🌑"
        try:
            if len(PICS) == 1:
                PIC = PICS[0]
            else:
                try:
                                        PIC = random.choice(PICS)
                except Exception:
                    PIC = random.choice(PICS)
            await client.edit_message_media(
                query.message.chat.id,
                query.message.id,
                InputMediaPhoto(PIC)
            )
        except Exception:
            pass
        await query.message.edit_text(
            text=script.START_TXT.format(query.from_user.mention, gtxt, temp.U_NAME, temp.B_NAME),
            reply_markup=reply_markup,
            parse_mode=enums.ParseMode.HTML
        )
        await query.answer(MSG_ALRT)

    elif query.data == "donation":
        buttons = [[
                InlineKeyboardButton('🌲 Sᴇɴᴅ Dᴏɴᴀᴛᴇ Sᴄʀᴇᴇɴsʜᴏᴛ Hᴇʀᴇ', url=OWNER_LNK)
            ],[
                InlineKeyboardButton('⇍ ʙᴀᴄᴋ ⇏', callback_data='about')
            ]]
        reply_markup = InlineKeyboardMarkup(buttons)
        await query.message.edit_text(text="● ◌ ◌")
        await query.message.edit_text(text="● ● ◌")
        await query.message.edit_text(text="● ● ●")
        reply_markup = InlineKeyboardMarkup(buttons)
        await client.edit_message_media(
            query.message.chat.id,
            query.message.id,
            InputMediaPhoto('https://graph.org/file/99eebf5dbe8a134f548e0.jpg')
        )
        await query.message.edit_text(
            text=script.DREAMXBOTZ_DONATION.format(query.from_user.mention, QR_CODE, OWNER_UPI_ID),
            reply_markup=reply_markup,
            parse_mode=enums.ParseMode.HTML
        )

    elif query.data == "help":
        buttons = [[
            InlineKeyboardButton('⇋ ʙᴀᴄᴋ ᴛᴏ ʜᴏᴍᴇ ⇋', callback_data='start')
        ]]
        reply_markup = InlineKeyboardMarkup(buttons)
        await query.message.edit_text(
            text=script.HELP_TXT,
            reply_markup=reply_markup,
            parse_mode=enums.ParseMode.HTML
        )

    elif query.data == "about":
        buttons = [[
            InlineKeyboardButton('‼️ ᴅɪꜱᴄʟᴀɪᴍᴇʀ ‼️', callback_data='disclaimer'),
            InlineKeyboardButton ('🪔 sᴏᴜʀᴄᴇ', callback_data='source'),
        ],[
            InlineKeyboardButton('ᴅᴏɴᴀᴛɪᴏɴ 💰', callback_data='donation'),
        ],[
            InlineKeyboardButton('⇋ ʙᴀᴄᴋ ᴛᴏ ʜᴏᴍᴇ ⇋', callback_data='start')
        ]]
        reply_markup = InlineKeyboardMarkup(buttons)
        await query.message.edit_text(
            text=script.ABOUT_TXT.format(temp.U_NAME, temp.B_NAME, OWNER_LNK),
            reply_markup=reply_markup,
            link_preview_options=LinkPreviewOptions(is_disabled=True),
            parse_mode=enums.ParseMode.HTML
        )

    elif query.data == "give_trial":
        try:
            user_id = query.from_user.id
            has_free_trial = await db.check_trial_status(user_id)
            if has_free_trial:
                await query.answer(
                    "🚸 ʏᴏᴜ'ᴠᴇ ᴀʟʀᴇᴀᴅʏ ᴄʟᴀɪᴍᴇᴅ ʏᴏᴜʀ ꜰʀᴇᴇ ᴛʀɪᴀʟ ᴏɴᴄᴇ !\n\n📌 ᴄʜᴇᴄᴋᴏᴜᴛ ᴏᴜʀ ᴘʟᴀɴꜱ ʙʏ : /plan",
                    show_alert=True
                )
                return
            else:
                await db.give_free_trial(user_id)
                await query.answer("✅ Trial activated!", show_alert=True)

                msg = await client.send_photo(
                    chat_id=query.message.chat.id,
                    photo="https://i.ibb.co/0jC8MSDZ/photo-2025-07-26-10-42-36-7531339283701956616.jpg",
                    caption=(
                        "<b>🥳 ᴄᴏɴɢʀᴀᴛᴜʟᴀᴛɪᴏɴꜱ\n\n"
                        "🎉 ʏᴏᴜ ᴄᴀɴ ᴜsᴇ ꜰʀᴇᴇ ᴛʀᴀɪʟ ꜰᴏʀ <u>5 ᴍɪɴᴜᴛᴇs</u> ꜰʀᴏᴍ ɴᴏᴡ !\n\n"
                        "ɴᴇᴇᴅ ᴘʀᴇᴍɪᴜᴍ 👉🏻 /plan</b>"
                    ),
                    parse_mode=enums.ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("🚀 Buy Premium 🚀", callback_data="premium_info")
                    ]])
                )
                spawn_delete(DELETE_TIME, msg)
                return
        except Exception:
            logger.exception("Error in give_trial callback")



    elif query.data == "source":
        buttons = [[
            InlineKeyboardButton('ᴅʀᴇᴀᴍxʙᴏᴛᴢ 📜', url='https://github.com/DreamXBotz/Auto_Filter_Bot.git'),
            InlineKeyboardButton('⇋ ʙᴀᴄᴋ ⇋', callback_data='about')
        ]]
        reply_markup = InlineKeyboardMarkup(buttons)
        await query.message.edit_text(
            text=script.SOURCE_TXT,
            reply_markup=reply_markup,
            parse_mode=enums.ParseMode.HTML
        )

    elif query.data == "ref_point":
        _pts = await referdb.get_refer_points(query.from_user.id)
        await query.answer(f'You Have: {_pts} Refferal points.', show_alert=True)

    elif query.data == "disclaimer":
            btn = [[
                    InlineKeyboardButton("⇋ ʙᴀᴄᴋ ⇋", callback_data="about")
                  ]]
            reply_markup = InlineKeyboardMarkup(btn)
            await query.message.edit_text(
                text=(script.DISCLAIMER_TXT),
                reply_markup=reply_markup,
                parse_mode=enums.ParseMode.HTML
            )

    elif query.data == "premium_info":
        try:
            btn = [[
                InlineKeyboardButton('• ʙᴜʏ ᴘʀᴇᴍɪᴜᴍ •', callback_data='buy_info'),
            ],[
                InlineKeyboardButton('• ʀᴇꜰᴇʀ ꜰʀɪᴇɴᴅꜱ', callback_data='reffff'),
                InlineKeyboardButton('ꜰʀᴇᴇ ᴛʀɪᴀʟ •', callback_data='give_trial')
            ],[
                InlineKeyboardButton('⇋ ʙᴀᴄᴋ ᴛᴏ ʜᴏᴍᴇ ⇋', callback_data='start')
            ]]
            reply_markup = InlineKeyboardMarkup(btn)
            await client.edit_message_media(
                chat_id=query.message.chat.id,
                message_id=query.message.id,
                media=InputMediaPhoto(media=SUBSCRIPTION, caption=script.BPREMIUM_TXT, parse_mode=enums.ParseMode.HTML),
                reply_markup=reply_markup
            )
        except Exception:
            logger.exception("Exception in 'premium_info' callback")


    elif query.data == "buy_info":
        try:
            btn = [[
                InlineKeyboardButton('ᴜᴘɪ', callback_data='upi_info')
            ],[
                InlineKeyboardButton('⇋ ʙᴀᴄᴋ ᴛᴏ ᴘʀᴇᴍɪᴜᴍ ⇋', callback_data='premium_info')
            ]]
            reply_markup = InlineKeyboardMarkup(btn)
            await client.edit_message_media(
                chat_id=query.message.chat.id,
                message_id=query.message.id,
                media=InputMediaPhoto(media=SUBSCRIPTION, caption=script.PREMIUM_TEXT, parse_mode=enums.ParseMode.HTML),
                reply_markup=reply_markup
            )
        except Exception:
            logger.exception("Exception in 'buy_info' callback")

    elif query.data == "upi_info":
        try:
            btn = [[
                InlineKeyboardButton('• ꜱᴇɴᴅ  ᴘᴀʏᴍᴇɴᴛ ꜱᴄʀᴇᴇɴꜱʜᴏᴛ •', url=OWNER_LNK),
            ],[
                InlineKeyboardButton('⇋ ʙᴀᴄᴋ ⇋', callback_data='buy_info')
            ]]
            reply_markup = InlineKeyboardMarkup(btn)
            await client.edit_message_media(
                chat_id=query.message.chat.id,
                message_id=query.message.id,
                media=InputMediaPhoto(media=SUBSCRIPTION, caption=script.PREMIUM_UPI_TEXT.format(OWNER_UPI_ID), parse_mode=enums.ParseMode.HTML),
                reply_markup=reply_markup
            )
        except Exception:
            logger.exception("Exception in 'upi_info' callback")



    elif query.data.startswith("grp_pm"):
        _, grp_id = query.data.split("#")
        user_id = query.from_user.id if query.from_user else None
        if not await is_check_admin(client, int(grp_id), user_id):
            return await query.answer(script.NT_ADMIN_ALRT_TXT, show_alert=True)

        btn = await group_setting_buttons(int(grp_id))
        dreamx = await client.get_chat(int(grp_id))
        await query.message.edit(text=await get_settings_text(grp_id, dreamx.title), reply_markup=InlineKeyboardMarkup(btn), parse_mode=enums.ParseMode.HTML)

    elif query.data.startswith("removegrp"):
        user_id = query.from_user.id
        data = query.data
        grp_id = int(data.split("#")[1])
        if not await is_check_admin(client, grp_id, query.from_user.id):
            return await query.answer(script.NT_ADMIN_ALRT_TXT, show_alert=True)
        await db.remove_group_connection(grp_id, user_id)
        await query.answer("Group removed from your connections.", show_alert=True)
        connected_groups = await db.get_connected_grps(user_id)
        if not connected_groups:
            await query.edit_message_text("Nᴏ Cᴏɴɴᴇᴄᴛᴇᴅ Gʀᴏᴜᴘs Fᴏᴜɴᴅ .")
            return
        group_list = []
        for group in connected_groups:
            try:
                Chat = await client.get_chat(group)
                group_list.append([
                    InlineKeyboardButton(
                        text=Chat.title, callback_data=f"grp_pm#{Chat.id}")
                ])
            except Exception:
                pass
        await query.edit_message_text(
            "⚠️ ꜱᴇʟᴇᴄᴛ ᴛʜᴇ ɢʀᴏᴜᴘ ᴡʜᴏꜱᴇ ꜱᴇᴛᴛɪɴɢꜱ ʏᴏᴜ ᴡᴀɴᴛ ᴛᴏ ᴄʜᴀɴɢᴇ.\n\n"
            "ɪꜰ ʏᴏᴜʀ ɢʀᴏᴜᴘ ɪꜱ ɴᴏᴛ ꜱʜᴏᴡɪɴɢ ʜᴇʀᴇ,\n"
            "ᴜꜱᴇ /reload ɪɴ ᴛʜᴀᴛ ɢʀᴏᴜᴘ ᴀɴᴅ ɪᴛ ᴡɪʟʟ ᴀᴘᴘᴇᴀʀ ʜᴇʀᴇ.",
            reply_markup=InlineKeyboardMarkup(group_list)
        )

    elif query.data.startswith("setgs"):
        ident, set_type, status, grp_id = query.data.split("#")
        userid = query.from_user.id if query.from_user else None
        if not await is_check_admin(client, int(grp_id), userid):
            await query.answer(script.NT_ADMIN_ALRT_TXT, show_alert=True)
            return
        if status == "True":
            await save_group_settings(int(grp_id), set_type, False)
            await query.answer("ᴏꜰꜰ ✗")
        else:
            await save_group_settings(int(grp_id), set_type, True)
            await query.answer("ᴏɴ ✓")
        settings = await get_settings(int(grp_id))
        if settings is not None:
            btn = await group_setting_buttons(int(grp_id))
            reply_markup = InlineKeyboardMarkup(btn)
            await query.message.edit_reply_markup(reply_markup)
    await query.answer(MSG_ALRT)


async def auto_filter(client, msg, spoll=False):
    """
    Core auto_filter logic with timing/debug logging removed.
    """
    curr_time = datetime.now(pytz.timezone('Asia/Kolkata')).time()

    async def _schedule_delete(sent_obj, orig_msg, delay, post_msg=None):
        keep([sent_obj, post_msg], delay)     # auto clean: a timer longer than 6 minutes stays as it is
        try:
            # NOTE: the search-result card is no longer archived here.
            # The poster-style post (sent after the user clicks "Download File")
            # is archived to RESULT_ARCHIVE_CHANNEL 30s before it is deleted.
            if post_msg:
                # post is saved to RESULT_ARCHIVE_CHANNEL 30s before everything gets deleted
                await wait_then_archive(client, post_msg, delay)
            else:
                await asyncio.sleep(delay)
            try:
                await sent_obj.delete()
            except Exception:
                pass
            try:
                await orig_msg.delete()
            except Exception:
                pass
            if post_msg:
                try:
                    await post_msg.delete()
                except Exception:
                    pass
        except Exception:
            # ignore scheduling errors
            pass
    m = None
    shown_search = None    # user's own wording for the card title (kept with "&" / "and")
    season_wanted = None   # set when the user typed a season + episode (S07E07): the card then shows only that season
    try:
        if not spoll:
            message = msg
            if message.text.startswith("/"):
                return
            if re.findall(r"((^\/|^,|^!|^\.|^[\U0001F600-\U000E007F]).*)", message.text):
                return
            if len(message.text) < 100:
                message_text = message.text or ""
                search = message_text.lower()
                m = await message.reply_text(script.SEARCHING_TXT.format(search))
                find = search.split(" ")
                search = ""
                removes = ["in", "upload", "series", "full",
                           "horror", "thriller", "mystery", "print", "file"]
                for x in find:
                    if x in removes:
                        continue
                    else:
                        search = search + x + " "
                search = re.sub(r"\b(pl(i|e)*?(s|z+|ease|se|ese|(e+)s(e)?)|((send|snd|giv(e)?|gib)(\sme)?)|movie(s)?|new|latest|bro|bruh|broh|helo|that|find|dubbed|link|venum|iruka|pannunga|pannungga|anuppunga|anupunga|anuppungga|anupungga|film|undo|kitti|kitty|tharu|kittumo|kittum|movie|any(one)|with\ssubtitle(s)?)\b", "", search, flags=re.IGNORECASE)
                search = search.replace("-", " ")
                search = search.replace(":", "")
                search = re.sub(r"\s+", " ", search).strip()
                shown_search = search   # what the card title shows ("fast & furious"), the search itself drops "&" / "and"
                # "fast & furious" / "fast and furious" -> "fast furious": file names write it as "and", "&" or nothing
                _no_and = " ".join(w for w in search.split() if w not in ("&", "and"))
                if _no_and:
                    search = _no_and
                search = re.sub(r"\s+", " ", search.replace("'", " ")).strip()   # "ocean's" -> "ocean s" (file names are saved like that)
                settings = await get_settings(message.chat.id)
                if settings.get('result_card'):
                    _sq = _season_only_query(search)
                    if _sq:
                        search = _sq   # "game of thrones s07e07" -> whole season S07
                        shown_search = None
                    _m = re.search(r"\bs0*(\d{1,3})$", search)   # also a plain "game of thrones s07"
                    season_wanted = int(_m.group(1)) if _m else None
                files, offset, total_results = await get_search_results(message.chat.id, search, offset=0, filter=True)
                if not files:
                    if settings.get("spell_check") and not getattr(message, "_ai_retried", False):
                        ai_sts = await m.edit('🤖 ᴘʟᴇᴀꜱᴇ ᴡᴀɪᴛ, ᴀɪ ɪꜱ ᴄʜᴇᴄᴋɪɴɢ ʏᴏᴜʀ ꜱᴘᴇʟʟɪɴɢ...')
                        is_misspelled = await ai_spell_check(chat_id=message.chat.id, wrong_name=search)
                        if is_misspelled:
                            await ai_sts.edit(f'✅ Aɪ Sᴜɢɢᴇsᴛᴇᴅ: <code>{is_misspelled}</code>\n🔍 Searching for it...')
                            message.text = is_misspelled
                            message._ai_retried = True   # one AI retry only (no endless suggest -> search -> suggest loop)
                            await ai_sts.delete()
                            return await auto_filter(client, message)
                        await ai_sts.delete()
                        result = await advantage_spell_chok(client, message)
                        return result
                    else:
                        try:
                            if m:
                                await m.delete()
                        except Exception:
                            pass
                        result = await advantage_spell_chok(client, message)
                        return result
            else:
                return
        else:
            message = msg.message.reply_to_message
            search, files, offset, total_results = spoll
            m = await message.reply_text(f'🔎 sᴇᴀʀᴄʜɪɴɢ {search}')
            settings = await get_settings(message.chat.id)
            await msg.message.delete()
        if USER_MSG_DELETE_TIME and message.chat.id < 0:
            asyncio.create_task(_delete_user_msg(message, USER_MSG_DELETE_TIME))
        key = f"{message.chat.id}-{message.id}"
        FRESH[key] = search
        temp.GETALL[key] = files
        req = message.from_user.id if message.from_user else 0
        uid = req
        user_mention = message.from_user.mention if message.from_user else "ᴜꜱᴇʀ"
        temp.SHORT[uid] = message.chat.id
        try:
            await client.send_reaction(chat_id=message.chat.id, message_id=message.id, emoji="🍿")
        except Exception:
            pass
        # PM search: our poster-style post goes FIRST, the normal (old) result is sent below it
        post_msg = None
        if PM_POST_FIRST and message.chat.type == enums.ChatType.PRIVATE:
            post_msg = await send_search_postcard(client, message.chat.id, files)
        if settings.get('result_card'):
            user_first = message.from_user.first_name if message.from_user else "Tʜᴇʀᴇ"
            card_title = (shown_search or search).strip().title() if (shown_search or search) else display_name(files[0].file_name, files[0].caption)
            # whole card text is bold; names are HTML-escaped so a "<" in a name can't break the card
            card_cap = (
                f"<b>Hey 👋 <u>{_html.escape(str(user_first))}</u> 😍\n\n"
                f"📝 Title : <i><u>{_html.escape(str(card_title))}</u></i>\n"
                f"📮 <i>Your File is Ready Below</i> 👇</b>"
            )
            # one button per movie (title + year + language): a click sends ALL files of that movie only
            movie_rows = []
            try:
                all_files = files
                try:
                    more, _o, _t = await get_search_results(message.chat.id, search, max_results=CARD_SEARCH_LIMIT, offset=0, filter=True)
                    if more:
                        all_files = more
                except Exception as e:
                    logger.warning("card: could not load all matches, using the first page: %s", e)
                movies = _group_files_by_movie(all_files)
                if season_wanted is not None:
                    # S07E07 / episode typed: only the season button (S07), never the single episode
                    only = [mv for mv in movies if str(mv["season"]).isdigit() and int(mv["season"]) == season_wanted]
                    movies = only or movies
                if len(movies) > 1 or season_wanted is not None:
                    for i, mv in enumerate(movies[:CARD_MAX_MOVIE_BTNS], start=1):
                        sub_key = f"{key}G{i}"   # only letters/digits/-: it travels inside a t.me ?start= link
                        temp.GETALL[sub_key] = mv["files"]
                        _t = asyncio.create_task(db.save_getall(sub_key, [f.file_id for f in mv["files"]]))
                        _BG_TASKS.add(_t)
                        _t.add_done_callback(_BG_TASKS.discard)
                        movie_rows.append([InlineKeyboardButton(
                            _movie_button_label(mv),
                            callback_data=f"cardsend#{sub_key}"   # always the poster-style movie card post (all qualities of that movie / season)
                        )])
            except Exception as e:
                logger.exception("card: movie grouping failed, falling back to one download button: %s", e)
                movie_rows = []
            if movie_rows:
                card_cap = card_cap.replace("<i>Your File is Ready Below</i>", "<i>Select your movie below</i>")
            card_btn = [[InlineKeyboardButton("📢 Join Update Channel", url=temp.MOVIE_CH_LINK or f"https://t.me/{temp.U_NAME}?startgroup=true")]]
            if movie_rows:
                card_btn += movie_rows
            else:
                card_btn.append([InlineKeyboardButton(
                    "📩 Download File ↗️",
                    callback_data=f"cardsend#{key}" if settings.get('post_style_dl') else f"sendfiles#{key}"
                )])
            sent = await message.reply_text(
                text=card_cap,
                reply_markup=InlineKeyboardMarkup(card_btn),
                link_preview_options=LinkPreviewOptions(is_disabled=True),
                parse_mode=enums.ParseMode.HTML
            )
            if m:
                try:
                    await m.delete()
                except Exception:
                    pass
            try:
                if settings.get('auto_delete'):
                    asyncio.create_task(_schedule_delete(sent, message, DELETE_TIME, post_msg))
            except Exception:
                pass
            return
        if settings.get('button'):
            btn = [
                [
                    InlineKeyboardButton(text=f"🔗 {get_size(file.file_size)} ≽ " + display_name(file.file_name, file.caption), callback_data=f'file#{file.file_id}'),
                ]
                for file in files
            ]
            btn.insert(0,
                       [
                           InlineKeyboardButton(
                               'Qᴜᴀʟɪᴛʏ', callback_data=f"qualities#{req}#{key}"),
                           InlineKeyboardButton(
                               "Lᴀɴɢᴜᴀɢᴇ", callback_data=f"languages#{req}#{key}"),
                           InlineKeyboardButton(
                               "Sᴇᴀsᴏɴ",  callback_data=f"seasons#{req}#{key}")
                       ]
                       )
            btn.insert(0,
                       [
                           InlineKeyboardButton(
                               "ʀᴇᴍᴏᴠᴇ ᴀᴅs", url=f"https://t.me/{temp.U_NAME}?start=premium", style=enums.ButtonStyle.PRIMARY),
                           InlineKeyboardButton(
                               "Sᴇɴᴅ Aʟʟ", callback_data=f"sendfiles#{key}", style=enums.ButtonStyle.SUCCESS)

                       ])
        else:
            btn = []
            btn.insert(0,
                       [
                           InlineKeyboardButton(
                               'Qᴜᴀʟɪᴛʏ', callback_data=f"qualities#{req}#{key}"),
                           InlineKeyboardButton(
                               "Lᴀɴɢᴜᴀɢᴇ", callback_data=f"languages#{req}#{key}"),
                           InlineKeyboardButton(
                               "Sᴇᴀsᴏɴ",  callback_data=f"seasons#{req}#{key}")
                       ]
                       )
            btn.insert(0,
                       [
                           InlineKeyboardButton(
                               "ʀᴇᴍᴏᴠᴇ ᴀᴅs", url=f"https://t.me/{temp.U_NAME}?start=premium", style=enums.ButtonStyle.PRIMARY),
                           InlineKeyboardButton(
                               "Sᴇɴᴅ Aʟʟ", callback_data=f"sendfiles#{key}", style=enums.ButtonStyle.SUCCESS)
                       ])

        if offset != "":
            req = message.from_user.id if message.from_user else 0
            if ULTRA_FAST_MODE:
                btn.append(
                    [InlineKeyboardButton("ᴘᴀɢᴇ", callback_data="pages"), InlineKeyboardButton(
                        text="1", callback_data="pages"), InlineKeyboardButton(text="ɴᴇxᴛ ⋟", callback_data=f"next_{req}_{key}_{offset}")]
                )
            else:
                try:
                    if settings['max_btn']:
                        btn.append(
                            [InlineKeyboardButton("ᴘᴀɢᴇ", callback_data="pages"), InlineKeyboardButton(
                                text=f"1/{math.ceil(int(total_results)/10)}", callback_data="pages"), InlineKeyboardButton(text="ɴᴇxᴛ ⋟", callback_data=f"next_{req}_{key}_{offset}")]
                        )
                    else:
                        btn.append(
                            [InlineKeyboardButton("ᴘᴀɢᴇ", callback_data="pages"), InlineKeyboardButton(
                                text=f"1/{math.ceil(int(total_results)/int(MAX_B_TN))}", callback_data="pages"), InlineKeyboardButton(text="ɴᴇxᴛ ⋟", callback_data=f"next_{req}_{key}_{offset}")]
                        )
                except KeyError:
                    await save_group_settings(message.chat.id, 'max_btn', True)
                    btn.append(
                        [InlineKeyboardButton("ᴘᴀɢᴇ", callback_data="pages"), InlineKeyboardButton(
                            text=f"1/{math.ceil(int(total_results)/10)}", callback_data="pages"), InlineKeyboardButton(text="ɴᴇxᴛ ⋟", callback_data=f"next_{req}_{key}_{offset}")]
                    )
        else:
            btn.append([InlineKeyboardButton(
                text="↭ ɴᴏ ᴍᴏʀᴇ ᴘᴀɢᴇꜱ ᴀᴠᴀɪʟᴀʙʟᴇ ↭", callback_data="pages")])

        if settings.get('imdb'):
            imdb = await get_posterx(search, file=(files[0]).file_name) if TMDB_POSTER else await get_poster(search, file=(files[0]).file_name)
        else:
            imdb = None
        cur_time = datetime.now(pytz.timezone('Asia/Kolkata')).time()
        time_difference = timedelta(hours=cur_time.hour, minutes=cur_time.minute, seconds=(cur_time.second+(cur_time.microsecond/1000000))) - \
            timedelta(hours=curr_time.hour, minutes=curr_time.minute,
                      seconds=(curr_time.second+(curr_time.microsecond/1000000)))
        remaining_seconds = "{:.2f}".format(time_difference.total_seconds())
        TEMPLATE = script.IMDB_TEMPLATE_TXT
        settings = await get_settings(message.chat.id)
        if settings.get('template'):
            TEMPLATE = settings['template']
        if imdb:
            cap = render_imdb_template(
                TEMPLATE, user_mention,
                query=search,
                title=imdb['title'],
                votes=imdb['votes'],
                aka=imdb["aka"],
                seasons=imdb["seasons"],
                box_office=imdb['box_office'],
                localized_title=imdb['localized_title'],
                kind=imdb['kind'],
                imdb_id=imdb["imdb_id"],
                cast=imdb['cast'],
                runtime=imdb['runtime'],
                countries=imdb['countries'],
                certificates=imdb['certificates'],
                languages=imdb['languages'],
                director=imdb['director'],
                writer=imdb['writer'],
                producer=imdb['producer'],
                composer=imdb['composer'],
                cinematographer=imdb['cinematographer'],
                music_team=imdb['music_team'],
                distributors=imdb['distributors'],
                release_date=imdb['release_date'],
                year=imdb['year'],
                genres=imdb['genres'],
                poster=imdb['poster'],
                plot=imdb['plot'] if settings.get('button') else "N/A",
                rating=imdb['rating'],
                url=imdb['url'],
                remaining_seconds=remaining_seconds,
                total_results=total_results,
                search=search,
            )
            temp.IMDB_CAP[uid] = cap
            if not settings.get('button'):
                cap += "\n\n<b><u>Your Requested Files Are Here</u></b>\n\n"
                for idx, file in enumerate(files, start=1):
                    cap += f"<b>\n{idx}. <a href='https://telegram.me/{temp.U_NAME}?start=file_{message.chat.id}_{file.file_id}'>[{get_size(file.file_size)}] {display_name(file.file_name, file.caption)}\n</a></b>"
        else:
            temp.IMDB_CAP[uid] = None
            if ULTRA_FAST_MODE:
                if settings.get('button'):
                    cap = f"<b>🏷 ᴛɪᴛʟᴇ : <code>{search}</code>\n⏰ ʀᴇsᴜʟᴛ ɪɴ : <code>{remaining_seconds} Sᴇᴄᴏɴᴅs</code>\n\n📝 ʀᴇǫᴜᴇsᴛᴇᴅ ʙʏ : {user_mention}\n⚜️ ᴘᴏᴡᴇʀᴇᴅ ʙʏ : ⚡ {message.chat.title or temp.B_LINK or 'ᴅʀᴇᴀᴍxʙᴏᴛᴢ'} \n\n<u>Your Requested Files Are Here</u> \n\n</b>"
                else:
                    cap = f"<b>🏷 ᴛɪᴛʟᴇ : <code>{search}</code>\n⏰ ʀᴇsᴜʟᴛ ɪɴ : <code>{remaining_seconds} Sᴇᴄᴏɴᴅs</code>\n\n📝 ʀᴇǫᴜᴇsᴛᴇᴅ ʙʏ : {user_mention}\n⚜️ ᴘᴏᴡᴇʀᴇᴅ ʙʏ : ⚡ {message.chat.title or temp.B_LINK or 'ᴅʀᴇᴀᴍxʙᴏᴛᴢ'} \n\n<u>Your Requested Files Are Here</u> \n\n</b>"
                    for idx, file in enumerate(files, start=1):
                        cap += f"<b>\n{idx}. <a href='https://telegram.me/{temp.U_NAME}?start=file_{message.chat.id}_{file.file_id}'>[{get_size(file.file_size)}] {display_name(file.file_name, file.caption)}\n</a></b>"
            else:
                if settings.get('button'):
                    cap = f"<b>🏷 ᴛɪᴛʟᴇ : <code>{search}</code>\n🧱 ᴛᴏᴛᴀʟ ꜰɪʟᴇꜱ : <code>{total_results}</code>\n⏰ ʀᴇsᴜʟᴛ ɪɴ : <code>{remaining_seconds} Sᴇᴄᴏɴᴅs</code>\n\n📝 ʀᴇǫᴜᴇsᴛᴇᴅ ʙʏ : {user_mention}\n⚜️ ᴘᴏᴡᴇʀᴇᴅ ʙʏ : ⚡ {message.chat.title or temp.B_LINK or 'ᴅʀᴇᴀᴍxʙᴏᴛᴢ'} \n\n<u>Your Requested Files Are Here</u> \n\n</b>"
                else:
                    cap = f"<b>🏷 ᴛɪᴛʟᴇ : <code>{search}</code>\n🧱 ᴛᴏᴛᴀʟ ꜰɪʟᴇꜱ : <code>{total_results}</code>\n⏰ ʀᴇsᴜʟᴛ ɪɴ : <code>{remaining_seconds} Sᴇᴄᴏɴᴅs</code>\n\n📝 ʀᴇǫᴜᴇsᴛᴇᴅ ʙʏ : {user_mention}\n⚜️ ᴘᴏᴡᴇʀᴇᴅ ʙʏ : ⚡ {message.chat.title or temp.B_LINK or 'ᴅʀᴇᴀᴍxʙᴏᴛᴢ'} \n\n<u>Your Requested Files Are Here</u> \n\n</b>"

                    for idx, file in enumerate(files, start=1):
                        cap += f"<b>\n{idx}. <a href='https://telegram.me/{temp.U_NAME}?start=file_{message.chat.id}_{file.file_id}'>[{get_size(file.file_size)}] {display_name(file.file_name, file.caption)}\n</a></b>"
        if message.chat.id < 0:
            cap = strip_cap_header(cap)
        sent = None
        try:
            if imdb and imdb.get('poster'):
                try:
                    if TMDB_POSTER:
                        photo = imdb.get('backdrop') if imdb.get('backdrop') and LANDSCAPE_POSTER else imdb.get('poster')
                    else:
                        photo = imdb.get('poster')
                    sent = await message.reply_photo(photo=photo, caption=cap, reply_markup=InlineKeyboardMarkup(btn), parse_mode=enums.ParseMode.HTML)
                    if m:
                        await m.delete()
                except (MediaEmpty, PhotoInvalidDimensions, WebpageMediaEmpty):
                    pic = imdb.get('poster')
                    poster = pic.replace('.jpg', "._V1_UX360.jpg")
                    sent = await message.reply_photo(photo=poster, caption=cap, reply_markup=InlineKeyboardMarkup(btn), parse_mode=enums.ParseMode.HTML)
                    if m:
                        await m.delete()
                except Exception as e:
                    logger.exception(e)
                    sent = await message.reply_text(text=cap, reply_markup=InlineKeyboardMarkup(btn), link_preview_options=LinkPreviewOptions(is_disabled=True), parse_mode=enums.ParseMode.HTML)
            else:
                sent = await message.reply_text(text=cap, reply_markup=InlineKeyboardMarkup(btn), link_preview_options=LinkPreviewOptions(is_disabled=True), parse_mode=enums.ParseMode.HTML)
                if m:
                    await m.delete()
        except Exception as e:
            logger.exception("Failed to send result: %s", e)
            return
        try:
            if settings.get('auto_delete'):
                asyncio.create_task(_schedule_delete(sent, message, DELETE_TIME, post_msg))
        except KeyError:
            try:
                await save_group_settings(message.chat.id, 'auto_delete', True)
            except Exception:
                pass
            asyncio.create_task(_schedule_delete(sent, message, DELETE_TIME, post_msg))
        return
    except Exception as e:
        logger.exception(e)
        return

async def ai_spell_check(chat_id, wrong_name):
    async def search_movie(wrong_name):
        search_results = await asyncio.to_thread(imdb.search_movie, wrong_name)
        if not search_results or not hasattr(search_results, "titles"):
            return []
        movie_list = [movie.title for movie in search_results.titles]
        return movie_list
    movie_list = await search_movie(wrong_name)
    if not movie_list:
        return
    for _ in range(5):
        closest_match = process.extractOne(wrong_name, movie_list)
        if not closest_match or closest_match[1] <= 80:
            return
        movie = closest_match[0]
        files, _, _ = await get_search_results(chat_id=chat_id, query=movie)
        if files:
            return movie
        movie_list.remove(movie)

async def advantage_spell_chok(client, message):
    search = message.text
    mention = message.from_user.mention if message.from_user else "ᴜꜱᴇʀ"
    query = re.sub(
        r"\b(pl(i|e)*?(s|z+|ease|se|ese|(e+)s(e)?)|((send|snd|giv(e)?|gib)(\sme)?)|movie(s)?|new|latest|br((o|u)h?)*|^h(e|a)?(l)*(o)*|mal(ayalam)?|t(h)?amil|file|that|find|und(o)*|kit(t(i|y)?)?o(w)?|thar(u)?(o)*w?|kittum(o)*|aya(k)*(um(o)*)?|full\smovie|any(one)|with\ssubtitle(s)?)",
        "", message.text, flags=re.IGNORECASE)
    query = query.strip() + " movie"
    try:
        movies = await get_poster(search, bulk=True)
    except Exception as e:
        logger.exception("get_poster failed for query=%s: %s", query, e)
        try:
            k = await message.reply(script.I_CUDNT.format(mention))
            spawn_delete(60, k, message)
        except Exception:
            pass
        return
    if not movies:
        google = quote_plus(search)
        button = [[InlineKeyboardButton(
            "🔍 ᴄʜᴇᴄᴋ sᴘᴇʟʟɪɴɢ ᴏɴ ɢᴏᴏɢʟᴇ 🔍", url=f"https://www.google.com/search?q={google}")]]
        k = await message.reply_text(text=script.I_CUDNT.format(search), reply_markup=InlineKeyboardMarkup(button))
        spawn_delete(60, k, message)
        return
    user = message.from_user.id if message.from_user else 0
    buttons = [
        [InlineKeyboardButton(text=movie.title, callback_data=f"spol#{movie.imdb_id}#{user}")
         ] for movie in movies]

    buttons.append([InlineKeyboardButton(
        text="🚫 ᴄʟᴏsᴇ 🚫", callback_data='close_data', style=enums.ButtonStyle.DANGER)])
    d = await message.reply_text(text=script.CUDNT_FND.format(mention), reply_markup=InlineKeyboardMarkup(buttons))
    spawn_delete(60, d, message)
    
