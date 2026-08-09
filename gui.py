"""
GTA5 Scrobbler - simple tkinter GUI.

On first launch (no Last.fm credentials) a setup screen asks for the
API key / secret and guides the user through authorization. It can be
skipped; everything is also available later under Settings.

Main page panels:
  Now Playing        current song (locked by the detection engine)
  Recent Scrobbles   last 5 entries from app.log
  Log                live feed of every engine message (also saved to app.log)

The engine runs in a background thread and reports through a queue;
this file only ever touches tkinter widgets from the main thread.
"""

import os
import queue
import threading
import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox, ttk

import auth
import config
import fingerprint
import lastfm_config
from engine import ScrobblerEngine
from paths import DATA_DIR, LOG_FILE, log_line


# app.log line format for scrobbles:
#   "YYYY-MM-DD HH:MM:SS Scrobbled: Artist - Title"
def read_recent_scrobbles(count=5):
    if not LOG_FILE.exists():
        return []

    try:
        lines = LOG_FILE.read_text(
            encoding="utf-8"
        ).splitlines()
    except OSError:
        return []

    recent = []

    for line in lines:
        if " Scrobbled: " not in line:
            continue

        # Skip the "[Last.fm] Scrobbled: ..." engine line; keep only
        # the canonical scrobble record (log_scrobble) to avoid dupes.
        if "[Last.fm]" in line:
            continue

        parts = line.split(" ", 2)

        if len(parts) < 3:
            continue

        title = line.split(" Scrobbled: ", 1)[1]

        # Drop a possibly partial last line (concurrent append)
        if not title.strip():
            continue

        recent.append(f"{parts[1][:5]}  {title.strip()}")

    return recent[-count:]


class App:
    def __init__(self, root):
        self.root = root
        root.title("GTA5 Scrobbler")
        root.geometry("720x600")
        root.minsize(560, 480)

        self.messages = queue.Queue()
        self.engine = None
        self.building = False
        self.after_id = None
        self._music_prompted = False

        self.setup_frame = None
        self.main_frame = None
        self.btn_setup_auth = None

        self.settings_win = None
        self.settings_widgets = {}

        self.build_setup_frame()
        self.build_main_frame()

        if auth.has_credentials():
            self.show_main()
        else:
            self.show_setup()

        self.refresh_scrobbles()
        self.log(f"Data folder: {DATA_DIR}")

        if fingerprint.MUSIC_DIR:
            self.log(f"Music folder: {fingerprint.MUSIC_DIR}")
        else:
            self.log(
                "Music folder: not set yet - "
                "choose it in Settings -> Music folder."
            )
            self.root.after(400, self.prompt_music_folder)

        root.protocol("WM_DELETE_WINDOW", self.on_close)

        # Surface any unexpected Tk callback error instead of silently
        # swallowing it (e.g. errors in poll()).
        root.report_callback_exception = self._on_tk_error

        self.after_id = root.after(100, self.poll)

    # ========================================================
    # Screens
    # ========================================================

    def show_setup(self):
        self.showing_setup = True
        self.main_frame.pack_forget()
        self.setup_frame.pack(fill="both", expand=True)
        self.refresh_lastfm_status()

    def show_main(self):
        self.showing_setup = False
        self.setup_frame.pack_forget()
        self.main_frame.pack(fill="both", expand=True)

    # ========================================================
    # Setup screen
    # ========================================================

    def build_setup_frame(self):
        frame = ttk.Frame(self.root, padding=20)
        self.setup_frame = frame

        ttk.Label(
            frame,
            text="First-time Setup",
            font=("Segoe UI", 18, "bold"),
        ).pack(anchor="w")

        ttk.Label(
            frame,
            text=(
                "To scrobble, GTA5 Scrobbler needs your Last.fm API key and secret.\n"
                "Create them for free at https://www.last.fm/api/account/create\n\n"
                "Save the credentials, then click 'Authorize Last.fm' and follow the "
                "browser to get your session.\n"
                "You can skip this and set it all up later from Settings."
            ),
            justify="left",
        ).pack(anchor="w", pady=(8, 18))

        row = ttk.Frame(frame)
        row.pack(fill="x", pady=3)
        ttk.Label(row, text="API Key:", width=14).pack(side="left")
        self.setup_key = ttk.Entry(row)
        self.setup_key.pack(side="left", fill="x", expand=True)

        row = ttk.Frame(frame)
        row.pack(fill="x", pady=3)
        ttk.Label(row, text="API Secret:", width=14).pack(side="left")
        self.setup_secret = ttk.Entry(row, show="*")
        self.setup_secret.pack(side="left", fill="x", expand=True)

        # Pre-fill with whatever is already in .env
        if lastfm_config.LASTFM_API_KEY:
            self.setup_key.insert(0, lastfm_config.LASTFM_API_KEY)
        if lastfm_config.LASTFM_API_SECRET:
            self.setup_secret.insert(0, lastfm_config.LASTFM_API_SECRET)

        self.setup_status = ttk.Label(
            frame,
            text="",
            foreground="#555555",
        )
        self.setup_status.pack(anchor="w", pady=(8, 0))

        btn_row = ttk.Frame(frame)
        btn_row.pack(anchor="w", pady=(16, 0))

        ttk.Button(
            btn_row,
            text="Save Credentials",
            command=self.save_setup_credentials,
        ).pack(side="left")

        self.btn_setup_auth = ttk.Button(
            btn_row,
            text="Authorize Last.fm",
            command=self.start_auth,
        )
        self.btn_setup_auth.pack(side="left", padx=(8, 0))

        ttk.Button(
            btn_row,
            text="Skip",
            command=self.show_main,
        ).pack(side="left", padx=(8, 0))

    def save_setup_credentials(self):
        try:
            auth.save_credentials(
                self.setup_key.get(),
                self.setup_secret.get(),
            )
        except ValueError as e:
            self.setup_status.configure(
                text=str(e),
                foreground="#c00000",
            )
            return

        self.setup_status.configure(
            text="Credentials saved. Now click 'Authorize Last.fm'.",
            foreground="#2a7a2a",
        )

    # ========================================================
    # Main screen
    # ========================================================

    def build_main_frame(self):
        parent = ttk.Frame(self.root)
        self.main_frame = parent

        toolbar = ttk.Frame(parent, padding=(8, 6))
        toolbar.pack(fill="x")

        self.btn_start = ttk.Button(
            toolbar,
            text="Start",
            command=self.start_engine,
        )
        self.btn_start.pack(side="left")

        self.btn_stop = ttk.Button(
            toolbar,
            text="Stop",
            command=self.stop_engine,
            state="disabled",
        )
        self.btn_stop.pack(side="left", padx=(6, 0))

        self.btn_build = ttk.Button(
            toolbar,
            text="Build Database",
            command=self.build_database,
        )
        self.btn_build.pack(side="left", padx=(6, 0))

        ttk.Button(
            toolbar,
            text="Settings",
            command=self.open_settings,
        ).pack(side="left", padx=(6, 0))

        self.btn_open = ttk.Button(
            toolbar,
            text="Open Data Folder",
            command=self.open_data_folder,
        )
        self.btn_open.pack(side="left", padx=(6, 0))

        # ------------------------------------------------
        # Now playing
        # ------------------------------------------------

        np_frame = ttk.LabelFrame(
            parent,
            text="Now Playing",
            padding=(10, 6),
        )
        np_frame.pack(fill="x", padx=8, pady=(8, 4))

        self.lbl_now = ttk.Label(
            np_frame,
            text="--",
            font=("Segoe UI", 16, "bold"),
            anchor="center",
        )
        self.lbl_now.pack(fill="x")

        self.lbl_confidence = ttk.Label(
            np_frame,
            text="",
            font=("Segoe UI", 9),
            foreground="#888888",
            anchor="center",
        )
        self.lbl_confidence.pack(fill="x")

        self.lbl_status = ttk.Label(
            parent,
            text="Not running.",
            foreground="#777777",
            anchor="center",
        )
        self.lbl_status.pack(fill="x")

        # ------------------------------------------------
        # Build progress (hidden until needed)
        # ------------------------------------------------

        self.progress = ttk.Progressbar(
            parent,
            mode="determinate",
        )

        self.lbl_progress = ttk.Label(
            parent,
            text="",
            foreground="#555555",
        )

        # ------------------------------------------------
        # Recent scrobbles
        # ------------------------------------------------

        sc_frame = ttk.LabelFrame(
            parent,
            text="Recent Scrobbles",
            padding=(10, 6),
        )
        sc_frame.pack(fill="x", padx=8, pady=4)

        self.scrobble_labels = []

        for _ in range(5):
            lbl = ttk.Label(
                sc_frame,
                text="",
                anchor="w",
                font=("Segoe UI", 9),
            )
            lbl.pack(fill="x")
            self.scrobble_labels.append(lbl)

        # ------------------------------------------------
        # Log
        # ------------------------------------------------

        log_frame = ttk.LabelFrame(
            parent,
            text="Log",
            padding=(6, 6),
        )
        log_frame.pack(
            fill="both",
            expand=True,
            padx=8,
            pady=(4, 8),
        )

        self.log_text = tk.Text(
            log_frame,
            state="disabled",
            wrap="char",
            font=("Consolas", 9),
        )

        scroll = ttk.Scrollbar(
            log_frame,
            command=self.log_text.yview,
        )
        self.log_text.configure(
            yscrollcommand=scroll.set
        )

        scroll.pack(side="right", fill="y")
        self.log_text.pack(side="left", fill="both", expand=True)

    # ========================================================
    # Settings dialog
    # ========================================================

    def open_settings(self):
        if self.settings_win is not None:
            self.settings_win.lift()
            return

        win = tk.Toplevel(self.root)
        win.title("Settings")
        win.transient(self.root)
        win.grab_set()
        win.resizable(False, False)

        self.settings_win = win
        self.settings_widgets = {}

        row = ttk.Frame(win)
        row.pack(fill="x", padx=12, pady=(14, 0))
        ttk.Label(row, text="API Key:", width=14).pack(side="left")
        key_entry = ttk.Entry(row)
        key_entry.insert(0, lastfm_config.LASTFM_API_KEY)
        key_entry.pack(side="left", fill="x", expand=True)
        self.settings_widgets["key"] = key_entry

        row = ttk.Frame(win)
        row.pack(fill="x", padx=12, pady=(8, 0))
        ttk.Label(row, text="API Secret:", width=14).pack(side="left")
        secret_entry = ttk.Entry(row, show="*")
        secret_entry.insert(0, lastfm_config.LASTFM_API_SECRET)
        secret_entry.pack(side="left", fill="x", expand=True)
        self.settings_widgets["secret"] = secret_entry

        row = ttk.Frame(win)
        row.pack(fill="x", padx=12, pady=(12, 0))
        ttk.Label(row, text="Music folder:", width=14).pack(side="left")
        music_entry = ttk.Entry(row)
        music_entry.insert(0, config.CONFIG["paths"]["music_dir"])
        music_entry.pack(side="left", fill="x", expand=True)
        ttk.Button(
            row,
            text="Browse...",
            command=lambda: self.browse_music_dir(music_entry),
        ).pack(side="left", padx=(6, 0))
        self.settings_widgets["music_dir"] = music_entry

        status = ttk.Label(
            win,
            text="",
            foreground="#555555",
        )
        status.pack(anchor="w", padx=12, pady=(12, 0))
        self.settings_widgets["status"] = status

        hint = ttk.Label(
            win,
            text=(
                "API key / secret: https://www.last.fm/api/account/create\n"
                "After saving, click 'Authorize Last.fm' and confirm in the browser.\n"
                "Music folder is where Build Database looks for your songs "
                "(subfolders included)."
            ),
            foreground="#777777",
            justify="left",
        )
        hint.pack(anchor="w", padx=12, pady=(4, 0))

        btn_row = ttk.Frame(win)
        btn_row.pack(fill="x", padx=12, pady=(14, 14))

        ttk.Button(
            btn_row,
            text="Save",
            command=self.save_settings,
        ).pack(side="left")

        auth_btn = ttk.Button(
            btn_row,
            text="Authorize Last.fm",
            command=self.start_auth,
        )
        auth_btn.pack(side="left", padx=(8, 0))
        self.settings_widgets["btn_auth"] = auth_btn

        ttk.Button(
            btn_row,
            text="Close",
            command=self.close_settings,
        ).pack(side="right")

        win.protocol("WM_DELETE_WINDOW", self.close_settings)

        self.refresh_lastfm_status()

    def browse_music_dir(self, entry):
        chosen = filedialog.askdirectory(
            title="Choose music folder",
            initialdir=entry.get() or None,
            parent=self.root,
        )

        if chosen:
            entry.delete(0, "end")
            entry.insert(0, chosen)

    def save_settings(self):
        widgets = self.settings_widgets

        try:
            auth.save_credentials(
                widgets["key"].get(),
                widgets["secret"].get(),
            )
        except ValueError as e:
            widgets["status"].configure(
                text=str(e),
                foreground="#c00000",
            )
            return

        music_dir = widgets["music_dir"].get().strip()

        if not music_dir:
            widgets["status"].configure(
                text="Music folder is required.",
                foreground="#c00000",
            )
            return

        config.save(
            {"paths": {"music_dir": music_dir}}
        )
        fingerprint.set_music_dir(music_dir)

        widgets["status"].configure(
            text="Settings saved.",
            foreground="#2a7a2a",
        )

        self.refresh_lastfm_status()

    def close_settings(self):
        if self.settings_win is not None:
            self.settings_win.destroy()

        self.settings_win = None
        self.settings_widgets = {}

    def refresh_lastfm_status(self):
        session = auth.has_session()

        text = (
            "Session: configured. Scrobbling enabled."
            if session
            else "Session: not configured."
        )

        if self.setup_status is not None:
            self.setup_status.configure(text=text)

        if self.settings_widgets:
            self.settings_widgets["status"].configure(text=text)

    # ========================================================
    # Log helpers
    # ========================================================

    def log(self, message):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", message + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

        log_line(message)

    def refresh_scrobbles(self):
        recent = read_recent_scrobbles(5)

        display = list(reversed(recent))

        while len(display) < 5:
            display.append("")

        if not recent:
            display[0] = "No scrobbles yet."

        for label, text in zip(
            self.scrobble_labels,
            display,
        ):
            label.configure(text=text)

    # ========================================================
    # Engine control
    # ========================================================

    def start_engine(self):
        if (
            self.engine
            and self.engine.running
            or self.building
        ):
            return

        self.engine = ScrobblerEngine(
            log=lambda m: self.messages.put(
                ("log", m)
            ),
            status=lambda m: self.messages.put(
                ("status", m)
            ),
            now_playing=lambda n, s: self.messages.put(
                ("now", n, s)
            ),
            scrobbled=lambda t: self.messages.put(
                ("scrobble", t)
            ),
            on_stopped=lambda: self.messages.put(
                ("engine_stopped",)
            ),
        )

        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.lbl_status.configure(text="Starting...")

        self.log("Starting engine...")
        self.engine.start()

    def stop_engine(self):
        if self.engine:
            self.engine.stop()
            self.btn_stop.configure(state="disabled")
            self.log("Stopping...")

    def on_engine_stopped(self):
        self.btn_start.configure(state="normal")
        self.btn_stop.configure(state="disabled")
        self.lbl_status.configure(text="Not running.")

    # ========================================================
    # Build database
    # ========================================================

    def prompt_music_folder(self):
        """Startup reminder when no music folder has been chosen yet."""

        if fingerprint.MUSIC_DIR or self._music_prompted:
            return

        self._music_prompted = True

        if messagebox.askyesno(
            "Music folder",
            "Your music folder is not set yet.\n"
            "You need to choose it before building the "
            "database.\n\n"
            "Open Settings to pick it now?",
            parent=self.root,
        ):
            self.open_settings()

    def build_database(self):
        if self.building:
            return

        if not fingerprint.MUSIC_DIR:
            self.log(
                "Music folder is not set. "
                "Choose it in Settings -> Music folder first."
            )
            self.open_settings()
            return

        self.building = True
        self.btn_build.configure(state="disabled")
        self.btn_start.configure(state="disabled")

        self.progress.configure(value=0, maximum=1)
        self.progress.pack(fill="x", padx=8)

        self.lbl_progress.configure(text="Scanning...")
        self.lbl_progress.pack(fill="x", padx=8)

        self.log(
            f"Building fingerprint database from "
            f"{fingerprint.MUSIC_DIR} ..."
        )

        def work():
            try:
                database = fingerprint.build_database(
                    on_progress=lambda d, t, n, s: (
                        self.messages.put(
                            ("db_progress", d, t, n, s)
                        )
                    )
                )

                self.messages.put(("db_done", len(database)))

            except Exception as e:
                self.messages.put(
                    (
                        "db_error",
                        f"{type(e).__name__}: {e}",
                    )
                )

        threading.Thread(
            target=work,
            daemon=True,
        ).start()

    def _finish_build(self, count):
        self.building = False
        self.btn_build.configure(state="normal")
        self.btn_start.configure(state="normal")

        self.progress.pack_forget()
        self.lbl_progress.pack_forget()

        if count == 0:
            self.log(
                "No audio files found in the music folder. "
                "Add songs and build again."
            )
        else:
            self.log(f"Database built: {count} songs")

    # ========================================================
    # Authorization
    # ========================================================

    def _set_auth_busy(self, busy):
        state = "disabled" if busy else "normal"

        if self.btn_setup_auth is not None:
            self.btn_setup_auth.configure(state=state)

        if self.settings_widgets:
            self.settings_widgets["btn_auth"].configure(
                state=state
            )

    def start_auth(self):
        if not auth.has_credentials():
            messagebox.showwarning(
                "Authorize Last.fm",
                "Save your API key and secret first.",
                parent=self.root,
            )
            return

        self._set_auth_busy(True)

        def work():
            try:
                url = auth.get_auth_url()
            except Exception as e:
                self.messages.put(
                    (
                        "auth_error",
                        f"{type(e).__name__}: {e}",
                    )
                )
                self.messages.put(("auth_busy", False))
                return

            self.messages.put(("auth_url", url))

        threading.Thread(
            target=work,
            daemon=True,
        ).start()

    def handle_auth_url(self, url):
        self._set_auth_busy(False)

        try:
            webbrowser.open(url)
        except Exception as e:
            self.log(f"Could not open the browser: {e}")

        ok = messagebox.askokcancel(
            "Authorize Last.fm",
            "If the browser did not open, visit this URL and authorize:\n\n"
            f"{url}\n\n"
            "Then click OK.",
            parent=self.root,
        )

        if not ok:
            self.log("Authorization cancelled.")
            return

        self._set_auth_busy(True)
        self.log("Waiting for Last.fm response...")

        def work():
            try:
                session = auth.complete_auth(url)
            except Exception as e:
                self.messages.put(
                    (
                        "auth_error",
                        f"{type(e).__name__}: {e}",
                    )
                )
                self.messages.put(("auth_busy", False))
                return

            env_file = auth.save_session_key(session)
            self.messages.put(("auth_saved", str(env_file)))
            self.messages.put(("auth_busy", False))

        threading.Thread(
            target=work,
            daemon=True,
        ).start()

    # ========================================================
    # Misc
    # ========================================================

    def open_data_folder(self):
        try:
            os.startfile(str(DATA_DIR))
        except OSError as e:
            self.log(f"Cannot open data folder: {e}")

    def on_close(self):
        if self.after_id is not None:
            self.root.after_cancel(self.after_id)

        if self.engine:
            self.engine.stop()

        self.root.destroy()

    # ========================================================
    # Queue polling
    # ========================================================

    def _on_tk_error(self, exc, val, tb):
        import traceback

        text = "".join(
            traceback.format_exception(exc, val, tb)
        ).rstrip()

        self.log(f"Unexpected error:\n{text}")

        try:
            messagebox.showerror(
                "Unexpected error",
                text,
                parent=self.root,
            )
        except Exception:
            pass

    def poll(self):
        try:
            while True:
                item = self.messages.get_nowait()
                self.dispatch(item)
        except queue.Empty:
            pass
        except Exception:
            import traceback

            self.log(
                "Error in event handler:\n"
                + "".join(traceback.format_exc()).rstrip()
            )

        self.after_id = self.root.after(100, self.poll)

    def dispatch(self, item):
        kind = item[0]

        if kind == "log":
            self.log(item[1])

        elif kind == "status":
            self.lbl_status.configure(text=item[1])

        elif kind == "now":
            self.lbl_now.configure(text=item[1])
            self.lbl_confidence.configure(
                text=f"Confidence: {item[2]:.4f}"
            )

        elif kind == "scrobble":
            self.refresh_scrobbles()

        elif kind == "engine_stopped":
            self.on_engine_stopped()

        elif kind == "db_progress":
            _, done, total, name, status = item
            self.progress.configure(
                maximum=max(total, 1),
                value=done,
            )
            self.lbl_progress.configure(
                text=f"[{done}/{total}] {name} - {status}"
            )

        elif kind == "db_done":
            self._finish_build(item[1])

        elif kind == "db_error":
            self.log(f"Database build failed: {item[1]}")
            self._finish_build(0)

        elif kind == "auth_url":
            self.handle_auth_url(item[1])

        elif kind == "auth_saved":
            self.log(f"Session key saved to {item[1]}.")
            self.log("Last.fm scrobbling is now enabled.")
            self.refresh_lastfm_status()

            # Leave the setup screen once everything is configured
            if self.showing_setup:
                self.show_main()

            messagebox.showinfo(
                "Authorize Last.fm",
                "Authorization complete. Scrobbling is now enabled.",
                parent=self.root,
            )

        elif kind == "auth_error":
            self.log(f"Authorization failed: {item[1]}")

            messagebox.showerror(
                "Authorization failed",
                item[1],
                parent=self.root,
            )

        elif kind == "auth_busy":
            self._set_auth_busy(item[1])


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
