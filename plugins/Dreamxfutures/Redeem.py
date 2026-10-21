 
from datetime import timedelta, datetime
import pytz
import string
import secrets
from pyrogram import Client, filters
from pyrogram.types import LinkPreviewOptions, InlineKeyboardMarkup, InlineKeyboardButton
from database.users_chats_db import db
from info import ADMINS, PREMIUM_LOGS
from utils import get_seconds, temp

# Gift codes are stored in MongoDB (db.codes), so they survive restarts / redeploys.

def generate_code(length=10):
    letters_and_digits = string.ascii_letters + string.digits
    return ''.join(secrets.choice(letters_and_digits) for _ in range(length))

from info import OWNER_IDS

@Client.on_message(filters.command("add_redeem") & filters.user(OWNER_IDS))
async def add_redeem_code(client, message):
    if len(message.command) == 3:
        try:
            time = message.command[1]
            num_codes = int(message.command[2])
        except ValueError:
            await message.reply_text("Please provide a valid number of codes to generate.")
            return
        if not 1 <= num_codes <= 50:
            await message.reply_text("Please choose between 1 and 50 codes at a time.")
            return
        if await get_seconds(time) <= 0:
            await message.reply_text("Invalid time format. Use like: <code>1min</code>, <code>1hour</code>, <code>1day</code>, <code>1week</code>, <code>1month</code>, <code>1year</code>")
            return

        codes = []
        for _ in range(num_codes):
            codes.append(generate_code())
        await db.add_redeem_codes({c: time for c in codes})

        codes_text = '\n'.join(f"➔ <code>/redeem {code}</code>" for code in codes)
        text = f"""
            <b>🎉 <u>Gɪғᴛᴄᴏᴅᴇ Gᴇɴᴇʀᴀᴛᴇᴅ ✅</u></b>

            <b> <u>Tᴏᴛᴀʟ ᴄᴏᴅᴇ:</u></b> {num_codes}

            {codes_text}

            <b>⏳ <u>Duration:</u></b> {time}

            🌟<u>𝗥𝗲𝗱𝗲𝗲𝗺 𝗖𝗼𝗱𝗲 𝗜𝗻𝘀𝘁𝗿𝘂𝗰𝘁𝗶𝗼𝗻</u>🌟

            <b> <u>Click on the code above</u> to copy it instantly!</b>
            <b> <u>Send the copied code to the bot</u>\n to unlock your premium features!</b>

            <b>🚀 Enjoy your premium access! 🔥</u></b>
            """
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("🔑 Redeem Now 🔥", url=f"https://t.me/{temp.U_NAME}")]])
        await message.reply_text(text, reply_markup=keyboard)
    else:
        await message.reply_text("<b>♻ Usage:\n\n➩ <code>/add_redeem 1min 1</code>,\n➩ <code>/add_redeem 1hour 10</code>,\n➩ <code>/add_redeem 1day 5</code></b>")


@Client.on_message(filters.command("redeem"))
async def redeem_code(client, message):
    if not message.from_user:
        return
    user_id = message.from_user.id
    if len(message.command) == 2:
        redeem_code = message.command[1]

        # Take the code out first (atomic) so two people can't use it at the same time;
        # it is put back below if the redeem did not actually succeed.
        time = await db.take_redeem_code(redeem_code)
        if time is not None:
            activated = False
            try:
                user = await client.get_users(user_id)
                try:
                    seconds = await get_seconds(time)
                except Exception:
                    await message.reply_text("Invalid time format in redeem code.")
                    return
                if seconds > 0:
                    data = await db.get_user(user_id)
                    current_expiry = data.get("expiry_time") if data else None
                    now_aware = datetime.now(pytz.utc)

                    if current_expiry:
                        current_expiry = current_expiry.replace(tzinfo=pytz.utc)
                    if current_expiry and current_expiry > now_aware:
                        expiry_str_in_ist = current_expiry.astimezone(pytz.timezone("Asia/Kolkata")).strftime("%d-%m-%Y\n⏱️ Expiry Time: %I:%M:%S %p")
                        await message.reply_text(
                            f"🚫 <b>Yᴏᴜ ᴀʟʀᴇᴀᴅʏ ʜᴀᴠᴇ ᴀᴄᴛɪᴠᴇ ᴘʀᴇᴍɪᴜᴍ ᴀᴄᴄᴇss!</b>\n\n"
                            f"⏳ <b>Cᴜʀʀᴇɴᴛ Pʀᴇᴍɪᴜᴍ Exᴘɪʀʏ:</b> {expiry_str_in_ist}\n\n"
                            f"<i>Yᴏᴜ ᴄᴀɴɴᴏᴛ ʀᴇᴅᴇᴇᴍ ᴀɴᴏᴛʜᴇʀ ᴄᴏᴅᴇ ᴜɴᴛɪʟ ʏᴏᴜʀ ᴄᴜʀʀᴇɴᴛ ᴘʀᴇᴍɪᴜᴍ ᴀᴄᴄᴇss ᴇxᴘɪʀᴇs.</i>\n\n"
                            f"<b>Tʜᴀɴᴋ ʏᴏᴜ ғᴏʀ ᴜsɪɴɢ ᴏᴜʀ sᴇʀᴠɪᴄᴇ! 🔥</b>",
                            link_preview_options=LinkPreviewOptions(is_disabled=True)
                        )
                        return
                    expiry_time = now_aware + timedelta(seconds=seconds)
                    user_data = {"id": user_id, "expiry_time": expiry_time}
                    await db.update_user(user_data)
                    activated = True

                    expiry_str_in_ist = expiry_time.astimezone(pytz.timezone("Asia/Kolkata")).strftime("%d-%m-%Y\n⏱️ Expiry Time: %I:%M:%S %p")
                    await message.reply_text(
                        f"🎉 <b>Premium activated successfully! 🚀</b>\n\n"
                        f"👤 <b>User:</b> {user.mention}\n"
                        f"⚡ <b>User ID:</b> <code>{user_id}</code>\n"
                        f"⏳ <b>Premium Access Duration:</b> <code>{time}</code>\n"
                        f"⌛️ <b>Expiry Date:</b> {expiry_str_in_ist}",
                        link_preview_options=LinkPreviewOptions(is_disabled=True)
                    )
                    log_message = f"""
                        #Redeem_Premium 🔓

                        👤 <b>User:</b> {user.mention}
                        ⚡ <b>User ID:</b> <code>{user_id}</code>
                        ⏳ <b>Premium Access Duration:</b> <code>{time}</code>
                        ⌛️ <b>Expiry Date:</b> {expiry_str_in_ist}

                        🎉 Premium activated successfully! 🚀
                        """
                    try:
                        await client.send_message(
                            PREMIUM_LOGS,
                            text=log_message,
                            link_preview_options=LinkPreviewOptions(is_disabled=True)
                        )
                    except Exception:
                        pass   # a bad PREMIUM_LOGS must never turn a successful redeem into an error
                else:
                    await message.reply_text("Invalid time format in redeem code.")
            except Exception as e:
                await message.reply_text(f"An error occurred while redeeming the code: {e}")
            finally:
                if not activated:
                    await db.put_back_redeem_code(redeem_code, time)
        else:
            await message.reply_text("Invalid Redeem Code or Expired.")
    else:
        await message.reply_text("Usage: /redeem <code>")
