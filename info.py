import re
import os
from os import environ, getenv
from Script import script

# Utility functions
id_pattern = re.compile(r'^.\d+$')

def is_enabled(value, default):
    if value is None:
        return default
    value = str(value).strip().lower()
    if value in ["true", "yes", "1", "enable", "y"]:
        return True
    elif value in ["false", "no", "0", "disable", "n"]:
        return False
    else:
        return default

# ============================
# Bot Information Configuration
# ============================
SESSION = environ.get('SESSION', 'dreamxbotz_search')   # Session name for the bot
_api_id = environ.get('API_ID', '').strip()
if not _api_id.isdigit():
    raise SystemExit("API_ID is missing or not a number. Set it from https://my.telegram.org")
API_ID = int(_api_id) # API ID from my.telegram.org
API_HASH = environ.get('API_HASH', '')  # API Hash from my.telegram.org
BOT_TOKEN = environ.get('BOT_TOKEN', "")    # Bot token from @BotFather
if not API_HASH.strip():
    raise SystemExit("API_HASH is missing. Set it from https://my.telegram.org")
if not BOT_TOKEN.strip():
    raise SystemExit("BOT_TOKEN is missing. Get it from @BotFather")

# ============================
# Bot Settings Configuration
# ============================
CACHE_TIME = int(environ.get('CACHE_TIME', 300))    # Cache time in seconds (default: 5 minutes)
USE_CAPTION_FILTER = is_enabled(environ.get('USE_CAPTION_FILTER', "True"), True)  # Use caption filter for search results (default: True)
INDEX_CAPTION = is_enabled(environ.get('SAVE_CAPTION', "True"), True) # Save caption db when idexing make it False if you dont use USE_CAPTION_FILTER for search results (default: True)
#Making it false will not save caption in db SO you can save some storage space
COVERX = is_enabled(environ.get('COVERX', "True"), True) # Use cover image for indexed files (default: True)
AUTO_POSTER_THUMB = is_enabled(environ.get('AUTO_POSTER_THUMB', "True"), True) # Replace the file's own (often branded) cover/thumbnail with the movie's clean TMDB/IMDB landscape poster when delivering to users
# If you disable it then bot will use a default thumb for all files

PICS_URL = (environ.get('PICS', 'https://i.ibb.co/gXKFr43/photo-2026-10-08-19-19-50-7694378913727774736.jpg https://i.ibb.co/KjRtcnKd/photo-2026-10-08-19-19-41-7694378870778101776.jpg https://i.ibb.co/232g4fG7/photo-2026-10-08-19-12-47-7694378793468690448.jpg')).split()
PICS = (environ.get('PICS', 'https://i.ibb.co/gXKFr43/photo-2026-10-08-19-19-50-7694378913727774736.jpg https://i.ibb.co/KjRtcnKd/photo-2026-10-08-19-19-41-7694378870778101776.jpg https://i.ibb.co/232g4fG7/photo-2026-10-08-19-12-47-7694378793468690448.jpg')).split()
NOR_IMG = environ.get("NOR_IMG", "https://graph.org/file/e20b5fdaf217252964202.jpg")
MELCOW_PHOTO = environ.get("MELCOW_PHOTO", "https://graph.org/file/56b5deb73f3b132e2bb73.jpg")
SPELL_IMG = environ.get("SPELL_IMG", "https://graph.org/file/13702ae26fb05df52667c.jpg")
SUBSCRIPTION = (environ.get('SUBSCRIPTION', 'https://graph.org/file/242b7f1b52743938d81f1.jpg'))
FSUB_PICS = (environ.get('FSUB_PICS', 'https://graph.org/file/7478ff3eac37f4329c3d8.jpg https://graph.org/file/56b5deb73f3b132e2bb73.jpg')).split()  # Fsub pic

# ============================
# Admin, Channels & Users Configuration
# ============================
ADMINS = [int(admin) if id_pattern.search(admin) else admin for admin in environ.get('ADMINS', '').split()] # Set your own admin ID(s) in the ADMINS env variable (no default admin)
# Owner(s): full control. Set OWNER_ID in env (space separated). If OWNER_ID is empty, the FIRST id in ADMINS is treated as owner.
OWNER_IDS = [int(o) if id_pattern.search(o) else o for o in environ.get('OWNER_ID', '').split()]
if not OWNER_IDS and ADMINS:
    OWNER_IDS = [ADMINS[0]]
ADMINS = ADMINS + [o for o in OWNER_IDS if o not in ADMINS]  # owners are always admins too (admin commands work for them)
CHANNELS = [int(ch) if id_pattern.search(ch) else ch for ch in environ.get('CHANNELS', '-100').split()]  # Channel id for auto indexing (make sure bot is admin)

LOG_CHANNEL = int(environ.get('LOG_CHANNEL') or '-100')  # Log channel id (make sure bot is admin)
POST_BOT_TOKEN = environ.get('POST_BOT_TOKEN', '').strip()  # 2nd (helper) bot: the 'Click here' links of every post card open THIS bot. Empty = off
BIN_CHANNEL = int(environ.get('BIN_CHANNEL') or '-100')  # Bin channel id (make sure bot is admin)
RESULT_ARCHIVE_CHANNEL = int(environ.get('RESULT_ARCHIVE_CHANNEL') or '-100')  # Private channel (bot must be admin) where every post is saved 30s before it auto-deletes
POST_ARCHIVE_BEFORE = int(environ.get('POST_ARCHIVE_BEFORE', '30'))  # save the post to RESULT_ARCHIVE_CHANNEL this many seconds BEFORE it is deleted
PREMIUM_LOGS = int(environ.get('PREMIUM_LOGS') or LOG_CHANNEL)  # Premium logs channel id (falls back to LOG_CHANNEL)
DELETE_CHANNELS = [int(dch) if id_pattern.search(dch) else dch for dch in environ.get('DELETE_CHANNELS', '-100').split()] #(make sure bot is admin)
support_chat_id = environ.get('SUPPORT_CHAT_ID', '-100')  # Support group id (make sure bot is admin)
reqst_channel = environ.get('REQST_CHANNEL_ID', '-100')  # Request channel id (make sure bot is admin)
SUPPORT_CHAT = environ.get('SUPPORT_CHAT', 'https://t.me/')  # Support group link (make sure bot is admin)

# FORCE_SUB 
auth_req_channels = environ.get("AUTH_REQ_CHANNELS", "-100")# requst to join Channel for force sub (make sure bot is admin) only for bot ADMINS  
auth_channels     = environ.get("AUTH_CHANNELS", "-100")# Channels for force sub (make sure bot is admin)

# ============================
# Payment Configuration
# ============================
QR_CODE = environ.get('QR_CODE', 'https://graph.org/file/e419f801841c2ee3db0fc.jpg')    # QR code image for payments
OWNER_UPI_ID = environ.get('OWNER_UPI_ID', 'ɴᴏ ᴀᴠᴀɪʟᴀʙʟᴇ ʀɪɢʜᴛ ɴᴏᴡ')    # Owner UPI ID for payments

PREMIUM_PLANS = {
    10: "7day",
    20: "15day",    
    40: "1month", 
    55: "45day",
    75: "60day",
}  # Premium plans: price in rupees -> duration (shown on the Premium page; payment is manual via UPI)

# ============================
# MongoDB Configuration
# ============================
DATABASE_URI = environ.get('DATABASE_URI', "")  # MongoDB URI for the database
if not DATABASE_URI.strip():
    raise SystemExit("DATABASE_URI is missing. Set your MongoDB connection string")
DATABASE_NAME = environ.get('DATABASE_NAME', "Cluster0") # Database name (default: cluster)
COLLECTION_NAME = environ.get('COLLECTION_NAME', 'dreamcinezone_files') # Collection name (default: dreamcinezone_files)

# If MULTIPLE_DB Is True Then Fill DATABASE_URI2 Value Else You Will Get Error.
MULTIPLE_DB = is_enabled(os.environ.get('MULTIPLE_DB', "False"), False) # Type True For Turn On MULTIPLE DB FUNTION 
DATABASE_URI2 = environ.get('DATABASE_URI2', "")  # MongoDB URI for the second database (if MULTIPLE_DB is True)
# ============================
# Movie Notification & Update Settings
# ============================
MOVIE_UPDATE_NOTIFICATION = is_enabled(environ.get('MOVIE_UPDATE_NOTIFICATION', "False"), False)  # Notification On (True) / Off (False)
MOVIE_UPDATE_CHANNEL = int(environ.get('MOVIE_UPDATE_CHANNEL') or '-100')  # Notification of sent to your channel
# PostCardDaily: PRIVATE channel (bot must be admin) where every post card (live auto-post, /post, /repost) is sent.
# Empty = post cards keep going to MOVIE_UPDATE_CHANNEL (old behaviour).
POSTCARD_CHANNEL = int(environ.get('POSTCARD_CHANNEL') or '0')
POSTCARD_TARGET = POSTCARD_CHANNEL if POSTCARD_CHANNEL <= -1000 else MOVIE_UPDATE_CHANNEL
DREAMXBOTZ_IMAGE_FETCH = is_enabled(environ.get('DREAMXBOTZ_IMAGE_FETCH', "True"), True)  # On (True) / Off (False)
LINK_PREVIEW = is_enabled(environ.get('LINK_PREVIEW', "True"), True) # True (default) = channel posts (live auto-post + /bulkpost) show the poster in the box BELOW the text (same as the PM post card). False = poster as a photo above the text
ABOVE_PREVIEW = is_enabled(environ.get('ABOVE_PREVIEW', "False"), False) # True = poster shows ABOVE the text, False = poster shows BELOW the text
TMDB_API_KEY = environ.get('TMDB_API_KEY', '') # preffer to use your own tmdb API Key get it from https://www.themoviedb.org/settings/api
TMDB_POSTER = is_enabled(environ.get('TMDB_POSTER', "True"), True) # Shows TMDB poster in notification msg
LANDSCAPE_POSTER = is_enabled(environ.get('LANDSCAPE_POSTER', "True"), True) # Shows landscape poster in notification msg
MOVIE_POST_WATERMARK = environ.get('MOVIE_POST_WATERMARK', '<a href="https://t.me/dreamxbotz">ᴅʀᴇᴀᴍxʙᴏᴛᴢ</a> 🤞') # Text shown after "💢 ᴘᴏᴡᴇʀᴇᴅ ʙʏ :" in the auto post, HTML allowed

# ============================
# Verification Settings
# ============================
IS_VERIFY = is_enabled(environ.get('IS_VERIFY', 'False'), False)  # Verification On (True) / Off (False)
LOG_VR_CHANNEL = int(environ.get('LOG_VR_CHANNEL') or LOG_CHANNEL) #Verification Channel Id (falls back to LOG_CHANNEL)
VERIFY_IMG = environ.get("VERIFY_IMG", "https://telegra.ph/file/9ecc5d6e4df5b83424896.jpg")

TUTORIAL = environ.get("TUTORIAL", "https://t.me/How_to_Open_Link_33/29")   # Tutorial link for verification
TUTORIAL_2 = environ.get("TUTORIAL_2", "https://t.me/How_to_Open_Link_33/29")   # Second tutorial link for verification
TUTORIAL_3 = environ.get("TUTORIAL_3", "https://t.me/How_to_Open_Link_33/29")   # Third tutorial link for verification

# Verification (Must Fill All Veriables. Else You Got Error
SHORTENER_API = environ.get("SHORTENER_API", "") # Shortener API key
SHORTENER_WEBSITE = environ.get("SHORTENER_WEBSITE", "") # Shortener website

SHORTENER_API2 = environ.get("SHORTENER_API2", "")  # Shortener API key for second website
SHORTENER_WEBSITE2 = environ.get("SHORTENER_WEBSITE2", "") # Shortener website for second website

SHORTENER_API3 = environ.get("SHORTENER_API3", "")
SHORTENER_WEBSITE3 = environ.get("SHORTENER_WEBSITE3", "") # Shortener website for third website

TWO_VERIFY_GAP = int(environ.get('TWO_VERIFY_GAP', "1200")) # Time gap for two-step verification in seconds (default: 20 minutes)
THREE_VERIFY_GAP = int(environ.get('THREE_VERIFY_GAP', "54000"))    

# ============================
# Channel & Group Links Configuration
# ============================
GRP_LNK = environ.get('GRP_LNK', 'https://t.me/Magicofgroup') # Group link for the bot
OWNER_LNK = environ.get('OWNER_LNK', 'https://t.me/DragonAdminCantact_bot') # Owner link for the bot
UPDATE_CHNL_LNK = environ.get('UPDATE_CHNL_LNK', 'https://t.me/Magicofgroup') # Update channel link for the bot

# ============================
# Group protection (plugins/antispam.py)
# ============================
ANTISPAM = is_enabled(environ.get('ANTISPAM', "True"), True)  # master switch for the group protection (links / @mentions / forwards deleted, added bots banned)
ANTISPAM_DEFAULT_ON = is_enabled(environ.get('ANTISPAM_DEFAULT_ON', "True"), True)  # protection state of a group that never used /antispam on|off
ANTISPAM_BAN_BOTS = is_enabled(environ.get('ANTISPAM_BAN_BOTS', "True"), True)  # ban every bot that somebody adds to a group (only the owners in ADMINS may add bots)
ANTISPAM_ADMINS_CAN_ADD_BOTS = is_enabled(environ.get('ANTISPAM_ADMINS_CAN_ADD_BOTS', "True"), True)  # a group admin / owner may add bots to his own group (False = only ADMINS)
ANTISPAM_ALLOWED = [x for x in re.split(r'[,\s]+', environ.get('ANTISPAM_ALLOWED', '')) if x]  # links / @names / domains that are always allowed, e.g. "@mychannel mysite.com"

# ============================
# Chat guard (plugins/chat_guard.py)
# ============================
CHAT_GUARD = is_enabled(environ.get('CHAT_GUARD', "True"), True)  # True = the bot works ONLY in groups / channels the owner approved (private chats are never touched)
CHAT_GUARD_AUTO_LEAVE = is_enabled(environ.get('CHAT_GUARD_AUTO_LEAVE', "False"), False)  # True = the bot also leaves an unapproved chat at once (False = it stays silent and asks the owner)
ALLOWED_CHATS = environ.get('ALLOWED_CHATS', '')  # extra chat ids / @usernames that are always allowed (space separated)

# ============================
# User Configuration
# ============================
auth_users = [int(user) if id_pattern.search(user) else user for user in environ.get('AUTH_USERS', '').split()]
AUTH_USERS = (auth_users + ADMINS) if auth_users else []
PREMIUM_USER = [int(user) if id_pattern.search(user) else user for user in environ.get('PREMIUM_USER', '').split()]

# ============================
# Miscellaneous Configuration
# ============================
ULTRA_FAST_MODE = is_enabled(environ.get('ULTRA_FAST_MODE', "False"), False) # Set to True for fast search, False for original search

MAX_B_TN = environ.get("MAX_B_TN", "5") # Maximum number of buttons in a row (default: 5)
PORT = int(environ.get("PORT", "8080"))  # Port for the web server (default: 8080)
MSG_ALRT = environ.get('MSG_ALRT', 'Share & Support Us ♥️') # Alert message for users
FILE_DELETE_TIME = max(10, int(environ.get("FILE_DELETE_TIME", "50")) // 10 * 10)  # delivered file auto-delete; countdown shows 50s,40s,... (10s steps)
DELETE_TIME = int(environ.get("DELETE_TIME", "300"))  #  deletion time in seconds (default: 5 minutes). Adjust as per your needs.
AUTO_CLEAN_TIME = int(environ.get("AUTO_CLEAN_TIME", "360"))  # EVERYTHING the bot sends in a PM / group (replies, filters, errors) is deleted after this many seconds (default 360 = 6 minutes, 0 = off). The delete times above are not changed.
CLEAN_USER_PM_MESSAGES = is_enabled(environ.get("CLEAN_USER_PM_MESSAGES", "True"), True)  # True = the messages users type in the bot PM are also deleted after AUTO_CLEAN_TIME (PM only)
BROADCAST_DELETE_TIME = int(environ.get("BROADCAST_DELETE_TIME", "86400"))  # a broadcast stays this many seconds (default 24 h) until the owner changes it with /bcdelete (0 = never)
START_CMD_DELETE_TIME = int(environ.get("START_CMD_DELETE_TIME", "3"))     # user's /start message is deleted after this many seconds (default: 3)
# False (default) = when TMDB has NO landscape backdrop for a title, do not put the portrait poster on a blurred
# canvas; use the video's own (landscape) thumbnail instead. True = old behaviour (portrait poster on blur).
ALLOW_PORTRAIT_POSTER = is_enabled(environ.get("ALLOW_PORTRAIT_POSTER", "False"), False)
POSTCARD_LANDSCAPE = is_enabled(environ.get("POSTCARD_LANDSCAPE", "True"), True)  # True = post card mein landscape, False = portrait poster
POSTCARD_PREVIEW = is_enabled(environ.get("POSTCARD_PREVIEW", "True"), True)
HIDE_USER_COMMANDS = is_enabled(environ.get("HIDE_USER_COMMANDS", "True"), True)
USER_MSG_DELETE_TIME = int(environ.get("USER_MSG_DELETE_TIME", "5"))  # group: user ka search message itne second baad delete (0 = band)
START_HOME_DELETE_TIME = int(environ.get("START_HOME_DELETE_TIME", "300"))  # bot's /start home message is deleted after this many seconds (default: 5 minutes)
# Branded file name in every caption / post heading:  "🐉 Jailer (2023) 1080p NF WEB-DL x264 [Hindi ORG - Tamil] 𝑫𝒓𝒂𝒈𝒐𝒏𝑭𝒊𝒓𝒆𝑾𝒐𝒓𝒍𝒅.mkv"
# (emoji in front, tag before the extension). Set a variable EMPTY to switch that part off.
NAME_BRAND_EMOJI = environ.get('NAME_BRAND_EMOJI', '🐉')
NAME_BRAND_TAG = environ.get('NAME_BRAND_TAG', '𝑫𝒓𝒂𝒈𝒐𝒏𝑭𝒊𝒓𝒆𝑾𝒐𝒓𝒍𝒅')
CUSTOM_FILE_CAPTION = environ.get("CUSTOM_FILE_CAPTION", f"{script.CAPTION}")   # Custom caption for files
BATCH_FILE_CAPTION = environ.get("BATCH_FILE_CAPTION", CUSTOM_FILE_CAPTION) # Custom caption for batch files
IMDB_TEMPLATE = environ.get("IMDB_TEMPLATE", f"{script.IMDB_TEMPLATE_TXT}")     # Custom IMDB template 
MAX_LIST_ELM = int(environ.get("MAX_LIST_ELM") or 10) or None # Maximum number of elements in a list (default: 10, set 0 for no limit)
INDEX_REQ_CHANNEL = int(environ.get('INDEX_REQ_CHANNEL') or LOG_CHANNEL)  # Index Request Channel ID (make sure bot is admin)
NO_RESULTS_MSG = is_enabled(environ.get("NO_RESULTS_MSG", "True"), True)  # True if you want no results messages in Log Channel
MAX_BTN = is_enabled((environ.get('MAX_BTN', "True")), True)    # Max Button On (True) / Off (False)
P_TTI_SHOW_OFF = is_enabled((environ.get('P_TTI_SHOW_OFF', "False")), False)    # P_TTI_SHOW_OFF On (True) / Off (False)
IMDB = is_enabled((environ.get('IMDB', "True")), True)    # IMDB Results On (True) / Off (False)
TMDB_ON_SEARCH = is_enabled((environ.get('TMDB_ON_SEARCH', "False")), False)    # Use TMDB Poster On Search Results
AUTO_FFILTER = is_enabled((environ.get('AUTO_FFILTER', "True")), True) # Auto Filter On (True) / Off (False)
AUTO_DELETE = is_enabled((environ.get('AUTO_DELETE', "True")), True) # Auto Delete On (True) / Off (False)
LONG_IMDB_DESCRIPTION = is_enabled(environ.get("LONG_IMDB_DESCRIPTION", "False"), False) # Long IMDB Description On (True) / Off (False)
SPELL_CHECK_REPLY = is_enabled(environ.get("SPELL_CHECK_REPLY", "True"), True) # Spell Check Mode On (True) / Off (False)
MELCOW_NEW_USERS = is_enabled((environ.get('MELCOW_NEW_USERS', "False")), False) # Melcow New Users On (True) / Off (False)
PROTECT_CONTENT = is_enabled((environ.get('PROTECT_CONTENT', "False")), False) # Protect Content On (True) / Off (False)
PM_SEARCH = is_enabled(environ.get('PM_SEARCH', "True"), True)  # PM Search On (True) / Off (False)
PM_POST_FIRST = is_enabled(environ.get('PM_POST_FIRST', "True"), True)  # PM search: send the poster-style post card first, the normal result below it
EMOJI_MODE = is_enabled(environ.get('EMOJI_MODE', "True"), True)  # Emoji status On (True) / Off (False)
BUTTON_MODE = is_enabled((environ.get('BUTTON_MODE', "False")), False) # pm & Group button or link mode (True) / Off (False)
STREAM_MODE = is_enabled(environ.get('STREAM_MODE', "True"), True) # Set Stream mode True or False
PREMIUM_STREAM_MODE = is_enabled(environ.get('PREMIUM_STREAM_MODE', "False"), False) # Set Stream mode True or False only for premium users
MAINTENANCE = is_enabled(environ.get('MAINTENANCE', "False"), False)


# ============================
# Bot Configuration
# ============================

# The placeholder default "-100" is not a real channel id (real ones look like -1001234567890);
# keep only real ids, otherwise every /start would try to check a channel that does not exist.
AUTH_REQ_CHANNELS = [int(ch) for ch in auth_req_channels.split() if ch and id_pattern.match(ch) and int(ch) <= -1000]
AUTH_CHANNELS = [int(ch) for ch in auth_channels.split() if ch and id_pattern.match(ch) and int(ch) <= -1000]
REQST_CHANNEL = int(reqst_channel) if reqst_channel and id_pattern.search(reqst_channel) else None
SUPPORT_CHAT_ID = int(support_chat_id) if support_chat_id and id_pattern.search(support_chat_id) else None
LANGUAGES = {"ᴍᴀʟᴀʏᴀʟᴀᴍ":"mal","ᴛᴀᴍɪʟ":"tam","ᴇɴɢʟɪsʜ":"eng","ʜɪɴᴅɪ":"hin","ᴛᴇʟᴜɢᴜ":"tel","ᴋᴀɴɴᴀᴅᴀ":"kan","ɢᴜᴊᴀʀᴀᴛɪ":"guj","ᴍᴀʀᴀᴛʜɪ":"mar","ᴘᴜɴᴊᴀʙɪ":"pun"}
QUALITIES = ["360P", "480P", "720P", "1080P", "1440P", "2160P", "4K"]

SEASON_COUNT = 12
SEASONS = [f"S{str(i).zfill(2)}" for i in range(1, SEASON_COUNT + 1)]

BAD_WORDS = {
    "PrivateMovieZ",
    "toonworld4all",
    "themoviesboss",
    "1tamilmv",
    "tamilblasters",
    "1tamilblasters",
    "skymovieshd",
    "extraflix",
    "hdm2",
    "moviesmod",
    "hdhub4u",
    "mkvcinemas",
    "primefix",
    "join",
    "www",
    "villa",
    "tg",
    "original",
    "movie",
    "filmy4wap",
    "xyz",
    "world4ufree"
} # Set of bad words to filter out (whole words, any case)
# Bad PREFIXES: junk uploader tags stuck to the START of a file name / caption
# (e.g. "❤️ arenaRocky 1976 1080p"). Add new tags here. Removed only at the very start.
BAD_PREFIXES = [
    "arena",
]


# Symbols / emoji (hearts, stars, etc.) and zero-width chars that uploaders put in front of their tag
_TAG_EMOJI = "\u2190-\u2BFF\u3030\u303d\u3297\u3299\U0001F000-\U0001FAFF\ufe0f\u200d\u20e3"
_ZERO_WIDTH = "\u200b-\u200f\u2060\ufeff"


def norm_res(text):
    """540p is ALWAYS treated as 480p (the one rule, used everywhere: names, captions, buttons, slots, search order).
    Works on a single token ('540p') or on a whole text ('Movie 540p WEB-DL')."""
    if not text:
        return text
    return re.sub(r"(?<![0-9A-Za-z])540\s*p(?![0-9A-Za-z])", "480p", text, flags=re.IGNORECASE)


def strip_bad_prefixes(text):
    """Removes BAD_PREFIXES tags (e.g. "❤️ arena") from a file name / caption.

    Handles (any upper / lower case):
      "❤️ arenaDrishyam ..."   "💚arena Drishyam ..."   "❤️ arena.Drishyam.2026"   "arenaDrishyam ..."
      stacked tags ("@x ❤️ arenaDrishyam") and "emoji + tag" in the middle / end of a caption.
    A real title such as "Arena 2011" or "Arena of Valor" (no emoji, not glued) is left alone.
    """
    if not text or not isinstance(text, str):
        return text
    out = text
    for word in BAD_PREFIXES:
        w = re.escape(word)
        for _ in range(3):  # tag can be stacked / repeated
            before = out
            # 1) at the very start, after any symbol / emoji:  "❤️ arenaDrishyam", "(arena) Drishyam"
            out = re.sub(
                rf"^[\s{_ZERO_WIDTH}]*[^\w\s]+\s*(?i:{w})(?!s(?:\W|$))[\s._\-\]\)]*",
                "", out, count=1)
            # 2) at the very start, glued to the real title (title begins with a capital / digit)
            out = re.sub(rf"^[\s{_ZERO_WIDTH}]*{w}(?=[A-Z0-9])", "", out, count=1)
            if out == before:
                break
        # 3) anywhere: emoji + tag  ("Drishyam ❤️ arena", "x ❤️ arenaDrishyam")
        out = re.sub(rf"[{_TAG_EMOJI}]+\s*(?i:{w})(?![a-z])[ \t._\-\]\)]*", "", out)
    return out.strip() or text
 

# ============================
# Small / sample video filter
# ============================
# Videos smaller than MIN_FILE_SIZE_MB (default 30 MB) are treated as samples / junk:
# they are NOT indexed, NOT shown in search, NOT auto-posted. Set MIN_FILE_SIZE_MB=0 to turn it off.
# Only videos are checked (audio, subtitles, zip, pdf ... are never touched).
try:
    MIN_FILE_SIZE_MB = float(environ.get("MIN_FILE_SIZE_MB", "30"))
except ValueError:
    MIN_FILE_SIZE_MB = 30.0
MIN_FILE_SIZE = int(MIN_FILE_SIZE_MB * 1024 * 1024)
_VIDEO_EXTS = ("mkv", "mp4", "avi", "webm", "mov", "m4v", "ts", "flv", "wmv", "mpg", "mpeg", "3gp")
_VIDEO_EXT_RE = re.compile(r"[\s.](?:" + "|".join(_VIDEO_EXTS) + r")$", re.I)


def is_small_video(file_size, file_type=None, mime_type=None, file_name=None):
    """True for a video (by type, mime or extension) that is smaller than MIN_FILE_SIZE."""
    if MIN_FILE_SIZE <= 0:
        return False
    try:
        size = int(file_size or 0)
    except (TypeError, ValueError):
        return False
    if size <= 0 or size >= MIN_FILE_SIZE:
        return False
    return (
        file_type == "video"
        or str(mime_type or "").lower().startswith("video/")
        or bool(_VIDEO_EXT_RE.search(str(file_name or "").strip()))
    )


# Same rule as a MongoDB filter (used by search, auto-post and the /cleansmall command)
SMALL_VIDEO_FILTER = {
    "file_size": {"$gt": 0, "$lt": MIN_FILE_SIZE},
    "$or": [
        {"file_type": "video"},
        {"mime_type": {"$regex": "^video/", "$options": "i"}},
        {"file_name": {"$regex": r"[\s.](" + "|".join(_VIDEO_EXTS) + r")$", "$options": "i"}},
    ],
}
NOT_SMALL_VIDEO = {"$nor": [SMALL_VIDEO_FILTER]} if MIN_FILE_SIZE > 0 else {}


# ============================
# Server & Web Configuration
# ============================

ON_HEROKU = 'DYNO' in environ
APP_NAME = environ.get('APP_NAME', None) if ON_HEROKU else None
BIND_ADDRESS = getenv('WEB_SERVER_BIND_ADDRESS', '0.0.0.0')
FQDN = (
    environ.get('FQDN', BIND_ADDRESS)
    if not ON_HEROKU or environ.get('FQDN')
    else f"{APP_NAME}.herokuapp.com"
)
FQDN = re.sub(r'^https?://', '', str(FQDN)).rstrip('/')
NO_PORT = is_enabled(environ.get('NO_PORT'), False)
HAS_SSL = is_enabled(getenv('HAS_SSL'), True)

if HAS_SSL:
    URL = f"https://{FQDN}/"
else:
    URL = f"http://{FQDN}/" if NO_PORT else f"http://{FQDN}:{PORT}/"
    
SLEEP_THRESHOLD = int(environ.get('SLEEP_THRESHOLD', '60'))
WORKERS = int(environ.get('WORKERS', '4'))
SESSION_NAME = str(environ.get('SESSION_NAME', 'dreamXBotz'))
MULTI_CLIENT = False
name = str(environ.get('name', 'DREAMXBOTZ'))
PING_INTERVAL = int(environ.get("PING_INTERVAL", "298"))  # 5 minutes
# ============================
REACTIONS = ["🤝", "😇", "🤗", "😍", "👍", "🎅", "😐", "🥰", "🤩", "😱", "🤣", "😘", "👏", "😛", "😈", "🎉", "⚡️", "🫡", "🤓", "😎", "🏆", "🔥", "🤭", "🌚", "🆒", "👻", "😁"]

# ============================
# Commands Bot
# ============================
# Telegram "/" menu is built per role (see dreamxbotz/util/bot_commands.py):
#   normal users see USER_CMDS only, group admins also see GROUP_ADMIN_CMDS,
#   bot admins see USER + ADMIN, the owner sees USER + ADMIN + OWNER.
USER_CMDS = {
    "start": "Start the bot",
    "request": "Request missing data",
    "settings": "Open your settings",
    "history": "last 30 days file history",
    "premium": "Premium options & plans",
    "refer": "Refer & get free premium",
}

GROUP_ADMIN_CMDS = {
    "settings": "Change group settings",
    "details": "Check group settings",
    "reload": "Link this group to your PM",
    "set_shortner": "Set 1st shortener",
    "set_shortner_2": "Set 2nd shortener",
    "set_shortner_3": "Set 3rd shortener",
    "set_tutorial": "Set 1st tutorial video",
    "set_tutorial_2": "Set 2nd tutorial video",
    "set_tutorial_3": "Set 3rd tutorial video",
    "set_time": "Set 1st verification gap",
    "set_time_2": "Set 2nd verification gap",
    "set_log_channel": "Set verification log channel",
    "set_fsub": "Set custom force-sub channel",
    "remove_fsub": "Remove custom force-sub channel",
    "set_caption": "Set custom file caption",
    "set_template": "Set IMDb template",
    "antispam": "Anti-spam on / off / status",
}

ADMIN_CMDS = {
    "admin_cmd": "Admin command list",
    "stats": "Bot stats",
    "users": "List of users",
    "chats": "List of chats",
    "ban": "Ban a user",
    "unban": "Unban a user",
    "banned": "List banned users / chats",
    "invite": "Get chat invite link",
    "verify": "Verification on / off (group)",
    "movie_update": "Movie update on / off",
    "pm_search": "PM search on / off",
    "del_msg": "Clear updates notification list",
    "delete": "Delete a file from DB",
    "save": "Save a file to DB",
    "setskip": "Set index skip number",
    "post": "Create a post",
    "bulkpost": "Bulk post",
    "autopost": "Auto post",
    "postrange": "Post a range of files",
    "ccopy": "Channel copy",
    "autoforward": "Auto copy new videos: movies and series to 2 channels",
    "franchise": "Join movie names into one franchise (order of collections)",
    "bcdelete": "Auto delete time of broadcasts (owner)",
    "statuschannel": "Private channel for the messages of the background jobs",
    "repost": "Repost",
    "postsettings": "Post format settings",
    "setwatermark": "Set watermark",
    "filecaption": "Show file caption",
    "setfilecaption": "Edit file caption from bot",
    "prettyname": "Pretty file names ON/OFF",
    "prettycheck": "Check how names are built (random sample)",
    "resetfilecaption": "Reset file caption",
    "langbox": "Language box under videos on / off",
    "videocaption": "Standard caption on copied videos on / off",
    "setlinktext": "Set link text",
    "setdivider": "Set divider",
    "setbutton": "Set post button",
    "removebutton": "Remove post button",
    "resetpostformat": "Reset post format",
}

OWNER_CMDS = {
    "owner_cmd": "Owner command list",
    "allowchat": "Approve a group / channel for the bot",
    "disallowchat": "Remove the approval of a chat",
    "allowedchats": "List of approved chats",
    "approveall": "Approve all saved groups",
    "system": "Server info",
    "logs": "Get recent error logs",
    "restart": "Restart the bot",
    "maintenance": "Maintenance mode on / off",
    "commands": "Refresh the / command menus",
    "broadcast": "Broadcast to all users",
    "grp_broadcast": "Broadcast to all groups",
    "send": "Send a message to a user",
    "add_premium": "Add premium user",
    "remove_premium": "Remove premium user",
    "get_premium": "Premium info of a user",
    "premium_users": "List of premium users",
    "add_redeem": "Generate redeem codes",
    "trial_reset": "Reset free trial",
    "leave": "Leave a chat",
    "disable": "Disable a chat",
    "enable": "Enable a chat",
    "deletefiles": "Delete junk files from DB",
    "deleteall": "Delete ALL files from DB",
    "cleanprefix": "Remove junk tag (arena) from indexed files",
    "cleansmall": "Delete sample / small videos from DB",
    "ram": "RAM usage (/ram clean = free memory now)",
    "clean_groups": "Clean inactive groups",
    "resetallgroup": "Reset settings of all groups",
    "clear_junk": "Clear junk users",
    "junk_group": "Clear junk groups",
    "delreq": "Delete join requests",
}

Bot_cmds = USER_CMDS  # backward compatibility


#Don't Change Anything Here
if not MULTIPLE_DB:
    DATABASE_URI2 = DATABASE_URI

# ============================
# Logs Configuration
# ============================
LOG_STR = "Current Customized Configurations are:-\n"
LOG_STR += ("IMDB Results are enabled, Bot will be showing imdb details for your queries.\n" if IMDB else "IMDB Results are disabled.\n")
LOG_STR += ("P_TTI_SHOW_OFF found, Users will be redirected to send /start to Bot PM instead of sending file directly.\n" if P_TTI_SHOW_OFF else "P_TTI_SHOW_OFF is disabled, files will be sent in PM instead of starting the bot.\n")
LOG_STR += ("BUTTON_MODE is found, filename and file size will be shown in a single button instead of two separate buttons.\n" if BUTTON_MODE else "BUTTON_MODE is disabled, filename and file size will be shown as different buttons.\n")
LOG_STR += (f"CUSTOM_FILE_CAPTION enabled with value {CUSTOM_FILE_CAPTION}, your files will be sent along with this customized caption.\n" if CUSTOM_FILE_CAPTION else "No CUSTOM_FILE_CAPTION Found, Default captions of file will be used.\n")
LOG_STR += ("Long IMDB storyline enabled." if LONG_IMDB_DESCRIPTION else "LONG_IMDB_DESCRIPTION is disabled, Plot will be shorter.\n")
LOG_STR += ("Spell Check Mode is enabled, bot will be suggesting related movies if movie name is misspelled.\n" if SPELL_CHECK_REPLY else "Spell Check Mode is disabled.\n")
