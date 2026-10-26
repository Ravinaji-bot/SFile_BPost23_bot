"""
MASTER FILE of one movie / series season.

One movie has many qualities (480p ... 2160p). Usually ONE of those files has the full, clean details
(year, every audio language, subtitles, a clean caption) and the others are poor ones (no language, no year,
messy name). This module reads the files of one movie one by one, gives each a score and picks the best as the
MASTER. Its details are then used for every other quality of the same movie / season:

  * caption : year / audio languages / subtitles that a file has NOT got itself are taken from the master
              (what a file says itself always wins - nothing is overwritten, only gaps are filled);
  * poster  : the poster / cover is looked up with the master's name, so every quality shows the SAME poster
              (before, a file without a language in its name could pick another film's poster).

What is NEVER copied: resolution, size, source (WEB-DL / HDRip), codec - those belong to the file itself.

Switch it off without touching the code:  MASTER_FILL=0  in the environment.
"""
import logging
import os
import re
import time
import unicodedata
from collections import OrderedDict

logger = logging.getLogger(__name__)

ENABLED = os.environ.get("MASTER_FILL", "1").strip() != "0"

_GROUPS = OrderedDict()      # (title, season, year) -> (expires_at, master or None)
_GROUPS_MAX = 300
_TTL_OK = 30 * 60            # a chosen master is remembered 30 minutes
_TTL_FAIL = 60               # a failed lookup only 1 minute
_SEARCH_LIMIT = 100          # files read from the database for one movie

# cam / telesync prints: never a master, and they never get language / subtitles copied in (those would be lies)
_LOW_SRC = {"hdcam", "hdts", "cam", "telesync", "hdtc", "camrip", "predvd"}

_JUNK_RE = re.compile(r"@\w|https?://|t\.me/|www\.", re.I)


def _norm(text):
    return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", text or "").casefold())


def facts(name, caption=""):
    """The details of ONE file: dict(title, year, season, res, ott, src, codec, subs, langs, multi, name)
    or None when the name (and the first caption lines) cannot be read as a release name."""
    try:
        from plugins.pretty_name import _parse, effective_filename
        use = effective_filename(name or "", caption or "") or name or ""
        f = {}
        shown = _parse(use, True, caption or "", None, f)
        if not shown or "title" not in f:
            return None
        f["name"] = use
        return f
    except Exception as e:                                           # never break a delivery
        logger.debug("master.facts failed for %r: %s", name, e)
        return None


def group_key(f):
    return (_norm(f["title"]), f["season"] or "")


def same_group(a, b):
    """Same movie / same season. Two different years (a remake) are two different movies."""
    return group_key(a) == group_key(b) and not (a["year"] and b["year"] and a["year"] != b["year"])


def score(f, name="", caption=""):
    """How complete and clean the details of this file are (higher = better master)."""
    if (f.get("src") or "").lower() in _LOW_SRC:
        return -100                                                  # a cam print is never the master
    s = 10                                                           # the name could be read at all
    if f["year"]:
        s += 3
    s += min(len(f["langs"]), 4) * 3                                 # every audio language counts
    if f["multi"] and not f["langs"]:
        s += 1
    if f["subs"]:
        s += 2
    s += 1 if f["src"] else 0
    s += 1 if f["ott"] else 0
    s += 1 if f["codec"] else 0
    try:
        from plugins.pretty_name import _caption_lines
        s += min(len(_caption_lines(caption)), 3)                    # a caption with real lines
    except Exception:
        pass
    if _JUNK_RE.search(f"{name} {caption}"):                         # @channel / links = messy source
        s -= 2
    digits = re.sub(r"\D", "", f["res"] or "")
    return s + (int(digits) / 10000.0 if digits else 0)              # tie: the higher quality


def pick_master(items):
    """items = [(file_name, caption), ...] of ONE movie / season  ->  the best one as a dict
    (name, use_name, caption, facts, score, index) or None when no file can be read."""
    best = None
    for i, (name, cap) in enumerate(items or []):
        f = facts(name, cap)
        if not f:
            continue
        sc = score(f, name, cap)
        if best is None or sc > best["score"]:
            best = {"score": sc, "index": i, "name": name, "use_name": f["name"], "caption": cap, "facts": f}
    return best


def fill_of(master):
    """What the other files may take from the master."""
    f = master["facts"]
    return {"name": master["use_name"], "year": f["year"], "langs": list(f["langs"]), "subs": f["subs"],
            "title": f["title"]}


def master_name(items, default=None):
    """Sync helper for post cards / autopost: the file name to use for the poster lookup of a whole group."""
    if not ENABLED:
        return default
    try:
        m = pick_master(items)
        return (m["use_name"] if m else None) or default
    except Exception:
        return default


def group_fill(items):
    """Sync: what the files of ONE already-known group (autopost collection) may take from its master, or None."""
    if not ENABLED:
        return None
    try:
        m = pick_master(items)
        return fill_of(m) if m else None
    except Exception:
        return None


def fill_for_file(fill, name, caption=""):
    """The group's fill for ONE file - None for a cam print / a file whose name cannot be read."""
    if not fill:
        return None
    f = facts(name, caption)
    if not f or (f.get("src") or "").lower() in _LOW_SRC:
        return None
    if f["year"] and fill.get("year") and f["year"] != fill["year"]:
        return None
    return fill


async def _find_master(me, name, caption):
    from database.ia_filterdb import get_search_results
    files, _, _ = await get_search_results(None, me["title"], max_results=_SEARCH_LIMIT)
    items, years = [], set()
    for d in files or []:
        n = getattr(d, "file_name", None) or ""
        c = getattr(d, "caption", None) or ""
        f = facts(n, c)
        if f and same_group(me, f):
            items.append((n, c))
            if f["year"]:
                years.add(f["year"])
    if not me["year"] and len(years) > 1:
        return None                      # "Dune" without a year and two Dunes in the database: do not guess
    if not any(n == name for n, _ in items):
        items.append((name, caption))    # the file itself is always part of its group
    return pick_master(items)


async def master_fill_for(doc):
    """The details this file may take from the master of its movie / season, or None (nothing to fill,
    feature off, or anything went wrong). Cached per movie / season, so Send All reads the database once."""
    if not ENABLED:
        return None
    try:
        name = getattr(doc, "file_name", None) or ""
        cap = getattr(doc, "caption", None) or ""
        me = facts(name, cap)
        if not me or (me.get("src") or "").lower() in _LOW_SRC:
            return None
        key = group_key(me) + (me["year"],)
        now = time.monotonic()
        hit = _GROUPS.get(key)
        if hit and hit[0] > now:
            master = hit[1]
        else:
            try:
                master = await _find_master(me, name, cap)
                ttl = _TTL_OK
            except Exception as e:
                logger.warning("master lookup failed for %r: %s", me["title"], e)
                master, ttl = None, _TTL_FAIL
            _GROUPS[key] = (now + ttl, master)
            while len(_GROUPS) > _GROUPS_MAX:
                _GROUPS.popitem(last=False)
        if not master:
            return None
        mf = master["facts"]
        if me["year"] and mf["year"] and me["year"] != mf["year"]:
            return None
        return fill_of(master)
    except Exception as e:
        logger.warning("master_fill_for failed: %s", e)
        return None


def clear_master_cache():
    n = len(_GROUPS)
    _GROUPS.clear()
    return n
