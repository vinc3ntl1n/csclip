# csclip

Watches your FACEIT account and turns every new CS2 match into:

- **Highlight clips**: 3k / 4k / ace rounds and won 1v2+ clutches. Quiet time between kills is cut out.
- **montage.mp4**: all of a match's clips joined together.
- **full_pov.mp4**: your whole match from your own eyes.

```
videos/2026-11-13_2141_de_mirage_6de46e85/
  01_r02_3k.mp4
  02_r06_1v2.mp4
  03_r30_4k_1v3.mp4
  montage.mp4
  full_pov.mp4
  highlights.json      <- what was found, with demo ticks
```

## How it works

1. **Find matches**: the FACEIT Data API lists your latest finished matches.
2. **Get the demo**: downloaded with the FACEIT Downloads API, or picked up from your Downloads folder (see below).
3. **Find highlights**: the demo is parsed with [demoparser2](https://github.com/LaihoE/demoparser) to find multi-kills,
   clutches and the rounds you were alive in.
4. **Record**: [CS Demo Manager](https://github.com/akiver/cs-demo-manager) (open source, MIT) opens CS2 with
   [HLAE](https://github.com/advancedfx/advancedfx), plays the demo from your POV and encodes MP4s with FFmpeg.
   CS2 opens and closes by itself.

## Requirements

Recording needs CS2 to play the demo, so it has to run on a **Windows PC with CS2**, usually the one you play on.
On macOS you can still run it with `render = false` to get the highlight list.

- CS2 installed through Steam, with Steam running.
- [CS Demo Manager](https://cs-demo-manager.com) 3.20 or newer. Open it once and finish its first-run database
  setup, because the CLI uses the same database. HLAE and FFmpeg are downloaded by CSDM automatically.
- Python 3.11+ from python.org (its installer includes Tkinter, which the app window needs).

## Setup (Windows)

Get the code with either option:

- **No git:** open https://github.com/vinc3ntl1n/csclip while logged in, then **Code → Download ZIP**, and unzip it.
- **With [Git for Windows](https://git-scm.com/download/win):** `git clone https://github.com/vinc3ntl1n/csclip`.
  Run `git pull` in the folder later to get updates.

Then open PowerShell in that folder (click File Explorer's address bar, type `powershell`, press Enter):

```powershell
py -m venv .venv
.venv\Scripts\pip install -e .
copy config.example.toml config.toml
notepad config.toml
```

In `config.toml`, fill in `api_key` and `nickname`, then run `.venv\Scripts\faceit-highlights check`.

### FACEIT access

- **Data API key** (free, instant): [developers.faceit.com](https://developers.faceit.com) → App Studio → create an
  app → API Keys → *Server side*. Put it in `api_key` along with your `nickname`.
- **Demo downloads**: FACEIT made demo files private. A signed link from the
  [Downloads API](https://docs.faceit.com/getting-started/Guides/download-api) is now the only way to download one
  through the API, and access needs an application ([form](https://fce.gg/downloads-api-application), FACEIT says
  about 30 days).
  - **Once you have a token**, put it in `download_token`. Everything is then hands-off.
  - **Until then**, each new match page opens in your normal browser. You click **Watch demo** and the demo is
    picked up from `inbox_dir` (your Downloads folder by default). Demo files you download yourself are processed
    the same way. The download can't be automated through the website: FACEIT shows a Cloudflare "verify you
    are human" check to automated browsers.

### The app (one button)

```powershell
faceit-highlights app
```

Click **Get my new matches**. It then:

1. checks FACEIT for matches it hasn't done yet,
2. opens each one's page in your browser (until you have a download token). Click **Watch demo** on each,
3. waits up to 10 minutes for the downloads, then finds the highlights and records the videos.

**Open videos folder** opens the folder with your clips.

To get a desktop button, right-click the desktop → New → Shortcut and enter
`C:\path\to\faceit-highlights\.venv\Scripts\faceit-highlights-app.exe`. It opens the window without a console.

### Command line

```powershell
faceit-highlights check     # tests the API key, finds CS Demo Manager and FFmpeg
faceit-highlights run       # process new matches once
faceit-highlights watch     # keep checking every poll_minutes
faceit-highlights process C:\path\to\demo.dem.zst   # any single demo
faceit-highlights process demo.dem --steam-id 7656119... --no-render   # just list the highlights
```

To start it with Windows, add `C:\path\to\faceit-highlights\.venv\Scripts\faceit-highlights.exe watch` to Task
Scheduler, with "Start in" set to this folder.

While a video is recording CS2 is open, so don't start a game until it closes. Already processed matches are tracked
in `data/state.json`, so each match is only recorded once. A match that fails is retried on the next two checks.

## Full POV size and time

| Setting | Footage per match | File size (rough) |
|---|---|---|
| `mode = "alive"` (default): each round until you die | ~20-30 min | ~1-2 GB at 1080p60 |
| `mode = "full"`: whole match, unbroken | ~40-55 min | ~2.5-4 GB at 1080p60 |

These are estimates for x264 at `crf = 23`. CS2 footage compresses differently depending on the map and action,
so check your first file. To halve the size, use `framerate = 30`, `crf = 26`, or `width = 1280` / `height = 720`.
Recording runs at about real time, so a full POV keeps the PC busy for about as long as the footage.
Highlight clips are only 10-40 MB each.

## Tuning highlights

All in `[highlights]` in `config.toml`: `min_kills`, `min_clutch_enemies`, `special_kills` (`"noscope"`,
`"wallbang"`, `"smoke"`, `"blind"`, `"air"`), padding (`seconds_before` / `seconds_after`), and
`max_quiet_seconds`, which sets how long a gap between kills can be before it gets cut.

## Development

```sh
pip install -e '.[dev]'
pytest
CS2_TEST_DEMO=path/to/demo.dem CS2_TEST_STEAMID=7656119... pytest   # also run against a real demo
```
