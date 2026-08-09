"""
Central location for all runtime data files.

When packaged as an exe the working directory may be read-only, so
everything is stored under %APPDATA%\\GTA5-scrobbler instead:
  config.json        settings
  fingerprints.pkl   fingerprint database (built by fingerprint.py)
  app.log            full log (every engine/GUI message + scrobbles)
  .env               Last.fm API key / secret / session key
"""

import datetime
import os
from pathlib import Path

APP_NAME = "GTA5-scrobbler"


def _get_data_dir():
    base = os.environ.get("APPDATA")

    if not base:
        base = str(Path.home())

    directory = Path(base) / APP_NAME

    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError:
        directory = Path.cwd()

    return directory


DATA_DIR = _get_data_dir()

CONFIG_FILE = DATA_DIR / "config.json"
DB_FILE = DATA_DIR / "fingerprints.pkl"
LOG_FILE = DATA_DIR / "app.log"
ENV_FILE = DATA_DIR / ".env"


def log_line(message):
    """
    Append one timestamped line to app.log (the full log).

    Used by the GUI for every log message and by scrobble.py for
    successful scrobbles, so app.log records everything.
    """
    try:
        with open(
            LOG_FILE,
            "a",
            encoding="utf-8",
        ) as f:
            f.write(
                f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S}"
                f" {message}\n"
            )
    except OSError:
        pass
