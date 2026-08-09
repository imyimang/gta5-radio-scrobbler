"""
GTA5 real-time music detector engine.

Runs the whole capture -> fingerprint -> decide -> scrobble loop and
reports progress through callbacks so it can be driven by the GUI
(gui.py) without any change to the logic.
"""

import pickle
import threading
import time
from collections import deque
from pathlib import Path

import numpy as np

from pycaw.pycaw import AudioUtilities
from proctap import ProcessAudioCapture

from config import CONFIG
from paths import DB_FILE, log_line
from recognize import (
    Matcher,
    prepare,
    CAPTURE_SAMPLE_RATE,
    QUERY_WINDOW_SECONDS,
)
from scrobble import Scrobbler


# ============================================================
# Settings (from config.json; missing keys use config.py defaults)
# ============================================================

GTA5_PROCESS_NAMES = set(
    CONFIG["gta5_process_names"]
)

# Seconds of audio to keep (proctap is 48kHz)
BUFFER_SECONDS = CONFIG["capture"]["buffer_seconds"]

# Detect every N seconds
DETECT_INTERVAL = CONFIG["capture"]["detect_interval"]

# Minimum buffer length before detection starts
MIN_BUFFER_SECONDS = (
    QUERY_WINDOW_SECONDS
    + CONFIG["capture"]["min_buffer_extra_seconds"]
)

# Consecutive confirmations required to lock a song
CONFIRM_COUNT = CONFIG["detection"]["confirm_count"]

# Consecutive confirmations before updating NOW PLAYING
NOW_PLAYING_COUNT = CONFIG["detection"]["now_playing_count"]

# Minimum share of viable windows that must vote for a candidate
MIN_VOTES_FRACTION = CONFIG["detection"]["min_votes_fraction"]

# Minimum candidate score
MIN_SCORE = CONFIG["detection"]["min_score"]

# Reject when the runner-up is too close (avoids false positives)
MIN_SCORE_MARGIN = CONFIG["detection"]["min_score_margin"]

# Silence threshold: skip detection when buffer RMS is below this
SILENCE_RMS = CONFIG["capture"]["silence_rms"]

# Position continuity tolerance (seconds)
POSITION_TOLERANCE = CONFIG["detection"]["position_tolerance"]

# How many candidates to display
TOP_RESULTS = CONFIG["detection"]["top_results"]


# ============================================================
# GTA5 PID
# ============================================================

def find_gta5():
    """
    Find GTA5 among Windows Audio Sessions.
    """

    sessions = AudioUtilities.GetAllSessions()

    for session in sessions:
        process = session.Process

        if process is None:
            continue

        try:
            name = process.name()
            pid = process.pid
        except Exception:
            continue

        if name in GTA5_PROCESS_NAMES:
            return pid, name

    return None, None


# ============================================================
# Rolling audio buffer (48kHz stereo float32)
# ============================================================

class AudioBuffer:
    def __init__(self, max_seconds):
        self.max_samples = int(
            CAPTURE_SAMPLE_RATE
            * max_seconds
        )

        self.chunks = deque()
        self.total_samples = 0

        self.lock = threading.Lock()

    def add(self, data):
        """
        data is the float32 stereo PCM bytes from ProcTap.
        """

        audio = np.frombuffer(
            data,
            dtype=np.float32,
        )

        if len(audio) == 0:
            return

        audio = audio.reshape(
            -1,
            2,
        )

        # Convert to mono
        mono = np.mean(
            audio,
            axis=1,
        )

        with self.lock:
            self.chunks.append(mono)
            self.total_samples += len(mono)

            while (
                self.total_samples
                > self.max_samples
                and self.chunks
            ):
                old = self.chunks.popleft()

                self.total_samples -= len(
                    old
                )

    def get(self):
        """
        Get a full snapshot of the current buffer
        (48kHz mono float32).
        """

        with self.lock:
            if not self.chunks:
                return None

            return np.concatenate(
                list(self.chunks)
            )

    def duration(self):
        with self.lock:
            return (
                self.total_samples
                / CAPTURE_SAMPLE_RATE
            )


# ============================================================
# Song name helper
# ============================================================

def get_song_name(filename):
    """
    Currently just uses the filename.
    Could read ID3 metadata later.
    """

    return Path(filename).stem


# ============================================================
# Engine
# ============================================================

def _default_log(message):
    """
    Default log: write to app.log and print to the console. The GUI
    replaces this with its own callback (which also writes app.log).
    """
    log_line(message)
    print(message)


class ScrobblerEngine:
    def __init__(
        self,
        log=None,
        status=None,
        now_playing=None,
        scrobbled=None,
        on_stopped=None,
    ):
        """
        log        -> called with every permanent log line
        status     -> called with ephemeral status text (overwritten)
        now_playing-> called with (song_name, score) when a song locks
        scrobbled  -> called with the artist - title on a successful scrobble
        on_stopped -> called once the run loop has fully finished
        """

        self.log = log or _default_log
        self.status = status or (lambda msg: None)
        self.on_now_playing = now_playing or (lambda name, score: None)
        self.on_scrobbled = scrobbled or (lambda title: None)
        self.on_stopped = on_stopped or (lambda: None)

        self.running = False
        self._stop_event = threading.Event()
        self._thread = None

    def _handle_scrobble(self, title):
        """Show a SCROBBLED banner, then forward to the external callback."""

        self.log("")
        self.log("=" * 70)
        self.log("SCROBBLED")
        self.log(title)
        self.log("=" * 70)
        self.log("")

        self.on_scrobbled(title)

    # --------------------------------------------------------
    # Lifecycle
    # --------------------------------------------------------

    def start(self):
        if self.running:
            return

        self._stop_event.clear()
        self.running = True

        self._thread = threading.Thread(
            target=self.run,
            daemon=True,
        )
        self._thread.start()

    def stop(self):
        self._stop_event.set()

    def wait(self):
        if self._thread is not None:
            self._thread.join()

    # --------------------------------------------------------
    # Main loop
    # --------------------------------------------------------

    def run(self):
        try:
            self._init_com()

            self._run()
        except Exception as e:
            self.log(f"Fatal error: {type(e).__name__}: {e}")
            import traceback

            for line in traceback.format_exc().splitlines():
                self.log(line)
        finally:
            self._uninit_com()
            self.running = False
            self.on_stopped()

    @staticmethod
    def _init_com():
        """
        COM must be initialized on the thread that touches COM APIs
        (pycaw AudioUtilities, proctap capture). Each engine run uses a
        fresh daemon thread, so initialize here at the top of it.
        """
        try:
            import comtypes

            comtypes.CoInitialize()
        except Exception:
            pass

    @staticmethod
    def _uninit_com():
        try:
            import comtypes

            comtypes.CoUninitialize()
        except Exception:
            pass

    def _run(self):
        self.log("=" * 70)
        self.log("GTA5 Real-time Music Detector")
        self.log("=" * 70)

        # ------------------------------------------------
        # Load the fingerprint database
        # ------------------------------------------------

        if not DB_FILE.exists():
            self.log("")
            self.log(f"Database not found: {DB_FILE}")
            self.log("Click 'Build Database' to create it first.")
            return

        self.log("")
        self.log("Loading fingerprint database...")

        with open(DB_FILE, "rb") as f:
            database = pickle.load(f)

        self.log(f"Songs: {len(database)}")

        matcher = Matcher(database)

        scrobbler = Scrobbler(
            log=self.log,
            on_scrobbled=self._handle_scrobble,
        )

        # ------------------------------------------------
        # Find GTA5
        # ------------------------------------------------

        self.log("")
        self.log("Looking for GTA5...")

        pid, process_name = find_gta5()

        if pid is None:
            self.log("GTA5 Audio Session not found.")
            self.log("Please start GTA5 and enter the game.")
            return

        self.log(f"Found: {process_name}")
        self.log(f"PID: {pid}")

        # ------------------------------------------------
        # Audio capture
        # ------------------------------------------------

        buffer = AudioBuffer(BUFFER_SECONDS)

        def on_audio(data, frames):
            buffer.add(data)

        capture = ProcessAudioCapture(
            pid,
            on_data=on_audio,
        )

        self.log("")
        self.log(f"Audio format: {capture.format}")
        self.log("")
        self.log("Capturing GTA5 audio...")
        self.log(f"Buffer: {BUFFER_SECONDS:.1f}s")
        self.log(f"Detection interval: {DETECT_INTERVAL:.1f}s")
        self.log("")
        self.log("-" * 70)

        capture.start()

        # ------------------------------------------------
        # Confirmation state
        # ------------------------------------------------

        last_candidate = None
        candidate_count = 0
        last_candidate_time = 0.0
        last_candidate_position = 0.0

        confirmed_song = None
        confirmed_song_start = 0.0

        # Song already shown as NOW PLAYING
        now_playing = None

        # Songs already scrobbled, to avoid duplicates
        scrobbled_songs = set()

        last_detection = 0.0

        try:

            while not self._stop_event.is_set():

                # ----------------------------------------
                # Check GTA5 is still running
                # ----------------------------------------

                current_pid, _ = find_gta5()

                if current_pid != pid:
                    self.log("")
                    self.log("GTA5 Audio Session is gone.")
                    break

                # ----------------------------------------
                # Wait for the next detection
                # ----------------------------------------

                now = time.monotonic()

                if now - last_detection < DETECT_INTERVAL:
                    time.sleep(0.2)
                    continue

                last_detection = now

                # ----------------------------------------
                # Grab the buffer and check length / volume
                # ----------------------------------------

                audio = buffer.get()

                if audio is None:
                    continue

                duration = (
                    len(audio) / CAPTURE_SAMPLE_RATE
                )

                if duration < MIN_BUFFER_SECONDS:
                    self.status(
                        f"Waiting for audio... {duration:.1f}s"
                    )
                    continue

                rms = float(
                    np.sqrt(np.mean(audio ** 2))
                )

                if rms < SILENCE_RMS:
                    self.status(f"Silence (rms={rms:.4f})")

                    last_candidate = None
                    candidate_count = 0

                    continue

                # ----------------------------------------
                # Match (48kHz -> 11025Hz -> multi-window voting)
                # ----------------------------------------

                self.log("")
                self.log(
                    f"[{time.strftime('%H:%M:%S')}] "
                    f"Detecting... (rms={rms:.3f})"
                )

                start_match = time.monotonic()

                results = matcher.match(
                    prepare(audio)
                )

                elapsed = time.monotonic() - start_match

                if not results:
                    self.log("No candidate songs found.")
                    continue

                # ----------------------------------------
                # Display top results
                # ----------------------------------------

                self.log(f"Match time: {elapsed:.2f}s")

                for i, result in enumerate(
                    results[:TOP_RESULTS],
                    1,
                ):

                    name = get_song_name(
                        result["filename"]
                    )

                    self.log(
                        f"{i:2}. "
                        f"v{result['votes']} "
                        f"{result['score']:.4f} "
                        f"@ {result['position']:7.2f}s "
                        f"{name}"
                    )

                # ----------------------------------------
                # Decision gates: votes, score, score margin
                # ----------------------------------------

                best = results[0]
                second = (
                    results[1]
                    if len(results) > 1
                    else None
                )

                # Viable windows = windows that produced a candidate
                # (keeps silent windows out of the count)
                viable_windows = sum(
                    r["votes"]
                    for r in results
                )

                min_votes = max(
                    2,
                    int(np.ceil(
                        MIN_VOTES_FRACTION
                        * viable_windows
                    )),
                )

                if best["votes"] < min_votes:
                    self.log(
                        f"Not enough votes "
                        f"({best['votes']}/{min_votes}), "
                        f"rejected."
                    )

                    last_candidate = None
                    candidate_count = 0

                    continue

                if best["score"] < MIN_SCORE:
                    self.log(
                        f"Below minimum score "
                        f"({MIN_SCORE:.2f}), "
                        f"rejected."
                    )

                    last_candidate = None
                    candidate_count = 0

                    continue

                # Only look at the score margin when votes are tied
                if (
                    second is not None
                    and best["votes"] == second["votes"]
                    and best["score"] - second["score"]
                    < MIN_SCORE_MARGIN
                ):
                    self.log(
                        f"Runner-up too close "
                        f"({second['score']:.4f}), "
                        f"rejected."
                    )

                    last_candidate = None
                    candidate_count = 0

                    continue

                # ----------------------------------------
                # Accumulate only on the same song + position continuity
                # ----------------------------------------

                filename = best["filename"]
                score = best["score"]
                position = best["position"]

                song_name = get_song_name(filename)

                if filename == last_candidate:

                    expected_position = (
                        last_candidate_position
                        + (now - last_candidate_time)
                    )

                    position_ok = (
                        abs(position - expected_position)
                        <= POSITION_TOLERANCE
                    )

                    if position_ok:
                        candidate_count += 1
                    else:
                        self.log(
                            f"Position jump "
                            f"({position:.1f}s vs "
                            f"expected {expected_position:.1f}s), "
                            f"resetting confirmation."
                        )
                        candidate_count = 1

                else:

                    last_candidate = filename
                    candidate_count = 1

                last_candidate_time = now
                last_candidate_position = position

                self.log(
                    f"Candidate: {song_name} | "
                    f"score {score:.4f} | "
                    f"pos {position:.1f}s | "
                    f"confirm {candidate_count}/{CONFIRM_COUNT}"
                )

                # ----------------------------------------
                # Confirm
                # ----------------------------------------

                if candidate_count >= CONFIRM_COUNT:

                    # Song changed: scrobble the previous one
                    if confirmed_song != filename:

                        if confirmed_song is not None:
                            played = now - confirmed_song_start
                            if confirmed_song not in scrobbled_songs:
                                if scrobbler.try_scrobble(
                                    confirmed_song,
                                    played,
                                ):
                                    scrobbled_songs.add(
                                        confirmed_song
                                    )

                        confirmed_song = filename
                        confirmed_song_start = now - position

                        # Scrobble this song right away on confirmation
                        # (played seconds still have to satisfy Last.fm rules)
                        if scrobbler.try_scrobble(
                            filename,
                            position,
                        ):
                            scrobbled_songs.add(filename)

                    else:

                        self.log("")
                        self.log(f"Now playing: {song_name}")

                        # Confirmed but not scrobbled yet: retry on every
                        # detection, submit the moment the rule is met
                        if filename not in scrobbled_songs:
                            if scrobbler.try_scrobble(
                                filename,
                                now - confirmed_song_start,
                            ):
                                scrobbled_songs.add(filename)

                # NOW PLAYING triggers at a lower confirmation count
                if (
                    candidate_count >= NOW_PLAYING_COUNT
                    and now_playing != filename
                ):

                    now_playing = filename

                    self.log("")
                    self.log("=" * 70)
                    self.log("NOW PLAYING")
                    self.log(song_name)
                    self.log(f"Confidence: {score:.4f}")
                    self.log("=" * 70)

                    self.on_now_playing(song_name, score)

                    scrobbler.update_now_playing(filename)

        finally:

            self.log("")
            self.log("Stopping GTA5 audio capture...")

            capture.stop()

            # Scrobble the current song before exiting (if not sent yet)
            if (
                confirmed_song is not None
                and confirmed_song not in scrobbled_songs
            ):
                played = time.monotonic() - confirmed_song_start
                if scrobbler.try_scrobble(
                    confirmed_song,
                    played,
                ):
                    scrobbled_songs.add(confirmed_song)

            self.log("Done.")
