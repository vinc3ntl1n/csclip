"""A small window with one button: get my new FACEIT matches and turn them into videos."""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import scrolledtext, ttk

from .config import ConfigError, load_config
from .pipeline import Pipeline

WAIT_FOR_DOWNLOADS_S = 10 * 60
PROJECT_DIR = Path(__file__).resolve().parent.parent


class _QueueWriter:
    """Stands in for stdout so everything the pipeline prints shows up in the window."""

    def __init__(self, messages: queue.Queue):
        self.messages = messages

    def write(self, text: str) -> int:
        self.messages.put(text)
        return len(text)

    def flush(self) -> None:
        pass


def default_config_path() -> Path:
    # A desktop shortcut may start the app from any folder, so also look next to the code.
    for candidate in (Path.cwd() / "config.toml", PROJECT_DIR / "config.toml"):
        if candidate.exists():
            return candidate
    return PROJECT_DIR / "config.toml"


class App:
    def __init__(self, root: tk.Tk, config_path: Path):
        self.root = root
        self.config_path = config_path
        root.title("FACEIT Highlights")
        root.geometry("820x520")
        root.minsize(600, 360)

        bar = ttk.Frame(root, padding=10)
        bar.pack(fill="x")
        self.go = ttk.Button(bar, text="Get my new matches", command=self.get_matches)
        self.go.pack(side="left", ipadx=12, ipady=6)
        ttk.Button(bar, text="Open videos folder", command=self.open_videos).pack(side="left", padx=8, ipady=6)
        self.status = tk.StringVar(value="Ready")
        ttk.Label(bar, textvariable=self.status).pack(side="left", padx=12)

        self.log = scrolledtext.ScrolledText(root, height=20, state="disabled", wrap="word", font="TkFixedFont")
        self.log.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        self.messages: queue.Queue = queue.Queue()
        sys.stdout = sys.stderr = _QueueWriter(self.messages)
        root.after(100, self._drain)
        print(f"Settings: {config_path}")

    def _drain(self) -> None:
        text = []
        while not self.messages.empty():
            item = self.messages.get_nowait()
            if isinstance(item, tuple):  # ("done", status) from the worker thread
                self.go.config(state="normal")
                self.status.set(item[1])
            else:
                text.append(item)
        if text:
            self.log.config(state="normal")
            self.log.insert("end", "".join(text))
            self.log.see("end")
            self.log.config(state="disabled")
        self.root.after(100, self._drain)

    def get_matches(self) -> None:
        self.go.config(state="disabled")
        self.status.set("Working... CS2 may open by itself to record, don't play until this says Done")
        threading.Thread(target=self._work, daemon=True).start()

    def _work(self) -> None:
        status = "Done"
        try:
            cfg = load_config(self.config_path)
            Pipeline(cfg).poll(wait_for_downloads_s=WAIT_FOR_DOWNLOADS_S, reopen_waiting=True)
        except Exception as error:
            print(f"Error: {error}")
            status = "Stopped, see the log"
        self.messages.put(("done", status))

    def open_videos(self) -> None:
        try:
            folder = load_config(self.config_path, require_demo_source=False).paths.output_dir
        except ConfigError as error:
            print(f"Error: {error}")
            return
        folder.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(folder)
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(folder)])


def main(config_path: Path | None = None) -> None:
    root = tk.Tk()
    App(root, config_path or default_config_path())
    root.mainloop()
