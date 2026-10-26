"""
Pretty release names for every caption the bot builds - NO re-upload, NO database change.

A stored name such as
    Lust.Stories.2018.1080p.NF.WEB-DL.DDP5.1.x264-Telly.mkv
is shown as
    Lust Stories (2018) 1080p WEB-DL x264.mkv
and with language tags in the name (Tam-Tel-Hin / HIN-TAM / Hindi English ...)
    Lust Stories (2018) 1080p WEB-DL x264 [Dual Audio] [Hindi ORG - Tamil].mkv

The name is rebuilt every time a caption is made, so it also applies to every file that
is already saved in the database. Series names (S01, [E09-16], COMBINED, OTT, ESub/MSubs ...) are handled too. Names with nothing to anchor on
(no year, season or resolution) are left untouched.

Admin commands:
    /prettyname                 - status
    /prettyname on | off        - switch the feature
    /prettyname org on | off    - put "ORG" after the first language
    /prettyname caption on | off - drop the stored extra caption lines ({file_caption}) when the template has {file_name}
    /prettyname test <name>     - preview a name (extra lines after it = its caption)
    /prettycheck [n]            - sample n random indexed files and show which name each one gets
"""
import html
import logging
import re
import unicodedata

from pyrogram import Client, filters, enums
from pyrogram.types import Message

from info import ADMINS, strip_bad_prefixes, strip_bad_words, NAME_BRAND_EMOJI, NAME_BRAND_TAG, norm_res
from database.users_chats_db import db
from utils import temp, clean_filename

logger = logging.getLogger(__name__)

LANGS = {
    "hin": "Hindi", "hindi": "Hindi", "tam": "Tamil", "tamil": "Tamil", "tel": "Telugu", "telugu": "Telugu",
    "eng": "English", "english": "English", "jap": "Japanese", "jpn": "Japanese", "japanese": "Japanese",
    "kan": "Kannada", "kannada": "Kannada", "mal": "Malayalam", "malayalam": "Malayalam",
    "ben": "Bengali", "bengali": "Bengali", "mar": "Marathi", "marathi": "Marathi",
    "pun": "Punjabi", "punjabi": "Punjabi", "kor": "Korean", "korean": "Korean",
    "chinese": "Chinese", "mandarin": "Chinese", "thai": "Thai", "gujarati": "Gujarati", "urdu": "Urdu",
    "spanish": "Spanish", "french": "French", "german": "German", "russian": "Russian", "turkish": "Turkish",
    "arabic": "Arabic", "indonesian": "Indonesian", "malay": "Malay", "vietnamese": "Vietnamese",
    "italian": "Italian", "portuguese": "Portuguese", "tagalog": "Tagalog", "bhojpuri": "Bhojpuri",
}
SOURCES = {s.lower(): s for s in ("WEB-DL", "WEBRip", "BluRay", "BRRip", "BDRip", "HDRip", "HDTV", "DVDRip",
                                  "HDTC", "CAMRip", "PreDVD",
                                  "HDCAM", "HDTS", "CAM", "TeleSync")}   # cam / telesync prints stay visible in the name
OTTS = {"nf": "NF", "amzn": "AMZN", "dsnp": "DSNP", "zee5": "ZEE5", "iq": "iQ", "wetv": "WeTV", "viu": "VIU",
        "sonyliv": "SonyLIV", "sliv": "SonyLIV", "jc": "JC", "mx": "MX", "altb": "ALTBalaji", "hoichoi": "Hoichoi",
        "sunnxt": "SunNXT", "hulu": "Hulu", "hbo": "HBO", "atvp": "ATVP", "tvf": "TVF", "aha": "AHA",
        "voot": "Voot", "hotstar": "Hotstar", "eros": "Eros"}
EDITIONS = {"extended": "Extended", "unrated": "Unrated", "remastered": "Remastered", "imax": "IMAX",
            "uncut": "Uncut", "theatrical": "Theatrical", "3d": "3D", "repack": "REPACK", "proper": "PROPER"}
AUDIO_LABEL = {2: "[Dual Audio]", 3: "[Triple Audio]"}

# native-script language names that appear in captions such as "🔊 (CR) हिन्दी ~ AAC | English-日本語 ~ Opus"
_NATIVE = {"हिन्दी": "Hindi", "हिंदी": "Hindi", "日本語": "Japanese", "中文": "Chinese", "普通话": "Chinese",
           "한국어": "Korean", "தமிழ்": "Tamil", "తెలుగు": "Telugu", "മലയാളം": "Malayalam", "ಕನ್ನಡ": "Kannada",
           "বাংলা": "Bengali", "ਪੰਜਾਬੀ": "Punjabi", "मराठी": "Marathi", "ภาษาไทย": "Thai"}
_LANG_WORD = re.compile(r"(?<![A-Za-z])(" + "|".join(sorted({k for k in LANGS if len(k) > 3}, key=len, reverse=True)) + r")(?![A-Za-z])", re.I)
_NATIVE_RE = re.compile("|".join(map(re.escape, _NATIVE)))
_YEAR = re.compile(r"(?:19|20)\d{2}")
_RES = re.compile(r"(?:\d{3,4}p|4k)", re.I)
_SEASON = re.compile(r"S(\d{1,2})(?:E(\d{1,3})(?:-?E?(\d{1,3}))?)?", re.I)
_EP = re.compile(r"E(\d{1,3})(?:-E?(\d{1,3}))?", re.I)
_EP_T = re.compile(r"E(\d{1,3})T(\d{1,3})", re.I)                    # E01T17 = episodes 1 to 17
_EXT = re.compile(r"(?:\.|\s)(mkv|mp4|avi|webm|mov|m4v)$|\.ts$", re.I)
_SPLIT = re.compile(r"[\s\[\]\(\)\{\}\+,|~\u2013\u2014_.\u00b7]+")
_CONTRACT = {"s", "t", "re", "ll", "ve", "d", "m"}                    # "Devil s Plan" (stored without the ') -> "Devil's Plan"
_JUNK_TOK = re.compile(r"(?:merged|video|vid|file|document|doc|output|final|movie|clip|part\d*|\d+|mkv|mp4|avi|webm|mov|m4v)", re.I)
_SUBS_WORDS = r"\b(?:e-?subs?|m-?subs?|subtitles?|subs?|dubbed|org|dual|audio|multi)\b"


def _prepare(raw):
    # Telegram file names glue everything with "_": "Join_@xyz_Movie_2024" - make them words first so the
    # promo / bad-word cleaning below sees "Join", "@xyz" ... (the tokenizer treats "_" as a space anyway)
    text = strip_bad_prefixes((raw or "").replace("_", " "))
    text = re.sub(r"^\s*(?:join|tg|telegram)[\s.\-:|]*(?=@)", "", text, flags=re.I)   # "Join.@chan.Movie.2024" (dots glue it into one word)
    text = strip_bad_words(text) or text                              # join / tg / movie / boxoffice ... ("ORIGINAL" is kept: ORG tag)
    text = re.sub(r"\s+", " ", text).strip()                         # new lines inside a name
    text = re.sub(r"^\s*(?:\[[^\]]*\]|@\w+|www\.\S+?\.\w{2,4})[\s\-_.]*", "", text)   # leading [Site] / @chan / www.x.com
    m = _EXT.search(text)
    ext = ("." + (m.group(1) or "ts").lower()) if m else ""
    text = _EXT.sub("", text)
    text = text.replace("\u00d7", "x")                                # "x264" typed with a multiplication sign
    text = re.sub(r"WEB[ .\-]?DL", "WEB-DL", text, flags=re.I)
    text = re.sub(r"\bH[ .]?(26[45])\b", r"x\1", text, flags=re.I)
    text = re.sub(r"\b(S\d{1,2})(?=[A-Za-z])(?!E\d)", r"\1 ", text, flags=re.I)   # S01COMBiNED -> S01 COMBiNED
    toks = [t.strip("#") for t in _SPLIT.split(text) if t.strip("#")]
    return toks, ext


# ---------------------------------------------------------------- the file's original caption
def _caption_text(caption):
    return html.unescape(re.sub(r"<[^>]+>", " ", caption or ""))


def _caption_lines(caption):
    """Caption lines without html, bullets and emoji in front (empty lines dropped)."""
    out = []
    for line in _caption_text(caption).splitlines():
        line = re.sub(r"^[\W_]+", "", line).strip()
        if line:
            out.append(line)
    return out


def _langs_from_caption(caption):
    """Languages from the 🔊 / "Audio" line(s) or a plain language line ("‣ Hindi, Chinese | ESub") of the caption."""
    if not caption:
        return []
    found = []
    for line in unicodedata.normalize("NFKC", _caption_text(caption)).splitlines():
        hits = [(m.start(), LANGS[m.group(1).lower()]) for m in _LANG_WORD.finditer(line)]
        hits += [(m.start(), _NATIVE[m.group(0)]) for m in _NATIVE_RE.finditer(line)]
        if not hits:
            continue
        rest = _NATIVE_RE.sub("", _LANG_WORD.sub("", line))
        rest = re.sub(_SUBS_WORDS, "", rest, flags=re.I)
        only_langs = not re.search(r"[A-Za-z0-9]", rest)
        if "🔊" in line or re.search(r"\baudio", line, re.I) or only_langs:
            for _, name in sorted(hits):
                if name not in found:
                    found.append(name)
    return found


def _subs_from_caption(caption):
    t = _caption_text(caption)
    if re.search(r"\bm-?subs?\b", t, re.I):
        return "MSubs"
    if re.search(r"\be-?subs?\b|english\s+sub", t, re.I):
        return "ESubs"
    return ""


def _parse(raw, org=True, caption="", fill=None, _fields=None):
    """Clean release name built only from the parts worth keeping; None when the name has no year / season /
    resolution to anchor on. Credits, release groups, audio codecs, bitrates, symbols are dropped.
    fill    = details of the "master" file of the same movie / season (dreamxbotz.util.master). They only FILL
              what this file has not got (year, language, subtitles) - what the file itself says always wins.
    _fields = optional dict that receives the parsed details (used by master.py to compare the files)."""
    if not raw or not raw.strip():
        return None
    toks, ext = _prepare(raw)
    if len(toks) < 2:
        return None
    ri = next((i for i, t in enumerate(toks) if i > 0 and _RES.fullmatch(t)), len(toks))
    si = next((i for i, t in enumerate(toks) if i > 0 and _SEASON.fullmatch(t)), len(toks))
    years = [i for i, t in enumerate(toks) if 0 < i < min(ri, len(toks)) and _YEAR.fullmatch(t)]
    yi = years[-1] if years else None
    stop = min([x for x in (yi, si, ri) if x is not None] or [len(toks)])
    if stop >= len(toks) or stop == 0:
        return None
    tt = []
    for tok in toks[:stop]:
        if tok in _CONTRACT and tt and tt[-1].isalpha():
            tt[-1] = f"{tt[-1]}'{tok}"
        else:
            tt.append(tok)
    title = " ".join(tt).strip(" -")
    if title.islower():
        title = title.title()
    tail = []
    for t in toks[stop:]:                                             # x264-GROUP / Hin-Eng -> separate words
        if "-" in t and t.lower() not in SOURCES and not _EP.fullmatch(t) and not _SEASON.fullmatch(t):
            tail += [x for x in t.split("-") if x]
        else:
            tail.append(t)
    tlow = [t.lower() for t in tail]

    year = toks[yi] if yi is not None else ""
    season, eps, prev_ep = "", [], False
    for t in tail:
        m = _SEASON.fullmatch(t)
        if m and not season:
            season = f"S{int(m.group(1)):02d}"
            eps += [int(x) for x in (m.group(2), m.group(3)) if x]
            prev_ep = bool(m.group(2) and not m.group(3))
            continue
        m = _EP_T.fullmatch(t)
        if m:
            eps += [int(m.group(1)), int(m.group(2))]
            prev_ep = False
            continue
        m = _EP.fullmatch(t)
        if m:
            eps += [int(x) for x in (m.group(1), m.group(2)) if x]
            prev_ep = not m.group(2)
            continue
        if prev_ep and re.fullmatch(r"\d{1,3}", t) and len(eps) == 1 and int(t) > eps[0]:
            eps.append(int(t))                                         # "E09 16" (stored without the "-")
        prev_ep = False
    ep_txt = ""
    if eps:
        ep_txt = f"[E{eps[0]:02d}]" if eps[0] == eps[-1] else f"[E{eps[0]:02d}-E{eps[-1]:02d}]"
    combined = "COMBINED" if "combined" in tlow else "COMPLETE" if "complete" in tlow else ""
    edition = [EDITIONS[t] for t in tlow if t in EDITIONS]
    res = next((t.lower() if t.lower().endswith("p") else "2160p" for t in tail if _RES.fullmatch(t)), "")
    bit10 = "10Bit" if "10bit" in tlow else ""
    ott = next((OTTS[t] for t in tlow if t in OTTS), "")
    src = next((SOURCES[t] for t in tlow if t in SOURCES), "")
    hevc = any(t in ("x265", "hevc", "h265") for t in tlow)
    x264 = any(t in ("x264", "avc") for t in tlow)
    codec = "x265" if hevc else "x264" if x264 else ""
    subs = "MSubs" if any(t in ("msub", "msubs") for t in tlow) else \
           "ESubs" if any(t in ("esub", "esubs") for t in tlow) else ""
    langs = []
    for t in tlow:                                                    # Tam Tel Hin / HIN TAM / Hindi
        for part in t.split(","):
            n = LANGS.get(part)
            if n and n not in langs:
                langs.append(n)
    multi = "multi" in tlow
    if len(langs) < 2:                                                # name has no (or one) language -> try the caption
        from_caption = _langs_from_caption(caption)
        if len(from_caption) > len(langs):
            langs = from_caption
    if not subs:
        subs = _subs_from_caption(caption)
    if fill:                                                          # master file: fill only what is missing
        if not year and fill.get("year"):
            year = str(fill["year"])
        if not langs and fill.get("langs"):
            langs = list(fill["langs"])
        if not subs and fill.get("subs"):
            subs = fill["subs"]
    if _fields is not None:
        _fields.update(title=title, year=year, season=season, eps=eps, res=res, ott=ott, src=src, codec=codec,
                       subs=subs, langs=list(langs), multi=multi)
    if not (year or season or res):
        return None
    out = [f"{title} ({year})" if year else title, season, ep_txt, combined, *edition, res, bit10, ott,
           "HEVC" if hevc else "", src, codec, subs]
    if len(langs) > 1:
        out.append(AUDIO_LABEL.get(len(langs), "[Multi Audio]"))
        names = [f"{langs[0]} ORG" if org else langs[0]] + langs[1:]
        out.append(f"[{' - '.join(names)}]")
    elif langs:
        out.append(f"[{langs[0]}]")
    elif multi:
        out.append("[Multi Audio]")
    return " ".join(p for p in out if p) + ext


def pretty_name(raw, org=True, caption=""):
    """Pretty name, or `raw` unchanged when it cannot be rebuilt."""
    return _parse(raw, org, caption) or raw


def _name_from_caption(caption, org=True, fill=None):
    """First caption line (of the first 4) that is a real release name, cleaned. None when there is none."""
    for line in _caption_lines(caption)[:4]:
        p = _parse(line, org, caption, fill)
        if p:
            return p
    return None


def _is_junk_name(raw):
    toks = [t for t in re.split(r"[\s_.\-]+", raw or "") if t]
    return not toks or all(_JUNK_TOK.fullmatch(t) for t in toks)


def effective_filename(raw, caption):
    """Name used for the poster post (title / TMDB lookup): the file name when it is a real release name,
    otherwise the first real-looking caption line (merged files: "Merged_350985_part001.mkv"), else the file name."""
    if not _STATE["on"] or _parse(raw or "") is not None:
        return raw
    for line in _caption_lines(caption)[:4]:
        if _parse(line) is not None:
            return line
    return raw


# ---------------------------------------------------------------- settings (kept in bot_settings, cached)
_KEY_ON, _KEY_ORG, _KEY_CAP = "pretty_name_enabled", "pretty_name_org", "pretty_name_clean_caption"
_STATE = {"on": True, "org": True, "cap": True, "loaded": False}


def _bot_id():
    return temp.ME if isinstance(temp.ME, int) else getattr(temp.ME, "id", temp.ME)


async def load_pretty_settings():
    try:
        _STATE["on"] = bool(await db.get_bot_setting(_bot_id(), _KEY_ON, True))
        _STATE["org"] = bool(await db.get_bot_setting(_bot_id(), _KEY_ORG, True))
        _STATE["cap"] = bool(await db.get_bot_setting(_bot_id(), _KEY_CAP, True))
        _STATE["loaded"] = True
    except Exception as e:
        logger.warning("pretty_name settings load failed: %s", e)


def clean_caption(caption, template=""):
    """The stored caption of a file (extra lines such as "• Added Custom Chapters", audio notes, credits ...).
    With pretty names ON it is dropped from {file_caption} whenever the template already shows {file_name}."""
    if _STATE["on"] and _STATE["cap"] and "{file_name}" in (template or ""):
        return ""
    return caption


def _display_name(raw, caption=None, fill=None):
    """The name to show in a caption / button.
    1) the stored file name, pretty-printed, when it is a real release name (old behaviour, unchanged);
    2) otherwise the file's original caption (first real name line, cleaned) - for merged files whose
       file name is junk like "Merged_350985_part001.mkv";
    3) otherwise the old cleaned name."""
    raw = raw or ""
    if _STATE["on"]:
        try:
            p = _parse(raw, _STATE["org"], caption or "", fill)
            if not p and caption:
                p = _name_from_caption(caption, _STATE["org"], fill)
            if p:
                return p
            if caption and _is_junk_name(raw):
                for line in _caption_lines(caption)[:2]:
                    c = clean_filename(line)               # "Join @channel" cleans down to nothing -> skipped
                    if c and c.strip():
                        return c
        except Exception as e:                                       # never break a file delivery
            logger.warning("pretty_name failed for %r: %s", raw, e)
    return clean_filename(raw)


def display_name(raw, caption=None, fill=None):
    """_display_name() + the rule "540p is always 480p" (also inside names that were not pretty-printed)."""
    return norm_res(_display_name(raw, caption, fill))


_EXT_RE = re.compile(r"(\.(?:mkv|mp4|avi|mov|webm|m4v|ts|wmv|flv|zip|rar|7z|srt))$", re.IGNORECASE)


def _fold(text):
    """'𝑫𝒓𝒂𝒈𝒐𝒏𝑭𝒊𝒓𝒆𝑾𝒐𝒓𝒍𝒅' / 'Dragon Fire World' / 'DRAGONFIREWORLD' -> 'dragonfireworld' (fancy letters, case, spaces, signs ignored)."""
    return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", text or "").casefold())


def template_has_brand(template):
    """True when a caption template already shows the brand tag itself - e.g. '{file_name}</a>➥ 𝑫𝒓𝒂𝒈𝒐𝒏𝑭𝒊𝒓𝒆𝑾𝒐𝒓𝒍𝒅'.
    Then the bot must NOT add its own tag into {file_name} (else the tag shows twice)."""
    if not NAME_BRAND_TAG or not template:
        return False
    plain = re.sub(r"<[^>]*>", "", template)
    if NAME_BRAND_TAG in plain:
        return True
    i = plain.find("{file_name}")
    if i >= 0:                                         # the tag typed in plain letters right after {file_name}
        rest = plain[i + len("{file_name}"):].split("\n", 1)[0]
        return _fold(NAME_BRAND_TAG) in _fold(rest)
    return False


def brand_name(name, emoji=True, tag=True):
    """'Jailer (2023) 1080p ... [Hindi ORG - Tamil].mkv' -> '🐉 Jailer (2023) 1080p ... [Hindi ORG - Tamil] 𝑫𝒓𝒂𝒈𝒐𝒏𝑭𝒊𝒓𝒆𝑾𝒐𝒓𝒍𝒅.mkv'
    (emoji in front, tag before the extension; NAME_BRAND_EMOJI / NAME_BRAND_TAG in info.py, empty = off).
    tag=False -> no tag in the name (the caption template already carries it)."""
    name = (name or "").strip()
    if not name:
        return name
    m = _EXT_RE.search(name)
    stem, ext = (name[:m.start()], m.group(1)) if m else (name, "")
    stem = stem.rstrip()
    if tag and NAME_BRAND_TAG and NAME_BRAND_TAG not in stem:
        stem = f"{stem} {NAME_BRAND_TAG}"
    if emoji and NAME_BRAND_EMOJI and not stem.startswith(NAME_BRAND_EMOJI):
        stem = f"{NAME_BRAND_EMOJI} {stem}"
    return stem + ext


def branded_name(raw, caption=None, template=None, fill=None):
    """display_name() + the brand (emoji in front, tag before the extension) - for captions.
    template = the caption template that will be filled: if it already carries the tag, the tag is not added twice."""
    return brand_name(display_name(raw, caption, fill), tag=not template_has_brand(template))
