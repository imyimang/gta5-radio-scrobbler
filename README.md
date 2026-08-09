# GTA5 Radio Scrobbler

Real-time music detector for the GTA5 in-game radio that automatically scrobbles
the songs to Last.fm. Captures the audio of **the GTA5 process only** (nothing
else gets mixed in) and identifies songs by matching audio fingerprints against
your local music folder.

> **Note:** This tool works by matching the in-game audio with fingerprints generated 
> from your local music collection.

## Demo

[![Watch the demo](https://img.youtube.com/vi/Ho8KkRYwwsk/hqdefault.jpg)](https://youtu.be/Ho8KkRYwwsk)

## Features

- **Windows Only:** Captures GTA5 process audio directly (WASAPI process loopback)
- Fingerprint matching with multi-window voting to reduce false positives
- Updates Last.fm "now playing" within ~10 seconds, auto-scrobbles after threshold
- Works offline/without Last.fm (scrobbling is simply skipped)

## Requirements

- **OS:** Windows 10 (version 2004+) / Windows 11
- **Game:** GTA5 / GTA5 Enhanced running with audio output
- **Music:** A local folder containing the tracks played on GTA5 radio

## Install & Run

**Packaged release (recommended):** 
1. Download the latest `.zip` from Releases.
2. Extract the archive.
3. Run `GTA5Scrobbler.exe` (No Python installation required).

**From source (developers):**

```powershell
pip install -r requirements.txt
python gui.py
```

## Usage

1. **Music folder** — Settings -> Music folder -> Browse..., pick your music
   folder.
2. **Build Database** — click **Build Database** in the GUI. This scans your
   music folder and fingerprints every song.
3. **Last.fm** — create an API account at
   <https://www.last.fm/api/account/create>, enter key/secret in Settings,
   then click **Authorize Last.fm** and confirm in the browser.
4. **Start** — click **Start**, launch GTA5 if it isn't running, and the app
   detects and scrobbles your in-game radio.

> **Tip:** in GTA5's Audio settings, turn the **sound effects volume down** and
> the **music (radio) volume up** for cleaner capture and more accurate
> detection.

All data lives in `%APPDATA%\GTA5-scrobbler`:

| File | Purpose |
|---|---|
| `config.json` | Settings — only the music folder is set in the GUI, everything else is edited here |
| `.env` | Last.fm API key / secret / session key — never share it |
| `fingerprints.pkl` | Fingerprint database (rebuild after changing music) |
| `app.log` | Full log including successful scrobbles |

## Scrobble rules (Last.fm)

A track is scrobbled once it is **> 30 seconds** and played at least
**50% of its length or 4 minutes** (whichever comes first). The scrobble
timestamp is the track start time.

## Notes

- If you change your music folder or add songs, click **Build Database** again.
- The `fingerprint` / `matching` settings in `config.json` affect both
  fingerprinting and matching — after changing them you must rebuild the
  database.
