"""
Last.fm settings: reads sensitive data from .env.

API key / secret / session key are sensitive; they do not belong in
config.json and must never be committed. Put them in .env:

    LASTFM_API_KEY=your_key
    LASTFM_API_SECRET=your_secret
    LASTFM_SESSION_KEY=your_session_key

The .env file lives in the data directory (%APPDATA%\\GTA5-scrobbler)
and is written by the GUI authorization flow. If the same key also
exists as a system environment variable, the environment variable
takes precedence.

reload() recomputes everything from scratch each time, so saving new
credentials is picked up immediately without restarting the app.
"""

import os
from pathlib import Path

from paths import ENV_FILE

ENV_FILES = [ENV_FILE]

_SETTING_KEYS = (
    "LASTFM_API_KEY",
    "LASTFM_API_SECRET",
    "LASTFM_SESSION_KEY",
)

LASTFM_API_KEY = ""
LASTFM_API_SECRET = ""
LASTFM_SESSION_KEY = ""


def _parse_env_file(path):
    """Read KEY=value pairs from a .env file (values kept raw)."""

    values = {}

    if not path.exists():
        return values

    for raw in path.read_text(
        encoding="utf-8"
    ).splitlines():
        line = raw.strip()

        if (
            not line
            or line.startswith("#")
            or "=" not in line
        ):
            continue

        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("\"'")

        if key:
            values[key] = value

    return values


def reload():
    """
    Recompute the settings from the .env files and system environment.

    Never mutates os.environ, so a reload can actually pick up new
    values (the old implementation cached values in os.environ and
    the "environment variables take precedence" rule then blocked
    every later reload from updating them).
    """

    global LASTFM_API_KEY
    global LASTFM_API_SECRET
    global LASTFM_SESSION_KEY

    values = {}

    # Later entries in ENV_FILES override earlier ones, so ENV_FILES[0]
    # (the data directory) wins over the development fallback.
    for env_file in reversed(ENV_FILES):
        values.update(_parse_env_file(env_file))

    # Real system environment variables take precedence over .env files.
    for key in _SETTING_KEYS:
        system_value = os.environ.get(key)

        if system_value:
            values[key] = system_value

    LASTFM_API_KEY = values.get("LASTFM_API_KEY", "")
    LASTFM_API_SECRET = values.get("LASTFM_API_SECRET", "")
    LASTFM_SESSION_KEY = values.get("LASTFM_SESSION_KEY", "")


reload()
