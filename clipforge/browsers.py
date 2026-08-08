"""Detect installed Chromium browsers and launch one with the ClipForge
extension pre-loaded via --load-extension (no manual install clicks)."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

# candidate install paths on Windows (expanded with env vars)
_CANDIDATES = {
    "chrome": [
        r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
        r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
        r"%LocalAppData%\Google\Chrome\Application\chrome.exe",
    ],
    "edge": [
        r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
        r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
    ],
    "brave": [
        r"%ProgramFiles%\BraveSoftware\Brave-Browser\Application\brave.exe",
        r"%ProgramFiles(x86)%\BraveSoftware\Brave-Browser\Application\brave.exe",
        r"%LocalAppData%\BraveSoftware\Brave-Browser\Application\brave.exe",
    ],
}
_LABELS = {"chrome": "Google Chrome", "edge": "Microsoft Edge", "brave": "Brave"}


def _resolve(paths: list[str]) -> str | None:
    for p in paths:
        exp = os.path.expandvars(p)
        if Path(exp).exists():
            return exp
    return None


def detect() -> list[dict]:
    """Return installed browsers: [{id, name, path}]."""
    found = []
    for bid, paths in _CANDIDATES.items():
        exe = _resolve(paths)
        if exe:
            found.append({"id": bid, "name": _LABELS[bid], "path": exe})
    return found


def launch_with_extension(browser_id: str, ext_dir: Path, profile_dir: Path,
                          open_url: str) -> dict:
    """Spawn the browser with the unpacked extension loaded. Uses a dedicated
    ClipForge profile so the flag isn't ignored by an already-running instance.
    """
    info = {b["id"]: b for b in detect()}.get(browser_id)
    if not info:
        return {"ok": False, "error": f"{browser_id} not found on this PC."}

    ext_dir = ext_dir.resolve()
    profile_dir = profile_dir.resolve()
    profile_dir.mkdir(parents=True, exist_ok=True)

    args = [
        info["path"],
        f"--user-data-dir={profile_dir}",
        f"--load-extension={ext_dir}",
        f"--disable-extensions-except={ext_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        open_url,
    ]
    try:
        subprocess.Popen(args, close_fds=True)
        return {"ok": True, "browser": info["name"]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}
