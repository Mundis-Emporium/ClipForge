"""Persisted user settings + portable data locations.

Runtime data (downloads, exports, temp, profile, config.json) lives OUTSIDE the
repo, under the OS per-user data dir, so the checkout stays clean, contains no
machine-specific paths, and never gets synced by OneDrive/Dropbox. Users on any
OS/setup get sane defaults; everything is overridable in the UI.
"""
from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path

_LOCK = threading.Lock()


def data_root() -> Path:
    """Per-user data directory, platform-appropriate, never inside the repo."""
    env = os.environ.get("CLIPFORGE_DATA")
    if env:
        return Path(env)
    la = os.environ.get("LOCALAPPDATA")            # Windows
    if la:
        return Path(la) / "ClipForge"
    xdg = os.environ.get("XDG_DATA_HOME")          # Linux
    if xdg:
        return Path(xdg) / "ClipForge"
    mac = Path.home() / "Library" / "Application Support"
    if mac.exists():                               # macOS
        return mac / "ClipForge"
    return Path.home() / ".clipforge"              # fallback


def _config_path() -> Path:
    return data_root() / "config.json"


def default_config() -> dict:
    r = data_root()
    return {
        "downloads_dir": str(r / "downloads"),
        "output_dir": str(r / "output"),
        "cleanup_after": False,      # delete a run's sources after export
        "keep_downloads": True,      # keep prefetch cache between runs
        "cookies_browser": "",       # "", chrome, edge, brave, firefox, ...
        "max_jobs": 1,               # concurrent render jobs
        "idle_timeout_min": 0,       # auto-exit after N idle minutes (0 = never)
        "download_max_height": 1080,  # cap source download quality (0 = best)
        "auto_download": True,        # download captured links automatically
        "max_concurrent_downloads": 1,  # serialize to avoid site IP-throttling
    }


def load() -> dict:
    cfg = default_config()
    f = _config_path()
    if f.exists():
        try:
            cfg.update(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            pass
    return cfg


def save(cfg: dict) -> None:
    with _LOCK:
        p = _config_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def safe_name(name: str) -> str:
    """Sanitize a project/category name into a folder-safe token."""
    name = (name or "").strip()
    name = re.sub(r"[^A-Za-z0-9 _.-]", "", name)
    name = name.strip().replace(" ", "_")
    return name[:60]


def resolve_output_dir(cfg: dict, project: str = "", override: str = "") -> Path:
    base = Path(override) if override else Path(cfg["output_dir"])
    proj = safe_name(project)
    return base / proj if proj else base


def resolve_downloads_dir(cfg: dict, project: str = "", override: str = "") -> Path:
    base = Path(override) if override else Path(cfg["downloads_dir"])
    proj = safe_name(project)
    return base / proj if proj else base
