"""Bot menu in the style of the "Minato" bot: banner photo + quote-block text + inline buttons.

 /start  -> home: [How to use the bot (link)] [Settings][Others] [View my files history]
 /settings, /history, /premium, /refer, /request work in the bot PM and open the same pages as the buttons.

 Settings (per user, saved in MongoDB `menu_user_settings`):
   First Priority  Size | Quality   order of the search results in the PM
   File Delete     10s 30s 50s 90s 120s   time after which a delivered file is deleted
   Result Mode     BUTTON | TEXT     how the search results are shown in the PM
   Reset to Defaults (green = nothing changed, blue = 1 changed, red = 2 or more changed)
 History: every file the bot delivers to a PM is written to `file_history` (kept 30 days); the page shows one
 button per day ("15 Oct → 24 file") and the day page lists the file names as links that send the file again.
 Premium: the Telegram Stars plans of the bot (PREMIUM_PLANS, OWNER_LNK) - "Pay Now" opens the existing Stars invoice.
 Referral: uses the existing referral points (reff_<id> links, 10 points per new user, 100 points = 30 days).

The banners are in assets/menu/*.jpg (made by tools/make_menu_banners.py). The texts are in the TEXT section
below; change them there. Every message of this menu is a normal bot message, so the 6 minute auto clean applies.
"""
import html
import logging
import os
import re
import sys
from datetime import datetime, timedelta

import pytz
from pyrogram import Client, filters, enums, StopPropagation
from pyrogram.errors import MessageNotModified
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, LinkPreviewOptions

from database.refer import referdb
from database.users_chats_db import db
from info import (ADMINS, BUTTON_MODE, FILE_DELETE_TIME, MAX_B_TN, REQST_CHANNEL, PREMIUM_PLANS, OWNER_LNK)
from utils import temp

logger = logging.getLogger(__name__)

TIMEZONE = "Asia/Kolkata"      # same time zone as plugins/commands.py and plugins/pmfilter.py (days of the file history)

ASSET_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "menu")
SEP = "──── ──── ──── ──── ──── ──── ────"
DELETE_STEPS = (10, 30, 50, 90, 120)
HISTORY_DAYS = 30
POINTS_PER_REFER = 10          # same numbers as the referral code in plugins/commands.py (reff_ links)
POINTS_FOR_PREMIUM = 100
PREMIUM_DAYS_FOR_POINTS = 30
MAX_TEXT = 3900


def _default_delete():
    return min(DELETE_STEPS, key=lambda s: abs(s - int(FILE_DELETE_TIME or 50)))


def defaults():
    return {"priority": "size", "delete": _default_delete(), "mode": "button" if BUTTON_MODE else "text"}


# ============================================================================ TEXT (edit here)
def quote(*blocks):
    """Quote block: separator line, block, separator line, block ... separator line."""
    lines = [SEP]
    for block in blocks:
        lines.append(block)
        lines.append(SEP)
    return "<blockquote>" + "\n".join(lines) + "</blockquote>"


def _bot_hashtag():
    name = re.sub(r"[^0-9A-Za-z_]", "", str(temp.B_NAME or "Bot")) or "Bot"
    return "#" + name


def text_home():
    name = html.escape(str(temp.B_NAME or "Bot"))
    return quote(f"I Am {_bot_hashtag()}...",
                 "I provide kind of content to users.\nand it's completely #free.",
                 f"By <a href=\"https://t.me/{temp.U_NAME}\">{name}</a>")


def text_howto():
    return quote("❌ <b>Don't Send Like Foolish</b>",
                 "Avatar Movie\nThe Batman Hindi\nAvengers Movie Bhejo\nMoney Heist all Season\n"
                 "Squid Game Season 2 in Hindi\nWednesday Season 1 Episode 7 in Hindi",
                 "✅ <b>Send Like Sensible</b>",
                 "The Batman\nAvatar 2024\nAvengers Endgame\nMoney Heist S03\nWednesday S01E07",
                 "📌 Send name – nothing else")


def text_settings():
    return quote("Personalize Your Settings")


def text_others():
    return quote("Here are available options")


def plan_days(code):
    m = re.match(r"(\d+)\s*(day|days|month|months|year|years)?", str(code).lower())
    if not m:
        return 0
    n, unit = int(m.group(1)), (m.group(2) or "day")
    return n * (365 if unit.startswith("year") else 30 if unit.startswith("month") else 1)


def plan_label(code):
    m = re.match(r"(\d+)\s*(day|days|month|months|year|years)?", str(code).lower())
    if not m:
        return str(code)
    n, unit = int(m.group(1)), (m.group(2) or "day")
    word = "Year" if unit.startswith("year") else "Month" if unit.startswith("month") else "Day"
    return f"{n} {word}{'s' if n != 1 else ''}"


def text_premium():
    plans = "\n".join(f"<b>{plan_label(code)}:</b> ₹{price}" for price, code in sorted(PREMIUM_PLANS.items()))
    return quote("PREMIUM UPGRADE",
                 "<b>Benefits:</b>\n\n<b>Ad-Free:</b> Zero distractions\n<b>Direct Links:</b> Fast download link\n"
                 "<b>AI Scan:</b> AI-Language scanning\n<b>No Limit:</b> Get unlimited files\n"
                 "<b>No Captcha:</b> Skip verification",
                 f"<b>Membership Plans:</b>\n\n{plans}",
                 "<i>Pay by UPI and send the screenshot to the owner.</i>")


def text_referral(points):
    return quote("REFERRAL SYSTEM",
                 f"<b>Current Points:</b> {points}\n\nShare your link with friends\nThey must be new users\n"
                 f"You get {POINTS_PER_REFER} points per user\n\n"
                 f"{POINTS_FOR_PREMIUM} points = {PREMIUM_DAYS_FOR_POINTS} Days Premium")


def text_history(first_name, total):
    return quote("Here is file history",
                 f"<b>User:</b> {html.escape(first_name or 'User')}\n<b>History Period:</b> {HISTORY_DAYS} days\n"
                 f"<b>Total Received Files:</b> {total} files",
                 "Select a date to view files")


def text_empty_request():
    return quote("⚠️ <b>Empty request detected</b>",
                 "Send a title.\n\n<i>Example:</i> <code>/request Game of thrones</code>")


def referral_share_text(user_id):
    return f"🎬 Search any movie name to get movie file directly ✅\n\nhttps://t.me/{temp.U_NAME}?start=reff_{user_id}"


# ============================================================================ buttons
def _btn(text, **kw):
    """InlineKeyboardButton; `style` (green / blue / red button) is skipped on a library that does not know it."""
    style = kw.pop("style", None)
    if style is not None:
        try:
            return InlineKeyboardButton(text, style=style, **kw)
        except TypeError:
            pass
    return InlineKeyboardButton(text, **kw)


def _style(name):
    return getattr(enums.ButtonStyle, name, None)


def kb_home():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❓ How to use the bot", url=f"https://t.me/{temp.U_NAME}?start=howto")],
        [InlineKeyboardButton("⚙️ Settings", callback_data="m|settings"),
         InlineKeyboardButton("📋 Others", callback_data="m|others")],
        [InlineKeyboardButton("🕘 View my files history", callback_data="m|hist")],
    ])


def changed_count(s):
    d = defaults()
    return sum(1 for k in d if s.get(k) != d[k])


def kb_settings(s):
    n = changed_count(s)
    style = _style("SUCCESS" if n == 0 else "PRIMARY" if n == 1 else "DANGER")
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"🎯 First Priority: {s['priority'].capitalize()}", callback_data="m|set|priority")],
        [InlineKeyboardButton(f"🗑 File Delete: {s['delete']}s", callback_data="m|set|delete")],
        [InlineKeyboardButton(f"🎨 Result Mode: {s['mode'].upper()}", callback_data="m|set|mode")],
        [_btn("🔄 Reset to Defaults", callback_data="m|set|reset", style=style)],
        [InlineKeyboardButton("↩ Back", callback_data="m|home")],
    ])


def kb_others():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💎 Premium", callback_data="m|premium"),
         InlineKeyboardButton("🔗 Referral", callback_data="m|ref")],
        [InlineKeyboardButton("↩ Back", callback_data="m|others_back")],
    ])


def kb_premium():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Payment Details", url=f"https://t.me/{temp.U_NAME}?start=premium")],
        [InlineKeyboardButton("📩 Send Screenshot", url=OWNER_LNK)],
        [InlineKeyboardButton("↩ Back", callback_data="m|others")],
    ])


def _copy_btn(text, value):
    """Telegram "copy to clipboard" button; a library without `copy_text` gets a button that sends the text instead."""
    try:
        return InlineKeyboardButton(text, copy_text=value)
    except TypeError:
        return InlineKeyboardButton(text, callback_data="m|copy")


def kb_referral(user_id):
    return InlineKeyboardMarkup([
        [_copy_btn("📋 Copy Referral Link", referral_share_text(user_id))],
        [InlineKeyboardButton("↩ Back", callback_data="m|others")],
    ])


def kb_history(days):
    buttons = [InlineKeyboardButton(f"📅 {fmt_day_short(day)} → {n} file", callback_data=f"m|day|{day}") for day, n in days]
    rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton("↩ Back", callback_data="m|home")])
    return InlineKeyboardMarkup(rows)


def kb_back(target):
    return InlineKeyboardMarkup([[InlineKeyboardButton("← Back", callback_data=target)]])


# ============================================================================ user settings
_cache = {}      # user_id -> {"explicit": {...}} (only what the user changed)


def _col_settings():
    return db.db.menu_user_settings


async def _load(user_id):
    if user_id not in _cache:
        doc = await _col_settings().find_one({"_id": user_id}) or {}
        _cache[user_id] = {k: doc[k] for k in ("priority", "delete", "mode") if k in doc}
    return _cache[user_id]


async def get_user_settings(user_id):
    """Settings of a user with the defaults filled in."""
    s = defaults()
    s.update(await _load(user_id))
    return s


async def explicit_settings(user_id):
    """Only what the user changed (empty = he never touched the settings)."""
    return dict(await _load(user_id))


async def save_user_settings(user_id, values):
    _cache[user_id] = dict(values)
    await _col_settings().update_one({"_id": user_id}, {"$set": dict(values)}, upsert=True)


async def clear_user_settings(user_id):
    _cache[user_id] = {}
    await _col_settings().delete_one({"_id": user_id})


def next_value(key, current):
    if key == "priority":
        return "quality" if current == "size" else "size"
    if key == "mode":
        return "text" if current == "button" else "button"
    if key == "delete":
        i = DELETE_STEPS.index(current) if current in DELETE_STEPS else -1
        return DELETE_STEPS[(i + 1) % len(DELETE_STEPS)]
    raise KeyError(key)


async def user_delete_seconds(chat_id):
    """File delete time the user chose (None = he did not choose, the bot default is used)."""
    try:
        if int(chat_id) > 0:
            return (await explicit_settings(int(chat_id))).get("delete")
    except Exception:
        logger.debug("user_delete_seconds failed", exc_info=True)
    return None


# ============================================================================ history
def _tz():
    return pytz.timezone(TIMEZONE)


def today_str():
    return datetime.now(_tz()).strftime("%Y-%m-%d")


def fmt_day_short(day):
    return datetime.strptime(day, "%Y-%m-%d").strftime("%d %b")


def _col_history():
    return db.db.file_history


_hist_index = {"done": False}


async def record_history(user_id, file_name, file_id):
    """Called for every file delivered to a PM (never raises)."""
    try:
        if not _hist_index["done"]:
            await _col_history().create_index("ts", expireAfterSeconds=HISTORY_DAYS * 86400)
            await _col_history().create_index([("user_id", 1), ("day", 1)])
            _hist_index["done"] = True
        await _col_history().insert_one({"user_id": int(user_id), "ts": datetime.utcnow(), "day": today_str(),
                                         "name": str(file_name or "")[:300], "fid": str(file_id)})
    except Exception:
        logger.warning("file history not saved", exc_info=True)


async def history_days(user_id):
    """[(day, count)] newest first, only the last HISTORY_DAYS days."""
    since = (datetime.now(_tz()) - timedelta(days=HISTORY_DAYS)).strftime("%Y-%m-%d")
    counts = {}
    async for doc in _col_history().find({"user_id": int(user_id), "day": {"$gte": since}}, {"day": 1}):
        counts[doc["day"]] = counts.get(doc["day"], 0) + 1
    return sorted(counts.items(), key=lambda kv: kv[0], reverse=True)


async def history_files(user_id, day):
    docs = []
    async for doc in _col_history().find({"user_id": int(user_id), "day": day}):
        docs.append(doc)
    docs.sort(key=lambda d: d["ts"])
    return docs


def text_day(user_id, day, docs):
    head = f"📁 <b>Date: {day} | Total: {len(docs)} Files</b>\n\n"
    out, used = [], len(head)
    for i, d in enumerate(docs, 1):
        name = html.escape(d["name"][:150])
        line = f'{i}. <a href="https://t.me/{temp.U_NAME}?start=file_{user_id}_{d["fid"]}">{name}</a>'
        if used + len(line) + 1 > MAX_TEXT:
            out.append(f"…and {len(docs) - i + 1} more files")
            break
        out.append(line)
        used += len(line) + 1
    return head + "\n".join(out)


# ============================================================================ sending / editing pages
_file_ids = {}   # banner name -> Telegram file_id (after the first upload)


def _banner(name):
    return _file_ids.get(name) or os.path.join(ASSET_DIR, name + ".jpg")


def _remember(name, sent):
    try:
        if name not in _file_ids and sent and sent.photo:
            _file_ids[name] = sent.photo.file_id
    except Exception:
        pass


async def send_page(client, chat_id, banner, text, kb):
    sent = await client.send_photo(chat_id=chat_id, photo=_banner(banner), caption=text, reply_markup=kb,
                                   parse_mode=enums.ParseMode.HTML)
    _remember(banner, sent)
    return sent


async def edit_page(cq, banner, text, kb):
    """Edit the page the user is looking at; a text-only message is replaced by a new page."""
    msg = cq.message
    try:
        if msg.photo:
            res = await msg.edit_media(InputMediaPhoto(_banner(banner), caption=text, parse_mode=enums.ParseMode.HTML),
                                       reply_markup=kb)
            _remember(banner, res)
            return res
    except MessageNotModified:
        return msg
    except Exception:
        logger.debug("page edit failed, sending a new one", exc_info=True)
    sent = await send_page(cq._client if hasattr(cq, "_client") else msg._client, msg.chat.id, banner, text, kb)
    try:
        await msg.delete()
    except Exception:
        pass
    return sent


async def send_home(client, message):
    return await send_page(client, message.chat.id, "welcome", text_home(), kb_home())


async def send_howto(client, message):
    return await client.send_message(message.chat.id, text_howto(), parse_mode=enums.ParseMode.HTML)


# ---- page builders (return banner, text, keyboard)
async def page_settings(user_id):
    s = await get_user_settings(user_id)
    return "settings", text_settings(), kb_settings(s)


async def page_history(user):
    days = await history_days(user.id)
    total = sum(n for _, n in days)
    return "history", text_history(user.first_name, total), kb_history(days)


async def page_referral(user_id):
    return "referral", text_referral(await referdb.get_refer_points(user_id)), kb_referral(user_id)


# ============================================================================ commands (PM)
@Client.on_message(filters.private & filters.command("settings"), group=-20)
async def cmd_settings(client, message):
    banner, text, kb = await page_settings(message.from_user.id)
    await send_page(client, message.chat.id, banner, text, kb)
    raise StopPropagation


@Client.on_message(filters.private & filters.command("history"), group=-20)
async def cmd_history(client, message):
    banner, text, kb = await page_history(message.from_user)
    await send_page(client, message.chat.id, banner, text, kb)
    raise StopPropagation


@Client.on_message(filters.private & filters.command("premium"), group=-20)
async def cmd_premium(client, message):
    await send_page(client, message.chat.id, "premium", text_premium(), kb_premium())
    raise StopPropagation


@Client.on_message(filters.private & filters.command("refer"), group=-20)
async def cmd_refer(client, message):
    banner, text, kb = await page_referral(message.from_user.id)
    await send_page(client, message.chat.id, banner, text, kb)
    raise StopPropagation


@Client.on_message(filters.private & filters.command("request"), group=-20)
async def cmd_request(client, message):
    title = " ".join(message.command[1:]).strip()
    if len(title) < 2:
        await client.send_message(message.chat.id, text_empty_request(), parse_mode=enums.ParseMode.HTML)
        raise StopPropagation
    user = message.from_user
    req = (f"<b>📥 Request: <u>{html.escape(title[:200])}</u>\n\n👤 By: {user.mention} (<code>{user.id}</code>)</b>")
    targets = [REQST_CHANNEL] if REQST_CHANNEL else list(ADMINS)
    sent_ok = False
    for chat in targets:
        try:
            await client.send_message(chat, req, parse_mode=enums.ParseMode.HTML)
            sent_ok = True
            break
        except Exception:
            logger.warning("request could not be sent to %s", chat, exc_info=True)
    if not sent_ok:
        for admin in ADMINS:
            try:
                await client.send_message(admin, req, parse_mode=enums.ParseMode.HTML)
                sent_ok = True
            except Exception:
                pass
    reply = quote("✅ <b>Request received</b>", f"<b>{html.escape(title[:200])}</b>\nWe will add it as soon as possible.") \
        if sent_ok else quote("❌ <b>Could not send your request</b>", "Please try again later.")
    await client.send_message(message.chat.id, reply, parse_mode=enums.ParseMode.HTML)
    raise StopPropagation


# ============================================================================ buttons (callbacks)
@Client.on_callback_query(filters.regex(r"^m\|"), group=-20)
async def menu_callbacks(client, cq):
    parts = cq.data.split("|")
    action = parts[1]
    user = cq.from_user
    answered = False
    try:
        if action == "home":
            await edit_page(cq, "welcome", text_home(), kb_home())
        elif action == "settings":
            await edit_page(cq, *(await page_settings(user.id)))
        elif action == "set":
            key = parts[2]
            if key == "reset":
                await clear_user_settings(user.id)
                await cq.answer("Settings reset")
                answered = True      # a callback query can be answered only once
            else:
                exp = await explicit_settings(user.id)
                cur = (await get_user_settings(user.id))[key]
                exp[key] = next_value(key, cur)
                await save_user_settings(user.id, exp)
            await edit_page(cq, *(await page_settings(user.id)))
        elif action in ("others", "others_back"):
            if action == "others_back":
                await edit_page(cq, "welcome", text_home(), kb_home())
            else:
                await edit_page(cq, "options", text_others(), kb_others())
        elif action == "premium":
            await edit_page(cq, "premium", text_premium(), kb_premium())
        elif action == "ref":
            await edit_page(cq, *(await page_referral(user.id)))
        elif action == "copy":      # only used when the library has no copy-to-clipboard button
            await cq.message.reply_text(f"<code>{html.escape(referral_share_text(user.id))}</code>", parse_mode=enums.ParseMode.HTML,
                                        link_preview_options=LinkPreviewOptions(is_disabled=True))
        elif action == "hist":
            await edit_page(cq, *(await page_history(user)))
        elif action == "day":
            day = parts[2]
            docs = await history_files(user.id, day)
            await cq.message.reply_text(text_day(user.id, day, docs), reply_markup=kb_back("m|hist"),
                                        parse_mode=enums.ParseMode.HTML, link_preview_options=LinkPreviewOptions(is_disabled=True))
            try:
                await cq.message.delete()
            except Exception:
                pass
        if not answered:
            await cq.answer()
    except StopPropagation:
        raise
    except Exception:
        logger.exception("menu callback failed: %s", cq.data)
        try:
            await cq.answer("Something went wrong, try again", show_alert=True)
        except Exception:
            pass
    raise StopPropagation


# ============================================================================ hooks into the search (called from bot.py)
_QUALITY_RANK = (("2160", 6), ("4k", 6), ("1440", 5), ("1080", 4), ("720", 3), ("480", 2), ("540", 2), ("360", 1))


def quality_rank(name):
    low = str(name or "").lower()
    for token, rank in _QUALITY_RANK:
        if token in low:
            return rank
    return 0


def sort_files(files, priority):
    """Size: biggest first. Quality: best resolution first, then biggest."""
    try:
        if priority == "quality":
            return sorted(files, key=lambda f: (quality_rank(getattr(f, "file_name", "")), getattr(f, "file_size", 0) or 0), reverse=True)
        return sorted(files, key=lambda f: getattr(f, "file_size", 0) or 0, reverse=True)
    except Exception:
        return files


SORT_WINDOW = 400     # a user with a First Priority setting gets the best of the first 400 matches, sorted, then paged


def install_hooks():
    """PM searches follow the user's Result Mode / First Priority. Replaces `get_settings` and
    `get_search_results` in every module that imported them (only private chats are affected)."""
    import utils as _utils
    from database import ia_filterdb as _fdb
    orig_settings = _utils.get_settings
    orig_search = _fdb.get_search_results

    async def get_settings(group_id, *a, **k):
        s = await orig_settings(group_id, *a, **k)
        try:
            if int(group_id) > 0:
                exp = await explicit_settings(int(group_id))
                if "mode" in exp:
                    s = dict(s)
                    s["button"] = exp["mode"] == "button"
        except Exception:
            logger.debug("settings override failed", exc_info=True)
        return s

    async def get_search_results(chat_id, query, file_type=None, max_results=None, offset=0, filter=False):
        try:
            private = chat_id is not None and int(chat_id) > 0
            priority = (await explicit_settings(int(chat_id))).get("priority") if private else None
        except Exception:
            logger.debug("priority lookup failed", exc_info=True)
            priority = None
        if not priority:
            return await orig_search(chat_id, query, file_type=file_type, max_results=max_results, offset=offset, filter=filter)
        try:
            if max_results is None:
                st = await orig_settings(int(chat_id))
                max_results = 10 if st.get("max_btn", True) else int(MAX_B_TN)
            allf, next_off, total = await orig_search(chat_id, query, file_type=file_type, max_results=SORT_WINDOW, offset=0, filter=filter)
            if not allf:
                return allf, next_off, total
            allf = sort_files(list(allf), priority)
            page = allf[offset:offset + max_results]
            more = offset + len(page) < len(allf)
            return page, (offset + len(page) if more else ""), total
        except Exception:
            logger.warning("priority search failed, normal order used", exc_info=True)
            return await orig_search(chat_id, query, file_type=file_type, max_results=max_results, offset=offset, filter=filter)

    patched = 0
    for mod in list(sys.modules.values()):
        for name, orig, new in (("get_settings", orig_settings, get_settings),
                                ("get_search_results", orig_search, get_search_results)):
            try:
                if getattr(mod, name, None) is orig:
                    setattr(mod, name, new)
                    patched += 1
            except Exception:
                pass
    logger.info("menu hooks installed (%s references replaced)", patched)
