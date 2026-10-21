# Fix report

## Bugs fixed
| File | Problem | Fix |
|---|---|---|
| `database/users_chats_db.py` | `get_notcopy_user()` returned an `InsertOneResult` for new users, so verification code crashed (`.get`/`[...]` on it) | returns the real document |
| `utils.py` `get_cap()` | `TEMPLATE.format(query=..., **locals())` raised `TypeError` (duplicate `query`) and `{message}` was never supplied, so IMDb result captions failed | explicit `message`, `remaining_seconds`, etc. passed instead of `**locals()` |
| `utils.py` `get_posterx()` | TMDB plot is a string; `plot[0]` returned only the first letter | handled str / list correctly |
| `utils.py` | dead, broken code block (undefined `text`) left after `get_landscape_thumb()` | removed |
| `utils.py` | `get_size()` could `IndexError` on huge values; `list_to_str()` added trailing commas; `humanbytes()` could `KeyError` | fixed |
| `plugins/route.py` | handlers returned `None` on `AttributeError` / connection reset (aiohttp 500 "missing return"); `/watch` crashed on paths with no digits; range end was not clamped per RFC 7233 | proper 404 / 499 responses, range clamped |
| `dreamxbotz/util/file_properties.py` | message without media -> `AttributeError` | raises `FIleNotFound` |
| `dreamxbotz/util/render_template.py` | every download page view re-downloaded the file from its own stream URL just to read its size | removed (Telegram already provides the size) |
| `dreamxbotz/util/custom_dl.py` | cache-cleaner task had no reference (can be garbage collected) | reference kept |
| `plugins/pmfilter.py` | sync IMDb search inside `ai_spell_check` blocked the event loop | `asyncio.to_thread` |
| `database/ia_filterdb.py` | DB-size cache was bypassed once DB > 10 MB (a `dbstats` call per saved file); `dreamxbotz_get_series` returned `[]` instead of `{}` on error | cache threshold 400 MB; returns `{}` |
| `bot.py` | startup crashed if the bot could not post to `LOG_CHANNEL` | logged as a warning |
| `info.py` | missing `API_HASH` / `BOT_TOKEN` / `DATABASE_URI` gave confusing errors later | clear early error |
| `requirements.txt` | `aiohttp` is imported directly but not listed | added |

## Security fixes
* Hard-coded **TMDB bearer token** (`Imdbposter.py`) and **ImgBB API key** (`telegraph.py`) removed -> env vars `TMDB_BEARER_TOKEN`, `IMGBB_API_KEY` (documented in `.env.example` / README). **Those two leaked keys should be revoked/rotated.**
* TLS verification was disabled for TMDB requests (`ssl=False`) -> removed.
* `/system` (server info) was available to everyone -> admins only.
* `/img` upload: file handle is closed and the temp file is always deleted.

## Things to know (not changed)
* `plugins/monkey_patch.py` patches internal Kurigram APIs; `kurigram` is pinned to `2.2.25` in `requirements.txt` - do not upgrade it without testing the monkey patch.
* `fonts/Poppins-Bold.ttf` (poster watermark font) is now included in the project (Poppins is licensed under the SIL Open Font License).
* `Procfile` defines both `web` and `worker` running the same bot; only scale one of them on Heroku.

## Update: file delivery format
* Languages now come in **one quote box on one line**: `🔊 #Hindi #English #Tamil` (`get_languages_html` in `utils.py`).
* A second quote box with the **VLC Player** note is added under it for video files.
* Under every delivered file the bot replies `⚠️ Deleting in 50s, save quickly…` and edits it every 10 seconds (50, 40, 30, 20, 10), then deletes the file and the notice becomes "successfully deleted". Works for single files, fallback files and "Send All". Time is set with `FILE_DELETE_TIME` (default 50).

## Update: PM search order
* When a user searches in the bot PM: **1)** the poster-style post card (poster + title / audio / genres / quality + download links, `dreamxbotz/util/postcard.py`) comes first, **2)** the normal old result (buttons / result card) comes below it. Switch with `PM_POST_FIRST` (default `True`).
* The post card is deleted together with the old result when auto-delete runs.

## Update: post archive
* The post is deleted 3 minutes (180s) after it is sent. Exactly **30 seconds before that (at 2:30)** a copy (poster + caption + buttons) is saved to the private channel set in **`RESULT_ARCHIVE_CHANNEL`** (bot must be admin there). The deletion time never changes, even if saving fails. `POST_DATABASE_CHANNEL` was removed.
* Also applies to the post shown first in PM search (saved 30s before it is deleted with the result, when auto-delete is on).
* Code: `archive_post`, `wait_then_archive`, `archive_and_delete_post` in `dreamxbotz/util/postcard.py`.

## Update: /bulkpost for old files (2.5K videos)
* `/bulkpost scan` reads every saved file, `/bulkpost start` creates **one post per movie with ALL its qualities** (480p, 720p, 1080p, 2160p ...) and sends it, newest year first, to the private channel in **`RESULT_ARCHIVE_CHANNEL`**. The records are kept in their own `bulk_posts` collection, so the live auto-posts in `MOVIE_UPDATE_CHANNEL` are never touched. A movie is never posted twice.
* Files of one movie that differ only by a missing year (`RRR 1080p` + `RRR 2022 720p`) are merged into one post when there is exactly one matching year.
* **Bug fixed - quality was `N/A` for every file without a caption** (`get_qualities()` returned the truthy string "N/A", so the file name was never read). Old files usually have no caption, so every download line would have said "Unknown". Quality is now read from the file name first, the caption fills what is missing; names are normalised (`WEB-DL`, `BluRay`, `HEVC`, `4K`).
* Header "Quality" line shows resolutions when no source (WEB-DL/BluRay) is known; two files of the same resolution are told apart by source/language.
* `send_movie_update()` / `update_movie_message()` accept `chat_id` and `coll` (default = old behaviour) - used by /bulkpost.
* Poster: every image (portrait poster, backdrop, square, tiny, ultra-wide, PNG/RGBA) comes out exactly **2560x1440 landscape**.

## You must set these environment variables (the keys were removed from the code)
* `TMDB_API_KEY` **or** `TMDB_BEARER_TOKEN` - without one of them poster / movie lookups find nothing.
* `IMGBB_API_KEY` - only needed for the `/img` upload command.

## Extra fixes in v3
| File | Problem | Fix |
|---|---|---|
| `plugins/commands.py` (`allfiles_` delivery) | If one file of a group post had been deleted from the DB, `files_[0]` raised `IndexError` and the whole delivery stopped | missing files are skipped; "No such file" shown if none are left |
| `requirements.txt` | `aiohttp` listed twice | duplicate removed |

## /start auto-delete (v5)
* `/start` (home screen, private chat): the user's `/start` message is deleted after **3 s**, the bot's home message (photo + buttons) after **5 min**.
* Changeable via env vars `START_CMD_DELETE_TIME` (default 3) and `START_HOME_DELETE_TIME` (default 300). Deep links (`/start file_...`, verification, referral) are not affected.

## Update: only 6 qualities in every post (movies and web series)
* A post now lists **only** these qualities, in this order, each **at most once**: `480p HEVC`, `480p`, `720p HEVC`, `720p`, `1080p HEVC`, `1080p`. Applies to live auto-posts and `/bulkpost`.
* If several files fall in the same quality, one is shown: the one with more audio languages, then the bigger file. For web series this is decided per episode.
* `x265` / `H265` / `H.265` count as HEVC.
* Other qualities (360p, 2160p/4K, files with no quality in the name) are not listed. If a title has none of the six, its files are listed as they are so the post is never empty.
* Header lines (Audio / OTT / Quality) are built only from the files that are actually shown.
* Web-series links are now one file per episode (no more in-memory group links that stop working after a restart).

## Merge note (v6 + autopost)
This package = v6 + the `/autopost` upload (plugins/autopost.py, bot.py resume hook, env vars, README) on top of the newer upload (post-card, countdown delete, 6-quality posts, archive channel, VLC note) **+** the earlier fixes:
* `fonts/Poppins-Bold.ttf` included, `requirements.txt` aiohttp duplicate removed.
* `/start` auto-delete (user's `/start` 3 s, bot home 5 min).
* "No such file" guard / skip-missing-file fix in `allfiles_` delivery.
* Restart-proof `allfiles_` links (saved in MongoDB `getall_links`) re-added, because the newer upload had dropped them.

## New feature: `/autopost` (plugins/autopost.py)
* Reads all saved files (both databases), groups them like the channel auto-post does
  (`extract_media_info`), and sends every movie / series season as one collection to the
  movie channel / series channel, with a sticker after each collection.
* Files inside a collection are ordered: season pack, then episodes, then lowest quality first.
* Same caption as the bot gives to users (language boxes included), optional poster cover with
  the watermark + `1080p HEVC` / `S01E01-04` labels (one TMDB lookup per collection, not per file).
* Queue + progress in MongoDB (`autopost_queue`), stop/start/restart safe, FloodWait handled,
  duplicates and audio files skipped, clear message if the bot is not admin in a channel.
* `bot.py` starts `resume_autopost()` so a running job continues after a redeploy.
* `/autopost` review fixes: `<s>` in the help text was read as an HTML strikethrough tag, error texts are HTML-escaped, and the cover image stream is rewound before every send attempt (FloodWait retry / fallback).

## New feature: `/ccopy` (plugins/ccopy.py)
* Scans a source private channel (message ids, like `/index`), groups its videos into collections (movie / series season), keeps only the six allowed qualities (same picking rule as the posts) and copies them to a destination private channel with `copy_message` (no "Forwarded from" tag). A sticker is sent below every finished collection.
* Queue + progress in MongoDB (`ccopy_queue`), stop / restart safe, FloodWait handled, `bot.py` starts `resume_ccopy()`.
* Commands: `/ccopy from | to | sticker | test | scan | start [n] | stop | status | delay | caption on|off | retry | clear confirm`. The sticker falls back to the `/autopost` sticker if none is set for `/ccopy`.
* `/ccopy mode card`: posts ONE post card per collection (poster + the max-6-quality download links, built by the same code as the PM search card) to the destination channel, sticker below it. The picked files are indexed in the bot database so the links work. `/ccopy mode files` (default) copies the videos instead.
* `/ccopy sticker`: change the collection-end sticker any time - send `/ccopy sticker` and then any sticker (detected automatically for 2 min), or reply to a sticker with it; `/ccopy sticker show` previews the current one, `/ccopy sticker off` removes it. Falls back to the `/autopost` sticker when none is set.

## New: posting time window + `/postrange`
* `/autopost schedule 10:00 22:00` (India time, overnight allowed, `off` = any time): jobs only send inside
  the window and wait/continue by themselves outside it (`plugins/autopost.py`).
* `/postrange <from> <to> <start> <end> [delay]` (`plugins/postrange.py`): copies all posts whose number is
  between start and end into another channel. Skips deleted/service numbers, stops by itself after ~1000
  empty numbers in a row, respects the time window, saves progress in MongoDB and resumes after a restart
  (`bot.py` starts `resume_postrange()`).

## Merge note (autopost_v2 + v6 + ccopy)
* Combined: the `autopost_v2` upload (`/autopost schedule` time window, `/postrange`) with the v6 package (6-quality posts, post-card, `/ccopy`, `/start` auto-delete, fonts, restart-proof links, autopost review fixes).
* `/ccopy` now respects the same daily time window (`/autopost schedule 10:00 22:00`) for files, post cards and the sticker; `/ccopy status` shows it.
* `bot.py` starts `resume_autopost()`, `resume_ccopy()` and `resume_postrange()`.
* `/postrange` review fixes: the usage text used `<start> <end> ...` placeholders, which Telegram's HTML parser swallows (the help showed no values) - now `[start] [end] ...`; error texts are HTML-escaped.


## Group protection (`plugins/antispam.py`, settings in `info.py`)
* Bots added by users are banned; links (http/https, www., t.me, hidden/text links, link buttons), @mentions and forwarded messages from non-admins are deleted at once (also on edit). Admins, bot owners, anonymous admins, other bots and the linked channel's auto-forward are exempt; allow-list via `ANTISPAM_ALLOWED`; per-group `/antispam on|off|status`. Runs in its own handler groups (-41 / -40) before the normal plugins.
* Tested with 19 message types (links, hidden links, mentions, forwards, captions, emoji before a link, allowed items, `/cmd@bot`, e-mail style text) and the exemption / bot-ban rules.

## v7 - full code review (this package)
Checked: syntax of all 70+ .py files, undefined names, intra-project imports, `db.*` methods, un-awaited coroutines, `script.X.format()` placeholders.
| File | Problem | Fix |
|---|---|---|
| `info.py` | Default `AUTH_CHANNELS` / `AUTH_REQ_CHANNELS` = `"-100"` was turned into the fake channel id `-100`, so every `/start` and every search tried to check a channel that does not exist (error logs + extra API calls, also saved into new group settings as `fsub`) | only real channel ids (`<= -1000`) are kept; unset = empty list |
| `bot.py` | background tasks (`check_expired_premium`, `keep_alive`, `ping_server`, `resume_autopost`, `resume_ccopy`, `resume_postrange`) had no reference and could be garbage collected mid-run | kept in a set via `_spawn()` |
| `info.py` | `ULTRA_FAST_MODE` default argument was `True` while the env default is `"False"` (inconsistent) | default now `False` |

Not changed (works, only noted): `database/refer.py` uses the synchronous `pymongo` client inside async handlers (short blocking calls); `Procfile` has `web` and `worker` running the same bot, scale only one.


## v8 - separate command sets: users / admins / owner
* New env `OWNER_ID` (`info.py`, `OWNER_IDS`). Empty = first id of `ADMINS` is the owner. Owners are added to `ADMINS` automatically, so they can use admin commands too.
* Owner-only (filter changed from `ADMINS` to `OWNER_IDS`): `/system /logs /restart /maintenance /commands /broadcast /grp_broadcast /send /add_premium /remove_premium /get_premium /premium_users /add_redeem /trial_reset /leave /disable /enable /deletefiles /deleteall /clean_groups /resetallgroup /clear_junk /junk_group /delreq` and the maintenance on/off buttons.
* Help lists: `/help` (everyone, user commands only), `/admin_cmd` (admins), `/owner_cmd` (owner). `Script.py`: `USER_CMD`, `ADMIN_CMD` (no owner commands any more), `OWNER_CMD`.
* Telegram "/" menu is now per role (`dreamxbotz/util/bot_commands.py`, set at every start and by `/commands`): default scope = user commands only; group admins = + group commands; each admin / owner gets their own menu in PM (they must have started the bot once).
* A user typing an admin/owner command gets no reply at all, so nothing leaks.

## v9 - final review (static checks)
Checked: syntax of every .py file (compileall), undefined names, intra-project imports, `script.*` and `db.*` references, un-awaited coroutines, command menus vs handler filters (user / admin / owner), hard-coded secrets, env vars vs `.env.example` / `app.json`.
| File | Problem | Fix |
|---|---|---|
| `.env.example` | Inline comments after values (`ANTISPAM=True   # ...`). Docker `--env-file` and some hosts do NOT strip them, so `ANTISPAM_ALLOWED` would have become the comment text and the switches silently fell back to defaults | every comment moved to its own line |
| package | `__pycache__` folders must not be shipped | removed |

No other code errors found. Still true (not changed): `kurigram` is unpinned (pin the version that works for you), `database/refer.py` uses the sync `pymongo` client, `Procfile` has `web` and `worker` running the same bot (scale only one).

## Update: 10bit qualities in private-channel posts
* Private-channel posts (`/bulkpost` -> RESULT_ARCHIVE_CHANNEL, and `/ccopy` files + card mode) now allow **9 qualities**: the old six (480p HEVC, 480p, 720p HEVC, 720p, 1080p HEVC, 1080p) **plus 480p 10bit, 720p 10bit, 1080p 10bit**. Order: 480p HEVC, 480p, 480p 10bit, 720p ..., 1080p ....
* A file with `10bit` / `10-bit` / `10 bit` / `Hi10P` in its name or caption goes to its 10bit slot (even if it is also x265). Same pick rule: one file per quality, more audio languages then bigger size; per episode for series.
* Live channel auto-post (MOVIE_UPDATE_CHANNEL) and the PM search card are unchanged (still the six).
* `/autopost` is unchanged: it sends every file, so 10bit files already come.
* `merge_qualities()` now adds a `10bit` token to the quality string. `tenbit=True` parameter added to `build_post_caption`, `send_movie_update`, `update_movie_message`, `_pick_per_slot`, `_quality_slot`, `send_search_postcard`.
* /ccopy: run `/ccopy scan` again so the new 10bit files are picked (old queue keeps its old picks).

## Update: edit the file caption from inside the bot (no repo edit)
* New `plugins/file_caption.py`. Admin commands: `/filecaption` (show), `/setfilecaption <text>` (or reply to a message), `/resetfilecaption`.
* Placeholders: `{file_name}` `{file_size}` `{file_caption}`. Telegram formatting (bold / link / quote) is kept (saved as HTML). Invalid or >1024-char captions are rejected with a preview shown on success.
* Stored in `bot_settings` (key `file_caption_override`), so it survives restarts/redeploys. When set it overrides the group `caption` setting and `CUSTOM_FILE_CAPTION`; when empty everything works as before.
* Applied to all 4 delivery paths in `plugins/commands.py` and to autopost (`AUTOPOST_CAPTION` env still has priority there).
* Safety: `_deliver_file` never lets a long caption break delivery - if caption + language/VLC boxes exceed Telegram's 1024 limit the boxes are skipped, and if it is still too long the plain file name is used. `/setfilecaption` rejects captions over ~850 characters.

## Streaming fixes (v13)
| File | Problem | Fix |
|---|---|---|
| `dreamxbotz/util/custom_dl.py` | `yield_file` swallowed `TimeoutError`/`AttributeError` silently -> player got a truncated file with a fixed Content-Length and no log | error is logged and re-raised, `CancelledError` passes through, `work_loads` can never go negative |
| `plugins/route.py` | plain `200` responses also carried a `Content-Range` header (only valid for `206`) | sent only for range requests |
| `plugins/route.py` | message id taken from the first digits found anywhere in the path (wrong id for names like `Movie.2024`) | `_parse_path()` takes the leading id first |
| `plugins/route.py` | an extra client that is not admin in `BIN_CHANNEL` made the whole request fail with 500 | falls back to the main bot (client 0) |
| `plugins/route.py` | file with size 0/unknown produced an invalid range | returns 404 |

Checked: range/seek slicing (first/middle/last chunk, single chunk, 1 byte, exact 1 MB multiples) gives byte-identical output in 2100 random tests.
Needs a live test with your BIN_CHANNEL: open `/watch/<id>/<name>?hash=<hash>`, seek forward/back, and download with a resume-capable downloader.

## v14 - bug-fix pass
| File | Problem | Fix |
|---|---|---|
| `utils.py`, `plugins/pmfilter.py` | **Memory leak**: `temp.GETALL / SHORT / IMDB_CAP / VERIFICATIONS`, `FRESH` and `BUTTONS` got a new entry on every search and were never cleaned, so RAM grew until the host killed the bot | new `BoundedDict` (keeps only the newest 5k-20k entries, oldest dropped first) |
| `plugins/pmfilter.py` `give_filter` | A `KeyError` raised *anywhere inside* `auto_filter` was caught as "setting missing": it re-saved `auto_ffilter=True` and ran `auto_filter` again (duplicate results, error repeated outside the handler) | missing setting is handled before the call; errors inside `auto_filter` are only logged |
| `plugins/pmfilter.py` | `from_user` is `None` (anonymous admin / channel sender): `temp.SHORT[...]`, `temp.IMDB_CAP[...]`, `.mention` crashed `auto_filter` and `advantage_spell_chok` | guarded (`uid`, `user_mention`) |
| `plugins/pmfilter.py` | Quality / language / season buttons: `FRESH.get(key).replace(...)` raised `AttributeError` on an old button (after restart or cache cleanup) | shows the normal "old request" alert |
| `plugins/pmfilter.py` `pm_text` | `except Exception: pass` hid every PM search error | `logger.exception(...)` |
| `plugins/commands.py`, `database/users_chats_db.py` | Verification group id lived only in memory (`temp.VERIFICATIONS`): after a restart the verification link used group `0`, so the wrong settings were read | group id is saved in the `verify_id` document and used as fallback |
| `database/refer.py`, `plugins/commands.py`, `plugins/pmfilter.py` | Sync `pymongo` blocked the bot's event loop on every referral / `/start`; `get_refer_points` could return `None` | async `motor` client, all call sites `await` it, `None` -> `0` |
| `README.md` | License badge / text said MIT, but the `LICENSE` file is AGPL-3.0 | README now says AGPL-3.0 (with the section 13 note) |

Not changed on purpose: `BAD_WORDS` in `info.py` (words such as `join`, `tg`, `original` are removed from file names / titles - edit the list if it cuts a real movie name).
Checked after the patch: all files compile, no undefined names, no un-awaited `referdb` calls. Not run against a live bot / MongoDB - test `/start`, a group search, the Quality / Language / Season buttons and a referral link once after deploying.

## v15 - "arena" tag removal everywhere
| File | Problem | Fix |
|---|---|---|
| `info.py` `strip_bad_prefixes` | Missed lowercase-glued names (`❤️ arenadrishyam`), stacked tags, tag in the middle / end of a caption, html captions | rewritten: any case, any heart / emoji, stacked tags, `emoji + tag` anywhere. Real titles (`Arena 2011`, `Arena of Valor`, `Arenas`) are untouched |
| `database/ia_filterdb.py` `save_file` | The raw name / caption (with `❤️ arena`) was stored in the DB | file name **and** caption are cleaned before they are stored, so new files are clean in the index |
| `plugins/commands.py` | The caption sent with a delivered file was the raw stored caption (`f_caption = files.caption`) | cleaned before sending (also used for the language box) |
| `plugins/commands.py`, `plugins/files_delete.py` | Delete-by-name fallback compares with the stored (now cleaned) name | same cleaning applied to the compared name |
| `plugins/clean_prefix.py` (new) | Files that were indexed earlier still have the tag stored | owner command `/cleanprefix` (count only) and `/cleanprefix run` (applies it, both DBs) |

Auto-post (`channel.py`, `autopost.py`, `bulk_post.py`) already goes through `strip_bad_prefixes` / `clean_filename`, so it now benefits from the stronger function.
To block another tag, add it to `BAD_PREFIXES` in `info.py` (lowercase word).
Limit: the file name stored *inside Telegram* (the title on the file bubble itself) cannot be changed by the bot without re-uploading the file; the bot only controls the caption, result lists, index and posts.

## v16
* `BAD_WORDS` (`info.py`): added `movie`, `filmy4wap`, `xyz`, `world4ufree` (whole words, any case; removed from result lists, captions and auto-post titles).
  Note: `movie` is also a real title word ("Scary Movie", "The Lego Movie") - delete that line from `BAD_WORDS` if it cuts a title.

## v17 - sample / small videos blocked everywhere
Videos under `MIN_FILE_SIZE_MB` (default **30 MB**, env var, `0` = off) are treated as samples / junk. Only videos are checked (by type, `video/*` mime or `.mkv/.mp4/...` name) - audio, subtitles, zip, pdf are never touched.
| Where | What happens |
|---|---|
| `database/ia_filterdb.py` `save_file` | small video is **not saved** (returns code 4). Covers channel auto-index, `/index`, `/save`, ccopy |
| `plugins/channel.py` | no auto-post for it (it stops when `save_file` does not save) |
| `get_search_results` | small videos are excluded from every search, even if still in the DB |
| `plugins/autopost.py`, `plugins/bulk_post.py` | skip small videos when they read the DB |
| `plugins/ccopy.py` | small videos never go on a post card |
| `plugins/index.py`, `/save` | show a "Small/sample skipped" counter / message |
| `plugins/clean_prefix.py` | new owner command `/cleansmall` (count + 5 examples) and `/cleansmall run` (deletes them from the DB, both DBs) |

## v18 - RAM cleaner (512 MB hosts)
**Cause:** every poster / cover is a 1280x720 .. 2560x1440 image; building one needs several copies at once (10-60 MB). Python / glibc keep those freed pages instead of returning them to the OS, so RSS only goes up while the bot posts for days. Several in-memory caches also grew with every search.
| Where | Change |
|---|---|
| `memory_guard.py` (new) | `tune_malloc()` at start (freed big buffers go back to the OS at once); background `memory_cleaner()` every 120 s: `gc` + `malloc_trim`, drops idle title locks / finished timers; above 70 % of `RAM_LIMIT_MB` it also drops the search caches; above 90 % twice in a row (bot older than 30 min) it restarts itself before the host kills it. `/autopost`, `/ccopy`, `/postrange` continue by themselves after a restart |
| `plugins/ram.py` (new) | owner command `/ram` (usage, limits, cache sizes, tasks, threads) and `/ram clean` (deep clean now, before / after) |
| `bot.py` | starts the guard, graceful restart (`dreamxbotz.stop()` then re-exec) |
| `plugins/Dreamxfutures/Imdbposter.py` | only **one** poster is built at a time (semaphore) and the images are closed right after use |
| `utils.py` `add_episode_label`, `plugins/autopost.py` | cover image closed immediately after each send / label |
| `utils.py`, `plugins/pmfilter.py`, `database/ia_filterdb.py` | smaller caches: results 600, imdb captions 1500, buttons / queries 2000, regex cache 512 (results hold heavy Media objects) |
| `Dockerfile` | `MALLOC_ARENA_MAX=2`, `MALLOC_TRIM_THRESHOLD_` / `MALLOC_MMAP_THRESHOLD_` = 256 KB |
| `.env.example` | `RAM_LIMIT_MB`, `RAM_CLEAN_INTERVAL`, `RAM_AUTO_RESTART` |
The idle-lock cleanup never removes a title lock that is held or that another post is still waiting for (no two posts for one title at the same time).
Not touched on purpose: poster resolution (still 2560x1440 for post cards, 1280x720 for autopost covers) - lowering it saves more RAM but changes the quality.


## v19 - Hindi story on the "Audio, Subs & Story" Telegraph page
* `plugins/extract.py`: the Telegraph page now starts with **📖 कहानी (Story)** in Hindi, then the audio / subtitle tracks. The story is looked up on TMDB (same matching as posts) in parallel with the mediainfo download: TMDB Hindi (`hi-IN`) overview -> English overview translated to Hindi -> English overview. For a web series the season's own story is used when TMDB has it. Needs `TMDB_API_KEY` or `TMDB_BEARER_TOKEN`; without it the page shows "story not available" and everything else still works.
* Button text changed to `ᴠɪᴇᴡ ᴀᴜᴅɪᴏ, ꜱᴜʙꜱ & ꜱᴛᴏʀʏ` (`plugins/commands.py`) and, after the page is ready, `ᴠɪᴇᴡ ᴛʀᴀᴄᴋꜱ & ꜱᴛᴏʀʏ`.
* The English -> Hindi translation uses the free (unofficial) Google Translate endpoint, no key needed; if it fails the English story is shown.

## v19 - group buttons open for everyone
* `plugins/pmfilter.py`: `ANYONE_CAN_CLICK = True` turns off the "this is not your movie request" alert (file links, Quality / Language / Season, pages, Send All). The Close button stays owner-only.

## v20 - Telegraph layout: index page + 3 detail pages
* `plugins/extract.py`: the page opened by the button is now an index: title = file name, `by @DragonFireWorld`, then `👇 Available Tracks 👇`, `1 Audio Track`, `2 Subtitles Track`, `3 Story Explain Script` (each line is a link to its own Telegraph page: audio + video info, subtitles, Hindi story) and a `Join Main Group` link.
* Env vars (optional): `TELEGRAPH_AUTHOR` (default `@DragonFireWorld`), `TELEGRAPH_AUTHOR_URL` (default `https://t.me/DragonFireWorld`), `MAIN_GROUP_URL` (default `https://t.me/DragonFirePrime_bot`).
* The 3 detail pages are created in parallel, then the index page (4 Telegraph calls per click).

## v21 - 540p is treated as 480p everywhere
* `plugins/channel.py` `_canon_quality`: `540p` becomes `480p`, so posts, the PM post card, `/bulkpost`, `/ccopy` and the 480p / 480p HEVC slots all pick up 540p files as 480p.
* `utils.py` `get_quality_label`: the quality printed on the cover / poster shows `480p` for a 540p file.
* `plugins/autopost.py`: 540p files are ordered together with 480p.
* `database/ia_filterdb.py` `get_search_results`: searching / filtering `480p` (Quality button) also finds files named `540p`.
* The file name stored in Telegram / the DB is not changed, only how the quality is detected and shown.

## v22 - fewer portrait ("full size") posters
Cause: (1) TMDB only returned English / no-language images because `include_image_language` was not sent, so many Indian films looked like they had no backdrop and the portrait poster (put on a blurred canvas) was used; (2) when a title really has no backdrop the portrait poster was still used.
* `Imdbposter.py`: `include_image_language=en,hi,ta,te,ml,kn,bn,mr,pa,gu,ur,null` is sent; if no backdrop is found in the preferred languages, any backdrop (any language) is used.
* New env `ALLOW_PORTRAIT_POSTER` (default `False`, in `info.py` / `.env.example`): when a title has no landscape backdrop, the video's own thumbnail is used instead of the portrait poster. Applies to the cover of delivered files (`utils.get_landscape_thumb`), live channel posts (`channel.py`) and `/bulkpost`. `True` = old behaviour.
* Posts already sent / saved in the DB are not changed; only new posts and deliveries.

## v23 - poster in the box below the text is always landscape
* The poster box under the post text (link preview, `POSTCARD_PREVIEW` / `LINK_PREVIEW`) used the raw TMDB image link, so a portrait poster made a tall box. It now goes through the existing `/lp/<token>.jpg` landscape proxy (`preview_url()` in `plugins/channel.py`, handler in `plugins/route.py`): a 1280x720 image with the same watermark, a portrait poster is shown centered on a blurred backdrop. Applies to the PM search post card (`dreamxbotz/util/postcard.py`) and to channel / `/bulkpost` posts in link-preview mode.
* Needs a public `FQDN` (if it is unset / local the raw link is used as before). The first view of a poster is built on demand (cached 1 day in RAM).
* `ALLOW_PORTRAIT_POSTER=False` (v22) no longer replaces the poster with a photo when `LINK_PREVIEW` is on, so the "poster below the text" style is kept.

## v24 - channel posts also use the box below the text
* `info.py`: `LINK_PREVIEW` now defaults to `True`. Live channel auto-posts and `/bulkpost` (RESULT_ARCHIVE_CHANNEL) therefore send a text message with the poster in the box BELOW the text (through the `/lp/` landscape proxy from v23), the same style as the PM post card and `/ccopy mode card`, instead of a photo above the text. `LINK_PREVIEW=False` in the environment brings the old photo-above-text style back.
* Posts already in the channel stay as they are (they keep being edited in their own style).
* If a title has no poster at all, the video's own thumbnail is still sent as a photo (a link preview needs an image URL).

## v25 - merge
* Combined the two lines of work: the RAM-guard package (v14-v18: bug fixes, `arena` tag, small-video filter, RAM cleaner) and the v19-v24 changes (Hindi story page, 540p = 480p, landscape posters, link-preview posts, open group buttons).
* The only difference between the two was `memory_guard.py`: the idle-lock cleanup safety (see v18) is kept.

## v26 - `/autoforward` (plugins/autoforward.py)
* New admin command: live copy of every NEW video from one source private channel to a movie channel (movies only) and a series channel (web series only), no "Forwarded from" tag. Movie / series is decided with `extract_media_info` (season marker = series), the same rule as the posts.
* Persistent queue in MongoDB (`autoforward_queue`), one worker, FloodWait handled, finished records removed after 3 days. `bot.py` starts `resume_autoforward()`, so a restart / RAM-guard restart continues with the pending items.
* Commands: `/autoforward from | movie | series <id>`, `on | off | status | delay | caption on|off | retry | clear confirm`. Listed in the admin `/` menu and `/admin_cmd`.
* The listener runs in its own handler group (-30), so it does not interfere with `media_handler` indexing even if the source channel is also in `CHANNELS`.

## v27 - restart-proof `/bulkpost` + limit kept after a restart
* `/bulkpost` now continues by itself after a restart / RAM-guard restart (`resume_bulkpost()` in `plugins/bulk_post.py`, started from `bot.py`). Before, only `/autopost`, `/ccopy` and `/postrange` did. The queue (`bulk_post_queue`) was always saved; now the "running" flag, the gap and the remaining count are saved too (`BULK_RUNNING`, `BULK_GAP`, `BULK_LEFT`). Posts already done are never posted again; a post that was being sent at the moment of the restart may be sent once more.
* On shutdown the worker no longer sends the "Bulk posting ended" message and keeps the running flag; `/bulkpost stop`, a finished queue or an error clear the flag, so those never resume.
* Resume checks `RESULT_ARCHIVE_CHANNEL` first; if it is not set / not reachable it sends a warning instead of marking the whole queue as failed.
* `/autopost start N` and `/ccopy start N`: the limit was lost on restart (the job continued without a limit). The remaining count is now saved after every finished collection (`AP_LEFT`, `CC_LEFT`) and used on resume.

## v28 - /postsettings cleanup
* `plugins/post_format.py`: removed the "Links box" and "Watermark box" buttons, status lines and callbacks. Their keys never existed in `_POST_FORMAT_KEYS`, so pressing them raised a KeyError, and the caption builder never used them. Bold, Layout, Header box and everything else in /postsettings are unchanged. Post-card features (PM search card, /ccopy card mode) were not touched.

## v28 - Telegraph token
* `plugins/extract.py`: the hard-coded default Telegraph access token was removed. Without the `TELEGRAPH_ACCESS_TOKEN` env var the bot now creates a new Telegraph account by itself at start-up (and retries when a page is made if that first try failed). The old token was public in the code - treat it as leaked. Old Telegraph pages keep working; only editing them with the old account is no longer possible from the bot (the bot never edits pages).
* Telegraph: when no `TELEGRAPH_ACCESS_TOKEN` env var is set, the freshly created token is printed once in the start-up log (WARNING). Copy it into the env var, then delete this log line from the code.

## v30 - "S01 COMBINED E01 05" posts lost files
* `plugins/channel.py`: names like `Show_S01_COMBINED_E01_05_1080p...` and `..._E06_09_...` were both read as "Full Season" (the word COMBINED and the dash-turned-space range were not understood), so the post card kept only ONE file per quality. Now they are read as E01-E05 and E06-E09 and both are listed. `_ep_key()` also re-reads the episode from the stored file name, so posts built from files saved earlier are fixed too.

## v31 - review fixes (merged into v30)
| Area | Problem | Fix |
| --- | --- | --- |
| `Redeem.py` / `users_chats_db.py` | gift codes lived in a Python dict and were lost on every restart | stored in MongoDB (`codes`), taken atomically, put back if the redeem fails |
| `Premium.py` | buying / `/add_premium` replaced the remaining premium time | `db.extend_premium()` adds the new time on top of what is left |
| `info.py` / `.env.example` | `PREMIUM_LOGS` and `LOG_VR_CHANNEL` defaulted to the invalid `-100` | fall back to `LOG_CHANNEL`; log-channel errors can no longer turn a success into an error or burn a verify link |
| handlers | `/ping`, `/alive`, `/plan`, `/details`, welcome (600 s), verify-complete, PM result cards slept inside the handler and blocked workers | deleted in background (`spawn_delete`, `_spawn`) |
| `commands.py` | `export_chat_invite_link` ran before the admin check / outside try; anonymous admin crashed `/set_caption`, `/set_time*` | admin check first, safe invite helper, anonymous guard |
| `pmfilter.py` | `str(userid) not in ADMINS` never matched (ADMINS are ints); unguarded `get_chat_member` | fixed |
| `utils.py` | `save_group_settings` wrote every default (incl. shortener API keys) into each group; `is_subscribed` created a new invite link per check | only the changed key is stored; invite links cached 6 h |
| `users_chats_db.py` | no indexes, duplicate users possible | `ensure_indexes()` at start-up (+90-day TTL for `getall_links`), upserts |
| `bot.py` / Dockerfile | mixed local/UTC datetimes | process clock forced to UTC |
| `antispam.py` | every bot added by a group admin was banned | group admins may add bots (`ANTISPAM_ADMINS_CAN_ADD_BOTS`, default True) |
| misc | `??` emoji, `/reload` had no admin check, verify screen used the global gap, delayed update task could be garbage collected, templates compiled on every request | fixed |

Not changed: `requirements.txt` (IMDBKit `@main` is unpinned - pin it to a commit you trust), Docker still runs as root.

## v32 - collection order: all seasons of a show / all movies of a franchise together
* `/autopost`, `/ccopy` and `/bulkpost` sorted their queue by year only (all 2022 collections, then all 2023 ...), so S01 (2022) and S02 (2023) of one show were far apart.
* New `plugins/collection_order.py`: the three jobs read their queue with `GROUP_SORT` (newest show/franchise first; inside it S01, S02, S03 ... or oldest movie first) and group the queue by themselves (`ensure_grouped`) when the job starts or `status` is shown. Old queues need no new scan. The sticker still follows every collection.
* New: `/autopost regroup`, `/ccopy regroup`, `/bulkpost regroup` (job must be stopped) and `/franchise add|remove|list` for movie names that do not start the same (`/franchise add Fast Five, Furious 7, Fast X | Fast and Furious`). Listed in the admin menu.

## v34 - auto clean (6 minutes) + owner timer for broadcasts
* New `dreamxbotz/util/autoclean.py` (started from `bot.py`) and `plugins/autoclean.py` (`/bcdelete`). Everything the bot sends in a PM or a group - command replies, search results / filters, errors, status messages - is deleted `AUTO_CLEAN_TIME` seconds later (default 360 = 6 minutes, 0 = off).
* The existing delete times are NOT changed (`DELETE_TIME`, `FILE_DELETE_TIME`, `/start` messages, post cards, welcome card, the countdown of delivered files). A message with an own timer shorter than 6 minutes is gone before the clean-up runs; one with a longer timer (welcome card 10 min, or a value above 360 in the env) is registered with `keep()`, so the clean-up waits for that time.
* Channels are never touched: only private chats, groups and supergroups; log / bin / archive / update channels and the `/autopost`, `/ccopy`, `/autoforward` targets are protected as well.
* Broadcasts are the only messages that stay, and they get the auto delete time of the bot OWNER: `/bcdelete 30m | 2h | 1d | off` (default `BROADCAST_DELETE_TIME` = 24 h). It counts from the moment each user / group receives the message.
* Jobs are saved in MongoDB (`autoclean_jobs`), so a restart / RAM-guard restart loses nothing; one sweeper deletes the due messages in batches of up to 100 per chat.
* `/clear_junk` and `/junk_group` (copy and delete at once) are not scheduled.

## v35 - the users' own messages in the bot PM are cleaned too
* `plugins/autoclean.py`: a first-in-line handler (group -100, it never stops the other handlers) notes every message a user types in the bot PM; it is deleted after `AUTO_CLEAN_TIME` (6 minutes) like the bot's own messages, so the PM stays empty. PM only - groups are not touched. `CLEAN_USER_PM_MESSAGES=False` switches it off.

## v36 - status channel + sticker after every finished post
* New `/statuschannel -100...` (plugins/statuschannel.py, helpers in `dreamxbotz/util/jobtools.py`): the progress ("20175 collections done ..."), "ended", warning and "Bot restarted - continues" messages of `/autopost`, `/bulkpost`, `/ccopy`, `/postrange` and `/autoforward` go to that private channel and no longer to the bot PM. `/statuschannel off` brings them back to the admin PM; if the channel can not be reached the message falls back to the PM, so nothing is lost. The channel is protected from the auto clean. Replies to your own commands still come to the PM (cleaned after 6 minutes).
* One sticker for all jobs (`/ccopy` sticker, else `/autopost` sticker, else `AUTOPOST_STICKER`): `/autopost` and `/ccopy` already sent it after every collection; now `/bulkpost` sends it after every posted movie and `/autoforward` after every movie / every season (the group is closed when the next movie / season starts or when no new file arrives for 60 seconds). `/postrange` copies the source channel as it is, so the stickers that are in the source stay in place.

## v37 - "NO SUCH FILE EXISTS !" on some file / quality links (also in v14)
* `plugins/commands.py` (`/start file_...`): before the database lookup the id of the link went through a base64 "decode" and was replaced by whatever followed the first `_` byte. The ids stored in the database are themselves base64 text, so about 6.5% of all files (those whose bytes contain `_`) were looked up under a garbled id and always answered "NO SUCH FILE EXISTS !" - the same files every time, for example one 1080p link of a post card while the 480p / 720p links worked. Now the id is looked up AS IT IS first; the old decode is only a fallback (old wrapped links still work). Measured on 20,000 generated ids: 93.7% found before, 100% after.
* When a link still finds nothing, the reason is now written to the log (`start link ...: no file in the database`, `post list not found`, `none of the files of this post is in the database`).

## v38 - post links never expire
* `database/users_chats_db.py`: v30 put a 90 day expiry index on `getall_links` (the saved file lists behind the multi-file post links `allfiles_` / `postcard_`). MongoDB deleted every list older than 90 days, so the buttons of older posts answered "NO SUCH FILE EXISTS !" (this does not exist in v14 / v25). The index is no longer created, and `ensure_indexes()` drops it from a database that already has it (logged as "old expiry index ... dropped"). Lists that MongoDB already deleted can not be restored.
## Pretty names, step 2 - pretty file names (new)
`plugins/pretty_name.py` rebuilds every caption's `{file_name}` at send time, so it also applies to all files already in the database (no migration, no re-upload):
`Lust.Stories.2018.1080p.NF.WEB-DL.DDP5.1.x264-Telly.mkv` -> `Lust Stories (2018) 1080p WEB-DL x264.mkv`
`...HIN-TAM...` -> `... [Dual Audio] [Hindi ORG - Tamil].mkv`
Used by: file delivery in PM / groups (single, Send All, fallback) and autopost captions. Names that are already pretty, or have no year, fall back to the old `clean_filename()`.
Admin: `/prettyname` (status), `/prettyname on|off`, `/prettyname org on|off`, `/prettyname test <name>`. Default ON.

## Pretty names, step 3 - pretty names everywhere
- Search-result **buttons** (all result views, filters, season/quality/language pages) and the text result lists now show the pretty name (`utils._display_name` / `plugins.pretty_name.display_name`).
- **Poster / card posts** (PM search post card, `/ccopy` card mode, channel auto-posts): a movie post's heading shows the full pretty file name of its first (lowest quality) file, e.g. `🎬 The Raven (2012) 480p BluRay x264 [Dual Audio] [Hindi ORG - English].mkv`, instead of `🎬 The Raven 2012`. The links below are unchanged. Series headings and names that cannot be prettified (no year) keep the old heading.
- `/postsettings` has a new **Heading full name: ON/OFF** button (`show_names`, default ON; `/resetpostformat` restores it).
- `/prettyname off` still switches all of it back to the old names.

## Pretty names, step 5 - messy series / multi-tag names
- `pretty_name()` rewritten as a whitelist parser: it keeps only title, year, season, episodes, COMBINED, resolution, 10Bit, OTT, source, codec, subs and languages. Release groups, credits (`~ Swapnonil ~ PMZ`), fancy unicode, audio codecs / bitrates (`DDP2 0 ~ 128kbps`, `AAC`, `HE-AAC`), `DS4K`, stray symbols and line breaks inside names are dropped. Handles `S01COMBiNED`, `[E09-16]`, `E01 - E02`, `×264`, `[#Thai]`, `MULTi`.
- Languages are also read from the file's original caption (its audio line: `🔊 ... हिन्दी | English-日本語`) when the name has fewer than two.
- Extra stored caption lines (`• Added Custom Chapters`, audio notes ...) no longer appear: `{file_caption}` is blanked when the caption template contains `{file_name}` (`/prettyname caption on|off`).
- `/prettyname test <name>`: the lines after the first line are treated as that file's caption.

## Pretty names, step 6 - caption fallback for junk file names, cleaned-name parsing
- **Old way first, caption as an instant fallback:** the name is built from the stored `file_name` when that is a real release name (year / season / resolution). Only when it is not (merged files: `Merged_350985_part002.mkv`) the first real name line of the file's original caption is used (links, `@channel`, bullets and emoji are dropped). If the caption has no usable name either, the old cleaned name is shown. No delay - the caption is already in the database, so the 364k files that are already indexed are covered without re-indexing.
- Used by: delivered file captions, search-result buttons and lists, autopost captions, and poster posts (`effective_filename()` gives the post a real title for TMDB / grouping, so Part001 / Part002 land in one post).
- The parser now understands names as `save_file()` stores them (all `- . _ ( ) [ ] ' ,` replaced by spaces): `E09 16` -> `[E09-E16]`, `Devil s Plan` -> `Devil's Plan`, `WEB DL`, `H 265`.
- New: `E01T17` / `E018T33` (episode ranges), `Hindi Dubbed (ORG)`, `ESub` / `MSubs` and a plain language line (`‣ Hindi, Chinese | ESub`) read from the caption. Commas never appear in the title.
- New admin command `/prettycheck [n]`: samples n random indexed files (default 300) and shows how many get the file-name based name, the caption based name, or stay unchanged, with before/after examples.
- Known limits: `?` and `-` inside a title are lost for indexed files (`save_file` removes them before the parser sees them); the sample in `/prettycheck` reads the primary DB only.

## Merge note (v38 + pretty names)
v38 (auto clean, collection order, status channel, jobtools, post links never expire) merged with the pretty-name work (`plugins/pretty_name.py`, caption fallback for junk file names, `/prettyname`, `/prettycheck`, heading full name in posts). Conflicts were resolved in favour of v38 where it changed the same code: the 90-day expiry index on `getall_links` stays removed (it made old post links stop working), `keep()` calls of the auto clean are kept in `utils._delete_msgs_later` and in the welcome-card timer.

## v39 - bot menu in the style of the "Minato" bot (plugins/menu_ui.py)
* `/start` now opens a banner photo + quote-block text + buttons: **How to use the bot** (link button, `?start=howto` sends the "Don't send like foolish / Send like sensible" guide), **Settings**, **Others**, **View my files history**. The old home buttons (Add me to your group, Help, Top Searching, Upgrade) are no longer on the home page; their code is still there.
* **Settings** (saved per user in `menu_user_settings`): First Priority Size | Quality (order of the PM search results; Quality = 4K, 1080p, 720p ...), File Delete 10s -> 30s -> 50s -> 90s -> 120s -> 10s (the time after which a delivered file is deleted; a user who never touched it keeps the bot default `FILE_DELETE_TIME`), Result Mode BUTTON | TEXT (how PM search results are shown), Reset to Defaults (green = nothing changed, blue = 1 changed, red = 2 or more; a library without coloured buttons shows a plain button).
* **Others**: Premium, Referral, Back. **Premium** shows the bot's own plans (`STAR_PREMIUM_PLANS`, paid with Telegram Stars) and "Pay Now" opens the existing Stars invoice; **Referral** uses the existing referral points (`reff_` links) and "Copy Referral Link" is a real copy-to-clipboard button with the share text "Search any movie name to get movie file directly" + link.
* **View my files history**: every file delivered to a PM is saved (30 days, `file_history`); one button per day ("15 Oct -> 24 file", newest first), the day page lists the file names as links that send the file again.
* Commands in the PM: `/start`, `/request` (empty request gives the "Empty request detected" help, with a title it goes to the request channel or the admins), `/settings`, `/history`, `/premium`, `/refer` - the Telegram command menu of the users shows exactly these 6. The other commands still work, they are only not listed.
* The 6 banners are in `assets/menu/*.jpg` (made by `tools/make_menu_banners.py`, needs Pillow). The texts are in the TEXT section at the top of `plugins/menu_ui.py`.
* The 6 minute auto clean applies to these pages like to every other bot message.

## Merge note (v39 + pretty names)
The v38 + pretty-name zip (`plugins/pretty_name.py`, caption fallback, `/prettyname`, `/prettycheck`) merged with v39 (the Minato-style bot menu, `plugins/menu_ui.py`). Files changed by both sides (`bot.py`, `info.py`, `plugins/commands.py`, `FIXES.md`) were merged by hand-checked three-way merge; all other files come from the pretty-name zip or from v39 unchanged.

## v40.1 - fixes
* `info.py`: `LOG_CHANNEL`, `BIN_CHANNEL`, `RESULT_ARCHIVE_CHANNEL`, `MOVIE_UPDATE_CHANNEL` accept an empty value (copying `.env.example` with `RESULT_ARCHIVE_CHANNEL=` crashed the start with `int('')`).
* `plugins/commands.py`: referral works again - `is_new_user` is read before the user is added to the DB (before, every referral answered "already joined" and gave no points).
* Telegram Stars payment removed (`buy_<n>` invoice, pre-checkout and successful-payment handlers, Stars buttons / texts). `STAR_PREMIUM_PLANS` is now `PREMIUM_PLANS` (price in rupees, shown on the Premium page); payment is by UPI only, with the "Payment Details" / "Send Screenshot" buttons.
* Not changed on purpose: the auto-post title word filter (`IGNORE_WORDS` / `BAD_WORDS`).

## v41 - chat guard (owner approval) + small fix
* New `plugins/chat_guard.py`: the bot works only in groups / channels the OWNER approved. Everything else is ignored (no replies, filters, indexing, posting, join-request handling, buttons). Private chats are never touched.
  * Allowed automatically: chats set in the config (`CHANNELS`, `LOG_CHANNEL`, `BIN_CHANNEL`, `DELETE_CHANNELS`, `SUPPORT_CHAT_ID`, `REQST_CHANNEL`, `AUTH_CHANNELS`, `AUTH_REQ_CHANNELS`, `MOVIE_UPDATE_CHANNEL`, `INDEX_REQ_CHANNEL`, `LOG_VR_CHANNEL`, `RESULT_ARCHIVE_CHANNEL`, `PREMIUM_LOGS`, `ALLOWED_CHATS`), chats the owner / an admin set for a job (/autopost, /autoforward, /ccopy, /bulkpost, /postrange, /statuschannel ... - read from `bot_settings`), and every chat where the owner (`OWNER_ID`) added the bot.
  * A new chat where somebody else adds the bot: the owner gets a PM with **Approve / Leave** buttons; until then the bot is silent there. `CHAT_GUARD_AUTO_LEAVE=True` makes the bot leave at once.
  * Owner commands: `/allowchat [id]` (inside a group no id needed), `/disallowchat id`, `/allowedchats`, `/approveall` (approves every group saved in the database).
  * First start: the owner gets a list of the saved groups that are not approved (the bot is silent there until `/approveall` or `/allowchat`).
  * `/grp_broadcast` skips chats that are not approved. Switch everything off with `CHAT_GUARD=False`.
* `plugins/menu_ui.py`: "Reset to Defaults" answered the button twice (`QUERY_ID_INVALID` in the log after every reset). Fixed.

## v42 - one caption under every video + language box switch
* The caption of the screenshot (file name line + `⚠️ Use VLC Player` box + the "Powered by" text of `/setfilecaption`) is now built by ONE function (`standard_caption()` in `plugins/file_caption.py`) and used for: files delivered in PM / groups (single, Send All, batch), `/autopost`, and every video copied by `/ccopy` (files mode) and `/autoforward` (they used to copy the source caption). `/postrange` is not changed on purpose (it mirrors the source channel exactly); card posts (`/bulkpost`, live auto-post, `/ccopy card`) have no video file caption.
* The `🔊 #Hindi #English` language box is switched OFF by default (code kept): `/langbox on|off`.
* `/videocaption on|off` (default ON): OFF = `/ccopy` and `/autoforward` go back to their own `caption on|off` setting. While ON, a copied video always gets the standard caption (the old keep / remove setting applies to non-video files only).
* `/autopost` captions now also get the VLC box (they had only the language box).

## v43 - branded file name in every caption and card heading
* `plugins/pretty_name.py`: new `brand_name()` / `branded_name()`: `🐉 Jailer (2023) 1080p NF WEB-DL x264 [Dual Audio] [Hindi ORG - Tamil] 𝑫𝒓𝒂𝒈𝒐𝒏𝑭𝒊𝒓𝒆𝑾𝒐𝒓𝒍𝒅.mkv` (emoji in front, tag before the extension). Used for the file name in delivered video captions (PM / groups, Send All, batch), `/autopost`, `/ccopy` + `/autoforward` standard caption, and the heading of movie card posts (PM post card, live auto-post, `/bulkpost`, `/ccopy card`). Search-result buttons and lists keep the short pretty name.
* `info.py`: `NAME_BRAND_EMOJI` (default 🐉) and `NAME_BRAND_TAG` (default 𝑫𝒓𝒂𝒈𝒐𝒏𝑭𝒊𝒓𝒆𝑾𝒐𝒓𝒍𝒅); set one EMPTY in the env to switch that part off.
* Card post heading emoji default changed from 🎬 to 🐉 (`POST_TITLE_EMOJI` in `bot_settings` still overrides it). Series headings (`🐉 Title Year [Season 01]`) have no file name, so only the emoji changes there.
* If your `/setfilecaption` text already has the tag after `{file_name}` (the default Script.CAPTION has `➥ 𝑫𝒓𝒂𝒈𝒐𝒏𝑭𝒊𝒓𝒆𝑾𝒐𝒓𝒍𝒅`), it now shows twice: remove it from the template.

## v44 - tag only once, 540p = 480p, "View Audio, Subs & Story" on cards and /ccopy videos, caption fixes
| # | Area | Problem | Fix |
|---|---|---|---|
| 1 | tag shown twice | `{file_name}` already carried the brand tag, so a caption with `{file_name}</a>➥ 𝑫𝒓𝒂𝒈𝒐𝒏𝑭𝒊𝒓𝒆𝑾𝒐𝒓𝒍𝒅` (the default caption too) showed it twice | `pretty_name.template_has_brand()` - when the caption template already shows the tag (fancy or plain letters, after `{file_name}`), the bot does NOT add its own tag to the name. Used everywhere through `file_caption.render_caption()` (PM, Send All, fallback, autopost, /ccopy, /autoforward). The old v43 advice "remove the tag from your template" is no longer needed |
| 2 | 540p | `pretty_name` printed 540p; the rule lived in 6 places | one `info.norm_res()`: 540p is ALWAYS 480p (names, captions, buttons, slots, sort order). Search still finds 540p files under 480p |
| 3 | new button | - | "ℹ️ ᴠɪᴇᴡ ᴀᴜᴅɪᴏ, ꜱᴜʙꜱ & ꜱᴛᴏʀʏ ℹ️" is the LAST row of every post card (live post, PM card, `/start` postcard, `/bulkpost`, `/ccopy card`) and sits on the LAST video of every `/ccopy files` collection and `/autopost` collection (just above the sticker). `/autoforward` (live): the last video of a movie / season gets it when the group is finished (next group starts or 60 s idle), right before the sticker - the last copied video is remembered in `AF_LAST`, so a restart does not lose it |
| 3a | button handler | errors were posted into the chat / channel, the button stayed on "please wait", it failed when the file was not in the database, every press built 4 Telegraph pages | errors are pop-ups and the button comes back; works from the video message itself (no DB record needed) or through BIN_CHANNEL for a card; result cached in MongoDB (`extract_cache`) - the 2nd press (any user) is instant; 2 people pressing together build it once; `wait_data` now answers |
| 4 | caption fail | single-file fallback did `return` when the caption could not be filled - the user got NO file | `commands.make_caption()` never raises; worst case a plain bold file name. Also fixed: the raw `send_cached_media` fallback read `file.caption` (a Pyrogram Video has none) |
| 5 | HTML | file name / caption were not escaped in `commands.py` (`<`, `&`) | escaped like in `standard_caption()` |
| 6 | /setfilecaption | length was checked with a 28-character sample name - a real caption could pass 1024 characters and silently fall back to the plain name | checked with a 130-character branded name + the VLC (+ language) box; the preview shows the real branded name |
| 7 | poster | cover, PM card and channel posts used different routes; `get_posterx()` never sent `season` / `is_series` | `utils.find_poster()` (TMDB first, IMDb when `TMDB_POSTER=false`, season worked out from the file name) is used by the PM cover and the PM card |
| 8 | autopost cover | used a portrait poster even with `ALLOW_PORTRAIT_POSTER=false` | same rule as the PM cover |
| 9 | docs | `/ccopy` said "six qualities", the code uses 9 slots (10bit included) | text corrected (behaviour unchanged) |
| 10 | config | brand variables were not listed | `NAME_BRAND_EMOJI` / `NAME_BRAND_TAG` added to `.env.example`, `app.json` and README |

Not added (decided): Telegram Stars payment of the older v38 zip - premium stays manual (UPI).

## v45 - /autocycle (new files post by themselves), /sendto, /autopost new
| # | What | How it works |
|---|---|---|
| 1 | **Inbox** `new_files` | `ia_filterdb.save_file()` puts every NEWLY saved file (index channel, `/index`, request index) in the inbox. Old files are never in it. `/ccopy` saves with `inbox=False`. `/autopost scan` empties the inbox of the files the scan already has |
| 2 | `/autocycle on` | ONE command, then a round every 4 h (`/autocycle every <h>`, 1-48), restart-proof (`bot.py` resume hook). A round takes the oldest 200 inbox files (`/autocycle limit`), groups them by movie / season and for every group: **files -> MOVIE / SERIES channel of /autopost** (same caption, button on the last file, sticker), then **card -> RESULT_ARCHIVE_CHANNEL**. The public live card (`MOVIE_UPDATE_CHANNEL`) is not touched |
| 3 | new quality / episode later | the card of that movie / season is **edited** (it is added to it, no repost; no time limit); the new files go to the files channel as a new message + sticker. A new season = a new card. A no-year file ('RRR 1080p') joins the card of 'RRR 2022' when that is unambiguous (same rule as /bulkpost) |
| 4 | never twice | `posted_log` remembers `<channel>|<file>`; `autopost._send_file` writes it, rounds / `/sendto` / `/autopost new` read it |
| 5 | safety | daily window of `/autopost schedule` is used, FloodWait handled by autopost, a broken group is retried 3 rounds then dropped (never blocks the inbox), a channel error stops the round with a message and keeps the inbox, rounds wait while the old-files `/autopost` job runs |
| 6 | `/sendto <channel> <name> [files\|card\|both]` | one movie / season to ANY channel now (files + sticker and / or its card). It ignores the sent-log (you asked for it) |
| 7 | `/autopost new <channel> [confirm]` | sends to ANY channel every file that was not sent to it before. Without `confirm` it only counts; with `confirm` it sends (stop + run again continues) |
| 8 | `/autocycle` extras | `now`, `stop`, `off`, `status`, `files on\|off`, `cards on\|off`, `inbox`, `inbox clear confirm` |

How to start: 1) `/autopost movie <id>`, `/autopost series <id>` (the private channels) and RESULT_ARCHIVE_CHANNEL; 2) old files ONCE: `/autopost scan`, `/autopost start`; 3) when that is finished: `/autocycle on`.

Decided: a file added by `/index` does not make a public live card (an old `/index` would flood the public channel) - it goes to the private channels and the archive card through the cycle.

## v46 - one easy-to-read look for every job / status message
New `dreamxbotz/util/style.py` (head / row / block / tip / cmd / bar). The rules, so the messages stay easy to read on a phone:
- title = bold + ONE emoji, the state next to it (`✅ ON`, `▶️ running`, `⏸ idle`);
- a line `━━━━━━━━━━━━━━` between groups, one fact per line: emoji, plain label, **bold value**;
- channel ids and commands in `<code>` (tap to copy), "what to do next" in a quote box;
- plain letters for normal text (no fancy fonts: hard to read, not searchable); at most 3 styles in a message; only HTML tags.
Restyled: `/autocycle` (status, help, round summary, errors), `/sendto`, `/autopost new`, `/autopost` `/ccopy` `/bulkpost` `/postrange` `/autoforward` (status, scan finished, job ended, restarted). `/postrange` status got a progress bar (`▰▰▰▱▱▱▱▱▱▱ 37%`).
Not touched (on purpose): `Script.py` (start / help / about / premium texts are already in your small-caps style), file captions (set by `/setfilecaption`), the brand tag. Behaviour of every command is unchanged.


## v16 - security + crash review (static, not run against a live bot)
| File | Problem | Fix |
|---|---|---|
| `plugins/pmfilter.py` `auto_filter` | **Critical:** the group IMDb template (`/set_template`, any group admin) was filled with `TEMPLATE.format(..., **locals())`, so `{client.bot_token}` / `{message._client.api_hash}` printed the bot secrets into the group | new `render_imdb_template()` allows plain fields only (+ `{message.from_user.mention}`); unsafe / broken template falls back to the default; `/set_template` rejects such templates. **Rotate BOT_TOKEN / API_HASH if any group admin could have used this.** |
| `plugins/pmfilter.py` `cb_handler` | `create_chat_invite_link(REQST_CHANNEL)` ran on EVERY button press (new invite link + API call per click) | removed |
| `plugins/pmfilter.py` | `autofilter_delete` (drops the whole file DB) and `killfilesdq` had no permission check - if the owner ran `/deleteall` in a group, any member could press Yes | owner-only check |
| `plugins/pmfilter.py` | Language / quality toggle used `str.replace` on the code (`tel`, `mal`, `eng`, `mar` ...), cutting letters out of titles (Hotel, Animal, Engineer) | whole-word removal |
| `plugins/pmfilter.py` | AI spell-check could call itself again and again (suggest -> no result -> suggest ...) | one AI retry only |
| `plugins/pmfilter.py` `spol` | `get_poster()` returning nothing -> `AttributeError` | friendly alert |
| `plugins/commands.py` | Force-sub block: `reply_markup` undefined when the start link has no `_` -> UnboundLocalError was swallowed and force-sub skipped | defined before use |
| `plugins/commands.py` | Send All: one failing file stopped the loop, the files already sent never got the delete countdown | per-file try/except |
| `plugins/commands.py` | `/set_shortner*` printed the full API key in the (public) group | key masked, command message deleted |
| `plugins/commands.py` | `/request` text was put into HTML unescaped; no reply at all when `REQST_CHANNEL` is not set | escaped; confirmation sent |
| `plugins/route.py` | any URL containing digits caused a Telegram API call before the hash was checked | links without a valid 6-char hash get 403 first; 500 pages no longer show the raw error; traceback is logged |
| `info.py` | `INDEX_REQ_CHANNEL=""` crashed startup; `IMDB` fallback default was False | fixed |

## v47 - PostCardDaily channel store, force-sub key, two caps
* **PostCardDaily (`plugins/channel.py`, `plugins/post_format.py`)**: a post's `message_id` is only valid inside ONE channel, but the channel was never stored. After `POSTCARD_CHANNEL` was set or changed, the bot could edit / delete a different message with the same id in the new channel. Now the channel is stored with every post (`movie_updates` / `/bulkpost` records, field `channel`). When a title's record has no channel (an old record) or another channel, the bot does NOT touch the old message: it posts a fresh card to the current channel and stores the channel. So after this update every old title gets ONE new card the next time it is updated. `/repost` deletes the old post only when it was sent to the current channel.
* **Force-sub from the settings menu (`plugins/settings_ui.py`, `utils.py`)**: the menu saved channels in `fsub_id`, but the bot enforces `fsub`, so a force-sub set from the menu was never enforced. The menu now uses `fsub` (same key as the `/set_fsub` command). Old `fsub_id` values are merged into `fsub` the first time the group's settings are loaded and the old key is removed (the `AUTH_CHANNELS` default is not copied into the group).
* **Small**: `temp.SETTINGS` (per-group settings cache) is bounded to 5000 groups (`BoundedDict`); `/trendlist <n>` (public) is limited to 1 - 50.
* Checked: all files compile, undefined-name and local-import checks are clean, the changed functions were run with stubs (old record / other channel / same channel, fsub merge, cache cap). Not run against a live bot: try it on a test bot first.

## v48 - two small fixes
* **`/autocycle off` / `stop` no longer stops `/autopost` (`plugins/autocycle.py`)**: both jobs share autopost's stop flag. While the `/autopost` backfill is running the flag now belongs to it: `/autocycle off|stop` does not stop it, and a new autocycle round does not cancel a `/autopost stop` that was just sent. When nothing of autocycle is running, `/autocycle off|stop` leaves no stale stop flag behind.
* **Captions are HTML-escaped (`plugins/channel.py`)**: title, audio, genres and OTT text of channel posts, the PM post card, `/bulkpost` and `/ccopy` cards are escaped, so a title with `<`, `>` or `&` can no longer break the whole post (ENTITY parse error).
* Checked: files compile, undefined-name / local-import checks clean; the escape and the stop-flag rules were run with stubs. Not run against a live bot.

## Update: helper bot for post-card links (`POST_BOT_TOKEN`)
* Set `POST_BOT_TOKEN` (token of a second bot) and every "Click here" link in the post cards (live auto-post, /bulkpost, /ccopy, PM card, manual posts) opens **that bot** instead of the main bot. Empty = nothing changes.
* The helper bot runs inside the same process, with the same database, and loads only `plugins/commands.py` (+ `banned.py`): /start, verification, force-sub and file delivery.
* A file_id works only for the bot it came from, so the file goes main bot -> `BIN_CHANNEL` (once) -> helper bot reads it and gets its own id (`dreamxbotz/util/postbot.py`, cached in MongoDB `posthelper_fileids`).
* Make the helper bot **admin** in `BIN_CHANNEL` and in every force-sub channel (AUTH_CHANNELS / AUTH_REQ_CHANNELS).

## Update: default video caption (v48)
* Default caption (`Script.CAPTION`) now: title link with the brand **before** `.mkv` (`... [Hindi ORG - Tamil] 𝑫𝒓𝒂𝒈𝒐𝒏𝑭𝒊𝒓𝒆𝑾𝒐𝒓𝒍𝒅.mkv`), then the VLC box, then a `Powered by ➥⌈ @DragonFireWorld ⌋` quote box.
* Language box stays OFF by default (not removed): `/langbox on` brings it back.
* If a custom caption was saved earlier from the bot (`/setfilecaption`) or in group settings, run `/resetfilecaption` to use the new default.
