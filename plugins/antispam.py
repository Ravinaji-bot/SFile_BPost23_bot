"""Group protection.

In every group where this bot is ADMIN (with "Delete messages" and "Ban users" rights):

1. A BOT that somebody adds to the group is banned at once (only the bot owners in ADMINS
   may add bots). The "X added a bot" service message is removed too.
2. A message is deleted immediately when it
      * contains a link (http/https, www., any website, t.me / telegram.me channel, group or
        invite link, hidden text links, links on inline buttons), or
      * contains an @username mention, or
      * is a forwarded message.
3. NOT touched: group admins / owner, the bot owners (ADMINS), anonymous admins, other bots,
   the automatic forward of the linked channel, a mention of THIS bot (@thisbot) and links to
   this bot (t.me/thisbot?start=...). More always-allowed links: env ANTISPAM_ALLOWED.
   The bot's own links GRP_LNK, UPDATE_CHNL_LNK and SUPPORT_CHAT are allowed automatically.

Group admins can switch it per group:  /antispam on | off | status
Global switches (env): ANTISPAM, ANTISPAM_DEFAULT_ON, ANTISPAM_BAN_BOTS, ANTISPAM_ALLOWED

A deleted message is not handled by the other plugins (no search is done for spam text).
If the bot lacks the right to delete / ban, nothing is deleted and the message works as before.
"""
import asyncio
import logging
import re

from pyrogram import Client, filters
from pyrogram.types import Message

from info import (
    ADMINS, ANTISPAM, ANTISPAM_ALLOWED, ANTISPAM_BAN_BOTS, ANTISPAM_DEFAULT_ON,
    GRP_LNK, UPDATE_CHNL_LNK, SUPPORT_CHAT, ANTISPAM_ADMINS_CAN_ADD_BOTS,
)
from utils import get_settings, save_group_settings, is_check_admin

logger = logging.getLogger(__name__)

KEY = "antispam"            # per-group setting
GROUP_BAN, GROUP_SPAM = -41, -40   # own handler groups: they run BEFORE the normal plugins and never replace them
_tasks = set()

_URL_RE = re.compile(r"(?i)(?:\b[a-z][a-z0-9+.-]*://|\bwww\.|\b(?:t|telegram)\.(?:me|dog)/|\btg://)[^\s]+")
_MENTION_RE = re.compile(r"(?<![\w@./])@([A-Za-z][A-Za-z0-9_]{3,31})")
_TG_HOSTS = ("t.me", "telegram.me", "telegram.dog")


# --------------------------------------------------------------------------- link helpers
def _slice16(text, offset, length):
    """Telegram entity offsets are in UTF-16 units, python strings are not."""
    raw = text.encode("utf-16-le")
    return raw[offset * 2:(offset + length) * 2].decode("utf-16-le", "ignore")


def _split_url(u):
    """'https://t.me/Name/12?x' -> ('t.me', 'name');  'www.site.com/a' -> ('site.com', 'a')."""
    u = (u or "").strip().strip("<>()[]{}\"'.,;:!?")
    m = re.match(r"(?i)tg://resolve\?domain=(\w+)", u)
    if m:
        return "t.me", m.group(1).lower()
    u = re.sub(r"(?i)^[a-z][a-z0-9+.-]*://", "", u)
    u = re.sub(r"(?i)^www\.", "", u)
    host, _, rest = u.partition("/")
    host = re.split(r"[?#:]", host)[0].lower()
    if host in _TG_HOSTS:
        host = "t.me"
    seg = re.split(r"[/?#]", rest)[0].lower() if rest else ""
    return host, seg


def _build_allow(extra):
    names, hosts = set(), set()
    for item in list(extra) + [GRP_LNK, UPDATE_CHNL_LNK, SUPPORT_CHAT]:
        item = (item or "").strip()
        if not item:
            continue
        if item.startswith("@"):
            names.add(item[1:].lower())
        elif "." not in item and "/" not in item:
            names.add(item.lower())              # plain channel / bot name
        else:
            host, seg = _split_url(item)
            if host == "t.me":
                if seg and seg not in ("joinchat", "c", "addstickers") and not seg.startswith("+"):
                    names.add(seg)
            elif host:
                hosts.add(host)
    return names, hosts


_ALLOW_NAMES, _ALLOW_HOSTS = _build_allow(ANTISPAM_ALLOWED)


def _link_allowed(cand, me_username):
    me_username = (me_username or "").lower()
    if cand.startswith("@"):
        name = cand[1:].lower()
        return name == me_username or name in _ALLOW_NAMES
    host, seg = _split_url(cand)
    if host == "t.me":
        return bool(seg) and (seg == me_username or seg in _ALLOW_NAMES)
    return any(host == d or host.endswith("." + d) for d in _ALLOW_HOSTS)


def _is_forward(m):
    if getattr(m, "is_automatic_forward", False):
        return False                             # post of the linked channel, shown by Telegram itself
    return any(getattr(m, a, None) for a in
               ("forward_origin", "forward_date", "forward_from", "forward_from_chat", "forward_sender_name"))


def _find_violation(m, me_username):
    """Returns a short reason ('forward' / 'link' / 'mention') or None. Pure function."""
    if _is_forward(m):
        return "forward"
    text = getattr(m, "text", None) or getattr(m, "caption", None) or ""
    entities = getattr(m, "entities", None) or getattr(m, "caption_entities", None) or []
    cands = []                                   # (kind, text)
    for e in entities:
        name = str(getattr(getattr(e, "type", None), "name", getattr(e, "type", ""))).upper()
        if name == "TEXT_LINK" and getattr(e, "url", None):
            cands.append(("link", e.url))
        elif name == "URL":
            cands.append(("link", _slice16(text, e.offset, e.length)))
        elif name == "MENTION":
            cands.append(("mention", _slice16(text, e.offset, e.length)))
    cands += [("link", u) for u in _URL_RE.findall(text)]                       # fallback if no entities
    cands += [("mention", "@" + n) for n in _MENTION_RE.findall(text)]
    markup = getattr(m, "reply_markup", None)
    for row in (getattr(markup, "inline_keyboard", None) or []):                 # url buttons (inline bots)
        for b in row:
            if getattr(b, "url", None):
                cands.append(("link", b.url))
    for kind, c in cands:
        if c and not _link_allowed(c, me_username):
            return kind
    return None


# --------------------------------------------------------------------------- settings / rights
async def _enabled(chat_id):
    if not ANTISPAM:
        return False
    try:
        return bool((await get_settings(chat_id)).get(KEY, ANTISPAM_DEFAULT_ON))
    except Exception:
        return ANTISPAM_DEFAULT_ON


async def _exempt(client, m):
    """True for people whose messages are never deleted."""
    if m.sender_chat and m.sender_chat.id == m.chat.id:
        return True                              # anonymous group admin
    u = m.from_user
    if u is None:
        return False                             # a channel writing as itself
    if u.is_bot or u.id in ADMINS:
        return True
    return await is_check_admin(client, m.chat.id, u.id)


def _later(coro):
    t = asyncio.create_task(coro)
    _tasks.add(t)
    t.add_done_callback(_tasks.discard)


# --------------------------------------------------------------------------- 1) added bots
async def _delete_later(msg, delay):
    try:
        await asyncio.sleep(delay)
        await msg.delete()
    except Exception as e:
        logger.debug("antispam: service message not deleted: %s", e)


@Client.on_message(filters.new_chat_members & filters.group, group=GROUP_BAN)
async def ban_added_bots(client: Client, message: Message):
    if not ANTISPAM_BAN_BOTS or not await _enabled(message.chat.id):
        return
    adder = message.from_user
    if adder and adder.id in ADMINS:
        return                                   # the bot owners may add bots
    if (ANTISPAM_ADMINS_CAN_ADD_BOTS and adder
            and await is_check_admin(client, message.chat.id, adder.id)):
        return                                   # a group admin / owner may add his own bots
    banned = 0
    for u in message.new_chat_members:
        if not u.is_bot or u.id == client.me.id:
            continue
        try:
            await client.ban_chat_member(message.chat.id, u.id)
            banned += 1
            logger.info("antispam: banned bot @%s added to %s", u.username, message.chat.id)
        except Exception as e:
            logger.warning("antispam: can not ban bot %s in %s: %s (give me the 'Ban users' right)",
                           u.id, message.chat.id, e)
    if banned:
        _later(_delete_later(message, 5))        # the 'X added a bot' line; later so other plugins can still read it


# --------------------------------------------------------------------------- 2) links / mentions / forwards
async def _check_message(client, message):
    if not await _enabled(message.chat.id):
        return
    reason = _find_violation(message, getattr(client.me, "username", None))
    if not reason:
        return
    if await _exempt(client, message):
        return
    try:
        await message.delete()
    except Exception as e:
        logger.debug("antispam: can not delete in %s: %s (give me the 'Delete messages' right)", message.chat.id, e)
        return
    logger.info("antispam: deleted %s in %s", reason, message.chat.id)
    message.stop_propagation()                   # do not search / answer a spam message


@Client.on_message(filters.group & filters.incoming & ~filters.service, group=GROUP_SPAM)
async def antispam_new(client: Client, message: Message):
    await _check_message(client, message)


@Client.on_edited_message(filters.group & filters.incoming & ~filters.service, group=GROUP_SPAM)
async def antispam_edited(client: Client, message: Message):
    await _check_message(client, message)            # a link added later by editing


# --------------------------------------------------------------------------- 3) /antispam
@Client.on_message(filters.command("antispam") & filters.group, group=GROUP_BAN)
async def antispam_cmd(client: Client, message: Message):
    u = message.from_user
    if not u or not (u.id in ADMINS or await is_check_admin(client, message.chat.id, u.id)):
        return                                   # only group admins
    arg = message.command[1].lower() if len(message.command) > 1 else "status"
    if arg in ("on", "off"):
        await save_group_settings(message.chat.id, KEY, arg == "on")
    elif arg != "status":
        return await message.reply_text("Usage: /antispam on | off | status")
    on = await _enabled(message.chat.id)
    rights = ""
    try:
        me = await client.get_chat_member(message.chat.id, client.me.id)
        pr = getattr(me, "privileges", None)
        can_del = bool(pr and getattr(pr, "can_delete_messages", False))
        can_ban = bool(pr and getattr(pr, "can_restrict_members", False))
        rights = (f"\nMy rights: delete messages {'✅' if can_del else '❌'} | ban users {'✅' if can_ban else '❌'}"
                  + ("" if (can_del and can_ban) else "\n⚠️ Make me admin with both rights, otherwise I can not clean the group."))
    except Exception:
        pass
    await message.reply_text(
        f"🛡 Group protection: {'ON ✅' if on else 'OFF ❌'}\n"
        "Deleted at once: links, @mentions, forwarded messages.\n"
        f"Added bots: {'banned' if ANTISPAM_BAN_BOTS else 'allowed'}.{rights}\n"
        "Change: /antispam on | off")
