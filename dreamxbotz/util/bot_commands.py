import logging
from pyrogram.types import (
    BotCommand, BotCommandScopeDefault, BotCommandScopeChat, BotCommandScopeAllChatAdministrators,
)
from info import ADMINS, OWNER_IDS, USER_CMDS, GROUP_ADMIN_CMDS, ADMIN_CMDS, OWNER_CMDS, HIDE_USER_COMMANDS

logger = logging.getLogger(__name__)


def _cmds(*dicts):
    out, seen = [], set()
    for d in dicts:
        for name, desc in d.items():
            if name not in seen:
                seen.add(name)
                out.append(BotCommand(name, desc))
    return out


async def set_role_commands(client):
    """Build the "/" menu per role so users never see admin / owner commands.

    default scope        -> users: USER_CMDS only
    group administrators -> USER_CMDS + GROUP_ADMIN_CMDS
    each bot admin (PM)  -> USER + GROUP_ADMIN + ADMIN
    each owner (PM)      -> everything
    Returns the number of scopes that were updated.
    """
    done = 0
    if HIDE_USER_COMMANDS:
        # normal users see no "/" menu at all (admins / owners keep theirs below)
        await client.delete_bot_commands(scope=BotCommandScopeDefault())
    else:
        await client.set_bot_commands(_cmds(USER_CMDS), scope=BotCommandScopeDefault())
    done += 1
    await client.set_bot_commands(_cmds(USER_CMDS, GROUP_ADMIN_CMDS), scope=BotCommandScopeAllChatAdministrators())
    done += 1
    owners = {o for o in OWNER_IDS if isinstance(o, int)}
    for uid in {a for a in ADMINS if isinstance(a, int)}:
        if uid in owners:
            cmds = _cmds(USER_CMDS, GROUP_ADMIN_CMDS, ADMIN_CMDS, OWNER_CMDS)
        else:
            cmds = _cmds(USER_CMDS, GROUP_ADMIN_CMDS, ADMIN_CMDS)
        try:
            await client.set_bot_commands(cmds, scope=BotCommandScopeChat(chat_id=uid))
            done += 1
        except Exception as e:  # admin has not started the bot yet, etc.
            logger.warning("Could not set command menu for %s: %s", uid, e)
    return done
