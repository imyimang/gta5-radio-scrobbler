# GTA5 Radio Scrobbler

Real-time music detector for the GTA5 in-game radio that automatically scrobbles
the songs to Last.fm.

Captures the audio of the GTA5 process only and identifies songs by matching audio fingerprints generated from your GTA5 User Music folder.

Because it reads the game's audio stream directly, the radio can be scrobbled with no speakers or headphones needed.

## Demo

[![Watch the demo](https://img.youtube.com/vi/Ho8KkRYwwsk/hqdefault.jpg)](https://youtu.be/Ho8KkRYwwsk)

## Requirements

- **OS:** Windows 10 (version 2004+) / Windows 11
- **Game:** GTA5 / GTA5 Enhanced running with audio output
- **Music:** A local folder containing the tracks played on GTA5 radio.
  Make sure the files carry ID3 tags (artist/title) — scrobbling uses the
  tags; when they are missing the file name is parsed instead (expects a
  `Artist - Title` layout) and untagged tracks may fail to scrobble.

## Install & Run

**From source (recommended):**

```powershell
pip install -r requirements.txt
python gui.py
```

**Packaged release :** 
1. Download the latest `.zip` from Releases.
2. Extract the archive.
3. Run `GTA5Scrobbler.exe` (No Python installation required).

## Usage

1. **Last.fm** — create an API account at
   <https://www.last.fm/api/account/create>, enter key/secret in Settings,
   then click **Authorize Last.fm** and confirm in the browser.
2. **Music folder** — Settings -> Music folder -> Browse..., pick your music
   folder.
3. **Build Database** — click **Build Database** in the GUI. This scans your
   music folder and fingerprints every song.
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

Both rules rely on the track duration from the file's ID3 tags — files
without tags (or with a wrong tag duration) may fail these checks and never scrobble.

## Notes

- If you change your music folder or add songs, click **Build Database** again.
- The `fingerprint` / `matching` settings in `config.json` affect both
  fingerprinting and matching — after changing them you must rebuild the
  database.

## Project structure

| File | Purpose |
|---|---|
| `gui.py` | Entry point (`python gui.py`). Tkinter GUI: setup/authorization, settings, progress bar, live log. |
| `engine.py` | Real-time loop: captures GTA5 audio, matches it, decides on the song, scrobbles. Runs in a background thread. |
| `recognize.py` | Fingerprint matching core: resamples 48kHz → 11025Hz, builds query fingerprints, multi-window voting. |
| `fingerprint.py` | Builds the `fingerprints.pkl` database by fingerprinting every song in the music folder. |
| `scrobble.py` | Reads ID3 tags (falls back to the file name), applies Last.fm rules, sends now-playing / scrobbles. |
| `auth.py` | Last.fm authorization flow (API key/secret → browser → session key). |
| `config.py` | Loads `config.json` (non-sensitive settings) with built-in defaults. |
| `lastfm_config.py` | Loads sensitive settings from `.env` (API key / secret / session key). |
| `paths.py` | Central paths for the data files and the `log_line()` helper. |
