# Result-card test checklist (group, `result_card` ON)

Send each search in a test group and compare. Buttons depend on the files saved in your database.

| # | Search | Expected |
|---|---|---|
| 1 | `drishyam` | One button per movie, oldest first: `🎬 Drishyam (2013)`, `🎬 Drishyam (2014) • Telugu`, `🎬 Drishyam (2014) • Kannada`, `🎬 Drishyam (2015)`, `🎬 Drishyam 2 (2021) • Malayalam` ... (language only when title + year repeat) |
| 2 | `dhoom` | `🎬 Dhoom (2004)`, `🎬 Dhoom 2 (2006)`, `🎬 Dhoom 3 (2013)` |
| 3 | `game of thrones` | `🎬 Game Of Thrones •` (complete pack, if any), `🎬 Game Of Thrones S01 •`, `S02 •` ... one per season, no year, no language |
| 4 | `game of thrones s07e07` (also `season 7 episode 7`, `s07 e07`) | ONE button: `🎬 Game Of Thrones S07 •` |
| 5 | `fast & furious` | Card title `Fast & Furious`; buttons for every title that has "fast" and "furious" (`Fast.and.Furious`, `Fast.&.Furious`, `The Fast and the Furious` ...) |
| 6 | `spider man` | All Spider-Man movies (`Spider-Man` / `Spiderman` merged), up to 50 buttons |

## For every button click
1. The bot opens (verification / force-sub as before).
2. The **poster-style movie card post** arrives with ALL qualities of that movie (or that whole season).
3. A single-episode search never gives one episode only: it gives the whole season.
4. A search that finds only ONE movie still shows the old `📩 Download File ↗️` button.

## If something is wrong
* Same movie in two buttons -> note both file names (spelling / year differs).
* Two movies in one button -> note the file names (same title + year, no language in the name).
* Missing button -> check `CARD_MAX_MOVIE_BTNS` (50) and `CARD_SEARCH_LIMIT` (400).

## Extra search checks (after the search fixes)
| Search | Expected |
|---|---|
| `dhoom 2` | Only Dhoom 2 (not Dhoom 2004 / Dhoom 3) |
| `kgf 2` | Only KGF Chapter 2 |
| `drishyam (2015)` | Drishyam 2015 files (brackets are ignored) |
| `ocean's eleven` | Ocean's Eleven files (an apostrophe works) |
| `man superman` | Nothing from `Batman v Superman` |
