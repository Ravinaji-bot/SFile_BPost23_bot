import re
import asyncio
import aiohttp
import warnings
import logging
from io import BytesIO
from datetime import datetime
from difflib import SequenceMatcher
from PIL import Image, ImageFilter, ImageEnhance, ImageDraw, ImageFont
import os
from info import DREAMXBOTZ_IMAGE_FETCH, TMDB_API_KEY, MAX_LIST_ELM, strip_bad_prefixes

logger = logging.getLogger(__name__)

LONG_IMDB_DESCRIPTION = False

Image.MAX_IMAGE_PIXELS = None
warnings.simplefilter("ignore", Image.DecompressionBombWarning)

#TMDB API ADDED BY @Bharath_boy

# --- TMDB Configuration ---
TMDB_BEARER_TOKEN = os.environ.get('TMDB_BEARER_TOKEN', '')  # optional: TMDB v4 read token (or just set TMDB_API_KEY)
TMDB_BASE_URL = 'https://api.themoviedb.org/3'
TMDB_IMAGE_BASE_URL = 'https://image.tmdb.org/t/p/original'
MIN_RUNTIME = 40

_session: aiohttp.ClientSession | None = None

# --- Poster watermark settings (edit here to change look/position) ---
WATERMARK_TEXT = "@DragonFireWorld"
WATERMARK_COLOR = (255, 255, 255)      # white text
WATERMARK_STROKE_COLOR = (0, 0, 0)     # black border
WATERMARK_Y_RATIO = 0.10               # 0.10 = 10% from the top of the image
WATERMARK_SIZE_RATIO = 0.07           # font height as a share of image height
WATERMARK_FONT_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "fonts", "Poppins-Bold.ttf"
)


def add_watermark(img: "Image.Image") -> "Image.Image":
    """Writes WATERMARK_TEXT horizontally centred, at WATERMARK_Y_RATIO of the
    image height, in white with a black border. Never raises - if anything
    fails the original image is returned untouched."""
    if not WATERMARK_TEXT:
        return img
    try:
        w, h = img.size
        font_size = max(12, int(h * WATERMARK_SIZE_RATIO))
        try:
            font = ImageFont.truetype(WATERMARK_FONT_PATH, font_size)
        except Exception:
            font = ImageFont.load_default(size=font_size)
        stroke = max(2, font_size // 8)
        draw = ImageDraw.Draw(img)
        draw.text(
            (w / 2, h * WATERMARK_Y_RATIO),
            WATERMARK_TEXT,
            font=font,
            fill=WATERMARK_COLOR,
            stroke_width=stroke,
            stroke_fill=WATERMARK_STROKE_COLOR,
            anchor="mm",
        )
    except Exception as e:
        logger.error(f"Watermark failed: {e}")
    return img


async def get_session():
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=15)
        )
    return _session

_IMG_SEM = None


def _img_sem():
    """Only ONE poster is built at a time: each needs 10-60 MB, three at once can kill a 512 MB bot."""
    global _IMG_SEM
    if _IMG_SEM is None:
        _IMG_SEM = asyncio.Semaphore(1)
    return _IMG_SEM


def _build_jpeg(source, size, quality):
    """CPU-heavy part (decode, resize, blur, watermark, encode). Runs in a worker
    thread so the bot / web server are not frozen while a 2560x1440 poster is built."""
    img = Image.open(source).convert("RGB")
    canvas = None
    try:
        canvas = add_watermark(_to_landscape_canvas(img, size))
        out = BytesIO()
        canvas.save(out, format="JPEG", quality=quality)
        out.seek(0)
        return out
    finally:
        for im in (canvas, img):
            try:
                im.close()
            except Exception:
                pass


async def fetch_image(url, size=(2560, 1440)):
    if not DREAMXBOTZ_IMAGE_FETCH:
        logger.info("Image fetching is disabled.")
        return url

    try:
        session = await get_session()

        async with session.get(url) as response:
            if response.status != 200:
                logger.error(f"Failed to fetch image: {response.status} for {url}")
                return None

            data = await response.read()
            async with _img_sem():
                return await asyncio.to_thread(_build_jpeg, BytesIO(data), size, 92)

    except aiohttp.ClientError as e:
        logger.error(f"HTTP request error in fetch_image: {e}")
    except IOError as e:
        logger.error(f"I/O error in fetch_image: {e}")
    except Exception as e:
        logger.error(f"Unexpected error in fetch_image: {e}")

    return None


def _to_landscape_canvas(img: "Image.Image", size=(2560, 1440)) -> "Image.Image":
    """Always returns a landscape image of exactly `size`, without ever
    stretching the source out of shape.

    - A source that's already landscape-ish (e.g. a TMDB backdrop, or a
      16:9 video thumbnail) is simply cover-cropped/resized to fill `size`.
    - A source that's portrait (a normal movie poster, or a portrait video
      thumbnail) is placed on top of a blurred, darkened, cover-scaled copy
      of itself that fills the rest of the landscape canvas - this is the
      same "poster on a blurred backdrop" look used by most streaming apps,
      so nothing ever looks horizontally squashed.
    """
    target_w, target_h = size
    target_ratio = target_w / target_h
    img_ratio = img.width / img.height

    if img_ratio >= target_ratio * 0.9:
        # Landscape-ish already -> cover-crop to the exact target size.
        scale = max(target_w / img.width, target_h / img.height)
        resized = img.resize(
            (max(1, round(img.width * scale)), max(1, round(img.height * scale))),
            Image.LANCZOS,
        )
        left = (resized.width - target_w) // 2
        top = (resized.height - target_h) // 2
        return resized.crop((left, top, left + target_w, top + target_h))

    # Portrait source -> letterbox it on a blurred version of itself.
    bg_scale = max(target_w / img.width, target_h / img.height)
    bg = img.resize(
        (max(1, round(img.width * bg_scale)), max(1, round(img.height * bg_scale))),
        Image.LANCZOS,
    )
    bg = bg.filter(ImageFilter.GaussianBlur(40))
    bleft = (bg.width - target_w) // 2
    btop = (bg.height - target_h) // 2
    bg = bg.crop((bleft, btop, bleft + target_w, btop + target_h))
    bg = ImageEnhance.Brightness(bg).enhance(0.5)

    fg_h = target_h
    fg_w = round(img.width * (fg_h / img.height))
    if fg_w > target_w:
        fg_w = target_w
        fg_h = round(img.height * (fg_w / img.width))
    fg = img.resize((max(1, fg_w), max(1, fg_h)), Image.LANCZOS)
    fx = (target_w - fg.width) // 2
    fy = (target_h - fg.height) // 2
    bg.paste(fg, (fx, fy))
    return bg


async def build_poster_from_telegram_thumb(bot, file_id, size=(2560, 1440)):
    """Last-resort poster: used only when TMDB/IMDb have no poster or backdrop
    at all for a title. Downloads the thumbnail Telegram already generated for
    the uploaded video file (or one the uploader manually attached) and turns
    it into a proper landscape image, instead of posting with no image at all.
    """
    if not file_id:
        return None
    try:
        buf = await bot.download_media(file_id, in_memory=True)
        if not buf:
            return None
        buf.seek(0)
        return await asyncio.to_thread(_build_jpeg, buf, size, 90)
    except Exception as e:
        logger.error(f"Failed to build fallback poster from video thumbnail: {e}")
        return None


async def close_session():
    if _session and not _session.closed:
        await _session.close()

def list_to_str(lst):
    if lst:
        return ", ".join(map(str, lst))
    return ""


def _list_to_str_tmdb(data_list, limit=10, key=None):
    """Helper for formatting TMDB response lists to comma-separated strings."""
    if not data_list or not isinstance(data_list, list):
        return None
    items = data_list[:limit]
    if key:
        return ", ".join(str(item.get(key, '')) for item in items if item)
    return ", ".join(str(item) for item in items if item)


_LANG_MAP = {
    'hindi': 'hi', 'kannada': 'kn', 'tamil': 'ta', 'telugu': 'te',
    'malayalam': 'ml', 'bengali': 'bn', 'punjabi': 'pa', 'marathi': 'mr',
    'gujarati': 'gu', 'english': 'en', 'korean': 'ko', 'japanese': 'ja',
    'spanish': 'es', 'french': 'fr', 'russian': 'ru', 'thai': 'th',
}


def _language_codes(text):
    """File name / query se bhasha nikalta hai: '[Hindi - Kannada]' -> {'hi', 'kn'}"""
    t = (text or '').lower()
    return {code for name, code in _LANG_MAP.items()
            if re.search(r'\b' + name + r'\b', t)}


def _extract_title_and_year(query: str):
    """Extract title and optional year from a search query string.

    Looks for a 4-digit year (1900-2099) ANYWHERE in the string, not just at the very
    end - real filenames put the year in the middle, e.g. "Dark (2026) 1080p HDRip ORG...".
    Everything before that year is treated as the title. Falls back to the old
    end-of-string behavior if no such year is found anywhere.
    """
    query = strip_bad_prefixes(query)
    match = re.search(r'(19\d{2}|20\d{2})', query)
    if match:
        year = int(match.group(1))
        title = query[:match.start()].strip(" -_.([{")
        if title:
            return title, year

    match = re.search(r'^(.*?)(?:\s+(\d{4}))?$', query.strip())
    if match:
        title, year_str = match.groups()
        year = int(year_str) if year_str and year_str.isdigit() else None
        return title.strip(), year
    return query.strip(), None


async def _tmdb_get(path, params=None, api_key=None):
    """Async GET request to TMDB API using aiohttp."""
    url = f"{TMDB_BASE_URL}/{path.lstrip('/')}"
    _params = params.copy() if params else {}
    _headers = {}

    if api_key:
        _params['api_key'] = api_key
    elif TMDB_BEARER_TOKEN:
        _headers = {
            'Authorization': f'Bearer {TMDB_BEARER_TOKEN}',
            'Content-Type': 'application/json;charset=utf-8'
        }

    session = await get_session()
    async with session.get(url, params=_params, headers=_headers) as resp:
        resp.raise_for_status()
        return await resp.json()


async def _fetch_media_details(media_type: str, media_id: int, api_key=None):
    """Fetch full details for a movie or TV show from TMDB."""
    params = {
        'append_to_response': 'credits,external_ids,alternative_titles,release_dates,images',
        # without this TMDB only returns English / no-language images, so most Indian films
        # looked like they had no backdrop and the portrait poster was used
        'include_image_language': 'en,hi,ta,te,ml,kn,bn,mr,pa,gu,ur,null',
    }
    return await _tmdb_get(f"{media_type}/{media_id}", params=params, api_key=api_key)


async def _exact_year_movie_search(title, year, api_key=None, lang_codes=None):
    """Keeps searching TMDB (several strategies, several pages each) until it finds a MOVIE
    whose title matches AND whose release year is EXACTLY the filename year.
    lang_codes (e.g. {'hi','kn'}) = languages found in the filename; a film whose original
    language is in this set is preferred over a same-name, same-year film of another language.
    Returns the TMDB movie id, or None when nothing exact exists (caller then falls back
    to the old fuzzy +-1 year logic).
    """
    words = title.split()
    variants = [title]
    if len(words) > 2:
        variants.append(" ".join(words[:-1]))      # drop the last word
    variants = list(dict.fromkeys(v for v in variants if v))
    # (extra TMDB params, max pages) - tried in this order, stops at the first exact hit
    strategies = [({'year': year}, 5), ({'primary_release_year': year}, 3), ({}, 10)]
    seen = set()
    fallback_id = None   # same name + year, but language did not match

    def _ratio(a, b):
        if not a or not b:
            return 0
        return SequenceMatcher(None, a.lower(), b.lower()).ratio()

    for q in variants:
        for extra, max_pages in strategies:
            page = 1
            while page <= max_pages:
                params = {'query': q, 'language': 'en-US', 'page': page, 'include_adult': 'false'}
                params.update(extra)
                try:
                    data = await _tmdb_get('search/movie', params=params, api_key=api_key)
                except Exception:
                    break
                matches = []
                for r in data.get('results') or []:
                    if r.get('id') in seen:
                        continue
                    if (r.get('release_date') or '')[:4] != str(year):
                        continue
                    ratio = max(_ratio(title, r.get('title')), _ratio(title, r.get('original_title')))
                    if ratio < 0.85:
                        continue
                    matches.append((ratio, r.get('popularity', 0), r))
                matches.sort(key=lambda m: (m[0], m[1]), reverse=True)
                for _ratio_val, _pop, r in matches:
                    seen.add(r.get('id'))
                    try:
                        det = await _tmdb_get(f"movie/{r['id']}", api_key=api_key)
                    except Exception:
                        continue
                    runtime = det.get('runtime')
                    if det.get('video') or (runtime and runtime < MIN_RUNTIME):
                        continue
                    # language check: filename says Hindi-Kannada -> skip an 'en' film
                    orig_lang = r.get('original_language') or det.get('original_language')
                    if lang_codes and orig_lang not in lang_codes:
                        if fallback_id is None:
                            fallback_id = r['id']
                        continue
                    return r['id']
                if page >= (data.get('total_pages') or 1):
                    break
                page += 1
            # one strategy finished without a language match -> use the first name+year hit
            if fallback_id:
                return fallback_id
    return fallback_id


async def _exact_year_tv_search(title, year, season=None, api_key=None, lang_codes=None):
    """Web-series twin of _exact_year_movie_search. Keeps searching TMDB until it finds a TV
    show whose title matches AND whose year fits the filename year, either as the show's
    first-air year or (when the season number is known) as THAT SEASON's air year - a file
    like "Panchayat 2025 S04" carries the season's year, not the show's.
    lang_codes = languages found in the filename; same-language shows are preferred.
    Returns the TMDB tv id, or None (caller then uses the old fuzzy logic). Bounded work.
    """
    def _ratio(a, b):
        if not a or not b:
            return 0
        return SequenceMatcher(None, a.lower(), b.lower()).ratio()

    def _good(r):
        return max(_ratio(title, r.get('name')), _ratio(title, r.get('original_name'))) >= 0.85

    def _lang_ok(r):
        # language unknown -> everything is fine
        return (not lang_codes) or (r.get('original_language') in lang_codes)

    seen, pool = set(), []
    fallback_id = None   # name + year match, language did not match

    # 1) year-filtered search: first_air_date_year == filename year (exact first-air year)
    page = 1
    while page <= 5:
        try:
            data = await _tmdb_get('search/tv', params={'query': title, 'language': 'en-US', 'page': page, 'include_adult': 'false', 'first_air_date_year': year}, api_key=api_key)
        except Exception:
            break
        hits = [r for r in (data.get('results') or []) if _good(r) and (r.get('first_air_date') or '')[:4] == str(year)]
        hits.sort(key=lambda r: r.get('popularity', 0), reverse=True)
        for r in hits:
            if _lang_ok(r):
                return r['id']
            if fallback_id is None:
                fallback_id = r['id']
        if page >= (data.get('total_pages') or 1):
            break
        page += 1
    if fallback_id:
        return fallback_id

    # 2) plain multi-page search; collect every strong title match
    page = 1
    while page <= 8:
        try:
            data = await _tmdb_get('search/tv', params={'query': title, 'language': 'en-US', 'page': page, 'include_adult': 'false'}, api_key=api_key)
        except Exception:
            break
        for r in data.get('results') or []:
            if r.get('id') in seen or not _good(r):
                continue
            seen.add(r.get('id'))
            if (r.get('first_air_date') or '')[:4] == str(year):
                if _lang_ok(r):
                    return r['id']
                if fallback_id is None:
                    fallback_id = r['id']
                continue
            pool.append(r)
        if page >= (data.get('total_pages') or 1):
            break
        page += 1
    if fallback_id:
        return fallback_id

    # 3) the year may belong to the SEASON (not the show): same-language shows first
    if season:
        pool.sort(key=lambda r: (_lang_ok(r), r.get('popularity', 0)), reverse=True)
        for r in pool[:8]:
            try:
                sd = await _tmdb_get(f"tv/{r['id']}/season/{season}", api_key=api_key)
            except Exception:
                continue
            if (sd or {}).get('air_date', '')[:4] == str(year):
                return r['id']
    return None


async def _search_media_id(query: str, api_key=None, file: str = None, is_series: bool | None = None, season: int | None = None):
    """Search TMDB for the best matching movie/TV show and return (media_type, media_id).

    is_series:
      - True  -> caller has confirmed (e.g. found "S08E04" or a bare "S01" in
                 the filename) this is definitely a TV series: only 'tv'
                 results are considered.
      - False -> caller has confirmed there is NO season/episode marker at
                 all in the filename, so this is definitely a movie: only
                 'movie' results are considered.
      - None  -> caller doesn't know either way (e.g. a plain title typed
                 into a generic search command with no filename context);
                 falls back to guessing from a regex on the query/file, same
                 as before, WITHOUT the strict movie-only filter below (so a
                 TV show searched by bare name still matches correctly).
    """
    title, year = _extract_title_and_year(query)

    # Caller explicitly told us the type -> trust it completely and filter
    # strictly. Otherwise fall back to the old regex-based guess (kept lenient
    # on purpose - it's used by generic search paths that don't have a real
    # filename to check).
    explicit = is_series is not None
    if is_series is None:
        is_series = bool(re.search(r'[Ss]\d{1,2}\s?[Ee]\d{1,3}|\bSeason\s?\d{1,2}\b|\bS\d{1,2}\b', file or query, re.IGNORECASE))

    _langs = _language_codes(file or query)

    multi_results = []
    words = title.split()
    
    # Movies with a year in the filename: first keep searching (year-filtered, then
    # multi-page) until a title + EXACT-year movie is found. Only if none exists on TMDB
    # do we fall through to the older fuzzy matching below.
    if year and not is_series:
        _exact_id = await _exact_year_movie_search(title, year, api_key=api_key, lang_codes=_langs)
        if _exact_id:
            return 'movie', _exact_id
    if year and is_series:
        _tv_id = await _exact_year_tv_search(title, year, season=season, api_key=api_key, lang_codes=_langs)
        if _tv_id:
            return 'tv', _tv_id

    # Generate up to 3 fallback queries to minimize API rate limit usage
    queries_to_try = [title]
    if len(words) > 2:
        queries_to_try.append(" ".join(words[:-1]))  # Drop the last word
        queries_to_try.append(words[0])              # Keep just the first word
    elif len(words) == 2:
        queries_to_try.append(words[0])
        
    # Remove any duplicates but preserve order, capping at 3 attempts
    queries_to_try = list(dict.fromkeys(queries_to_try))[:3]
    
    for target_query in queries_to_try:
        if not target_query:
            continue
        params = {'query': target_query, 'language': 'en-US', 'page': 1, 'include_adult': 'false'}
        result = await _tmdb_get('search/multi', params=params, api_key=api_key)
        multi_results = result.get('results', [])
        if multi_results:
            break

    # Year-aware extra search: a common title like "Monster" has dozens of TMDB hits and
    # page 1 of search/multi (no year filter) can miss the film whose year matches the
    # filename, so a same-name film from a neighbouring year (2023) won instead of 2022.
    if year and not is_series:
        try:
            _mv = await _tmdb_get('search/movie', params={'query': title, 'language': 'en-US', 'page': 1, 'include_adult': 'false', 'year': year}, api_key=api_key)
            _seen = {(x.get('media_type'), x.get('id')) for x in multi_results}
            for _x in (_mv or {}).get('results', []):
                _x['media_type'] = 'movie'
                if ('movie', _x.get('id')) not in _seen:
                    multi_results.append(_x)
        except Exception:
            pass

    def get_ratio(s1, s2):
        if not s1 or not s2:
            return 0
        return SequenceMatcher(None, s1.lower(), s2.lower()).ratio()

    scored_results = []
    for r in multi_results:
        # Score the string matched against the ORIGINAL title, not the shortened target_query
        name = r.get('title') or r.get('name')
        ratio = get_ratio(name, title)
        if ratio >= 0.5:   # Lowered from 0.6 to 0.5 to allow for dropped/modified words
            scored_results.append((r, ratio))
        elif year and name and len(title) >= 3 and name.lower().startswith(title.lower()):
            # "Monster" 2026 -> "Monster: The Lizzie Borden Story" (2026): the filename
            # only has the short title, but the year matches exactly.
            r_date = r.get('release_date') or r.get('first_air_date') or ''
            if r_date[:4] == str(year):
                scored_results.append((r, 0.5))
    if not scored_results:
        scored_results = [(r, get_ratio(r.get('title') or r.get('name'), title)) for r in multi_results[:10]]

    today = datetime.utcnow().date()
    candidates_past, candidates_upcoming = [], []
    for r, ratio in scored_results:
        mtype = r.get('media_type')
        if is_series and mtype != 'tv':
            continue
        # Symmetric case: caller explicitly confirmed there's no season/episode
        # marker anywhere -> definitely a movie, so don't let a same-named TV
        # show sneak in through the year-proximity check below.
        if explicit and not is_series and mtype != 'movie':
            continue
        rd_str = r.get('release_date') or r.get('first_air_date')
        if not (rd_str and mtype in ['movie', 'tv']):
            continue
        try:
            rd_date = datetime.strptime(rd_str, '%Y-%m-%d').date()
        except ValueError:
            continue
        if year and not is_series:
            if abs(rd_date.year - year) > 1:
                continue
        if mtype == 'movie':
            try:
                details = await _fetch_media_details(mtype, r['id'], api_key=api_key)
                runtime = details.get('runtime')
                is_video = details.get('video', False)

                if is_video or (runtime and runtime < MIN_RUNTIME):
                    continue
            except Exception:
                continue
        # Whether THIS candidate's own year exactly matches the year the filename
        # told us (e.g. "War 2019" -> only a 2019 "War" should win) - this is
        # ranked above raw popularity/recency, since name+year together is what
        # actually identifies the correct movie when multiple share a title.
        year_exact = (year is not None and rd_date.year == year)
        # How far this candidate's year is from the filename year: when two shows share
        # the exact same title (e.g. "Monster" 2004 anime vs a 2022+ series), the one
        # whose year is closest to the filename year should win.
        year_gap = abs(rd_date.year - year) if year else 0
        _lang_ok = (not _langs) or (r.get('original_language') in _langs)
        candidate = {'type': mtype, 'id': r['id'], 'date': rd_date, 'score': r.get('popularity', 0), 'ratio': ratio, 'year_exact': year_exact, 'year_gap': year_gap, 'lang_ok': _lang_ok}
        (candidates_upcoming if rd_date > today else candidates_past).append(candidate)
        
    # Series: the filename year (e.g. "Monster (2004) S01" vs "Monster (2026) S01") decides
    # WHICH show it is. If any candidate is within 1 year of it, drop the far-off ones.
    # If none is close (an old show with a new season), keep all and let the ranking
    # below pick the nearest year.
    if is_series and year:
        if any(c['year_gap'] <= 1 for c in candidates_past + candidates_upcoming):
            candidates_past = [c for c in candidates_past if c['year_gap'] <= 1]
            candidates_upcoming = [c for c in candidates_upcoming if c['year_gap'] <= 1]
            
    # Sort priority: title-match strength first, then an EXACT year match
    # (not just "closer date"), then popularity. Sorting by raw date here used
    # to mean the more recent of two similarly-titled results could win even
    # when the OTHER one was the exact year from the filename - fixed now.
    candidates_past.sort(key=lambda x: (x['ratio'], x['year_exact'], x['lang_ok'], -x['year_gap'], x['score']), reverse=True)
    candidates_upcoming.sort(key=lambda x: (x['ratio'], x['year_exact'], x['lang_ok'], -x['year_gap'], x['score']), reverse=True)
    final = candidates_past or candidates_upcoming
    if not final:
        return None, None
    top = final[0]
    return top['type'], top['id']


def _process_images(images_data):
    """Organize poster and backdrop images by language."""
    posters_by_lang, backdrops_by_lang = {}, {}
    for img in images_data.get('posters', []):
        lang = img.get('iso_639_1') or 'no_lang'
        posters_by_lang.setdefault(lang, []).append(f"{TMDB_IMAGE_BASE_URL}{img['file_path']}")
    for img in images_data.get('backdrops', []):
        lang = img.get('iso_639_1') or 'no_lang'
        backdrops_by_lang.setdefault(lang, []).append(f"{TMDB_IMAGE_BASE_URL}{img['file_path']}")
    posters_by_lang['all'] = [f"{TMDB_IMAGE_BASE_URL}{i['file_path']}" for i in images_data.get('posters', [])]
    backdrops_by_lang['all'] = [f"{TMDB_IMAGE_BASE_URL}{i['file_path']}" for i in images_data.get('backdrops', [])]
    languages = sorted(set(posters_by_lang) | set(backdrops_by_lang))
    return {'posters': posters_by_lang, 'backdrops': backdrops_by_lang, 'available_languages': languages}


async def _fetch_tmdb_data(query: str, api_key=None, file: str = None, season: int = None, is_series: bool | None = None):
    """
    Core TMDB lookup: search → fetch details → build response dict.
    This replaces the external tmdb.blazeposters.workers.dev API call.
    """
    media_type, media_id = await _search_media_id(query, api_key=api_key, file=file, is_series=is_series, season=season)
    if not media_id:
        return None

    details = await _fetch_media_details(media_type, media_id, api_key=api_key)
    crew = details.get('credits', {}).get('crew', [])

    certificates = None
    if media_type == 'movie' and 'release_dates' in details:
        us = [r for r in details['release_dates']['results'] if r['iso_3166_1'] == 'US']
        if us and us[0]['release_dates']:
            certificates = us[0]['release_dates'][0].get('certification')

    runtime_display = None
    if media_type == 'movie':
        runtime = details.get('runtime')
        runtime_display = f"{runtime} min" if runtime else None
    else:
        er = _list_to_str_tmdb(details.get('episode_run_time', []))
        runtime_display = f"{er} min" if er else None

    images_structured = _process_images(details.get('images', {}))
    images_structured['original_language'] = details.get('original_language')

    # For a TV show, TMDB's main poster is usually the LATEST season's artwork, not the
    # one for the season this particular file actually belongs to (e.g. a Season 1 (2025)
    # file could otherwise show Season 2 (2026)'s poster). When we know which season this
    # file is, fetch that season's own poster and use it instead of the show-level default.
    season_poster_url = None
    season_air_date = None
    if media_type == 'tv' and season:
        try:
            season_data = await _tmdb_get(f"tv/{media_id}/season/{season}", api_key=api_key)
            if season_data:
                if season_data.get('poster_path'):
                    season_poster_url = f"{TMDB_IMAGE_BASE_URL}{season_data['poster_path']}"
                # A show's first_air_date is always season 1's date, so a Season 2/3/...
                # post would otherwise show the wrong year. Use this season's own
                # air_date instead whenever TMDB has it.
                if season_data.get('air_date'):
                    season_air_date = season_data['air_date']
        except Exception as e:
            logger.info(f"Could not fetch season {season} specific poster/air_date for tv/{media_id}: {e}")

    output_data = {
        'query': query, 'media_type': media_type, 'media_id': media_id,
        'title': details.get('title') or details.get('name'),
        'localized_title': details.get('original_title') or details.get('original_name'),
        'aka': _list_to_str_tmdb(details.get('alternative_titles', {}).get('titles', []), key='title'),
        'kind': media_type,
        'year': (season_air_date or details.get('release_date') or details.get('first_air_date', ''))[:4],
        'release_date': season_air_date or details.get('release_date') or details.get('first_air_date'),
        'imdb_id': details.get('external_ids', {}).get('imdb_id'),
        'tmdb_id': details.get('id'),
        'rating': details.get('vote_average'),
        'votes': details.get('vote_count'),
        'runtime': runtime_display,
        'certificates': certificates,
        'genres': _list_to_str_tmdb(details.get('genres', []), key='name'),
        'languages': _list_to_str_tmdb(details.get('spoken_languages', []), key='english_name'),
        'countries': _list_to_str_tmdb(details.get('production_countries', []), key='name'),
        'director': _list_to_str_tmdb([p for p in crew if p.get('job') == 'Director'], key='name'),
        'writer': _list_to_str_tmdb([p for p in crew if p.get('job') in ['Screenplay', 'Writer', 'Story']], key='name'),
        'producer': _list_to_str_tmdb([p for p in crew if p.get('job') == 'Producer'], key='name'),
        'composer': _list_to_str_tmdb([p for p in crew if p.get('job') == 'Original Music Composer'], key='name'),
        'cinematographer': _list_to_str_tmdb([p for p in crew if p.get('job') == 'Director of Photography'], key='name'),
        'cast': _list_to_str_tmdb(details.get('credits', {}).get('cast', []), key='name', limit=15),
        'plot': details.get('overview'),
        'tagline': details.get('tagline'),
        'box_office': details.get('revenue') if details.get('revenue', 0) > 0 else "N/A",
        'distributors': _list_to_str_tmdb(details.get('production_companies', []), key='name'),
        'poster_url': season_poster_url or (f"{TMDB_IMAGE_BASE_URL}{details.get('poster_path')}" if details.get('poster_path') else None),
        'url': f"https://www.themoviedb.org/{media_type}/{details.get('id')}",
        'images': images_structured,
    }

    if media_type == 'tv':
        output_data.update({
            'seasons': details.get('number_of_seasons'),
            'episodes': details.get('number_of_episodes'),
        })

    return output_data

def _pick_imdb_candidates(movie_list, title, year_val, bulk=False):
    """Choose which IMDb search results may be used for a filename's title + year.

    Old behaviour: if nothing matched the year, the WHOLE unfiltered list was used, so a brand
    new film (e.g. "The Last Treatment 2026", not on IMDb yet) got the first search hit - some
    other / older title - with the wrong name, year and poster.
    Now: exact year -> year +-1 -> (TV only) same title, any year -> otherwise NOTHING, and the
    caller falls back to the file's own thumbnail.  bulk=True (search lists) keeps the old behaviour.
    """
    if not year_val:
        return movie_list
    try:
        y = int(year_val)
    except (TypeError, ValueError):
        return movie_list

    def _year(m):
        try:
            return int(m.year)
        except (TypeError, ValueError):
            return None

    exact = [m for m in movie_list if _year(m) == y]
    if exact:
        return exact
    near = [m for m in movie_list if _year(m) is not None and abs(_year(m) - y) <= 1]
    if near:
        return near
    if bulk:
        return movie_list
    tv_kinds = ("tv series", "tvseries", "tvminiseries")
    same_tv = [m for m in movie_list
               if str(getattr(m, "kind", "") or "").lower() in tv_kinds
               and SequenceMatcher(None, str(getattr(m, "title", "") or "").lower(), (title or "").lower()).ratio() >= 0.9]
    return same_tv


async def get_movie_details(query, bulk=False, id=False, file=None):
    if not id:
        from utils import listx_to_str, imdb
        query = (query.strip()).lower()
        title = query
        year_val = None
        
        year_list = re.findall(r'[1-2]\d{3}$', query, re.IGNORECASE)
        if year_list:
            year_val = year_list[0]
            title = (query.replace(year_val, "")).strip()
        elif file is not None:
            year_list = re.findall(r'[1-2]\d{3}', file, re.IGNORECASE)
            if year_list:
                year_val = year_list[0]
        
        search_result = await asyncio.to_thread(imdb.search_movie, title.lower())
        if not search_result or not search_result.titles:
            return None
        
        movie_list = search_result.titles[:MAX_LIST_ELM]
        
        filtered = _pick_imdb_candidates(movie_list, title, year_val, bulk)
        
            
        kind_filter = ['movie', 'tv series', 'tvSeries', 'tvMiniSeries', 'tvMovie']
        filtered_kind = [m for m in filtered if m.kind and m.kind in kind_filter]
        
        if not filtered_kind:
            filtered_kind = filtered
        
        if bulk:
            return filtered_kind[:MAX_LIST_ELM]
        if not filtered_kind:
            return None   
        movie_brief = filtered_kind[0]
        movieid_str = movie_brief.imdb_id 
    else:
        movieid_str = query

    movie = await asyncio.to_thread(imdb.get_movie, movieid_str)
    if not movie:
        return None

    if movie.release_date:
        date = movie.release_date
    elif movie.year:
        date = str(movie.year)
    else:
        date = "N/A"
        
    plot = movie.plot[0] if isinstance(movie.plot, list) else movie.plot or ""
    if len(plot) > 800:
        plot = plot[:800] + "..."
    imdb_id = movie.imdb_id
    if not imdb_id.startswith("tt"):
        imdb_id = f"tt{imdb_id}"
    return {
        'title': movie.title,
        'votes': movie.votes,
        "aka": listx_to_str(movie.title_akas),
        "seasons": (
            len(movie.info_series.display_seasons)
            if getattr(movie, "info_series", None)
            and getattr(movie.info_series, "display_seasons", None)
            else "N/A"
        ),
        "box_office": movie.worldwide_gross,
        'localized_title': movie.title_localized,
        'kind': movie.kind,
        "imdb_id": imdb_id,
        "cast": listx_to_str(movie.stars),
        "runtime": listx_to_str(movie.duration),
        "countries": listx_to_str(movie.countries),
        "certificates": listx_to_str(movie.certificates),
        "languages": listx_to_str(movie.languages),
        "director": listx_to_str(movie.directors),
        "writer": listx_to_str([p.name for p in movie.writers]),
        "producer": listx_to_str([p.name for p in movie.producers]),
        "composer": listx_to_str([p.name for p in movie.composers]),
        "cinematographer": listx_to_str([p.name for p in movie.cinematographers]),
        "music_team": listx_to_str([p.name for p in movie.music_team]),
        "distributors": listx_to_str([c.name for c in movie.distributors]),        
        'release_date': date,
        'year': movie.year,
        'genres': listx_to_str(movie.genres),
        'poster': movie.cover_url,
        'poster_url': movie.cover_url.split("._V1_")[0] + "._V1_SX1280.jpg" if movie.cover_url and "._V1_" in movie.cover_url else movie.cover_url,
        'plot': plot,
        'rating': str(movie.rating),
        "url": movie.url or f"https://www.imdb.com/title/{imdb_id}"
    }


def _imdb_query(q: str) -> str:
    """'Monster 2004 S01E01 720p' -> 'Monster 2004' (title + year at the END, which is
    the form get_movie_details() understands, so the year is used to pick the right show)."""
    title, year = _extract_title_and_year(q)
    return f"{title} {year}" if year else (title or q)


async def get_movie_detailsx(query, id=False, file=None, season=None, is_series=None):
    """
    Primary movie details fetcher using direct TMDB API calls.
    Falls back to IMDb-based get_movie_details() on failure.
    """
    q = strip_bad_prefixes(str(query).strip())
    try:
        data = await _fetch_tmdb_data(q, api_key=TMDB_API_KEY or None, file=file, season=season, is_series=is_series)
        if not data:
            logger.info(f"TMDB returned no results for '{q}' → switching to IMDb fallback")
            return await get_movie_details(_imdb_query(q))
    except Exception as e:
        logger.info(f"TMDB direct call failed → fallback IMDb: {e}")
        return await get_movie_details(_imdb_query(q))

    # Normalize fields
    details = {}
    details['title'] = data.get('title') or data.get('localized_title')
    details['year'] = (data.get('year', 0)) if data.get('year') else None
    details['release_date'] = data.get('release_date')
    details['rating'] = round(float(data.get('rating', 0)), 1) if data.get('rating') is not None else None
    details['votes'] = int(data.get('votes', 0))
    details['runtime'] = data.get('runtime')
    details['certificates'] = data.get('certificates')
    details['tmdb_url'] = data.get('url')
    
    for key in ('genres', 'languages', 'countries'):
        raw = data.get(key)
        details[key] = [s.strip() for s in raw.split(',')] if raw else []
    for role in ('director', 'writer', 'producer', 'composer', 'cinematographer', 'cast'):
        raw = data.get(role)
        details[role] = [s.strip() for s in raw.split(',')] if raw else []
        
    details['plot'] = data.get('plot')
    details['tagline'] = data.get('tagline')
    details['box_office'] = (data.get('box_office', 0)) if data.get('box_office') else None
    raw_dist = data.get('distributors')
    details['distributors'] = [d.strip() for d in raw_dist.split(',')] if raw_dist else []
    details['imdb_id'] = data.get('imdb_id')
    details['tmdb_id'] = data.get('tmdb_id')
    
    posters = data.get('images', {}).get('posters', {})
    original_language = data.get('images', {}).get('original_language')
    poster_url = data.get('poster_url')
    if not poster_url:
        for key in ('en', original_language, 'xx'):
            if key and posters.get(key):
                poster_url = posters[key][0]
                break
    details['poster_url'] = poster_url.replace("/original/", "/w1280/") if poster_url else None

    backdrops = data.get('images', {}).get('backdrops', {})
    original_language = data.get('images', {}).get('original_language')
    backdrop_url = None
    for key in ('en', original_language, 'xx', 'no_lang'):
        if key and backdrops.get(key):
            backdrop_url = backdrops[key][0]
            break
    if not backdrop_url and backdrops.get('all'):   # any backdrop in any language is better than a portrait poster
        backdrop_url = backdrops['all'][0]
    details['backdrop_url'] = backdrop_url.replace("/original/", "/w1280/") if backdrop_url else None

    return details
