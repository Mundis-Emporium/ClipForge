"""ClipForge desktop entry. Runs the local server and shows it in a native
window (via pywebview). Falls back to the default browser if pywebview is
unavailable. This is the entry point PyInstaller packages into ClipForge.exe.
"""
from __future__ import annotations

import threading
import time
import urllib.request

import uvicorn

from app import app

HOST = "127.0.0.1"
PORT = 8765
URL = f"http://{HOST}:{PORT}"


def _serve():
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")


def _wait_until_up(timeout: float = 20.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        try:
            urllib.request.urlopen(URL + "/api/health", timeout=1)
            return True
        except Exception:
            time.sleep(0.2)
    return False


def run():
    threading.Thread(target=_serve, daemon=True).start()
    _wait_until_up()
    try:
        import webview  # pywebview
        webview.create_window("ClipForge", URL, width=1200, height=860,
                              min_size=(900, 640))
        webview.start()  # blocks until window closed
    except Exception:
        # no GUI toolkit / pywebview missing -> open in browser and block
        import webbrowser
        webbrowser.open(URL)
        print(f"\n  ClipForge running -> {URL}\n  (close this window to quit)\n")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    run()
