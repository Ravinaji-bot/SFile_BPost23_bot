import os as _os, time as _time
# The code mixes datetime.now() and UTC; making the process clock UTC keeps premium / verification
# times correct on every host (a PC / VPS in IST used to be off by 5.5 hours).
_os.environ["TZ"] = "UTC"
if hasattr(_time, "tzset"):
    _time.tzset()
from memory_guard import tune_malloc, memory_cleaner
tune_malloc()   # must run before the heavy imports: gives freed image buffers back to the OS
import plugins.monkey_patch  # noqa: F401
import os
import sys
import logging
import logging.config
from pyrogram import idle, __version__
from pyrogram.raw.all import layer
import time
from pyrogram.errors import FloodWait
import asyncio
from datetime import date, datetime
from pathlib import Path
import pytz
from aiohttp import web
from database.ia_filterdb import Media, Media2
from database.users_chats_db import db
from info import MULTIPLE_DB, ON_HEROKU, LOG_STR, LOG_CHANNEL, PORT, MOVIE_UPDATE_CHANNEL
from utils import temp
from Script import script
from plugins import web_server, check_expired_premium, keep_alive
from dreamxbotz.Bot import dreamxbotz, post_bot
from dreamxbotz.util.keepalive import ping_server
from dreamxbotz.Bot.clients import initialize_clients
from dreamxbotz.util.bot_commands import set_role_commands
from PIL import Image

Image.MAX_IMAGE_PIXELS = 120_000_000  # was 500M - too much memory for one image

logger = logging.getLogger(__name__)
logging.config.fileConfig('logging.conf')
logging.getLogger().setLevel(logging.INFO)
logging.getLogger("pyrogram").setLevel(logging.ERROR)
logging.getLogger("imdbpy").setLevel(logging.ERROR)
logging.getLogger("aiohttp").setLevel(logging.ERROR)
logging.getLogger("aiohttp.web").setLevel(logging.ERROR)
logging.getLogger("pymongo").setLevel(logging.WARNING)

botStartTime = time.time()
_bg_tasks = set()   # strong references: asyncio keeps only weak ones, a task could be garbage collected


def _spawn(coro):
    task = asyncio.create_task(coro)
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)
    return task

async def _graceful_restart():
    """Used by the RAM guard: stop the client cleanly, then start the same process again.
    /autopost, /ccopy and /postrange continue by themselves after the restart."""
    try:
        await asyncio.wait_for(dreamxbotz.stop(), timeout=10)
    except Exception:
        pass
    os.execl(sys.executable, sys.executable, *sys.argv)

def get_plugins_names(plugins_dir="plugins"):
    plugins_path = Path(plugins_dir)
    if not plugins_path.exists():
        logger.warning("Plugins directory not found: %s", plugins_path)
        return []

    return [
        ".".join(file.relative_to(plugins_path).with_suffix("").parts)
        for file in sorted(plugins_path.rglob("*.py"))
        if file.name != "__init__.py"
    ]

async def dreamxbotz_start():
    logger.info('\n\nInitializing DreamxBotz')
    dreamxbotz.loop = asyncio.get_running_loop()
    await dreamxbotz.start()
    bot_info = await dreamxbotz.get_me()
    try:
        from dreamxbotz.util import autoclean
        await autoclean.install(dreamxbotz)   # 6 minute auto delete of every PM / group message of the bot
    except Exception:
        logger.exception("autoclean not started")
    try:
        from plugins.menu_ui import install_hooks
        install_hooks()   # PM searches follow the user's Result Mode / First Priority settings
    except Exception:
        logger.exception("menu hooks not installed")
    dreamxbotz.username = bot_info.username
    await initialize_clients()
    plugins_names = get_plugins_names()
    if plugins_names:
        plugins_list = "\n".join(f"  {i}. {name}" for i, name in enumerate(plugins_names, 1))
        logger.info("Plugins Found (%d):\n%s", len(plugins_names), plugins_list)
    else:
        logger.warning("No plugins found.")

    if ON_HEROKU:
        _spawn(ping_server())
    b_users, b_chats = await db.get_banned()
    temp.BANNED_USERS = b_users
    temp.BANNED_CHATS = b_chats
    await db.ensure_indexes()
    await Media.ensure_indexes()
    if MULTIPLE_DB:
        await Media2.ensure_indexes()
        logger.info("Multiple Database Mode On. Now Files Will Be Save In Second DB If First DB Is Full")
    else:
        logger.info("Single DB Mode On ! Files Will Be Save In First Database")
    
    me = bot_info
    temp.ME = me.id
    temp.U_NAME = me.username
    temp.B_NAME = me.first_name
    temp.B_LINK = me.mention
    if post_bot is not None:
        try:
            await post_bot.start()
            temp.LINK_BOT = post_bot.me.username
            logger.info("Post helper bot started: @%s - card links open this bot", temp.LINK_BOT)
        except Exception as e:
            temp.LINK_BOT = None
            logger.error("POST_BOT_TOKEN is set but the helper bot did not start (%s) - card links use the main bot", e)
    try:
        from plugins.pretty_name import load_pretty_settings
        await load_pretty_settings()          # pretty file names ON/OFF (stored in bot_settings)
    except Exception as e:
        logger.warning("pretty_name settings not loaded: %s", e)
    try:
        movie_ch = await dreamxbotz.get_chat(MOVIE_UPDATE_CHANNEL)
        temp.MOVIE_CH_LINK = f"https://t.me/{movie_ch.username}" if movie_ch.username else (
            movie_ch.invite_link or await dreamxbotz.export_chat_invite_link(MOVIE_UPDATE_CHANNEL)
        )
    except Exception:
        logger.warning("Could not fetch MOVIE_UPDATE_CHANNEL invite link; result card will fall back to group-add link.")
    dreamxbotz.username = '@' + me.username
    try:
        await set_role_commands(dreamxbotz)   # users / admins / owner each get their own "/" menu
    except Exception as e:
        logger.warning("Could not set role based bot commands: %s", e)
    try:
        from plugins.file_caption import load_switches
        await load_switches()   # /langbox and /videocaption settings
    except Exception:
        logger.exception("caption switches not loaded")
    try:
        from plugins.chat_guard import startup_report
        await startup_report(dreamxbotz)   # chat guard: the bot works only in chats the owner approved
    except Exception:
        logger.exception("chat guard start-up report failed")
    _spawn(check_expired_premium(dreamxbotz))
    try:
        from plugins.autopost import resume_autopost
        _spawn(resume_autopost(dreamxbotz))   # continue /autopost after a restart
    except Exception as e:
        logger.warning("autopost resume not started: %s", e)
    try:
        from plugins.ccopy import resume_ccopy
        _spawn(resume_ccopy(dreamxbotz))   # continue /ccopy after a restart
    except Exception as e:
        logger.warning("ccopy resume not started: %s", e)
    try:
        from plugins.postrange import resume_postrange
        _spawn(resume_postrange(dreamxbotz))   # continue /postrange after a restart
    except Exception as e:
        logger.warning("postrange resume not started: %s", e)
    try:
        from plugins.bulk_post import resume_bulkpost
        _spawn(resume_bulkpost(dreamxbotz))   # continue /bulkpost after a restart
    except Exception as e:
        logger.warning("bulkpost resume not started: %s", e)
    try:
        from plugins.autoforward import resume_autoforward
        _spawn(resume_autoforward(dreamxbotz))   # start the /autoforward worker, it continues with the pending items
    except Exception as e:
        logger.warning("autoforward resume not started: %s", e)
    try:
        from plugins.autocycle import resume_autocycle
        _spawn(resume_autocycle(dreamxbotz))   # /autocycle: the automatic rounds continue after a restart
    except Exception as e:
        logger.warning("autocycle resume not started: %s", e)
    
    logger.info(f"{me.first_name} with Pyrogram v{__version__} (Layer {layer}) started on {me.username}.")
    logger.info(LOG_STR)
    logger.info(script.LOGO)
    tz = pytz.timezone('Asia/Kolkata')
    today = date.today()
    now = datetime.now(tz)
    current_time = now.strftime("%I:%M:%S %p")
    try:
        await dreamxbotz.send_message(chat_id=LOG_CHANNEL, text=script.RESTART_TXT.format(temp.B_LINK, today, current_time))
    except Exception as e:
        logger.warning("Could not send restart message to LOG_CHANNEL (%s). Is the bot admin there?", e)
    app = web.AppRunner(await web_server())
    await app.setup()
    bind_address = "0.0.0.0"
    await web.TCPSite(app, bind_address, PORT).start()
    _spawn(keep_alive())
    _spawn(memory_cleaner(_graceful_restart))   # RAM guard (see memory_guard.py)

    try:
        await idle()
    finally:
        await app.cleanup()
        if post_bot is not None:
            try:
                await post_bot.stop()
            except Exception:
                pass
        await dreamxbotz.stop()

if __name__ == '__main__':
    try:
        logger.info('Service started...')
        asyncio.run(dreamxbotz_start())
    except FloodWait as e:
        logger.info(f"FloodWait! Sleeping for {e.value} seconds.")
        time.sleep(e.value)
    except KeyboardInterrupt:
        logger.info('Service stopped. Bye.')