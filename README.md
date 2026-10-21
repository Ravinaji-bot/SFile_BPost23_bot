<h1 align="center">DreamxBotz Auto Filter Bot</h1>

<p align="center">
  <b>Powerful Telegram auto-filter, file indexing, verification, premium, and streaming bot.</b>
</p>

<p align="center">
  <img src="https://raw.githubusercontent.com/DreamXBotz/Pics/main/dreamxbotz.jpg" alt="DreamxBotz Logo" width="220">
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.12+-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.12+">
  <img src="https://img.shields.io/badge/Docker-Ready-2496ED?style=for-the-badge&logo=docker&logoColor=white" alt="Docker Ready">
  <img src="https://img.shields.io/badge/Telegram-Bot-26A5E4?style=for-the-badge&logo=telegram&logoColor=white" alt="Telegram Bot">
</p>

<p align="center">
  <a href="https://t.me/Princess_V4_bot">
    <img src="https://img.shields.io/badge/Demo%20Bot-Click%20Here-blue?style=for-the-badge&logo=telegram" alt="Demo Bot">
  </a>
  <a href="https://t.me/Deendayal_Support_Group">
    <img src="https://img.shields.io/badge/Support%20Group-Join-blue?style=for-the-badge&logo=telegram" alt="Support Group">
  </a>
  <a href="LICENSE">
    <img src="https://img.shields.io/badge/License-AGPL--3.0-blue?style=for-the-badge" alt="License">
  </a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Auto%20Filter-Fast%20Search-ff69b4?style=flat-square" alt="Auto Filter">
  <img src="https://img.shields.io/badge/MongoDB-Database-47A248?style=flat-square&logo=mongodb&logoColor=white" alt="MongoDB">
  <img src="https://img.shields.io/badge/Streaming-Supported-orange?style=flat-square" alt="Streaming">
  <img src="https://img.shields.io/badge/Premium-Enabled-purple?style=flat-square" alt="Premium">
  <img src="https://img.shields.io/badge/Verification-3%20Step-red?style=flat-square" alt="Verification">
</p>

DreamxBotz is a Telegram auto-filter bot for indexing files from channels/groups, searching them quickly, and sharing files through bot commands. It supports MongoDB storage, group settings, force subscription, verification, premium users, streaming links, and admin tools.

> This project is intended for educational use. Use it responsibly and follow Telegram rules, hosting provider rules, and copyright laws.

<!-- > ## ⚠ <u>Under Maintenance</u> ⚠
> This repository is currently under maintenance. Please **DO NOT deploy** until further notice. -->

## Table Of Contents

- [Features](#features)
- [Requirements](#requirements)
- [Environment Variables](#required-environment-variables)
- [Deploy On Render](#deploy-on-render)
- [Deploy On Heroku](#deploy-on-heroku)
- [Deploy With Docker](#deploy-with-docker)
- [Local Setup](#local-setup)
- [Commands](#commands)
- [Troubleshooting](#troubleshooting)
- [Credits](#credits)

## Features

| Search & Indexing | Access Control | Admin Tools |
| --- | --- | --- |
| Fast auto-filter search | Force subscription | Broadcast tools |
| Auto file indexing | Request-to-join FSub | User ban/unban |
| Caption-based filtering | Three-step verification | Chat enable/disable |
| Trending search list | Premium user support | Maintenance mode |
| Movie/series search | PM search toggle | Logs and stats |

| Media & Streaming | Database | Customization |
| --- | --- | --- |
| Online streaming links | MongoDB support | Group settings menu |
| Fast download links | Multiple DB support | Custom captions |
| Telegraph media info | User/chat database | IMDb templates |
| TMDB movie metadata | Referral and premium data | Shortener settings |
| Auto-delete tools | Search analytics | Tutorial links |

## Requirements

- Python 3.12+
- MongoDB database URI
- Telegram bot token from [@BotFather](https://t.me/BotFather)
- Telegram `API_ID` and `API_HASH` from [my.telegram.org](https://my.telegram.org)
- A log channel where the bot is added as admin

## Quick Links

| Link | URL |
| --- | --- |
| Demo Bot | [Open on Telegram](https://t.me/Princess_V4_bot) |
| Support Group | [Join Support](https://t.me/Deendayal_Support_Group) |
| Telegram API | [my.telegram.org](https://my.telegram.org) |
| BotFather | [Create Bot](https://t.me/BotFather) |
| MongoDB Atlas | [Create Database](https://www.mongodb.com) |

## Required Environment Variables

| Variable | Required | Description |
| --- | --- | --- |
| `BOT_TOKEN` | Yes | Telegram bot token from BotFather |
| `API_ID` | Yes | Telegram API ID from my.telegram.org |
| `API_HASH` | Yes | Telegram API hash from my.telegram.org |
| `DATABASE_URI` | Yes | MongoDB connection URI |
| `LOG_CHANNEL` | Yes | Telegram log channel ID, usually starts with `-100` |
| `ADMINS` | Yes | Space-separated Telegram user IDs or usernames |
| `OWNER_ID` | Recommended | Owner Telegram user ID(s). Owner-only commands work only for these ids. If empty, the first id in `ADMINS` is the owner |
| `CHANNELS` | Recommended | Space-separated channel/group IDs for indexing |

## Common Optional Variables

| Variable | Default | Description |
| --- | --- | --- |
| `DATABASE_NAME` | `Cluster0` | MongoDB database name |
| `COLLECTION_NAME` | `dreamcinezone_files` | MongoDB collection name |
| `BIN_CHANNEL` | `-100` | Channel used for file/bin logs |
| `PREMIUM_LOGS` | `-100` | Premium activity log channel |
| `AUTH_CHANNELS` | `-100` | Force subscription channel IDs |
| `AUTH_REQ_CHANNELS` | `-100` | Request-to-join force subscription channel IDs |
| `REQST_CHANNEL_ID` | `-100` | Request channel ID |
| `SUPPORT_CHAT_ID` | `-100` | Support group ID |
| `SUPPORT_CHAT` | `https://t.me/` | Support group link |
| `FQDN` | Web bind address | Public domain for stream links |
| `PORT` | `8080` | Web server port |
| `HAS_SSL` | `True` | Use HTTPS in generated stream URLs |
| `NO_PORT` | `False` | Hide port in generated HTTP stream URLs |
| `STREAM_MODE` | `True` | Enable stream mode |
| `PREMIUM_STREAM_MODE` | `False` | Restrict stream mode to premium users |
| `MAINTENANCE` | `False` | Enable maintenance mode |
| `IS_VERIFY` | `False` | Enable verification system |
| `SHORTENER_API` | Empty | Shortener API key for verification links |
| `SHORTENER_WEBSITE` | Empty | Shortener domain |
| `TMDB_API_KEY` | Empty | TMDB API key for movie metadata |
| `TMDB_BEARER_TOKEN` | Empty | Optional TMDB bearer token |
| `IMGBB_API_KEY` | Empty | ImgBB key for the `/img` upload command (command is disabled if empty) |
| `FILE_DELETE_TIME` | `50` | Seconds a delivered file stays before auto-delete (multiple of 10). A reply under the file counts down 50s, 40s, 30s... |
| `PM_POST_FIRST` | `True` | In bot PM, the poster-style post card is sent first and the normal (old) search result below it. Set `False` to send only the old result |
| `RESULT_ARCHIVE_CHANNEL` | Empty | Private channel id (bot must be admin). Every post is saved here 30s before it auto-deletes |
| `POST_ARCHIVE_BEFORE` | `30` | Seconds before deletion at which the post is saved to `RESULT_ARCHIVE_CHANNEL` |
| `TELEGRAPH_ACCESS_TOKEN` | Empty | Optional Telegraph access token |

## Example `.env`

Copy `.env.example` to `.env` and fill your real values:

```bash
cp .env.example .env
```

```env
BOT_TOKEN=123456:your_bot_token
API_ID=123456
API_HASH=your_api_hash
DATABASE_URI=mongodb+srv://username:db-password@cluster.mongodb.net/
DATABASE_NAME=Cluster0
COLLECTION_NAME=dreamcinezone_files
ADMINS=123456789
OWNER_ID=123456789
CHANNELS=-1001234567890
LOG_CHANNEL=-1001234567890
BIN_CHANNEL=-1001234567890
FQDN=your-app-name.onrender.com
HAS_SSL=True
NO_PORT=True
```

## Deploy On Render

<p>
  <img src="https://img.shields.io/badge/Render-Docker%20Deploy-46E3B7?style=for-the-badge&logo=render&logoColor=white" alt="Render Deploy">
</p>

1. Fork or upload this repository to GitHub.
2. Create a new Render Web Service.
3. Select Docker as the runtime.
4. Add the required environment variables from the table above.
5. Deploy the service.

Render must receive a valid `PORT` environment variable or use the default `8080`. If stream links use your Render domain, set:

```env
FQDN=your-app-name.onrender.com
HAS_SSL=True
NO_PORT=True
```

## Deploy On Heroku

<p>
  <img src="https://img.shields.io/badge/Heroku-Supported-430098?style=for-the-badge&logo=heroku&logoColor=white" alt="Heroku Supported">
</p>

This repository includes `app.json`, `Procfile`, and `heroku.yml`, so it can also run on Heroku-style deployments.

1. Create a Heroku app.
2. Add the required environment variables.
3. Deploy this repository.
4. Ensure the worker or web process is enabled according to your hosting setup.

## Deploy With Docker

<p>
  <img src="https://img.shields.io/badge/Docker-Build%20%26%20Run-2496ED?style=for-the-badge&logo=docker&logoColor=white" alt="Docker Build">
</p>

Build the image:

```bash
docker build -t dreamxbotz .
```

Run the container:

```bash
docker run --env-file .env -p 8080:8080 dreamxbotz
```

For Docker Compose, `.env` is optional at compose-load time, but the bot still needs required variables from `.env` or your shell environment.

## Local Setup

Create and activate a virtual environment:

```bash
python -m venv .venv
.venv\Scripts\activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Create a `.env` file from the example and add your configuration:

```bash
cp .env.example .env
```

Then run:

```bash
python bot.py
```

## Commands

<p>
  <img src="https://img.shields.io/badge/User%20Commands-Available-2ea44f?style=flat-square" alt="User Commands">
  <img src="https://img.shields.io/badge/Admin%20Commands-Available-d73a49?style=flat-square" alt="Admin Commands">
</p>

### User Commands

| Command | Description |
| --- | --- |
| `/start` | Start the bot |
| `/settings` | Open group settings |
| `/id` | Get Telegram ID |
| `/info` | Get user information |
| `/imdb` | Search IMDb/movie details |
| `/search` | Search IMDb/movie details |
| `/movies` | Search movie titles in private chat |
| `/series` | Search series titles in private chat |
| `/plan` | View premium plans |
| `/myplan` | Check active premium plan |
| `/redeem` | Redeem a premium code |
| `/font` | Generate styled text in private chat |
| `/img` | Upload replied media to Telegraph |
| `/cup` | Upload replied media to Telegraph |
| `/telegraph` | Upload replied media to Telegraph |
| `/stickerid` | Get sticker file ID |
| `/alive` | Check bot status |
| `/ping` | Check bot response time |
| `/request` | Send a group request report |
| `#request` | Send a group request report |

### Group admin commands (group admins, in their own group)

| Command | Description |
| --- | --- |
| `/settings`, `/details`, `/reload` | Group settings, view settings, link group to PM |
| `/set_shortner`, `/set_shortner_2`, `/set_shortner_3` | Shorteners |
| `/set_tutorial`, `/set_tutorial_2`, `/set_tutorial_3` | Tutorial links |
| `/set_time`, `/set_time_2`, `/set_log_channel` | Verification gap / log channel |
| `/set_fsub`, `/remove_fsub`, `/set_caption`, `/set_template`, `/antispam` | Force-sub, caption, IMDb template, anti-spam |

### Bot admin commands (`ADMINS`) - `/admin_cmd`

`/stats` `/users` `/chats` `/ban` `/unban` `/banned` `/invite` `/verify` `/movie_update` `/pm_search` `/del_msg` `/delete` `/save` `/setskip`

Posting: `/post` `/bulkpost` `/autopost` `/postrange` `/ccopy` `/autoforward` `/repost` `/postsettings` `/filecaption` `/setfilecaption` `/resetfilecaption` `/setwatermark` `/setlinktext` `/setdivider` `/setbutton` `/removebutton` `/resetpostformat`

### Owner-only commands (`OWNER_ID`) - `/owner_cmd`

These work **only** for the owner. Admins and users cannot run them, they are not in their `/` menu and the bot stays silent if they try.

`/system` `/logs` `/restart` `/maintenance` `/commands` `/broadcast` `/grp_broadcast` `/send` `/add_premium` `/remove_premium` `/get_premium` `/premium_users` `/add_redeem` `/trial_reset` `/leave` `/disable` `/enable` `/deletefiles` `/deleteall` `/clean_groups` `/resetallgroup` `/clear_junk` `/junk_group` `/delreq`

### Who sees what in the `/` menu
Set automatically at every start (and with `/commands`): users see only the user commands, group admins also see the group commands, bot admins also see the admin commands, the owner sees everything.

Indexing is handled by forwarding channel messages or sending supported Telegram message links to the bot in private chat.

## Public Repo Safety

<p>
  <img src="https://img.shields.io/badge/Security-Keep%20Secrets%20Private-critical?style=for-the-badge" alt="Security">
</p>

- Do not commit `.env`, session files, logs, or virtual environments.
- Rotate any token or API key that was previously committed publicly.
- Keep real values only in your hosting provider environment variables.
- For public forks, avoid hardcoding shortener, TMDB, Telegraph, MongoDB, or Telegram credentials.

## Troubleshooting

### Bot does not start

Check that `BOT_TOKEN`, `API_ID`, `API_HASH`, `DATABASE_URI`, `LOG_CHANNEL`, and `ADMINS` are set correctly.

### MongoDB connection error

Check that `DATABASE_URI` is valid, the database user has access, and your hosting provider IP is allowed in MongoDB Atlas.

### Stream links are wrong

Set `FQDN`, `HAS_SSL`, and `NO_PORT` according to your hosting provider domain.

## License

This project is licensed under the [GNU AGPL-3.0](LICENSE) (the `LICENSE` file in this repository).

If you modify this bot and let other people use it over a network (a Telegram bot, the stream / download
web pages), AGPL section 13 requires you to offer those users the source code of your modified version.
Keep this license, the copyright notices and the credits below when you fork or redeploy it.

## Credits

Special thanks to:

- [⌯ Ꭺɴᴏɴʏᴍᴏᴜs | ×͜× |](https://t.me/BeingXAnonymous)
- [⌯ ᴢɪsʜᴀɴ | ×͜× |](https://t.me/IM_JISSHU)
- [⌯ ʙʜᴀʀᴀᴛʜ | ×͜× |](https://t.me/Bharath_boy)
- [Support Group](https://t.me/Deendayal_Support_Group)

Thanks to the DreamXBotz community and all contributors who worked on the original project and related modules.

## Auto-post old files into private channels (`/autopost`)

Sends the files that are already saved in the database into private channels:
every movie / every web-series season is one **collection** (all qualities), a **sticker**
is sent when a collection is finished, then the next one starts.
Movies go to the movie channel, web series to the series channel.

1. Add the bot as **admin** in both private channels.
2. `/autopost movie -100xxxxxxxxxx` and `/autopost series -100yyyyyyyyyy`
3. Reply to a sticker with `/autopost sticker`, then `/autopost test`
4. `/autopost scan` (one collection per season) or `/autopost scan show` (one per series)
5. `/autopost start 5` to try 5 collections, then `/autopost start` for everything.

`/autopost stop`, `/autopost status`, `/autopost delay 2`, `/autopost cover on|off`,
`/autopost retry`, `/autopost clear confirm`. Progress is saved after every file and a
restart continues automatically. Roughly 20 files a minute is Telegram's limit, so a very
large library takes days.

### Time window (schedule) for posting
`/autopost schedule 10:00 22:00` - posts go out only between these two times (India time).
Overnight works too (`/autopost schedule 22:00 06:00`). Outside the window the bot waits and
continues by itself. `/autopost schedule off` = any time. The same window is used by `/postrange`.

## Copy posts between two numbers into another channel (`/postrange`)
The number of a post is the last number of its link (`t.me/c/1234567890/456` -> 456).

`/postrange <from_channel_id> <to_channel_id> <start> <end> [delay]`
e.g. `/postrange -1001111111111 -1002222222222 100 450`

Copies every post from `start` to `end` (both included, no "Forwarded from" tag; deleted numbers
are skipped). The bot must be admin in both channels. `/postrange status | stop | resume |
clear confirm`. Progress is saved and a restart continues automatically.


## Copy old videos from one private channel to another (`/ccopy`)

Reads a **source** private channel that holds many mixed old / new videos, builds one
**collection** per movie / per web-series season (old and new copies of the same title end up
together), keeps only these six qualities - `480p HEVC`, `480p`, `720p HEVC`, `720p`,
`1080p HEVC`, `1080p` (each at most once, per episode for series) - and **copies** them to the
destination channel **without the "Forwarded from" tag**. After every finished collection a
**sticker** is sent below it.

1. Add the bot as **admin** in both channels (the source must allow forwarding/saving content).
2. `/ccopy from -100xxxxxxxxxx` (source) and `/ccopy to -100yyyyyyyyyy` (destination)
3. `/ccopy sticker`, then send the sticker you like (the bot detects it). Then `/ccopy test`
4. `/ccopy scan` (the end of the channel is detected automatically, or `/ccopy scan <last message id>`)
5. `/ccopy start 5` to try 5 collections, then `/ccopy start` for everything.

**Post card mode:** `/ccopy mode card` posts ONE post card per collection instead of the video files
(poster + title / audio / genres box + download links of the max 6 qualities, the same card the bot
shows in PM search), and the sticker below it. The picked files are saved in the bot database
(if not already there) so the links work. `/ccopy mode files` goes back to copying the videos.

**Changing the sticker any time:** `/ccopy sticker` (then send any sticker) - or reply to a sticker with
`/ccopy sticker` - `/ccopy sticker show` shows the current one - `/ccopy sticker off` removes it.
The new sticker is used from the next finished collection. If no `/ccopy` sticker is set, the
`/autopost` sticker is used.

Other commands: `/ccopy stop | status | mode files|card | delay 2 | caption on|off | retry | clear confirm`.
`caption off` copies the videos without their caption. Progress is saved after every file and a
restart continues automatically. Collections that contain none of the six qualities are skipped.

The time window (`/autopost schedule 10:00 22:00`, India time) is also used by `/ccopy` and `/postrange`.

## Group protection (`plugins/antispam.py`)
In every group where the bot is **admin** (rights: *Delete messages* and *Ban users*):
* a **bot added by a user is banned** at once (only the bot owners in `ADMINS` may add bots), and the "X added a bot" line is removed;
* a message is **deleted immediately** when it has a **link** (any website, `t.me` channel / group / invite link, hidden text links, link buttons), an **@username mention**, or is a **forwarded message** - also when the link is added later by editing;
* **not touched:** group admins / owner, the bot owners, anonymous admins, other bots, the linked channel's automatic forward, `@thisbot` and links to this bot, plus everything in `ANTISPAM_ALLOWED` (and the bot's own `GRP_LNK`, `UPDATE_CHNL_LNK`, `SUPPORT_CHAT`);
* group admins switch it per group: `/antispam on | off | status` (status also shows whether the bot has the needed admin rights);
* a deleted message is not searched / answered by the other plugins.

Note: Telegram itself treats file names such as `movie.mov` or `file.zip` as web links, so a member who types such a name is treated like a link sender. Add the exact text to `ANTISPAM_ALLOWED` if that is a problem.


## Auto copy new videos into 2 channels (`/autoforward`)
Live copy (no "Forwarded from" tag) of every **new** video of one private channel: **movies** go to the
movie channel, **web series** (a season marker such as S01, S01E02, Season 1 in the file name) go to the
series channel. The same movie / series rule as the bot's posts is used.

1. Add the bot as **admin** in all three channels.
2. `/autoforward from -100xxxxxxxxxx` (source), `/autoforward movie -100yyyyyyyyyy`, `/autoforward series -100zzzzzzzzzz`
3. `/autoforward on`

Other: `/autoforward off | status | delay 3 | caption on|off | retry | clear confirm`, and `movie off` / `series off` / `from off` to unset a channel.
New items are queued in MongoDB (`autoforward_queue`) and copied one by one, so a restart or RAM-guard
restart continues with whatever is still pending. Samples smaller than `MIN_FILE_SIZE_MB` are skipped;
a file with neither a name nor a caption cannot be classified and is skipped (counted in `/autoforward status`).
Old files: use `/ccopy`.

## Restart / RAM-guard restart
`/autopost`, `/bulkpost`, `/ccopy` (files and card mode), `/postrange` and `/autoforward` all continue by themselves after a restart, from the saved progress (never from the beginning). A limit given to `start N` is remembered. `/xxx stop` switches the job off for good (no resume).

## Order of the collections
`/autopost`, `/ccopy` and `/bulkpost` send one show / franchise after the other: S01 -> sticker -> S02 -> sticker -> S03 ...
(whatever year each season is from) and Spider Man -> Spider Man 2 -> Spider Man No Way Home (oldest first). Between two
shows / franchises the newest goes first. Names that do not start the same are joined by hand:
`/franchise add Fast Five, Furious 7, Fast X | Fast and Furious`, then stop the job and send `/autopost regroup`
(`/ccopy regroup`, `/bulkpost regroup`). `/franchise list` shows the joined names.

## Auto clean (PM and groups)
Every message the bot sends in a PM or a group is deleted after `AUTO_CLEAN_TIME` seconds (default 360 = 6 minutes):
replies of commands, filters / search results, errors. The older delete times (`DELETE_TIME`, `FILE_DELETE_TIME`, `/start` ...)
stay as they were. Channels are never touched. **Broadcasts** are the only messages that stay, for the time the owner sets:
`/bcdelete 30m`, `/bcdelete 2h`, `/bcdelete 1d`, `/bcdelete off` (never), `/bcdelete` shows the current time
(default `BROADCAST_DELETE_TIME`, 24 h). The messages users type in the bot PM are deleted after the same time (PM only, `CLEAN_USER_PM_MESSAGES=False` turns it off), so the PM stays empty.

## Status channel and sticker
`/statuschannel -100xxxxxxxxxx` sends the progress, finished and warning messages of the background jobs (`/autopost`, `/bulkpost`,
`/ccopy`, `/postrange`, `/autoforward`) to one private channel instead of the bot PM (the bot must be admin there; `/statuschannel off`
= back to the PM). After every finished post / collection the same sticker follows in all jobs (`/autopost sticker` or `/ccopy sticker`).

## Bot menu (Minato style)
`/start` shows the home page: How to use the bot, Settings, Others (Premium, Referral), View my files history. `/settings`, `/history`, `/premium`,
`/refer` and `/request` open the same pages in the PM. Per-user settings: First Priority (Size / Quality), File Delete (10, 30, 50, 90, 120 s),
Result Mode (BUTTON / TEXT). Banners: `assets/menu/*.jpg` (`python tools/make_menu_banners.py` makes them again); texts: top of `plugins/menu_ui.py`.


## Chat guard (owner approval)
The bot works only in groups / channels that the owner approved: see `plugins/chat_guard.py` and `FIXES.md` (v41). Owner commands: `/allowchat`, `/disallowchat`, `/allowedchats`, `/approveall`. Env: `CHAT_GUARD`, `CHAT_GUARD_AUTO_LEAVE`, `ALLOWED_CHATS`.

## Branded file name (NAME_BRAND_EMOJI / NAME_BRAND_TAG)
Every caption and post heading shows `🐉 Name (Year) 1080p ... 𝑫𝒓𝒂𝒈𝒐𝒏𝑭𝒊𝒓𝒆𝑾𝒐𝒓𝒍𝒅.mkv`. Set the two variables to change it, or leave them empty to switch it off.
If your `/setfilecaption` text already shows the tag (for example `{file_name} ➥ 𝑫𝒓𝒂𝒈𝒐𝒏𝑭𝒊𝒓𝒆𝑾𝒐𝒓𝒍𝒅`) the bot does not add it again, so it appears only once. 540p files are always shown and sorted as 480p.

## "View Audio, Subs & Story" button
Last row of every post card (live post, PM card, /bulkpost, /ccopy card) and on the last video of every /ccopy files and /autopost collection, just above the sticker. /autoforward adds it to the last video of a movie / season when that group is finished (right before the sticker). The result is cached in MongoDB (`extract_cache`).

## /autocycle - new files post by themselves
Every new file saved by the bot waits in an inbox. `/autocycle on` (once) posts the inbox every 4 hours: files to the MOVIE / SERIES private channels (button + sticker), then the card to RESULT_ARCHIVE_CHANNEL. A new quality or episode of a movie / season that already has a card is added to that card. `/sendto <channel> <name>` posts one movie to any channel; `/autopost new <channel>` sends every file that channel has not got yet. Start: set the channels, send the old files once with `/autopost scan` + `/autopost start`, then `/autocycle on`. All commands: `/autocycle help`.

