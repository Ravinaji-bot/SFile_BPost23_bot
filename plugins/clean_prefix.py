"""/cleanprefix  - one-time cleanup of junk tags (info.BAD_PREFIXES, e.g. "❤️ arena")
in files that are ALREADY indexed. New files are cleaned automatically when they are indexed.

  /cleanprefix       -> only counts how many files would change (nothing is written)
  /cleanprefix run   -> applies the change to file_name and caption in the database(s)
"""
import re
import logging
from pyrogram import Client, filters
from pymongo import UpdateOne
import html
from info import OWNER_IDS, BAD_PREFIXES, MULTIPLE_DB, strip_bad_prefixes, SMALL_VIDEO_FILTER, MIN_FILE_SIZE, MIN_FILE_SIZE_MB
from database.ia_filterdb import Media, Media2

logger = logging.getLogger(__name__)

BATCH = 500


def plan_update(doc: dict) -> dict:
    """Fields of one DB document that must change (empty dict = nothing to do)."""
    upd = {}
    for field in ("file_name", "caption"):
        old = doc.get(field)
        if isinstance(old, str) and old:
            new = strip_bad_prefixes(old)
            if new and new != old:
                upd[field] = new
    return upd


async def _process(collection, apply: bool) -> int:
    pattern = "|".join(re.escape(p) for p in BAD_PREFIXES)
    if not pattern:
        return 0
    rx = {"$regex": pattern, "$options": "i"}
    query = {"$or": [{"file_name": rx}, {"caption": rx}]}
    changed, ops = 0, []
    async for doc in collection.find(query, {"file_name": 1, "caption": 1}):
        upd = plan_update(doc)
        if not upd:
            continue
        changed += 1
        if apply:
            ops.append(UpdateOne({"_id": doc["_id"]}, {"$set": upd}))
            if len(ops) >= BATCH:
                await collection.bulk_write(ops, ordered=False)
                ops = []
    if apply and ops:
        await collection.bulk_write(ops, ordered=False)
    return changed


@Client.on_message(filters.command("cleanprefix") & filters.user(OWNER_IDS))
async def clean_prefix_cmd(client, message):
    apply = len(message.command) > 1 and message.command[1].lower() == "run"
    msg = await message.reply_text("⏳ Checking the database...")
    try:
        total = await _process(Media.collection, apply)
        if MULTIPLE_DB:
            total += await _process(Media2.collection, apply)
    except Exception as e:
        logger.exception("cleanprefix failed")
        return await msg.edit_text(f"❌ Failed: <code>{e}</code>")
    if apply:
        await msg.edit_text(f"✅ Done. Cleaned <b>{total}</b> file(s) in the database.")
    else:
        await msg.edit_text(
            f"🔎 <b>{total}</b> file(s) contain the junk tag.\n\n"
            "Send <code>/cleanprefix run</code> to remove it from them."
        )


@Client.on_message(filters.command("cleansmall") & filters.user(OWNER_IDS))
async def clean_small_cmd(client, message):
    """/cleansmall = count + examples only, /cleansmall run = delete sample / small videos from the DB."""
    if MIN_FILE_SIZE <= 0:
        return await message.reply_text("MIN_FILE_SIZE_MB is 0, the small-video filter is off.")
    apply = len(message.command) > 1 and message.command[1].lower() == "run"
    msg = await message.reply_text("⏳ Checking the database...")
    cols = [Media.collection] + ([Media2.collection] if MULTIPLE_DB else [])
    total, examples = 0, []
    try:
        for col in cols:
            if apply:
                res = await col.delete_many(SMALL_VIDEO_FILTER)
                total += res.deleted_count
            else:
                total += await col.count_documents(SMALL_VIDEO_FILTER)
                if len(examples) < 5:
                    async for d in col.find(SMALL_VIDEO_FILTER, {"file_name": 1, "file_size": 1}).limit(5 - len(examples)):
                        examples.append(f"• {html.escape(str(d.get('file_name')))[:60]} - {(d.get('file_size') or 0) / 1048576:.2f} MB")
    except Exception as e:
        logger.exception("cleansmall failed")
        return await msg.edit_text(f"❌ Failed: <code>{e}</code>")
    if apply:
        return await msg.edit_text(f"✅ Deleted <b>{total}</b> sample / small video(s) (under {MIN_FILE_SIZE_MB:g} MB) from the database.")
    text = f"🔎 <b>{total}</b> video(s) are under {MIN_FILE_SIZE_MB:g} MB."
    if examples:
        text += "\n\nExamples:\n" + "\n".join(examples)
    if total:
        text += "\n\nSend <code>/cleansmall run</code> to delete them permanently."
    await msg.edit_text(text)
