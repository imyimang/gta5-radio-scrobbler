"""
Shared audio fingerprinting and matching core.

proctap provides raw audio as float32 stereo 48000Hz, while the
fingerprint database is built at 11025Hz mono.

Matching improvements:
- 48kHz -> 11025Hz resample (fixed the original sr mismatch)
- Per-frame energy mask (drops silent/pure-noise frames)
- Vectorized sliding search (prefix-sum masked mean)
- Multi-window voting (splits the buffer into several query windows)
"""

import numpy as np
import librosa

from config import CONFIG

# proctap raw format (fixed, does not change with config)
CAPTURE_SAMPLE_RATE = 48000

SAMPLE_RATE = CONFIG["fingerprint"]["sample_rate"]
HOP_LENGTH = CONFIG["fingerprint"]["hop_length"]
N_MELS = CONFIG["fingerprint"]["n_mels"]
FMIN = CONFIG["fingerprint"]["fmin"]
FMAX = CONFIG["fingerprint"]["fmax"]

QUERY_WINDOW_SECONDS = CONFIG["matching"]["query_window_seconds"]
QUERY_HOP_SECONDS = CONFIG["matching"]["query_hop_seconds"]
ENERGY_FRACTION = CONFIG["matching"]["energy_fraction"]
MIN_ACTIVE_FRACTION = CONFIG["matching"]["min_active_fraction"]
SEARCH_STEP = CONFIG["matching"]["search_step"]


def prepare(audio):
    """
    proctap raw audio (float32 stereo 48kHz)
    -> mono 11025Hz
    """
    if audio.ndim == 2:
        mono = np.mean(audio, axis=1)
    else:
        mono = audio

    return librosa.resample(
        mono,
        orig_sr=CAPTURE_SAMPLE_RATE,
        target_sr=SAMPLE_RATE,
    )


def make_fingerprint(y):
    """
    11025Hz mono -> (normalized mel_db, per-frame energy_db)

    mel_db is per-frame normalized; energy_db is the raw power
    in dB (ref = max of the whole segment) and is used to mask
    out silent frames during matching.
    """
    mel = librosa.feature.melspectrogram(
        y=y,
        sr=SAMPLE_RATE,
        n_fft=2048,
        hop_length=HOP_LENGTH,
        n_mels=N_MELS,
        fmin=FMIN,
        fmax=FMAX,
    )

    mel_db = librosa.power_to_db(mel, ref=np.max)
    energy_db = librosa.power_to_db(
        np.mean(mel, axis=0),
        ref=np.max,
    )

    mel_db -= np.mean(mel_db, axis=0, keepdims=True)

    std = np.std(mel_db, axis=0, keepdims=True)
    std[std < 1e-6] = 1
    mel_db /= std

    return mel_db, energy_db


def energy_floor(energy_db, fraction=ENERGY_FRACTION):
    """Frames below this energy are treated as silence."""
    return float(np.quantile(energy_db, fraction))


def find_best_match(
    reference,
    query,
    ref_energy,
    query_energy,
    ref_floor,
    query_floor,
):
    """
    Find the position in a whole song that best matches the query.

    reference / query: normalized mel fingerprint (64, n) / (64, q)
    Returns (score, position_frame).

    Matching keeps time alignment: query frame i only matches ref
    frame p+i (the diagonal), not a bag-of-frames full pairing. A
    single O(q*n) diagonal accumulation replaces a per-frame Python
    loop.
    """
    n = reference.shape[1]
    q = query.shape[1]

    if n < q:
        return -1.0, 0

    query_active = query_energy > query_floor
    ref_active = ref_energy > ref_floor

    n_query_active = int(np.count_nonzero(query_active))

    if n_query_active < MIN_ACTIVE_FRACTION * q:
        return -1.0, 0

    ref_hat = reference / np.maximum(
        np.linalg.norm(reference, axis=0),
        1e-8,
    )

    query_hat = query / np.maximum(
        np.linalg.norm(query, axis=0),
        1e-8,
    )

    # (q, n) pairwise cosine matrix
    cosine = query_hat.T @ ref_hat

    span = n - q + 1

    # Diagonal accumulation: num = sum of cos(query_i, ref_p+i),
    # only counting frames with energy on both sides
    r_active_float = ref_active.astype(np.float64)

    num_acc = np.zeros(span)
    den_acc = np.zeros(span)

    for i in range(q):
        if query_active[i]:
            row_window = cosine[i, i:i + span]
            ref_window = r_active_float[i:i + span]
            num_acc += row_window * ref_window
            den_acc += ref_window

    min_active = MIN_ACTIVE_FRACTION * q

    best_score = -1.0
    best_position = 0

    for position in range(
        0,
        span,
        SEARCH_STEP,
    ):
        denominator = den_acc[position]

        if denominator < min_active:
            continue

        score = num_acc[position] / denominator

        if score > best_score:
            best_score = score
            best_position = position

    return float(best_score), best_position


def make_query_windows(
    audio,
    window_sec=QUERY_WINDOW_SECONDS,
    hop_sec=QUERY_HOP_SECONDS,
):
    """
    Cut the whole audio into several overlapping query windows.
    The last window is always aligned to the tail (newest audio).
    """
    total = len(audio)
    window = int(window_sec * SAMPLE_RATE)
    hop = int(hop_sec * SAMPLE_RATE)

    if total < window:
        return []

    starts = list(range(
        0,
        total - window + 1,
        hop,
    ))

    if starts and starts[-1] != total - window:
        starts.append(total - window)

    return [
        audio[s:s + window]
        for s in starts
    ]


class Matcher:
    def __init__(self, database):
        self.database = database
        self.floors = {
            filename: energy_floor(data["energy"])
            for filename, data in database.items()
        }

    def match(self, audio):
        """
        audio: 11025Hz mono float32

        Returns:
        [
            {
                "filename": ...,
                "votes":   how many windows voted for this song,
                "score":   best window score for this song,
                "position": median window position (seconds)
            }
        ]
        Sorted by (votes, score).
        """
        windows = make_query_windows(audio)

        if not windows:
            return []

        query_data = [
            make_fingerprint(w)
            for w in windows
        ]

        query_floors = [
            energy_floor(energy)
            for _, energy in query_data
        ]

        aggregated = {}

        for (query, query_energy), query_floor in zip(
            query_data,
            query_floors,
        ):
            best_filename = None
            best_score = -1.0
            best_position = 0

            for filename, data in self.database.items():
                score, position = find_best_match(
                    data["fingerprint"],
                    query,
                    data["energy"],
                    query_energy,
                    self.floors[filename],
                    query_floor,
                )

                if score > best_score:
                    best_score = score
                    best_position = position
                    best_filename = filename

            if best_filename is None:
                continue

            entry = aggregated.setdefault(
                best_filename,
                {
                    "votes": 0,
                    "positions": [],
                    "score": -1.0,
                },
            )

            entry["votes"] += 1
            entry["positions"].append(best_position)
            entry["score"] = max(
                entry["score"],
                best_score,
            )

        results = []

        for filename, entry in aggregated.items():
            results.append({
                "filename": filename,
                "votes": entry["votes"],
                "score": entry["score"],
                "position": float(
                    np.median(entry["positions"])
                    * HOP_LENGTH
                    / SAMPLE_RATE
                ),
            })

        results.sort(
            key=lambda x: (x["votes"], x["score"]),
            reverse=True,
        )

        return results
