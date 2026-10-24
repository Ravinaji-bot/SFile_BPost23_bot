import re
import logging
import asyncio
import uuid
import hashlib
import base64
from datetime import datetime
from collections import defaultdict
from plugins.Dreamxfutures.Imdbposter import get_movie_detailsx, fetch_image, get_movie_details, build_poster_from_telegram_thumb
from database.users_chats_db import db
from pyrogram import Client, filters, enums
from info import ALLOW_PORTRAIT_POSTER, CHANNELS, MOVIE_UPDATE_CHANNEL, POSTCARD_TARGET, LINK_PREVIEW, ABOVE_PREVIEW, BAD_WORDS, TMDB_POSTER, MOVIE_POST_WATERMARK, strip_bad_prefixes, _fold_fancy, FQDN, URL, norm_res
from Script import script
from database.ia_filterdb import save_file, unpack_new_file_id
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, LinkPreviewOptions
from utils import temp, get_size, extract_button
from plugins.pretty_name import display_name, effective_filename, brand_name
import html as _html
from pymongo.errors import PyMongoError, DuplicateKeyError
from pyrogram.errors import MessageIdInvalid, MessageNotModified, FloodWait
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

# Precomputed sets for faster lookups
IGNORE_WORDS = {
    "rarbg", "dub", "sub", "sample", "mkv", "aac", "combined",
    "action", "adventure", "animation", "biography", "comedy", "crime", 
    "documentary", "drama", "fantasy", "film-noir", "history", 
    "horror", "music", "musical", "mystery", "romance", "sci-fi", "sport", 
    "thriller", "war", "western", "hdcam", "hdtc", "camrip", "ts", "tc", 
    "telesync", "dvdscr", "dvdrip", "predvd", "webrip", "web-dl", "tvrip", 
    "hdtv", "web dl", "webdl", "bluray", "brrip", "bdrip", "360p", "480p", 
    "720p", "1080p", "2160p", "4k", "1440p", "540p", "240p", "140p", "hevc", 
    "hdrip", "hin", "hindi", "tam", "tamil", "kan", "kannada", "tel", "telugu", 
    "mal", "malayalam", "eng", "english", "pun", "punjabi", "ben", "bengali", 
    "mar", "marathi", "guj", "gujarati", "urd", "urdu", "kor", "korean", "jpn", 
    "japanese", "nf", "netflix", "sonyliv", "sony", "sliv", "amzn", "prime", 
    "primevideo", "hotstar", "zee5", "jio", "jhs", "aha", "hbo", "paramount", 
    "apple", "hoichoi", "sunnxt", "viki"
}|BAD_WORDS

# Constants
CAPTION_LANGUAGES = {
    "hin": "Hindi", "hindi": "Hindi",
    "tam": "Tamil", "tamil": "Tamil",
    "kan": "Kannada", "kannada": "Kannada",
    "tel": "Telugu", "telugu": "Telugu",
    "mal": "Malayalam", "malayalam": "Malayalam",
    "eng": "English", "english": "English",
    "pun": "Punjabi", "punjabi": "Punjabi",
    "ben": "Bengali", "bengali": "Bengali",
    "mar": "Marathi", "marathi": "Marathi",
    "guj": "Gujarati", "gujarati": "Gujarati",
    "urd": "Urdu", "urdu": "Urdu",
    "kor": "Korean", "korean": "Korean",
    "jpn": "Japanese", "japanese": "Japanese",
}

OTT_PLATFORMS = {
    "nf": "Netflix", "netflix": "Netflix",
    "sonyliv": "SonyLiv", "sony": "SonyLiv", "sliv": "SonyLiv",
    "amzn": "Amazon Prime Video", "prime": "Amazon Prime Video", "primevideo": "Amazon Prime Video",
    "hotstar": "Disney+ Hotstar", "zee5": "Zee5",
    "jio": "JioHotstar", "jhs": "JioHotstar",
    "aha": "Aha", "hbo": "HBO Max", "paramount": "Paramount+",
    "apple": "Apple TV+", "hoichoi": "Hoichoi", "sunnxt": "Sun NXT", "viki": "Viki"
}

STANDARD_GENRES = {
    'Action', 'Adventure', 'Animation', 'Biography', 'Comedy', 'Crime', 'Documentary',
    'Drama', 'Family', 'Fantasy', 'Film-Noir', 'History', 'Horror', 'Music',
    'Musical', 'Mystery', 'Romance', 'Sci-Fi', 'Sport', 'Thriller', 'War', 'Western'
}

# Precompiled regex patterns
CLEAN_PATTERN = re.compile(r'@[^ \n\r\t\.,:;!?()\[\]{}<>\\/"\'=_%]+|\bwww\.[^\s\]\)]+|\([\@^]+\)|\[[\@^]+\]')
NORMALIZE_PATTERN = re.compile(r"[._]+|[()\[\]{}:;'–!,.?_]")
QUALITY_PATTERN = re.compile(
    r"\b(?:HDCam|HDTC|CamRip|TS|TC|TeleSync|DVDScr|DVDRip|PreDVD|"
    r"WEBRip|WEB-DL|TVRip|HDTV|WEB DL|WebDl|BluRay|BRRip|BDRip|"
    r"360p|480p|720p|1080p|2160p|4K|1440p|540p|240p|140p|HEVC|HDRip)\b", 
    re.IGNORECASE
)
YEAR_PATTERN = re.compile(r"(?<![A-Za-z0-9])(?:19|20)\d{2}(?![A-Za-z0-9])")
RANGE_REGEX = re.compile(r'\bS(\d{1,2})[^\w\n\r]*E(?:p(?:isode)?)?0*(\d{1,2})\s*(?:to|-)\s*(?:E(?:p(?:isode)?)?)?0*(\d{1,2})',re.IGNORECASE)
SINGLE_REGEX = re.compile(r'\bS(\d{1,2})[^\w\n\r]*E(?:p(?:isode)?)?0*(\d{1,3})', re.IGNORECASE)
NAMED_RANGE_REGEX = re.compile(r'Season[\s._]*0*(\d{1,2})[\s._\-,:]*Ep(?:isode)?[\s._]*0*(\d{1,3})[\s._]*(?:to|-|\u2013)[\s._]*(?:Ep(?:isode)?[\s._]*)?0*(\d{1,3})(?!\d)', re.IGNORECASE)
NAMED_REGEX = re.compile(r'Season[\s._]*0*(\d{1,2})[\s._\-,:]*Ep(?:isode)?[\s._]*0*(\d{1,3})', re.IGNORECASE)
EP_ONLY_RANGE = re.compile(r'\b(?:EP|Episode)0*(\d{1,3})\s*-\s*0*(\d{1,3})\b',re.IGNORECASE)
# Fallback for whole-season-pack files that carry only a season marker with NO
# episode number at all, e.g. "Show Name S01" or "Show Name Season 02" (common
# when a full season is uploaded as one file instead of per-episode).
# "S01 COMBINED E01 05" / "S01 COMPLETE E06 09": filler word between season and episodes
SEASON_FILLER_REGEX = re.compile(r'(\bS\d{1,2})[\s._\-]*(?:combined|complete|full|batch|pack)\b', re.IGNORECASE)
# indexing/normalize turns "E01-05" into "E01 05" (dash -> space)
SPACE_RANGE_REGEX = re.compile(r'\bS(\d{1,2})[^\w\n\r]*E(?:p(?:isode)?)?0*(\d{1,3})[\s._]+0*(\d{1,2})(?!\d|p\b|x|\s*bit)', re.IGNORECASE)
SEASON_ONLY_REGEX = re.compile(r'\bS0*(\d{1,2})\b|\bSeason\s*0*(\d{1,2})\b', re.IGNORECASE)


MEDIA_FILTER = filters.document | filters.video | filters.audio
locks = defaultdict(asyncio.Lock)
pending_updates = {}
error_tmdb = False

def clean_mentions_links(text: str) -> str:
    return CLEAN_PATTERN.sub("", text or "").strip()

def normalize(s: str) -> str:
    s = NORMALIZE_PATTERN.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()

def remove_ignored_words(text: str) -> str:
    IGNORE_WORDS_LOWER = {w.lower() for w in IGNORE_WORDS}
    return " ".join(word for word in text.split() if _fold_fancy(word).lower() not in IGNORE_WORDS_LOWER)

def get_qualities(text: str) -> str:
    qualities = QUALITY_PATTERN.findall(text)
    return ", ".join(qualities) if qualities else "N/A"


_QUALITY_CANON = {
    "web-dl": "WEB-DL", "web dl": "WEB-DL", "webdl": "WEB-DL", "webrip": "WEBRip",
    "bluray": "BluRay", "brrip": "BRRip", "bdrip": "BDRip", "hdrip": "HDRip",
    "hdtv": "HDTV", "tvrip": "TVRip", "dvdrip": "DVDRip", "dvdscr": "DVDScr",
    "predvd": "PreDVD", "camrip": "CamRip", "hdcam": "HDCam", "hdtc": "HDTC",
    "telesync": "TeleSync", "ts": "TS", "tc": "TC", "hevc": "HEVC", "4k": "4K",
}


_HEVC_ALIAS = re.compile(r"\b(?:hevc|[xh][ ._-]?265)\b", re.IGNORECASE)
_TENBIT_ALIAS = re.compile(r"(?<![a-z0-9])(?:10[ ._-]?bits?|hi10p?)(?![a-z0-9])", re.IGNORECASE)


def _canon_quality(token: str) -> str:
    t = norm_res(token.strip())   # 540p is ALWAYS 480p (info.norm_res - one rule for the whole bot)
    return _QUALITY_CANON.get(t.lower(), t.lower() if re.fullmatch(r"\d{3,4}p", t, re.I) else t)


def merge_qualities(filename: str, caption: str) -> str:
    """Quality string such as '1080p, WEB-DL, HEVC'. The file name is the main source
    (a caption is often missing on old files); the caption only fills in a kind of
    token (resolution / source) the file name does not have. 'N/A' if nothing found."""
    def tokens(text):
        out, seen = [], set()
        for t in QUALITY_PATTERN.findall(text or ""):
            c = _canon_quality(t)
            if c.lower() not in seen:
                seen.add(c.lower())
                out.append(c)
        return out

    def is_res(t):
        return bool(re.fullmatch(r"\d{3,4}p|4K|HEVC|10bit", t, re.I))

    ftok, ctok = tokens(filename), tokens(caption)
    # x265 / H265 / H.265 are HEVC too
    if _HEVC_ALIAS.search(filename or "") and not any(t.lower() == "hevc" for t in ftok):
        ftok.append("HEVC")
    elif _HEVC_ALIAS.search(caption or "") and not any(t.lower() == "hevc" for t in ftok + ctok):
        ctok.append("HEVC")
    # 10bit (10bit / 10-bit / 10 bit / Hi10P) -> its own token
    if _TENBIT_ALIAS.search(filename or ""):
        ftok.append("10bit")
    elif _TENBIT_ALIAS.search(caption or ""):
        ctok.append("10bit")
    result = list(ftok)
    have_res = any(is_res(t) for t in ftok)
    have_src = any(not is_res(t) for t in ftok)
    for t in ctok:
        if t in result:
            continue
        if (is_res(t) and not have_res) or (not is_res(t) and not have_src):
            result.append(t)
    return ", ".join(result) if result else "N/A"

def extract_ott_platform(text: str) -> str:
    text = text.lower()
    platforms = {plat for key, plat in OTT_PLATFORMS.items() if key in text}
    return " | ".join(platforms) if platforms else "N/A"

def extract_season_episode(filename: str) -> Tuple[Optional[int], Optional[str]]:
    filename = SEASON_FILLER_REGEX.sub(r"\1 ", filename or "")
    if m := SPACE_RANGE_REGEX.search(filename):
        if int(m.group(3)) > int(m.group(2)):
            return int(m.group(1)), f"{int(m.group(2))}-{int(m.group(3))}"
    if m := EP_ONLY_RANGE.search(filename):
        sm = SEASON_ONLY_REGEX.search(filename)
        season = int(sm.group(1) or sm.group(2)) if sm else 1
        return season, f"{int(m.group(1))}-{int(m.group(2))}"
    for pattern in (RANGE_REGEX, NAMED_RANGE_REGEX, SINGLE_REGEX, NAMED_REGEX):
        if m := pattern.search(filename):
            season = int(m.group(1))
            if pattern in (RANGE_REGEX, NAMED_RANGE_REGEX):
                ep = f"{int(m.group(2))}-{int(m.group(3))}"
            else:
                ep = m.group(2)
            return season, ep
    # No episode marker found - check if this is a whole-season-pack file
    # (e.g. "S01" or "Season 02" with no episode number). Treat it as that
    # season with no specific episode, instead of falling through to #MOVIE.
    if m := SEASON_ONLY_REGEX.search(filename):
        season = int(m.group(1) or m.group(2))
        return season, None
    return None, None

_bg_tasks = set()


def _keep(task):
    """asyncio keeps only weak references to tasks: hold one so the update is not garbage collected."""
    _bg_tasks.add(task)
    task.add_done_callback(_bg_tasks.discard)
    return task


def schedule_update(bot, base_name, delay=5):
    if handle := pending_updates.get(base_name):
        if not handle.cancelled():
            handle.cancel()
    
    loop = asyncio.get_event_loop()
    pending_updates[base_name] = loop.call_later(
        delay,
        lambda: _keep(asyncio.create_task(update_movie_message(bot, base_name)))
    )

def _series_group_key(base_name, season):
    """DB key for a series file. ALL seasons of a show share ONE key (and therefore ONE card post);
    the card lists the seasons one under the other (see _series_body_lines). `season` is kept in the
    signature only so the callers did not have to change."""
    return base_name


def extract_media_info(filename: str, caption: str):
    # strip_bad_prefixes runs BEFORE clean_mentions_links, so "TG : @channel Movie" is still recognised as a promo tag
    filename = normalize(strip_bad_prefixes(clean_mentions_links(strip_bad_prefixes(filename))).title())
    caption_clean = clean_mentions_links(strip_bad_prefixes(clean_mentions_links(strip_bad_prefixes(caption)))).lower() if caption else ""
    unified = f"{caption_clean} {filename.lower()}".strip()

    season = episode = year = None
    tag = "#MOVIE"
    processed_raw = base_raw = filename
    quality = merge_qualities(filename.lower(), caption_clean)
    ott_platform = extract_ott_platform(f"{filename} {caption_clean}")

    lang_keys = {k for k in CAPTION_LANGUAGES if k in caption_clean or k in filename.lower()}
    language = ", ".join(sorted({CAPTION_LANGUAGES[k] for k in lang_keys})) if lang_keys else "N/A"

    season, episode = extract_season_episode(filename)
    if season is not None:
        tag = "#SERIES"
        if m := (RANGE_REGEX.search(filename) or NAMED_RANGE_REGEX.search(filename) or SINGLE_REGEX.search(filename) or NAMED_REGEX.search(filename) or EP_ONLY_RANGE.search(filename) or SEASON_ONLY_REGEX.search(filename)):
            match_str = m.group(0)
            start_idx = filename.lower().find(match_str.lower())
            end_idx = start_idx + len(match_str)
            processed_raw = filename[:end_idx]
            base_raw = filename[:start_idx]
            if year_match := YEAR_PATTERN.search(filename.lower()[end_idx:]):
                y = year_match.group(0)
                yi = filename.lower().find(y, end_idx)
                if yi != -1:
                    processed_raw = filename[:yi+4]
                    base_raw += f" {y}"
    else:
        if year_match := YEAR_PATTERN.search(unified):
            year = year_match.group(0)
            year_idx = filename.lower().find(year.lower())
            if year_idx != -1:
                processed_raw = filename[:year_idx + 4]
                base_raw = processed_raw
        else:
            if qual_match := QUALITY_PATTERN.search(unified):
                qual_str = qual_match.group(0)
                qual_idx = filename.lower().find(qual_str.lower())
                if qual_idx != -1:
                    processed_raw = filename[:qual_idx]
                    base_raw = processed_raw

    base_name = normalize(remove_ignored_words(normalize(base_raw)))
    if year and year not in base_name:
        base_name += f" {year}"

    if base_name.endswith(")"):
        base_name = re.sub(r"\s+\(\d{4}\)$", "", base_name)
        if year:
            base_name += f" {year}"

    # -------------------------
    # NEW: strip season/episode tokens from final base_name
    # -------------------------
    def _strip_season_episode_tokens(name: str) -> str:
        """
        Remove common season/episode markers from a title while preserving a trailing year.
        Examples removed: S01, s01e02, 1x02, season 1, ep 02, episode 2, part 1
        """
        if not name:
            return name

        # Preserve trailing year (e.g. "Title (2020)" or "Title 2020")
        year_match = re.search(r'\(?\b(19|20)\d{2}\b\)?\s*$', name)
        year_part = ""
        if year_match:
            year_part = year_match.group(0)
            name = name[:year_match.start()].strip()

        # Common patterns to remove
        patterns = [
            r'\bS\d{1,2}E\d{1,2}\b',     # S01E02
            r'\bS\d{1,2}\b',             # S01
            r'\bE\d{1,2}\b',             # E02
            r'\b\d{1,2}x\d{1,2}\b',      # 1x02
            r'\bSeason\s*\d{1,2}\b',     # Season 1
            r'\bEp(?:isode)?\.?\s*\d{1,3}\b',  # Ep02, Episode 2
            r'\bEpisode\s*\d{1,3}\b',
            r'\bPart\s*\d{1,2}\b'
        ]

        for p in patterns:
            name = re.sub(p, ' ', name, flags=re.IGNORECASE)

        # Remove leftover separators and extra whitespace
        name = re.sub(r'[_\.\-]+', ' ', name)     # underscores/dots/hyphens
        name = re.sub(r'\s+', ' ', name).strip()

        # Reattach year in canonical form if we removed it earlier
        if year_part:
            y = re.search(r'(19|20)\d{2}', year_part)
            if y:
                name = f"{name} {y.group(0)}"

        return name.strip()

    base_name = _strip_season_episode_tokens(base_name)
    # If stripping accidentally removed everything, fall back to a safer value
    if not base_name:
        base_name = normalize(remove_ignored_words(normalize(processed_raw))) or filename

    # Safety net: if the title ended up with more than one 4-digit year token
    # (e.g. "Bakaiti 2026 2025"), keep only the LAST one - it's the one placed
    # right next to the title by convention - and drop the earlier duplicate(s).
    year_tokens = list(re.finditer(r'\b(19|20)\d{2}\b', base_name))
    if len(year_tokens) > 1:
        keep = year_tokens[-1]
        cleaned = base_name[:year_tokens[0].start()] + base_name[keep.start():]
        base_name = re.sub(r'\s+', ' ', cleaned).strip()

    return {
        "processed": normalize(processed_raw),
        "base_name": base_name,
        "tag": tag,
        "season": season,
        "episode": episode,
        "year": year,
        "quality": quality,
        "ott_platform": ott_platform,
        "language": language
    }


@Client.on_message(filters.chat(CHANNELS) & MEDIA_FILTER)
async def media_handler(bot, message):
    media = next(
        (getattr(message, ft) for ft in ("document", "video", "audio")
         if getattr(message, ft, None)),
        None
    )
    if not media:
        return

    media.file_type = next(ft for ft in ("document", "video", "audio") if getattr(message, ft, None))
    media.caption = message.caption or ""
    success, info = await save_file(media)
    if not success:
        return

    try:
        enc_file_id, _ = unpack_new_file_id(media.file_id)
    except Exception:
        enc_file_id = None
    file_size = getattr(media, "file_size", 0) or 0

    # Telegram auto-generates a thumbnail for video files (and uploaders of
    # this kind of file very often set one manually on documents too). Keep
    # its file_id around as a last-resort poster source, in case TMDB/IMDb
    # have no poster/backdrop at all for this title.
    thumbs = getattr(media, "thumbs", None)
    thumb_file_id = thumbs[-1].file_id if thumbs else None

    try:
        if await db.movie_update_status(bot.me.id):
            await process_and_send_update(bot, effective_filename(media.file_name, media.caption), media.caption, enc_file_id, file_size, thumb_file_id)
    except Exception:
        logger.exception("Error processing media")

async def process_and_send_update(bot, filename, caption, file_id=None, file_size=0, thumb_file_id=None):
    try:
        media_info = extract_media_info(filename, caption)
        base_name = media_info["base_name"]
        processed = media_info["processed"]

        # For series, group/post per season - each season gets its own DB
        # entry (and therefore its own post) instead of every season of a
        # show being merged into a single combined post.
        group_key = _series_group_key(base_name, media_info["season"]) if media_info["tag"] == "#SERIES" else base_name

        lock = locks[group_key]
        async with lock:
            await _process_with_lock(bot, filename, caption, media_info, base_name, group_key, processed, file_id, file_size, thumb_file_id)
    except PyMongoError as e:
        logger.error(f"Database error in process_and_send_update: {e}")
    except Exception as e:
        logger.exception(f"Processing failed in process_and_send_update: {e}")

async def _process_with_lock(bot, filename, caption, media_info, base_name, group_key, processed, file_id=None, file_size=0, thumb_file_id=None):
    if not hasattr(db, 'movie_updates'):
        db.movie_updates = db.db.movie_updates

    movie_doc = await db.movie_updates.find_one({"_id": group_key})
    error_tmdb=False
    file_data = {
        "filename": filename,
        "processed": processed,
        "quality": media_info["quality"],
        "language": media_info["language"],
        "ott_platform": media_info["ott_platform"],
        "timestamp": datetime.now(),
        "tag": media_info["tag"],
        "season": media_info["season"],
        "episode": media_info["episode"],
        "file_id": file_id,
        "file_size": file_size
    }

    if not movie_doc:
        if TMDB_POSTER:
            details = await get_movie_detailsx(base_name, season=media_info.get("season"), is_series=(media_info.get("season") is not None))
            if not details or details.get("error") or (not details.get("poster_url") and not details.get("backdrop_url")):
                error_tmdb=True
                logger.info("TMDB error switching to IMDB")
                details = await get_movie_details(base_name) or {}
        else:
            details = await get_movie_details(base_name) or {}

        raw_genres = details.get("genres", "N/A")
        if isinstance(raw_genres, str):
            genre_list = [g.strip() for g in raw_genres.split(",")]
            genres = ", ".join(g for g in genre_list if g in STANDARD_GENRES) or "N/A"
        else:
            genres = ", ".join(g for g in raw_genres if g in STANDARD_GENRES) or "N/A"
        # Posters should always come out landscape now (fetch_image/
        # build_poster_from_telegram_thumb both letterbox a portrait source
        # onto a landscape canvas), so always prefer a real backdrop when TMDB
        # has one - it's naturally landscape - falling back to the portrait
        # poster only if there's no backdrop at all.
        chosen_poster_url = (details.get("backdrop_url") if TMDB_POSTER and not error_tmdb else None) or details.get("poster_url")
        if (not ALLOW_PORTRAIT_POSTER and not LINK_PREVIEW and thumb_file_id
                and not (details.get("backdrop_url") and TMDB_POSTER and not error_tmdb)):
            chosen_poster_url = None   # portrait poster only -> use the video's own landscape thumbnail
        movie_doc = {
            "_id": group_key,
            "title": base_name,
            "files": [file_data],
            "poster_url": chosen_poster_url,
            "genres": genres,
            "rating": details.get("rating", "N/A"),
            "imdb_url": details.get("url", "")if not TMDB_POSTER or error_tmdb else details.get("tmdb_url"),
            "year": details.get("year") or media_info["year"],
            "tag": media_info["tag"],
            "ott_platform": media_info["ott_platform"],
            "message_id": None,
            "is_photo": False,
            "error_tmdb": error_tmdb,
            "is_backdrop": bool(details.get("backdrop_url")) if TMDB_POSTER and not error_tmdb else False,
            # No poster AND no backdrop from TMDB/IMDb at all -> remember this
            # file's own video thumbnail so send_movie_update() can build a
            # poster out of it instead of posting with no image.
            "fallback_thumb_file_id": thumb_file_id if not chosen_poster_url else None,
        }
        try:
            await db.movie_updates.insert_one(movie_doc)
            await send_movie_update(bot, group_key)
            movie_doc = await db.movie_updates.find_one({"_id": group_key})
        except DuplicateKeyError:
            movie_doc = await db.movie_updates.find_one({"_id": group_key})
            if movie_doc:
                if any(f["filename"] == filename for f in movie_doc["files"]):
                    return
                await db.movie_updates.update_one(
                    {"_id": group_key},
                    {"$push": {"files": file_data}}
                )
                movie_doc["files"].append(file_data)
                schedule_update(bot, group_key)
    else:
        if any(f["filename"] == filename for f in movie_doc["files"]):
            return
        await db.movie_updates.update_one(
            {"_id": group_key},
            {"$push": {"files": file_data}}
        )
        movie_doc["files"].append(file_data)
        schedule_update(bot, group_key)
def preview_url(poster_url):
    if not poster_url:
        return poster_url
    if not FQDN or FQDN.startswith(("0.0.0.0", "127.", "localhost")):
        return poster_url
    token = base64.urlsafe_b64encode(poster_url.encode()).decode().rstrip("=")
    return f"{URL}lp/{token}.jpg"
async def send_movie_update(bot, base_name, chat_id=None, coll=None, tenbit=False):
    """chat_id / coll are optional: /bulkpost uses them to post into the private archive
    channel and to keep its records apart from the live-upload posts."""
    target = chat_id or POSTCARD_TARGET
    coll = db.movie_updates if coll is None else coll
    max_retries = 3
    for attempt in range(max_retries):
        try:
            movie_doc = await coll.find_one({"_id": base_name})
            if not movie_doc:
                return None

            fmt = await get_post_format(bot.me.id)
            text = build_post_caption(movie_doc, base_name, fmt, tenbit)
            buttons = build_post_buttons(fmt, movie_doc, tenbit)
            # Every poster we send is now built landscape (see
            # _to_landscape_canvas in Imdbposter.py) regardless of whether the
            # source was a TMDB backdrop, a TMDB poster, or a video thumbnail.
            size = (2560, 1440)

            poster_url = movie_doc.get("poster_url")
            fallback_thumb = movie_doc.get("fallback_thumb_file_id")

            resized_poster = None
            if poster_url and not LINK_PREVIEW:
                resized_poster = await fetch_image(poster_url, size)
            elif not poster_url and fallback_thumb:
                # No TMDB/IMDb poster or backdrop at all - use this file's own
                # video thumbnail as a last-resort poster instead of a
                # plain-text post.
                resized_poster = await build_poster_from_telegram_thumb(bot, fallback_thumb, size)

            if resized_poster:
                try:
                    msg = await bot.send_photo(
                        chat_id=target,
                        photo=resized_poster,
                        caption=text,
                        reply_markup=buttons,
                        parse_mode=enums.ParseMode.HTML,
                        has_spoiler=fmt.get("spoiler", False)
                    )
                    is_photo = True
                except Exception as e:
                    # Photo captions are capped at 1024 chars by Telegram; if a
                    # post has many quality/episode lines it can exceed that,
                    # so fall back to a text message (4096 char limit) with the
                    # poster shown as a link preview instead of losing the post.
                    # (A video-thumbnail poster has no http URL, so it can only
                    # ever be a link preview when poster_url itself is set.)
                    if "CAPTION_TOO_LONG" in str(e).upper() or "too long" in str(e).lower():
                        text_content = f"<a href='{preview_url(poster_url)}'>&#8205;</a>{text}" if poster_url else text
                        msg = await bot.send_message(
                            chat_id=target,
                            text=text_content,
                            reply_markup=buttons,
                            parse_mode=enums.ParseMode.HTML,
                            link_preview_options=LinkPreviewOptions(is_disabled=not bool(poster_url), show_above_text=ABOVE_PREVIEW)
                        )
                        is_photo = False
                    else:
                        raise
            else:
                if poster_url and LINK_PREVIEW:
                    text = f"<a href='{preview_url(poster_url)}'>&#8205;</a>{text}"
                send_params = {
                    "chat_id": target,
                    "text": text,
                    "reply_markup": buttons,
                    "parse_mode": enums.ParseMode.HTML
                }
                if poster_url and LINK_PREVIEW:
                    send_params["link_preview_options"] = LinkPreviewOptions(is_disabled=False, show_above_text=ABOVE_PREVIEW)
                else:
                    send_params["link_preview_options"] = LinkPreviewOptions(is_disabled=not LINK_PREVIEW)
                msg = await bot.send_message(**send_params)
                is_photo = False

            # a message_id is only valid inside ONE channel: store which one it belongs to
            await coll.update_one(
                {"_id": base_name},
                {"$set": {"message_id": msg.id, "is_photo": is_photo, "channel": target}}
            )
            return msg
        except FloodWait as e:
            wait_time = e.value + 2
            await asyncio.sleep(wait_time)
        except Exception as e:
            logger.error(f"Failed to send movie update: {e}")
            break
    return None

async def update_movie_message(bot, base_name, chat_id=None, coll=None, tenbit=False):
    target = chat_id or POSTCARD_TARGET
    coll = db.movie_updates if coll is None else coll
    try:
        movie_doc = await coll.find_one({"_id": base_name})
        if not movie_doc:
            return

        fmt = await get_post_format(bot.me.id)
        text = build_post_caption(movie_doc, base_name, fmt, tenbit)
        buttons = build_post_buttons(fmt, movie_doc, tenbit)

        message_id = movie_doc.get("message_id")
        is_photo = movie_doc.get("is_photo", False)

        if not message_id:
            await send_movie_update(bot, base_name, chat_id=chat_id, coll=coll, tenbit=tenbit)
            return

        # The stored message_id is valid only in the channel it was posted to. A record without a
        # channel (posted before this field existed) or with another channel (POSTCARD_CHANNEL was
        # set / changed) must NOT be edited or deleted: the same id in this channel is a different
        # message. Leave the old post alone and post a fresh card here (the channel is stored then).
        if movie_doc.get("channel") != target:
            await coll.update_one(
                {"_id": base_name},
                {"$set": {"message_id": None, "is_photo": False}}
            )
            await send_movie_update(bot, base_name, chat_id=chat_id, coll=coll, tenbit=tenbit)
            return

        if movie_doc.get("poster_url") and LINK_PREVIEW and not is_photo:
            text = f"<a href='{preview_url(movie_doc['poster_url'])}'>&#8205;</a>{text}"

        try:
            if is_photo:
                await bot.edit_message_caption(
                    chat_id=target,
                    message_id=message_id,
                    caption=text,
                    reply_markup=buttons,
                    parse_mode=enums.ParseMode.HTML
                )
            else:
                await bot.edit_message_text(
                    chat_id=target,
                    message_id=message_id,
                    text=text,
                    reply_markup=buttons,
                    parse_mode=enums.ParseMode.HTML,
                    link_preview_options=LinkPreviewOptions(is_disabled=not LINK_PREVIEW, show_above_text=ABOVE_PREVIEW)
                )
            return
        except MessageNotModified:
            pass
        except MessageIdInvalid as e:
            logger.warning(f"Message update skipped due to error: {e}")
            pass
        except Exception:
            try:
                await bot.delete_messages(
                    chat_id=target,
                    message_ids=message_id
                )
                await coll.update_one(
                    {"_id": base_name},
                    {"$set": {"message_id": None, "is_photo": False}}
                )
            except Exception as e:
                logger.error(f"Error during message deletion/update in recovery: {e}")
                pass
            await send_movie_update(bot, base_name, chat_id=chat_id, coll=coll, tenbit=tenbit)
    except Exception as e:
        logger.error(f"Failed to update movie message for {base_name}: {e}")

DEFAULT_POST_FORMAT = {
    "bold": True,
    "watermark": MOVIE_POST_WATERMARK,
    "link_text": "Click Here",
    "divider": "───────•˚•── ✦ ──•˚•───────",
    "title_emoji": "🎬",
    "layout": "twoline",  # "twoline" = "✧ quality :\nlink (size)", "compact" = "quality : link"
    "header_box": False,  # wraps the audio/genres/ott/quality lines (and watermark) in a Telegram quote-box
    "button_text": "",    # optional inline button under the post, e.g. "📢 Join Channel"
    "button_url": "",     # URL for that button; button is shown only if BOTH are set
    "spoiler": False,     # if True, the poster photo is sent as a spoiler (blurred until tapped)
    "show_names": True,   # movie post heading = full pretty file name of the first file (instead of "Title Year")
}

_OLD_SHORT_DIVIDER = "────•˚•── ✦ ──•˚•────"

_POST_FORMAT_KEYS = {
    "bold": "POST_BOLD",
    "watermark": "POST_WATERMARK",
    "link_text": "POST_LINK_TEXT",
    "divider": "POST_DIVIDER",
    "title_emoji": "POST_TITLE_EMOJI",
    "layout": "POST_LAYOUT",
    "header_box": "POST_HEADER_BOX",
    "button_text": "POST_BUTTON_TEXT",
    "button_url": "POST_BUTTON_URL",
    "spoiler": "POST_SPOILER",
    "show_names": "POST_SHOW_NAMES",
}


async def get_post_format(bot_id):
    """Loads admin-customizable post-format settings (see plugins/post_format.py)
    from the database, falling back to DEFAULT_POST_FORMAT for anything not set."""
    fmt = dict(DEFAULT_POST_FORMAT)
    try:
        for key, db_key in _POST_FORMAT_KEYS.items():
            fmt[key] = await db.get_bot_setting(bot_id, db_key, DEFAULT_POST_FORMAT[key])
    except Exception:
        logger.exception("Failed to load post format settings, using defaults")
    if fmt.get("divider") == _OLD_SHORT_DIVIDER:   # saved earlier with the old short default -> use the long divider
        fmt["divider"] = DEFAULT_POST_FORMAT["divider"]
    return fmt


class _FileRef:
    """Tiny stand-in object exposing .file_id, matching what the 'allfiles'
    delivery flow in plugins/commands.py expects when bulk-sending a group
    of files under a single deep link."""
    __slots__ = ("file_id",)

    def __init__(self, file_id):
        self.file_id = file_id


_CODEC_TOKENS = {"hevc", "av1", "x264", "10bit"}
_RES_TOKEN_RE = re.compile(r"^\d{3,4}p$|^4k$", re.IGNORECASE)


def _split_quality(qraw: str):
    """Splits a raw 'quality' string (e.g. 'WEB-DL, 720p, HEVC') into
    (source_tokens, resolution/codec_tokens). Source tokens (WEB-DL, BluRay,
    HDRip, ...) go in the header summary line; resolution/codec tokens
    (720p, HEVC, 10bit ...) are what each download line is labelled with."""
    if not qraw or qraw == "N/A":
        return set(), []
    source, res = set(), []
    for token in (t.strip() for t in qraw.split(",")):
        if not token:
            continue
        if _RES_TOKEN_RE.match(token) or token.lower() in _CODEC_TOKENS:
            res.append(token)
        else:
            source.add(token)
    return source, res


# A slot is (height, is_hevc, is_10bit).
# Normal posts (live channel, PM search card): the six qualities
#   480p HEVC, 480p, 720p HEVC, 720p, 1080p HEVC, 1080p
# Private-channel posts (/bulkpost, /ccopy) use tenbit=True: the same six PLUS
#   480p 10bit, 720p 10bit, 1080p 10bit   (a 10bit file always goes in its 10bit slot)
_QUALITY_SLOTS = [(480, True, False), (480, False, False), (720, True, False), (720, False, False),
                  (1080, True, False), (1080, False, False)]
_QUALITY_SLOTS_10 = [(480, True, False), (480, False, False), (480, False, True),
                     (720, True, False), (720, False, False), (720, False, True),
                     (1080, True, False), (1080, False, False), (1080, False, True)]
_SLOT_INDEX = {slot: i for i, slot in enumerate(_QUALITY_SLOTS_10)}   # display order (superset)


def _slot_label(slot) -> str:
    if slot[2]:
        return f"{slot[0]}p 10bit"
    return f"{slot[0]}p HEVC" if slot[1] else f"{slot[0]}p"


def _quality_slot(qraw, tenbit=False):
    """(height, is_hevc, is_10bit) if the file is one of the allowed qualities, else None.
    With tenbit=False the 10bit tag is ignored (old behaviour: six qualities only)."""
    _, res = _split_quality(qraw or "N/A")
    height, hevc, ten = None, False, False
    for t in res:
        m = re.fullmatch(r"(\d{3,4})p", t, re.IGNORECASE)
        if m:
            height = int(m.group(1))
        elif t.lower() == "hevc":
            hevc = True
        elif t.lower() == "10bit":
            ten = True
    slot = (height, False, True) if (tenbit and ten) else (height, hevc, False)
    return slot if slot in _SLOT_INDEX and (tenbit or slot in _QUALITY_SLOTS) else None


def _file_rank(f):
    """When two files fall in the same slot only ONE is shown: the one with more audio
    languages, then the bigger file."""
    lang = f.get("language")
    n_lang = len([x for x in lang.split(",") if x.strip()]) if lang and lang != "N/A" else 0
    return (n_lang, f.get("file_size") or 0)


def _ep_key(f):
    """(season, episode) of a stored file. Older DB records saved episode=None for names like
    'S01 COMBINED E01 05'; re-read it from the file name so both files get their own line."""
    season, ep = f.get("season"), f.get("episode")
    if not ep and f.get("filename"):
        try:
            s2, e2 = extract_season_episode(normalize(f["filename"]))
            if e2 and (season is None or str(s2) == str(season)):
                ep = e2
        except Exception:
            pass
    return (season, ep)


def _pick_per_slot(files, key_fn=lambda f: None, tenbit=False):
    """{(slot, key): best_file} - one file per (slot, key). Files outside the allowed slots are left out."""
    best = {}
    for f in files:
        slot = _quality_slot(f.get("quality"), tenbit)
        if slot is None:
            continue
        k = (slot, key_fn(f))
        if k not in best or _file_rank(f) > _file_rank(best[k]):
            best[k] = f
    return best


def _visible_files(files, tenbit=False):
    """The files that will really be listed in the post (one per allowed quality /
    episode). Audio / OTT / quality header lines are built from these only."""
    series = [f for f in files if f.get("tag") == "#SERIES" and f.get("file_id")]
    movies = [f for f in files if f.get("tag") != "#SERIES" and f.get("file_id")]
    out = []
    for grp, key_fn in ((series, _ep_key), (movies, lambda f: None)):
        picked = _pick_per_slot(grp, key_fn, tenbit)
        out += list(picked.values()) if picked else grp
    return out


def _quality_sort_key(q: str):
    m = re.search(r"(\d{3,4})p", q or "")
    if m:
        return (0, int(m.group(1)), q)
    return (1, 0, q or "")


_RES_IN_NAME = re.compile(r"\s*\b(?:\d{3,4}p|4k|hevc|10bit)\b", re.IGNORECASE)


def _strip_res(name):
    """'Jeena Dil Se (2026) 480p WEB-DL x265 [Hindi].mkv' -> 'Jeena Dil Se (2026) WEB-DL x265 [Hindi].mkv'"""
    n = _RES_IN_NAME.sub("", name or "")
    n = re.sub(r"\[\s*\]|\(\s*\)", "", n)
    return re.sub(r"\s{2,}", " ", n).strip()


def _ep_bounds(ep):
    """'5' -> (5, 5), '1-10' -> (1, 10), anything else -> None"""
    try:
        a, _, b = str(ep).partition("-")
        a = int(a)
        return (a, int(b) if b else a)
    except (TypeError, ValueError):
        return None


def _episode_span(eps):
    """['1-5', '6', '7'] -> 'E01-E07'; with gaps -> 'E01-E02, E05'."""
    covered = set()
    for e in eps:
        b = _ep_bounds(e)
        if b is None:
            return ""
        covered.update(range(b[0], b[1] + 1))
    if not covered:
        return ""
    runs, run = [], [min(covered)]
    for n in sorted(covered)[1:]:
        if n == run[-1] + 1:
            run.append(n)
        else:
            runs.append(run)
            run = [n]
    runs.append(run)
    return ", ".join(f"E{r[0]:02d}" if len(r) == 1 else f"E{r[0]:02d}-E{r[-1]:02d}" for r in runs)


def _season_num_label(season):
    try:
        return f"{int(season):02d}"
    except (TypeError, ValueError):
        return str(season)


def _season_entries(sfiles, tenbit=False):
    """One entry per quality for ONE season: [(quality label, span label, link, total size)].
    A season pack gives 'Full Season'; separate episodes give ONE link for the whole group (E01-E10)."""
    picked = _pick_per_slot(sfiles, key_fn=_ep_key, tenbit=tenbit)
    by_slot = defaultdict(list)
    for (slot, key), f in picked.items():
        by_slot[slot].append((key[1], f))
    if not by_slot:   # nothing in the allowed slots -> list what exists, labelled by its raw resolution / codec
        for f in sfiles:
            _, rt = _split_quality(f.get("quality") or "N/A")
            by_slot[(" ".join(rt) if rt else "Unknown")].append((_ep_key(f)[1], f))

    def _order(s):
        return (0, _SLOT_INDEX[s]) if s in _SLOT_INDEX else (1, 0)

    out = []
    for slot in sorted(by_slot, key=_order):
        qlabel = _slot_label(slot) if slot in _SLOT_INDEX else slot
        items = by_slot[slot]
        packs = [f for ep, f in items if not ep]
        if packs:
            f = max(packs, key=_file_rank)
            out.append((qlabel, "Full Season", _file_link(f["file_id"]), f.get("file_size") or 0))
            continue
        items.sort(key=lambda it: (_ep_bounds(it[0]) or (10 ** 6, 0), str(it[0])))
        fs = [f for _, f in items]
        span = _episode_span([ep for ep, _ in items]) or f"{len(fs)} Episodes"
        out.append((qlabel, span, _group_link([f["file_id"] for f in fs]), sum((f.get("file_size") or 0) for f in fs)))
    return out



def _ep_sort_key(kv):
    """Sort (season, episode) numerically: E01, E02 ... E10 (a plain string sort gave E01, E10, E02)."""
    season, ep = kv[0]
    try:
        s_num = int(season) if season else 0
    except (TypeError, ValueError):
        s_num = 0
    m = re.match(r"\d+", str(ep or ""))
    return (s_num, int(m.group()) if m else 0, str(ep or ""))


def _fmt_episode_label(ep) -> str:
    if not ep:
        return "Full Season"
    ep = str(ep)
    if "-" in ep:
        a, b = ep.split("-", 1)
        try:
            return f"E{int(a):02d}-E{int(b):02d}"
        except ValueError:
            return f"E{a}-E{b}"
    try:
        return f"E{int(ep):02d}"
    except ValueError:
        return f"E{ep}"



# ----------------------------------------------------------------------------- series card (all seasons in one post)
def _parse_ep_range(ep):
    """'5' -> (5, 5); '1-10' / '01-10' -> (1, 10); empty / unknown -> None (a full-season file)."""
    if not ep:
        return None
    nums = [int(n) for n in re.findall(r"\d+", str(ep))]
    if not nums:
        return None
    a, b = nums[0], nums[-1]
    return (a, b) if a <= b else (b, a)


def _cover_episodes(items):
    """items: [(start, end, file)]. Returns the smallest list of them that still covers every episode any
    of them covers (greedy interval cover): E01-10 + E10-20 + E01-07 + E07-14 -> E01-10 + E10-20."""
    if not items:
        return []
    items = sorted(items, key=lambda t: (t[0], -t[1]))
    chosen, i, n = [], 0, len(items)
    while i < n:
        first = items[i]
        chosen.append(first)
        cur_end = first[1]
        i += 1
        while True:
            best, j = None, i
            while j < n and items[j][0] <= cur_end + 1:
                if best is None or items[j][1] > best[1]:
                    best = items[j]
                j += 1
            i = j
            if best is None or best[1] <= cur_end:
                break
            chosen.append(best)
            cur_end = best[1]
    return sorted(chosen, key=lambda t: (t[0], t[1]))


def _ep_union_label(ranges) -> str:
    """[(1,10),(10,20)] -> 'E01-E20'; [(1,7),(9,10)] -> 'E01-E07, E09-E10'; [(5,5)] -> 'E05'."""
    merged = []
    for a, b in sorted(ranges):
        if merged and a <= merged[-1][1] + 1:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return ", ".join(f"E{a:02d}" if a == b else f"E{a:02d}-E{b:02d}" for a, b in merged)


def _plain_len(text: str) -> int:
    """Length Telegram counts for a message: the visible text, not the HTML tags."""
    return len(_html.unescape(re.sub(r"<[^>]+>", "", text)))


def _season_label(season) -> str:
    try:
        return f"{int(season):02d}"
    except (TypeError, ValueError):
        return "N/A" if season in (None, "") else str(season)


def _series_entries(series_files, tenbit=False):
    """{season: {slot: {"ids": [...], "size": n, "eps": 'E01-E10', "ranges": [...]}}} - one entry per
    season + quality. The ids are the files that together cover the season (see _cover_episodes)."""
    fallback = not any(_quality_slot(f.get("quality"), tenbit) for f in series_files)
    by = defaultdict(lambda: defaultdict(list))
    for f in series_files:
        slot = _quality_slot(f.get("quality"), tenbit)
        if slot is None:
            if not fallback:
                continue
            _, rt = _split_quality(f.get("quality") or "N/A")
            slot = " ".join(rt) if rt else "Unknown"
        season, ep = _ep_key(f)
        by[season][slot].append((f, _parse_ep_range(ep)))
    out = {}
    for season, slots in by.items():
        out[season] = {}
        for slot, lst in slots.items():
            full = [f for f, r in lst if r is None]
            if full:                                    # a whole-season file covers everything
                best = max(full, key=_file_rank)
                out[season][slot] = {"ids": [best["file_id"]], "size": best.get("file_size") or 0,
                                     "eps": "Full Season", "ranges": []}
                continue
            uniq = {}
            for f, r in lst:                            # same episode range twice -> the better file
                if r not in uniq or _file_rank(f) > _file_rank(uniq[r]):
                    uniq[r] = f
            chosen = _cover_episodes([(a, b, uniq[(a, b)]) for (a, b) in uniq])
            out[season][slot] = {
                "ids": [c[2]["file_id"] for c in chosen],
                "size": sum((c[2].get("file_size") or 0) for c in chosen),
                "eps": _ep_union_label([(c[0], c[1]) for c in chosen]),
                "ranges": [(c[0], c[1]) for c in chosen],
            }
    return out


_COMPLETE_RE = re.compile(r"(combined|complete|batch|\bpack\b|full[\s._\-]*season)", re.IGNORECASE)


def _is_complete_name(name) -> bool:
    return bool(_COMPLETE_RE.search(name or ""))


def _sans_bold(text: str) -> str:
    """'DragonFireWorld' -> '𝗗𝗿𝗮𝗴𝗼𝗻𝗙𝗶𝗿𝗲𝗪𝗼𝗿𝗹𝗱' (plain ASCII only; text with HTML tags / already-styled text is left alone)."""
    if "<" in text or any(ord(c) > 127 for c in text):
        return text
    out = []
    for c in text.lstrip("@"):
        if "A" <= c <= "Z":
            out.append(chr(0x1D5D4 + ord(c) - 65))
        elif "a" <= c <= "z":
            out.append(chr(0x1D5EE + ord(c) - 97))
        elif "0" <= c <= "9":
            out.append(chr(0x1D7EC + ord(c) - 48))
        else:
            out.append(c)
    return "".join(out)


def _series_body_lines(series_files, tenbit, link_text, budget):
    """Season-wise body of a series card, ONE link per season + quality (the link sends every episode of
    that season in that quality). Gets shorter step by step until it fits Telegram's message limit:
      1 full:     'Season 01' + one line per quality  (eps | size)
      2 compact:  'S01 (E01-E10) : 480p | 720p | 1080p'
      3 minimal:  'S01 : 480p | 720p | 1080p'
      4 cut:      first seasons only + '... +N more seasons'"""
    entries = _series_entries(series_files, tenbit)
    if not entries:
        return []

    def s_key(s):
        try:
            return (0, int(s))
        except (TypeError, ValueError):
            return (1, 0)

    def q_key(slot):
        return (0, _SLOT_INDEX[slot]) if slot in _SLOT_INDEX else (1, 0)

    def qlabel(slot):
        return _slot_label(slot) if slot in _SLOT_INDEX else str(slot)

    seasons = sorted(entries, key=s_key)
    links = {(s, q): _group_link(e["ids"]) for s in seasons for q, e in entries[s].items()}

    def full():
        out = []
        for s in seasons:
            names = [f.get("filename") for f in series_files if _ep_key(f)[0] == s]
            complete = any(_is_complete_name(n) for n in names) or any(
                e["eps"] == "Full Season" for e in entries[s].values())
            out.append(f"◈  Season {_season_label(s)} ◈" + (" Complete ◈" if complete else ""))
            out.append("")
            for q in sorted(entries[s], key=q_key):
                e = entries[s][q]
                size = f" • {get_size(e['size'])}" if e["size"] else ""
                out.append(f"✧ {qlabel(q)} : ({e['eps']}{size})")
                out.append(f"➠ <a href='{links[(s, q)]}'>{link_text}</a>")
                out.append("")
        return out

    def compact(with_eps, n=None):
        out = []
        for s in seasons[:n]:
            allr = [r for e in entries[s].values() for r in e["ranges"]]
            eps = ""
            if with_eps:
                fulls = any(e["eps"] == "Full Season" for e in entries[s].values())
                eps = f" ({_ep_union_label(allr)})" if allr else (" (Full Season)" if fulls else "")
            parts = " | ".join(f"<a href='{links[(s, q)]}'>{qlabel(q)}</a>" for q in sorted(entries[s], key=q_key))
            out.append(f"✧ S{_season_label(s)}{eps} : {parts}")
        return out

    for build in (full, lambda: compact(True), lambda: compact(False)):
        lines = build()
        if _plain_len("\n".join(lines)) <= budget:
            return lines
    n = len(seasons)
    while n > 1:
        n -= 1
        lines = compact(False, n) + [f"… +{len(seasons) - n} more seasons"]
        if _plain_len("\n".join(lines)) <= budget:
            return lines
    return compact(False, 1)


def _file_link(file_id: str) -> str:
    return f"https://t.me/{temp.LINK_BOT or temp.U_NAME}?start=file_0_{file_id}"


def _group_link(file_ids) -> str:
    file_ids = [fid for fid in file_ids if fid]
    if not file_ids:
        return ""
    if len(file_ids) == 1:
        return _file_link(file_ids[0])
    # same list of files -> same key, so rebuilding / editing a card never piles up new DB rows
    key = hashlib.md5("|".join(file_ids).encode()).hexdigest()[:12]
    temp.GETALL[key] = [_FileRef(fid) for fid in file_ids]
    # also save it in MongoDB so the link still works after a restart / redeploy (upsert)
    try:
        asyncio.get_running_loop().create_task(db.save_getall(key, file_ids))
    except RuntimeError:
        pass
    return f"https://t.me/{temp.LINK_BOT or temp.U_NAME}?start=allfiles_0_{key}"


def card_extract_file_id(movie_doc, tenbit=False):
    """The file whose audio / subtitles / story the card's 'View Audio, Subs & Story' button shows: one of the
    files really listed in the card - the one with the most audio languages, then the biggest (same rule as
    the slots). Audio, subs and story are the same in every quality of a release."""
    try:
        vis = _visible_files((movie_doc or {}).get("files") or [], tenbit)
        return max(vis, key=_file_rank).get("file_id") if vis else None
    except Exception:
        return None


def build_post_buttons(fmt, movie_doc=None, tenbit=False):
    """Returns an InlineKeyboardMarkup: the admin-configured button (see /setbutton in plugins/post_format.py)
    and, as the LAST row (just above the sticker the bulk / ccopy jobs send after a card), the
    'View Audio, Subs & Story' button when movie_doc is given. None when there is nothing to show."""
    rows = []
    text = (fmt or {}).get("button_text") or ""
    url = (fmt or {}).get("button_url") or ""
    if text and url:
        rows.append([InlineKeyboardButton(text, url=url)])
    if movie_doc is not None:
        btn = extract_button(card_extract_file_id(movie_doc, tenbit))
        if btn:
            rows.append([btn])
    return InlineKeyboardMarkup(rows) if rows else None


def build_post_caption(movie_doc, base_name, fmt=None, tenbit=False):
    """Post caption. With fmt["show_names"] (default ON) a movie post's heading shows the full pretty file
    name of its first (lowest quality) file - "The Raven (2012) 480p BluRay x264 [Dual Audio] ... .mkv" -
    instead of just "The Raven 2012". The download links below stay as they were."""
    fmt = fmt or DEFAULT_POST_FORMAT
    return _build_post_caption(movie_doc, base_name, fmt, tenbit, names=bool(fmt.get("show_names", True)))


_PRETTY_HAS_YEAR = re.compile(r"\((?:19|20)\d{2}\)")


def _heading_name(f, names):
    """Full pretty file name for the heading, or '' (-> old "Title Year" heading) when it cannot be built."""
    if not names or not f or not f.get("filename"):
        return ""
    try:
        n = display_name(f["filename"])
    except Exception:
        return ""
    # no resolution (480p / 720p / 1080p / 4K ...) in the heading - the qualities are listed below the box
    n = _strip_res(n)
    # brand tag before the extension (the heading emoji is the post's title emoji)
    return brand_name(n, emoji=False) if n and _PRETTY_HAS_YEAR.search(n) else ""


def _build_post_caption(movie_doc, base_name, fmt=None, tenbit=False, names=False):
    """Builds the final auto-post caption. `fmt` (see get_post_format / the
    /postsettings admin command in plugins/post_format.py) controls bold,
    watermark, link text, divider and layout without touching this code."""
    fmt = fmt or DEFAULT_POST_FORMAT
    divider = fmt.get("divider", DEFAULT_POST_FORMAT["divider"])
    link_text = fmt.get("link_text", DEFAULT_POST_FORMAT["link_text"])
    title_emoji = fmt.get("title_emoji", DEFAULT_POST_FORMAT["title_emoji"])
    watermark = fmt.get("watermark", DEFAULT_POST_FORMAT["watermark"])
    layout = fmt.get("layout", DEFAULT_POST_FORMAT["layout"])
    bold = fmt.get("bold", DEFAULT_POST_FORMAT["bold"])
    header_box = fmt.get("header_box", DEFAULT_POST_FORMAT["header_box"])

    files = _visible_files(movie_doc.get("files", []), tenbit)

    all_languages, all_ott = set(), set()
    seasons_present = set()
    for f in files:
        if f.get("language") and f["language"] != "N/A":
            all_languages.update(x.strip() for x in f["language"].split(",") if x.strip())
        if f.get("ott_platform") and f["ott_platform"] != "N/A":
            all_ott.update(x.strip() for x in f["ott_platform"].split("|") if x.strip())
        if f.get("tag") == "#SERIES" and f.get("season"):
            seasons_present.add(f["season"])

    is_series = bool(seasons_present) or any(f.get("tag") == "#SERIES" for f in files)
    genres = movie_doc.get("genres", "N/A")
    language_str = ", ".join(sorted(all_languages)) if all_languages else "N/A"
    ott_str = ", ".join(sorted(all_ott)) if all_ott else "N/A"

    year_val = str(movie_doc.get("year") or "").strip()
    title = str(movie_doc.get("title") or base_name).strip()
    # Strip a trailing 4-digit year token from the title (not just an exact
    # match of year_val) - a wrong/extra year picked up from the filename
    # (e.g. "Bakaiti 2026" when TMDB's real year is 2025) used to survive
    # this check and print twice ("Bakaiti 2026 2025"). Now we always drop
    # whatever year is baked into the title and re-add the canonical one below.
    # Guard: only strip if something is left afterwards, so a movie whose
    # actual title IS a year (e.g. "1917", "2012", "1984") is never emptied out.
    _stripped_title = re.sub(r"\s*\(?\b(19|20)\d{2}\b\)?\s*$", "", title).strip()
    if _stripped_title:
        title = _stripped_title

    _present = sorted({s for s in (_quality_slot(f.get("quality"), tenbit) for f in files) if s}, key=_SLOT_INDEX.get)
    quality_str = ", ".join(_slot_label(s) for s in _present) if _present else "N/A"

    # text that comes from file names / TMDB goes into an HTML caption: escape < > & so a title like
    # "A<B" or "Tom & Jerry <x>" can not break the whole post (ENTITY parse error)
    _safe = lambda v: _html.escape(str(v), quote=False)   # noqa: E731
    info_lines = [
        f"🔊 ᴀᴜᴅɪᴏ  : {_safe(language_str)}",
        f"🎭 ɢᴇɴʀᴇs : {_safe(genres)}",
        f"🍿 ᴏᴛᴛ : {_safe(ott_str)}",
        f"🚀 ǫᴜᴀʟɪᴛʏ : {quality_str}",
    ]
    if header_box:
        info_block = "<blockquote>" + "\n".join(info_lines) + "</blockquote>"
    else:
        info_block = "\n".join(info_lines)

    lines = []

    if is_series:
        header_parts = [title_emoji, _safe(title)]
        if year_val:
            header_parts.append(year_val)
        if len(seasons_present) == 1:
            header_parts.append(f"[Season {_season_num_label(next(iter(seasons_present)))}]")
        elif len(seasons_present) > 1:
            try:
                nums = sorted(int(x) for x in seasons_present)
                if nums[-1] - nums[0] + 1 == len(nums):
                    header_parts.append(f"[Season {nums[0]:02d}-{nums[-1]:02d}]")
                else:
                    header_parts.append(f"[{len(nums)} Seasons]")
            except (TypeError, ValueError):
                pass
        header_title = " ".join(header_parts)
        if any(_is_complete_name(f.get("filename")) for f in files if f.get("tag") == "#SERIES"):
            header_title += " ◈ #Combined"

        lines += [header_title, divider, info_block, divider, ""]

        # ALL seasons in this one card: one link per season + quality (see _series_body_lines)
        series_files = [f for f in files if f.get("tag") == "#SERIES" and f.get("file_id")]
        budget = 3800 - _plain_len("\n".join(lines)) - 200     # head already built; room for watermark / preview char
        lines += _series_body_lines(series_files, tenbit, link_text, max(budget, 600))

    else:
        movie_files = [f for f in files if f.get("tag") != "#SERIES" and f.get("file_id")]
        picked = _pick_per_slot(movie_files, tenbit=tenbit)   # one file per allowed quality, no duplicates
        entries = [(_slot_label(slot), f) for (slot, _), f in sorted(picked.items(), key=lambda kv: _SLOT_INDEX[kv[0][0]])]
        if not entries:   # nothing in the six slots -> do not publish an empty post, list what exists
            movie_files.sort(key=lambda f: _quality_sort_key(f.get("quality") or ""))
            for f in movie_files:
                _, rt = _split_quality(f.get("quality") or "N/A")
                entries.append((" ".join(rt) if rt else "Unknown", f))
        full_name = _heading_name(entries[0][1], names) if entries else ""
        header_title = f"{title_emoji} {_safe(full_name) if full_name else _safe(f'{title} {year_val}')}".strip()
        lines += [header_title, divider, info_block, divider, ""]
        for qlabel, f in entries:
            link = _file_link(f["file_id"])
            size_str = f" ({get_size(f['file_size'])})" if f.get("file_size") else ""
            if layout == "compact":
                lines.append(f"{qlabel} : <a href='{link}'>{link_text}</a>{size_str}")
            else:
                lines.append(f"✧  {qlabel} : ")
                lines.append(f"➠ <a href='{link}'>{link_text}</a>{size_str}")
                lines.append("")

    if watermark:
        lines += ["╔═════════════════════╗",
                  f"    𝑷𝒐𝒘𝒆𝒓𝒆𝒅 𝒃𝒚 ➥ {_sans_bold(str(watermark))}      ",
                  "╚═════════════════════╝"]

    caption = "\n".join(lines).strip()
    return f"<b>{caption}</b>"   # the whole card is always bold
