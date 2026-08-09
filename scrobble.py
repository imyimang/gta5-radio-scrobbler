"""
Last.fm scrobbler: reads metadata from the song file / ID3 tags
and scrobbles once the playback conditions are met.

Last.fm rules:
- Only tracks > 30 seconds can be scrobbled
- Played >= 50% of the track, or >= 4 minutes (whichever is earlier)

When Last.fm is not configured, Scrobbler.enabled is False and
the engine skips all scrobbling.
"""

import re
import time
from pathlib import Path

import pylast
from mutagen import File as MutagenFile

import lastfm_config
from paths import log_line


def log_scrobble(artist, title):
    """
    Append a successful scrobble to app.log.
    """
    log_line(f"Scrobbled: {artist} - {title}")


# Removes " (Remastered 2009)", " (2009 Remastered)", " (Remaster)" etc.
_REMASTER = re.compile(
    r"\s*\((?:(?:19|20)\d{2}\s+)?"
    r"Remaster(?:ed)?(?:\s+(?:19|20)\d{2})?\)\s*$",
    re.IGNORECASE,
)

# Removes a trailing YouTube id, e.g. " [0Sr0efOe8yk]"
_ID_BRACKET = re.compile(
    r"\s*\[[0-9A-Za-z_-]{8,}\]\s*$",
)


def clean_title(text):
    """
    Remove trailing release info from a title, e.g. " (Remastered 2009)".
    """
    for _ in range(3):
        new = _REMASTER.sub("", text)
        if new == text:
            break
        text = new
    return text.strip()


def get_metadata(filename):
    """
    Read song info from ID3 tags, falling back to the filename
    when tags are missing.

    Returns:
    {
        "artist": ...,
        "title": ...,
        "album": ...,
        "duration": seconds,
    }
    """
    path = Path(filename)

    meta = {
        "artist": None,
        "title": None,
        "album": None,
        "duration": None,
    }

    try:
        audio = MutagenFile(path, easy=True)

        if audio is not None:
            if "artist" in audio:
                meta["artist"] = audio["artist"][0]
            if "title" in audio:
                meta["title"] = audio["title"][0]
            if "album" in audio:
                meta["album"] = audio["album"][0]

            length = getattr(
                audio.info,
                "length",
                None,
            )

            if length:
                meta["duration"] = int(round(length))

    except Exception:
        pass

    # Parse the filename when tags are incomplete
    if not meta["title"] or not meta["artist"]:
        stem = path.stem
        stem = _ID_BRACKET.sub("", stem)

        parts = re.split(
            r"\s+-\s+",
            stem,
            maxsplit=1,
        )

        if not meta["artist"] and len(parts) == 2:
            meta["artist"] = parts[0].strip()
            meta["title"] = parts[1].strip()

        if not meta["title"]:
            meta["title"] = stem.strip()

    if meta["title"]:
        meta["title"] = clean_title(meta["title"])

    if meta["artist"]:
        meta["artist"] = clean_title(meta["artist"])

    return meta


class Scrobbler:
    def __init__(self, log=None, on_scrobbled=None):
        self._cache = {}
        self._network = None
        self.log = log or print
        self.on_scrobbled = (
            on_scrobbled or (lambda title: None)
        )

        key = lastfm_config.LASTFM_API_KEY
        secret = lastfm_config.LASTFM_API_SECRET
        session = lastfm_config.LASTFM_SESSION_KEY

        if key and session:
            try:
                network = pylast.LastFMNetwork(
                    api_key=key,
                    api_secret=secret,
                    session_key=session,
                )

                network.get_authenticated_user()

                self._network = network

                self.log(
                    "[Last.fm] Connected, scrobbling enabled."
                )

            except Exception as e:
                self.log(
                    f"[Last.fm] Connection failed, "
                    f"scrobbling disabled: {e}"
                )

    @property
    def enabled(self):
        return self._network is not None

    def metadata(self, filename):
        if filename not in self._cache:
            self._cache[filename] = (
                get_metadata(filename)
            )
        return self._cache[filename]

    def update_now_playing(self, filename):
        """
        Tell Last.fm which track is currently playing.
        """
        if not self.enabled:
            return

        meta = self.metadata(filename)

        if not meta["artist"] or not meta["title"]:
            return

        try:
            self._network.update_now_playing(
                meta["artist"],
                meta["title"],
                album=meta["album"],
                duration=meta["duration"],
            )
        except Exception as e:
            self.log(
                f"[Last.fm] now_playing failed: {e}"
            )

    def try_scrobble(self, filename, played_seconds):
        """
        Decide whether to scrobble according to the Last.fm rules.

        played_seconds: estimated seconds already played
        """
        if not self.enabled:
            return False

        meta = self.metadata(filename)
        artist = meta["artist"]
        title = meta["title"]

        if not artist or not title:
            self.log(
                f"[Last.fm] Missing artist/title, "
                f"cannot scrobble: {filename}"
            )
            return False

        duration = meta["duration"]

        if duration and duration < 30:
            self.log(
                f"[Last.fm] {title} is under 30 "
                f"seconds, not scrobbling"
            )
            return False

        threshold = min(
            duration * 0.5 if duration else 240,
            240,
        )

        if played_seconds < threshold:
            self.log(
                f"[Last.fm] {title} played "
                f"{played_seconds:.0f}s, "
                f"below {threshold:.0f}s, "
                f"not scrobbling"
            )
            return False

        timestamp = (
            int(time.time())
            - int(played_seconds)
        )

        try:
            self._network.scrobble(
                artist=artist,
                title=title,
                timestamp=timestamp,
                album=meta["album"],
                duration=duration,
            )

            self.log(
                f"[Last.fm] Scrobbled: "
                f"{artist} - {title}"
            )

            log_scrobble(artist, title)

            self.on_scrobbled(
                f"{artist} - {title}"
            )

            return True

        except Exception as e:
            self.log(
                f"[Last.fm] scrobble failed: {e}"
            )
            return False
