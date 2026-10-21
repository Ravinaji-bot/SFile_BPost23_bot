import os
import logging
import aiohttp
from pyrogram import Client, filters
from pyrogram.types import Message

logger = logging.getLogger(__name__)

# Get a free key from https://api.imgbb.com/ and set it as IMGBB_API_KEY
IMGBB_API_KEY = os.environ.get("IMGBB_API_KEY", "")


@Client.on_message(filters.command(["img", "cup", "telegraph"], prefixes="/") & filters.reply)
async def c_upload(client, message: Message):
    if not IMGBB_API_KEY:
        return await message.reply_text("Upload is not configured. Set IMGBB_API_KEY first.")
    reply = message.reply_to_message
    if not reply.media:
        return await message.reply_text("Reply to a media to upload it to Cloud.")
    media_obj = getattr(reply, reply.media.value, None)
    if getattr(media_obj, "file_size", 0) and media_obj.file_size > 5 * 1024 * 1024:  # 5 MB
        return await message.reply_text("File size limit is 5 MB.")
    msg = await message.reply_text("Processing...")
    downloaded_media = None
    try:
        downloaded_media = await reply.download()
        if not downloaded_media:
            return await msg.edit_text("Something went wrong during download.")
        result = None
        with open(downloaded_media, "rb") as fh:
            data = aiohttp.FormData()
            data.add_field("key", IMGBB_API_KEY)
            data.add_field("image", fh)
            async with aiohttp.ClientSession() as session:
                async with session.post("https://api.imgbb.com/1/upload", data=data) as resp:
                    if resp.status == 200:
                        result = await resp.json()
        if result and result.get("success"):
            await msg.edit_text(f"{result['data']['url']}")
        else:
            await msg.edit_text("Something went wrong. Please try again later.")
    except Exception as e:
        logger.exception("telegraph upload failed")
        await msg.edit_text(f"Error: {e}")
    finally:
        if downloaded_media and os.path.exists(downloaded_media):
            try:
                os.remove(downloaded_media)
            except OSError:
                pass
