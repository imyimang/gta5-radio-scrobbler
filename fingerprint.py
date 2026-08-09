"""
Build the fingerprint database from your music folder.

Runs standalone (python fingerprint.py) or through the GUI. The
progress callback lets the GUI show a progress bar.
"""

import pickle
from pathlib import Path

import librosa
import numpy as np

from config import CONFIG
from paths import DB_FILE


# The music folder is user-chosen (Settings -> Music folder), not
# hardcoded. It is None until the user picks one.
_config_music_dir = CONFIG["paths"]["music_dir"].strip()

MUSIC_DIR = (
    Path(_config_music_dir)
    if _config_music_dir
    else None
)

SAMPLE_RATE = CONFIG["fingerprint"]["sample_rate"]
HOP_LENGTH = CONFIG["fingerprint"]["hop_length"]
N_MELS = CONFIG["fingerprint"]["n_mels"]
FMIN = CONFIG["fingerprint"]["fmin"]
FMAX = CONFIG["fingerprint"]["fmax"]
TRIM_TOP_DB = CONFIG["fingerprint"]["trim_top_db"]


def set_music_dir(path):
    """
    Point MUSIC_DIR at a new folder (used by the GUI Settings dialog
    after the user picks a different music folder).
    """
    global MUSIC_DIR

    MUSIC_DIR = Path(path)


def music_dir():
    """
    Return the configured music folder, or raise a clear error when the
    user has not chosen one yet.
    """
    if MUSIC_DIR is None:
        raise ValueError(
            "Music folder is not set. Choose it in "
            "Settings -> Music folder, or set "
            "paths.music_dir in config.json."
        )

    return MUSIC_DIR


def find_music_files():
    root = music_dir()

    files = []

    for ext in (
        "*.mp3",
        "*.wav",
        "*.flac",
        "*.m4a",
        "*.ogg",
    ):
        files.extend(root.glob("**/" + ext))

    files.sort()

    return files


def fingerprint_song(filename):
    y, sr = librosa.load(
        filename,
        sr=SAMPLE_RATE,
        mono=True,
    )

    # Trim leading/trailing silence
    y, _ = librosa.effects.trim(
        y,
        top_db=TRIM_TOP_DB,
    )

    # Mel spectrogram
    mel = librosa.feature.melspectrogram(
        y=y,
        sr=SAMPLE_RATE,
        n_fft=2048,
        hop_length=HOP_LENGTH,
        n_mels=N_MELS,
        fmin=FMIN,
        fmax=FMAX,
    )

    # Convert to dB
    mel_db = librosa.power_to_db(
        mel,
        ref=np.max,
    )

    # Per-frame raw energy (dB, ref = max of the whole song)
    # Used later to mask out silent frames during matching
    energy_db = librosa.power_to_db(
        np.mean(mel, axis=0),
        ref=np.max,
    )

    # Per time-frame normalization
    mel_db -= np.mean(
        mel_db,
        axis=0,
        keepdims=True,
    )

    std = np.std(
        mel_db,
        axis=0,
        keepdims=True,
    )

    std[std < 1e-6] = 1

    mel_db /= std

    return mel_db, energy_db


def build_database(on_progress=None):
    """
    Build the fingerprint database and write it to DB_FILE.

    on_progress(done, total, filename, status) is called for every song
    (status is one of "ok", "failed").

    Returns the database dict.
    """

    files = find_music_files()

    database = {}

    for index, filename in enumerate(files):
        try:
            mel_db, energy_db = fingerprint_song(filename)

            duration = (
                mel_db.shape[1]
                * HOP_LENGTH
                / SAMPLE_RATE
            )

            database[str(filename)] = {
                "fingerprint": mel_db,
                "energy": energy_db,
                "duration": duration,
            }

            if on_progress:
                on_progress(
                    index + 1,
                    len(files),
                    filename.name,
                    "ok",
                )

        except Exception as e:
            if on_progress:
                on_progress(
                    index + 1,
                    len(files),
                    filename.name,
                    f"failed: {type(e).__name__}: {e}",
                )

    with open(DB_FILE, "wb") as f:
        pickle.dump(
            database,
            f,
            protocol=pickle.HIGHEST_PROTOCOL,
        )

    return database
