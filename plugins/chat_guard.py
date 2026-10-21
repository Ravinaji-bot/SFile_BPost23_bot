"""Chat guard: the bot works only in groups / channels the OWNER has approved.

* A group / channel is allowed when
    - the owner (OWNER_ID) added the bot there                         -> approved automatically
    - the owner approved it (button in the owner PM, or /allowchat)
    - it is one of the chats set in the env / config (CHANNELS, LOG_CHANNEL, BIN_CHANNEL,
      DELETE_CHANNELS, SUPPORT_CHAT_ID, ... and ALLOWED_CHATS)
    - the owner / an admin set it for a job (/autopost, /autoforward, /ccopy, /bulkpost,
      /postrange, /statuschannel ...): those ids are stored in bot_settings and count as approved
* Every other group / channel: the bot is completely silent (no replies, no filters, no
  indexing, no auto posting from it, no join-request handling). The owner gets a PM with
  Approve / Leave buttons when someone adds the bot to a new chat.
* Private chats (users in the bot PM) are never touched.

Owner commands (OWNER_ID only):
  /allowchat [chat_id]     approve a chat (inside the group no id is needed)
  /disallowchat [chat_id]  take the approval back (the bot becomes silent there)
  /allowedchats            list of approved chats
  /approveall              approve every group the bot already knows (saved in the database)

Switches (env): CHAT_GUARD (default True), CHAT_GUARD_AUTO_LEAVE (default False: stay silent and
ask the owner; True: also leave an unapproved chat at once), ALLOWED_CHATS (extra ids / @usernames).
"""
import asyncio
import html
import logging
import time

from pyrogram import Client, filters, StopPropagation
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

import info
from info import OWNER_IDS
from database.users_chats_db import db
from utils import temp

logger = logging.getLogger(__name__)

K_ALLOWED = "CG_ALLOWED"      # [chat ids] approved by the owner (stored in bot_settings)
K_INIT = "CG_INIT"            # first start with the guard already done
G_CMD, G_BLOCK = -201, -200   # handler groups: they run before every other plugin

_DYN_TTL = 10                 # seconds: how long the "chats set for a job" list is cached
_state = {"approved": None, "dyn": set(), "dyn_at": 0.0, "static": None, "usernames": set()}
_notified = {}                # chat id -> time of the last owner PM (no repeated PMs)
_lock = asyncio.Lock()


def _enabled():
    return bool(getattr(info, "CHAT_GUARD", True))


def _real_chat_id(n):
    """A real group / channel id (the '-100' placeholder of the config and tiny numbers are not)."""
    return isinstance(n, int) and n < 0 and abs(n) >= 1_000_000


def _collect(*vals):
    ids, names = set(), set()
    for v in vals:
        if v is None:
            continue
        if isinstance(v, (list, tuple, set)):
            i2, n2 = _collect(*v)
            ids |= i2
            names |= n2
            continue
        for tok in str(v).replace(",", " ").split():
            tok = tok.strip()
            try:
                n = int(tok)
            except ValueError:
                if tok.startswith("@") and len(tok) > 1:
                    names.add(tok[1:].lower())
                continue
            if _real_chat_id(n):
                ids.add(n)
    return ids, names


def _static():
    if _state["static"] is None:
        names = ("CHANNELS", "LOG_CHANNEL", "BIN_CHANNEL", "RESULT_ARCHIVE_CHANNEL", "PREMIUM_LOGS",
                 "DELETE_CHANNELS", "SUPPORT_CHAT_ID", "REQST_CHANNEL", "AUTH_REQ_CHANNELS",
                 "AUTH_CHANNELS", "MOVIE_UPDATE_CHANNEL", "POSTCARD_CHANNEL", "INDEX_REQ_CHANNEL", "LOG_VR_CHANNEL",
                 "ALLOWED_CHATS")
        ids, unames = _collect(*[getattr(info, n, None) for n in names])
        _state["static"] = ids
        _state["usernames"] = unames
    return _state["static"]


async def _bot_id(bot=None):
    if bot is not None and getattr(bot, "me", None):
        return bot.me.id
    return temp.ME


async def _load_approved(bot=None):
    if _state["approved"] is None:
        async with _lock:
            if _state["approved"] is None:
                try:
                    lst = await db.get_bot_setting(await _bot_id(bot), K_ALLOWED, []) or []
                    _state["approved"] = {int(x) for x in lst}
                except Exception:
                    logger.exception("chat guard: could not read the approved chats")
                    return set()
    return _state["approved"]


async def _save_approved(bot=None):
    await db.update_bot_setting(await _bot_id(bot), K_ALLOWED, sorted(_state["approved"] or []))


async def _dynamic(bot=None):
    """Chats that the owner / an admin set for a job (autopost, autoforward, ccopy ...)."""
    now = time.monotonic()
    if now - _state["dyn_at"] < _DYN_TTL:
        return _state["dyn"]
    _state["dyn_at"] = now
    try:
        doc = await db.botcol.find_one({"id": int(await _bot_id(bot))}) or {}
        found = set()
        for k, v in doc.items():
            if k in ("_id", "id", K_ALLOWED):
                continue
            if isinstance(v, int) and not isinstance(v, bool) and _real_chat_id(v):
                found.add(v)
        _state["dyn"] = found
    except Exception:
        logger.warning("chat guard: could not read the job channels", exc_info=True)
    return _state["dyn"]


async def is_allowed(chat_id, username=None, bot=None):
    """True when the bot may work in this chat."""
    if not _enabled():
        return True
    try:
        chat_id = int(chat_id)
    except (TypeError, ValueError):
        return False
    if chat_id > 0:                      # a private chat with a user
        return True
    if chat_id in _static():
        return True
    if username and username.lower().lstrip("@") in _state["usernames"]:
        return True
    approved = await _load_approved(bot)
    if chat_id in approved:
        return True
    return chat_id in await _dynamic(bot)


async def approve(chat_id, bot=None):
    approved = await _load_approved(bot)
    approved.add(int(chat_id))
    await _save_approved(bot)


async def disapprove(chat_id, bot=None):
    approved = await _load_approved(bot)
    approved.discard(int(chat_id))
    await _save_approved(bot)


# --------------------------------------------------------------------------- owner PM

def _owner_ids():
    return [o for o in OWNER_IDS if isinstance(o, int)]


async def _tell_owner(bot, text, buttons=None):
    markup = InlineKeyboardMarkup(buttons) if buttons else None
    sent = False
    for oid in _owner_ids():
        try:
            await bot.send_message(oid, text, reply_markup=markup)
            sent = True
        except Exception as e:
            logger.info("chat guard: could not PM owner %s: %s", oid, e)
    if not sent:
        log_ch = getattr(info, "LOG_CHANNEL", None)
        if _real_chat_id(log_ch if isinstance(log_ch, int) else 0):
            try:
                await bot.send_message(log_ch, text, reply_markup=markup)
            except Exception as e:
                logger.warning("chat guard: owner notice not delivered: %s", e)


async def _on_bot_added(bot, chat, actor):
    """The bot was put into `chat`. Owner adds it -> approved; anybody else -> ask the owner."""
    if not _enabled() or chat is None or chat.id > 0:
        return
    if await is_allowed(chat.id, getattr(chat, "username", None), bot):
        return
    if actor is not None and actor.id in OWNER_IDS:
        await approve(chat.id, bot)
        logger.info("chat guard: %s (%s) approved - the owner added the bot", chat.title, chat.id)
        return
    now = time.monotonic()
    if now - _notified.get(chat.id, 0) < 60:
        return
    _notified[chat.id] = now
    kind = str(getattr(getattr(chat, "type", None), "name", "chat")).lower()
    who = f"{html.escape(actor.first_name or '')} (<code>{actor.id}</code>)" if actor else "unknown"
    auto_leave = bool(getattr(info, "CHAT_GUARD_AUTO_LEAVE", False))
    text = (
        "🛡 <b>Chat Guard</b>\n\n"
        f"The bot was added to a new {kind} that you have not approved:\n\n"
        f"<b>{html.escape(chat.title or 'N/A')}</b>\n"
        f"ID: <code>{chat.id}</code>\n"
        f"Added by: {who}\n\n"
        + ("The bot left that chat. To allow it: /allowchat " + str(chat.id) + " and add the bot again."
           if auto_leave else
           "The bot is silent there (no replies, no filters, no posting) until you approve.")
    )
    buttons = None if auto_leave else [[
        InlineKeyboardButton("✅ Approve", callback_data=f"cg_ok#{chat.id}"),
        InlineKeyboardButton("🚪 Leave", callback_data=f"cg_no#{chat.id}"),
    ]]
    await _tell_owner(bot, text, buttons)
    if auto_leave:
        try:
            await bot.leave_chat(chat.id)
        except Exception as e:
            logger.warning("chat guard: could not leave %s: %s", chat.id, e)


# --------------------------------------------------------------------------- blockers

def _is_me(bot, user):
    return user is not None and (getattr(bot, "me", None) and user.id == bot.me.id or user.id == temp.ME)


@Client.on_message(filters.group | filters.channel, group=G_BLOCK)
async def _guard_message(bot, message):
    chat = message.chat
    if chat is None:
        return
    new_members = getattr(message, "new_chat_members", None)
    if new_members and any(_is_me(bot, u) for u in new_members):
        await _on_bot_added(bot, chat, message.from_user)
    if not await is_allowed(chat.id, getattr(chat, "username", None), bot):
        raise StopPropagation


@Client.on_edited_message(filters.group | filters.channel, group=G_BLOCK)
async def _guard_edited(bot, message):
    chat = message.chat
    if chat is not None and not await is_allowed(chat.id, getattr(chat, "username", None), bot):
        raise StopPropagation


@Client.on_callback_query(group=G_BLOCK)
async def _guard_callback(bot, query):
    msg = getattr(query, "message", None)
    chat = getattr(msg, "chat", None)
    if chat is not None and chat.id < 0 and not await is_allowed(chat.id, getattr(chat, "username", None), bot):
        try:
            await query.answer("This chat is not approved by the bot owner.", show_alert=True)
        except Exception:
            pass
        raise StopPropagation


@Client.on_chat_member_updated(group=G_BLOCK)
async def _guard_member_update(bot, update):
    chat = update.chat
    new = getattr(update, "new_chat_member", None)
    if new is not None and _is_me(bot, getattr(new, "user", None)):
        status = str(getattr(new, "status", "")).lower()
        if "left" not in status and "banned" not in status and "restricted" not in status:
            await _on_bot_added(bot, chat, update.from_user)
    if chat is not None and chat.id < 0 and not await is_allowed(chat.id, getattr(chat, "username", None), bot):
        raise StopPropagation


@Client.on_chat_join_request(group=G_BLOCK)
async def _guard_join_request(bot, request):
    chat = request.chat
    if chat is not None and not await is_allowed(chat.id, getattr(chat, "username", None), bot):
        raise StopPropagation


# --------------------------------------------------------------------------- owner commands / buttons

_owner = filters.user(OWNER_IDS) if OWNER_IDS else filters.user([])


def _target_id(message):
    if len(message.command) > 1:
        try:
            return int(message.command[1])
        except ValueError:
            return None
    if message.chat and message.chat.id < 0:
        return message.chat.id
    return None


@Client.on_message(filters.command("allowchat") & _owner, group=G_CMD)
async def allowchat_cmd(bot, message):
    cid = _target_id(message)
    if cid is None or cid > 0:
        await message.reply_text("Usage: /allowchat -100xxxxxxxxxx  (inside the group no id is needed)")
        raise StopPropagation
    await approve(cid, bot)
    await message.reply_text(f"✅ Approved: <code>{cid}</code>")
    raise StopPropagation


@Client.on_message(filters.command("disallowchat") & _owner, group=G_CMD)
async def disallowchat_cmd(bot, message):
    cid = _target_id(message)
    if cid is None or cid > 0:
        await message.reply_text("Usage: /disallowchat -100xxxxxxxxxx")
        raise StopPropagation
    await disapprove(cid, bot)
    note = ""
    if cid in _static() or cid in await _dynamic(bot):
        note = "\n(This chat is also set in the config / a job, so it stays allowed until that is removed.)"
    await message.reply_text(f"🚫 Approval removed: <code>{cid}</code>{note}")
    raise StopPropagation


@Client.on_message(filters.command("allowedchats") & _owner, group=G_CMD)
async def allowedchats_cmd(bot, message):
    approved = sorted(await _load_approved(bot))
    lines = []
    for cid in approved[:60]:
        try:
            row = await db.get_chat(cid)
            title = (row or {}).get("title") or ""
        except Exception:
            title = ""
        lines.append(f"• <code>{cid}</code> {html.escape(str(title))}")
    text = (
        f"🛡 Chat Guard: <b>{'ON' if _enabled() else 'OFF'}</b>\n"
        f"Approved by owner: {len(approved)}\n"
        f"Set in config: {len(_static())}\n"
        f"Set for jobs: {len(await _dynamic(bot))}\n\n"
        + ("\n".join(lines) if lines else "No chat approved yet.")
    )
    if len(approved) > 60:
        text += f"\n... and {len(approved) - 60} more"
    await message.reply_text(text)
    raise StopPropagation


@Client.on_message(filters.command("approveall") & _owner, group=G_CMD)
async def approveall_cmd(bot, message):
    count = 0
    approved = await _load_approved(bot)
    async for row in await db.get_all_chats():
        cid = row.get("id")
        if isinstance(cid, int) and cid < 0 and cid not in approved:
            approved.add(cid)
            count += 1
    await _save_approved(bot)
    await message.reply_text(f"✅ {count} known groups approved.")
    raise StopPropagation


@Client.on_callback_query(filters.regex(r"^cg_(ok|no)#-?\d+$") & _owner, group=G_CMD)
async def guard_buttons(bot, query):
    action, cid = query.data.split("#")
    cid = int(cid)
    if action == "cg_ok":
        await approve(cid, bot)
        await query.answer("Approved")
        try:
            await query.message.edit_text(query.message.text.html + "\n\n✅ <b>Approved</b>")
        except Exception:
            pass
    else:
        await disapprove(cid, bot)
        try:
            await bot.leave_chat(cid)
            await query.answer("The bot left that chat")
            note = "🚪 <b>The bot left</b>"
        except Exception as e:
            await query.answer("Could not leave", show_alert=True)
            note = f"❌ Could not leave: {html.escape(str(e))}"
        try:
            await query.message.edit_text(query.message.text.html + "\n\n" + note)
        except Exception:
            pass
    raise StopPropagation


# --------------------------------------------------------------------------- start-up

async def startup_report(bot):
    """Called once from bot.py. Tells the owner about groups that are already in the database
    but not approved (the bot is silent there until /approveall or /allowchat)."""
    if not _enabled():
        logger.info("chat guard: OFF (CHAT_GUARD=False)")
        return
    approved = await _load_approved(bot)
    unknown = []
    try:
        async for row in await db.get_all_chats():
            cid = row.get("id")
            if isinstance(cid, int) and cid < 0 and cid not in approved and cid not in _static() \
                    and cid not in await _dynamic(bot):
                unknown.append((cid, row.get("title") or ""))
    except Exception:
        logger.warning("chat guard: could not read the saved groups", exc_info=True)
    logger.info("chat guard: ON, %d approved, %d saved groups not approved", len(approved), len(unknown))
    first = not await db.get_bot_setting(await _bot_id(bot), K_INIT, False)
    if first:
        await db.update_bot_setting(await _bot_id(bot), K_INIT, True)
        if unknown:
            sample = "\n".join(f"• <code>{c}</code> {html.escape(str(t))}" for c, t in unknown[:15])
            more = f"\n... and {len(unknown) - 15} more" if len(unknown) > 15 else ""
            await _tell_owner(
                bot,
                "🛡 <b>Chat Guard is ON</b>\n\n"
                "The bot now works only in chats you approved. These groups are saved in the "
                "database but NOT approved, so the bot is silent there:\n\n"
                f"{sample}{more}\n\n"
                "/approveall - approve all of them\n"
                "/allowchat <code>id</code> - approve one\n"
                "/allowedchats - see the list",
            )
        else:
            await _tell_owner(bot, "🛡 <b>Chat Guard is ON</b>\n\nThe bot works only in chats you approve "
                                   "(/allowchat, /disallowchat, /allowedchats).")
