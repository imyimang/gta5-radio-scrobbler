"""
Load config.json (non-sensitive settings).

Missing keys are filled with built-in defaults, so config.json only
needs to contain the keys you want to override. Sensitive data
(Last.fm API keys etc.) does not belong here; put it in .env
(see lastfm_config.py).
"""

import json

from paths import CONFIG_FILE

DEFAULTS = {
    "paths": {
        # Not set by default; the user picks it in Settings -> Music folder.
        "music_dir": "",
        "fingerprint_database": "fingerprints.pkl",
        "log_file": "app.log",
    },
    "gta5_process_names": [
        "GTA5_Enhanced.exe",
        "GTA5.exe",
    ],
    "capture": {
        "buffer_seconds": 15,
        "detect_interval": 5.0,
        # MIN_BUFFER = QUERY_WINDOW + this value
        "min_buffer_extra_seconds": 3.0,
        "silence_rms": 0.015,
    },
    "detection": {
        "confirm_count": 5,
        "now_playing_count": 2,
        "min_votes_fraction": 0.6,
        "min_score": 0.62,
        "min_score_margin": 0.03,
        "position_tolerance": 6.0,
        "top_results": 5,
    },
    "fingerprint": {
        "sample_rate": 11025,
        "hop_length": 512,
        "n_mels": 64,
        "fmin": 50,
        "fmax": 5000,
        "trim_top_db": 40,
    },
    "matching": {
        "query_window_seconds": 6.0,
        "query_hop_seconds": 3.0,
        "energy_fraction": 0.15,
        "min_active_fraction": 0.5,
        "search_step": 2,
    },
}


def _deep_merge(defaults, user):
    """Override only the keys provided by the user; keep defaults for the rest."""
    merged = dict(defaults)

    for key, value in user.items():
        if isinstance(value, dict) and isinstance(
            defaults.get(key), dict
        ):
            merged[key] = _deep_merge(
                defaults[key],
                value,
            )
        else:
            merged[key] = value

    return merged


def _load():
    if CONFIG_FILE.exists():
        try:
            user = json.loads(
                CONFIG_FILE.read_text(
                    encoding="utf-8"
                )
            )
        except (json.JSONDecodeError, OSError) as e:
            print(
                f"[config] Failed to read {CONFIG_FILE}: {e}"
            )
            user = {}
    else:
        user = {}

    return _deep_merge(DEFAULTS, user)
CONFIG = _load()


def save(updates):
    """
    Persist user settings (e.g. paths.music_dir) to config.json.

    updates is a nested dict of keys to override; it is deep-merged
    into the current CONFIG so unrelated keys are preserved. The
    in-memory CONFIG is updated too.
    """
    import json

    merged = _deep_merge(CONFIG, updates)
    CONFIG.clear()
    CONFIG.update(merged)

    try:
        CONFIG_FILE.write_text(
            json.dumps(merged, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError as e:
        print(f"[config] Failed to write {CONFIG_FILE}: {e}")
        return False

    return True
