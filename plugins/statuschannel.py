"""/statuschannel - one private channel for the messages of the background jobs.

The progress ("20175 collections done ..."), "ended" and warning messages of /autopost, /bulkpost,
/ccopy, /postrange and /autoforward (also "Bot restarted - continues ...") are sent to this channel and
no longer to the bot PM. The bot must be ADMIN of the channel.

    /statuschannel -100xxxxxxxxxx    set the channel (a test message is sent there)
    /statuschannel                   show the current channel
    /statuschannel off               messages come to the admin PM again
"""
import html

from pyrogram import Client, filters

from dreamxbotz.util.jobtools import status_channel, set_status_channel
from info import ADMINS


@Client.on_message(filters.command("statuschannel") & filters.user(ADMINS))
async def statuschannel_cmd(bot, message):
    bid = bot.me.id
    args = message.command[1:]
    cur = await status_channel(bid)
    if not args:
        return await message.reply_text(
            "📡 Status channel: " + (f"<code>{cur}</code>" if cur else "not set (job messages come to your PM)") +
            "\n\nSet: <code>/statuschannel -100xxxxxxxxxx</code> (the bot must be admin there)\n"
            "Remove: <code>/statuschannel off</code>")
    if args[0].lower() in ("off", "none", "remove"):
        await set_status_channel(bid, None)
        return await message.reply_text("✅ Status channel removed. Job messages come to the admin PM again.")
    try:
        cid = int(args[0])
        if cid >= 0:
            raise ValueError
    except ValueError:
        return await message.reply_text("❌ Send the channel id, it starts with -100 (example: <code>/statuschannel -1001234567890</code>).")
    try:
        chat = await bot.get_chat(cid)
        await bot.send_message(cid, "✅ Status channel connected. The progress and finished messages of "
                                    "/autopost, /bulkpost, /ccopy, /postrange and /autoforward come here.")
    except Exception as e:
        return await message.reply_text(f"❌ Cannot post in that channel ({html.escape(str(e)[:150])}). "
                                        "Add the bot as ADMIN there and try again.")
    await set_status_channel(bid, cid)
    await message.reply_text(f"✅ Status channel: <b>{html.escape(chat.title or str(cid))}</b> (<code>{cid}</code>)\n"
                             "The messages of the background jobs no longer come to the bot PM.")
