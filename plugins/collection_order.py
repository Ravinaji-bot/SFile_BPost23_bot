"""Order of the collections for /autopost and /ccopy.

Wanted order
  * Web series: ALL seasons of one show come one after the other, whatever year each season is from:
        S01 (2022) -> sticker -> S02 (2023) -> sticker -> S03 -> sticker ...
  * Movies: all movies of one franchise come together, oldest first:
        Spider Man (2002) -> sticker -> Spider Man 2 (2004) -> sticker -> Spider Man No Way Home (2021) ...
  * Between two different shows / franchises the one with the newest year goes first.

Every queue document gets three extra fields (`regroup()` fills them, from the `title`, `tag`,
`season` and `year` the queue already holds, no new scan is needed):
    gkey   the show / franchise the collection belongs to
    gyear  the newest year inside that show / franchise   (groups are sent newest first)
    ord    position inside the group: the season number (series) or release year (movies)
and the queue is read in this order:  gyear DESC, gkey, ord, _id   (see GROUP_SORT).

How a franchise is found (movies)
  The title is cleaned (year, "The/A/An/And/Of", trailing sequel markers such as 2, III, Part Two are
  removed). Titles with the same cleaned name are one franchise ("Cars", "Cars 2", "Cars 3"), and a
  title that starts with another title is part of that franchise too ("Spider Man" + "Spider Man No Way
  Home", "Anaconda" + "Anacondas Hunt For The Blood Orchid"; "Spiderman" = "Spider-Man").
  Names that do not start the same ("Fast Five", "Furious 7" for "Fast and Furious") are joined by hand:
        /franchise add Fast Five, Furious 7, Fast X | Fast and Furious
  then /autopost regroup  and  /ccopy regroup  (while the jobs are stopped).
"""
import html
import logging
import re

from pyrogram import Client, filters

from database.users_chats_db import db
from info import ADMINS

logger = logging.getLogger(__name__)

K_ALIASES = "FRANCHISE_ALIASES"          # [[alias name, franchise name], ...] (normalised)
GROUP_SORT = [("gyear", -1), ("gkey", 1), ("ord", 1), ("_id", 1)]

_STOP = {"the", "a", "an", "and", "of"}
_NUM_WORD = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
_ROMAN = {"ii": 2, "iii": 3, "iv": 4, "vi": 6, "vii": 7, "viii": 8, "ix": 9}
_SEQ_WORD = {"part", "chapter", "vol", "volume"}
_YEAR = re.compile(r"^(?:19|20)\d{2}$")


# --------------------------------------------------------------------------- name cleaning
def _tokens(title):
    return re.sub(r"[^0-9a-z]+", " ", str(title or "").lower()).split()


def _strip_year(toks):
    toks = list(toks)
    while len(toks) > 1 and _YEAR.match(toks[-1]):
        toks.pop()
    return toks


def _full(title):
    """Name without the trailing year and without The / A / An / And / Of; sequel markers are kept."""
    toks = _strip_year(_tokens(title))
    return [t for t in toks if t not in _STOP] or toks


def _is_seq(tok):
    return (tok.isdigit() and 0 < int(tok) <= 20) or tok in _ROMAN or tok in _NUM_WORD or tok in _SEQ_WORD


def _base(toks):
    """Name without trailing sequel markers: 'spider man 2' -> 'spider man', 'dune part two' -> 'dune'."""
    toks = list(toks)
    while len(toks) > 1 and _is_seq(toks[-1]):
        toks.pop()
    return toks


def _part(toks):
    """Sequel number at the end of the name (0 = none). Only used to order two movies of the same year."""
    if not toks:
        return 0
    t = toks[-1]
    if t.isdigit() and 0 < int(t) <= 20:
        return int(t)
    return _ROMAN.get(t) or _NUM_WORD.get(t) or 0


def _norm_tok(t):
    return t[:-1] if len(t) > 3 and t.endswith("s") else t        # anacondas = anaconda


def _sq(toks):
    return "".join(_norm_tok(t) for t in toks)                    # 'spider man' = 'spiderman'


def _is_prefix(a, b):
    """True when name `a` is the beginning of name `b` (token borders, spaces ignored)."""
    target = _sq(a)
    acc = ""
    for t in b:
        acc += _norm_tok(t)
        if acc == target:
            return True
        if len(acc) > len(target):
            return False
    return False


# --------------------------------------------------------------------------- grouping
def compute_groups(docs, aliases=()):
    """docs: dicts with _id, title, tag, season, year.  Returns {_id: {"gkey", "gyear", "ord"}}."""
    amap = {}
    for pair in aliases or ():
        try:
            a, t = pair
        except (TypeError, ValueError):
            continue
        amap[" ".join(_full(a))] = " ".join(_full(t))

    items = []
    for d in docs:
        series = d.get("tag") == "#SERIES"
        full = _full(d.get("title") or str(d["_id"]))
        own_part = _part(full)
        base = _base(full)
        target = amap.get(" ".join(full)) or amap.get(" ".join(base))
        if target:                       # named by hand with /franchise: use the franchise's name
            full = target.split()
            base = _base(full)
        try:
            year = int(d.get("year") or 0)
        except (TypeError, ValueError):
            year = 0
        items.append({"id": d["_id"], "series": series, "full": full, "base": base, "part": own_part,
                      "year": year, "season": d.get("season")})

    # ---- movies: join names that are the same or that start with the same name
    uniq = {}
    for it in items:
        if not it["series"]:
            uniq.setdefault(tuple(it["base"]), []).append(it)
    names = list(uniq)
    parent = {n: n for n in names}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    buckets = {}
    for n in names:
        buckets.setdefault(_sq(n)[:4], []).append(n)
    for group in buckets.values():
        group.sort(key=lambda n: (len(n), n))
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                same = _sq(a) == _sq(b)
                if (same or len(_sq(a)) >= 5 or len(a) >= 2) and _is_prefix(a, b):
                    ra, rb = find(a), find(b)
                    if ra != rb:
                        parent[rb] = ra
    rep = {}
    for n in names:
        r = find(n)
        if r not in rep or (len(_sq(n)), n) < (len(_sq(rep[r])), rep[r]):
            rep[r] = n
    for n in names:
        for it in uniq[n]:
            it["gkey"] = "m|" + _sq(rep[find(n)])
    for it in items:
        if it["series"]:
            it["gkey"] = "s|" + " ".join(it["full"])

    # ---- newest year of every group, position inside the group
    gyear = {}
    for it in items:
        gyear[it["gkey"]] = max(gyear.get(it["gkey"], 0), it["year"])
    out = {}
    for it in items:
        if it["series"]:
            try:
                pos = int(it["season"]) if it["season"] is not None else 0
            except (TypeError, ValueError):
                pos = 0
        else:
            pos = (it["year"] or 9999) * 1000 + min(it["part"], 999)
        out[it["id"]] = {"gkey": it["gkey"], "gyear": gyear[it["gkey"]], "ord": pos}
    return out


async def get_aliases(bot_id):
    try:
        return await db.get_bot_setting(bot_id, K_ALIASES, []) or []
    except Exception:
        return []


async def regroup(q, aliases=()):
    """Fills gkey / gyear / ord in every document of the queue collection `q`. Returns how many."""
    from pymongo import UpdateOne
    docs = [d async for d in q.find({}, {"title": 1, "tag": 1, "season": 1, "year": 1})]
    res = compute_groups(docs, aliases)
    ops = [UpdateOne({"_id": k}, {"$set": v}) for k, v in res.items()]
    for i in range(0, len(ops), 1000):
        await q.bulk_write(ops[i:i + 1000], ordered=False)
    return len(ops)


async def ensure_grouped(q, bot_id):
    """Queues made before this feature have no gkey: group them once, automatically."""
    try:
        await q.create_index([("status", 1), ("gyear", -1), ("gkey", 1), ("ord", 1), ("_id", 1)])   # for GROUP_SORT
        if await q.count_documents({"gkey": {"$exists": False}}):
            n = await regroup(q, await get_aliases(bot_id))
            logger.info("collection order: %s collections grouped", n)
    except Exception:
        logger.exception("collection order: grouping failed")


# --------------------------------------------------------------------------- /franchise
FRANCHISE_HELP = (
    "<b>/franchise</b> - put movies that do not start with the same name into one franchise\n"
    "(the bot already joins \"Spider Man\" + \"Spider Man 2\" + \"Spider Man No Way Home\" by itself)\n\n"
    "<code>/franchise add Fast Five, Furious 7, Fast X | Fast and Furious</code>\n"
    "<code>/franchise remove Fast Five</code>\n"
    "<code>/franchise list</code>\n\n"
    "After a change stop the jobs and send <code>/autopost regroup</code> and <code>/ccopy regroup</code>."
)


@Client.on_message(filters.command("franchise") & filters.user(ADMINS))
async def franchise_cmd(bot, message):
    bid = bot.me.id
    parts = (message.text or "").split(None, 2)
    sub = parts[1].lower() if len(parts) > 1 else ""
    rest = parts[2].strip() if len(parts) > 2 else ""
    pairs = [list(p) for p in await get_aliases(bid)]

    if sub == "add":
        if "|" not in rest:
            return await message.reply_text(FRANCHISE_HELP)
        left, target = (x.strip() for x in rest.split("|", 1))
        names = [n.strip() for n in left.split(",") if n.strip()]
        tkey = " ".join(_full(target))
        if not names or not tkey:
            return await message.reply_text(FRANCHISE_HELP)
        for n in names:
            key = " ".join(_full(n))
            pairs = [p for p in pairs if p[0] != key]
            if key != tkey:
                pairs.append([key, tkey])
        await db.update_bot_setting(bid, K_ALIASES, pairs)
        return await message.reply_text(
            f"✅ {len(names)} name(s) added to <b>{html.escape(target)}</b>.\n"
            "Stop the jobs, then send /autopost regroup and /ccopy regroup.")

    if sub == "remove":
        key = " ".join(_full(rest))
        new = [p for p in pairs if p[0] != key]
        if len(new) == len(pairs):
            return await message.reply_text("Not found. See /franchise list")
        await db.update_bot_setting(bid, K_ALIASES, new)
        return await message.reply_text("✅ Removed. Stop the jobs, then send /autopost regroup and /ccopy regroup.")

    if sub == "list":
        if not pairs:
            return await message.reply_text("No names added yet.\n\n" + FRANCHISE_HELP)
        lines = [f"• {html.escape(a)} → {html.escape(t)}" for a, t in pairs[:80]]
        more = f"\n… and {len(pairs) - 80} more" if len(pairs) > 80 else ""
        return await message.reply_text("<b>Franchise names</b>\n" + "\n".join(lines) + more)

    return await message.reply_text(FRANCHISE_HELP)
