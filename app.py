"""ClipForge server. Local web app.

Run:  python app.py   (opens http://127.0.0.1:8765)
"""
from __future__ import annotations

import os
import shutil
import sys
import threading
import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import uvicorn
from fastapi import FastAPI, UploadFile, File, Form, Request
from fastapi.responses import (
    FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, StreamingResponse,
)
from fastapi.staticfiles import StaticFiles

from clipforge import __version__, browsers, config, media, prefetch
from clipforge.jobs import STORE
from clipforge.pipeline import run_job

# bundled assets live in _MEIPASS when packaged by PyInstaller, else the repo
if getattr(sys, "frozen", False):
    BUNDLE = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
else:
    BUNDLE = Path(__file__).parent
BASE = Path(__file__).parent
STATIC = BUNDLE / "static"
EXT_DIR = BUNDLE / "extension"

DATA = config.data_root()             # per-user data, outside repo & OneDrive
WORK = DATA / "work"
UPLOADS = DATA / "uploads"
EXT_PROFILE = DATA / "browser-profile"
for d in (DATA, WORK, UPLOADS):
    d.mkdir(parents=True, exist_ok=True)

CONFIG = config.load()
prefetch.set_download_slots(CONFIG.get("max_concurrent_downloads", 1))

APP_URL = "http://127.0.0.1:8765"

# serialize heavy renders; configurable via CONFIG["max_jobs"]
EXECUTOR = ThreadPoolExecutor(max_workers=max(1, int(CONFIG.get("max_jobs", 1))))

# make venv-installed CLIs (auto-editor) discoverable to subprocess calls
_scripts = Path(sys.executable).parent
if str(_scripts) not in os.environ.get("PATH", ""):
    os.environ["PATH"] = str(_scripts) + os.pathsep + os.environ.get("PATH", "")

LAST_ACTIVITY = time.time()  # for optional idle auto-shutdown


def dl_dir() -> Path:
    d = Path(CONFIG["downloads_dir"])
    d.mkdir(parents=True, exist_ok=True)
    return d


def _build_stamp() -> str:
    """Short git commit of the running code, so we can confirm it's current.
    Best-effort: reads .git directly (no subprocess); 'release' when packaged."""
    try:
        head = (BASE / ".git" / "HEAD").read_text(encoding="utf-8").strip()
        if head.startswith("ref:"):
            ref = head.split(" ", 1)[1].strip()
            sha = (BASE / ".git" / ref).read_text(encoding="utf-8").strip()
        else:
            sha = head
        return sha[:7]
    except Exception:
        return "release"


BUILD = _build_stamp()


def _have(binpath: str) -> bool:
    return bool(binpath) and (Path(binpath).exists() or shutil.which(binpath))


def tools_status() -> dict:
    from clipforge import downloader
    return {
        "ffmpeg": _have(media.FFMPEG),
        "ffprobe": _have(media.FFPROBE),
        "auto_editor": shutil.which("auto-editor") is not None,
        "impersonation": bool(getattr(downloader, "_IMPERSONATE", False)),
    }


app = FastAPI(title="ClipForge", version=__version__)


@app.middleware("http")
async def _track_activity(request, call_next):
    global LAST_ACTIVITY
    LAST_ACTIVITY = time.time()
    return await call_next(request)


@app.get("/", response_class=HTMLResponse)
def index():
    return (STATIC / "index.html").read_text(encoding="utf-8")


@app.post("/api/quit")
def quit_app():
    """Shut the server down cleanly (from the UI Quit button)."""
    def _die():
        time.sleep(0.4)
        os._exit(0)
    threading.Thread(target=_die, daemon=True).start()
    return {"ok": True, "bye": True}


@app.get("/api/health")
def health():
    return {"ok": True, "version": __version__, "build": BUILD,
            "inbox": len(STORE.inbox),
            "tools": tools_status(), "data_dir": str(DATA)}


# ---- link inbox (bookmarklet / extension target) --------------------
def _prefetch_one(it):
    ck = CONFIG.get("cookies_browser", "")
    mh = int(CONFIG.get("download_max_height", 1080) or 0)
    prefetch.prefetch_item(it, STORE, dl_dir(), ck, mh)


def _maybe_prefetch(items):
    if CONFIG.get("auto_download", True):
        for it in items:
            if it.status == "new":
                _prefetch_one(it)


@app.get("/api/inbox")
def get_inbox():
    return {
        "items": [it.to_dict() for it in STORE.inbox],
        "links": STORE.inbox_urls(),
    }


@app.post("/api/inbox")
async def add_inbox(payload: dict):
    urls = payload.get("urls") or ([payload["url"]] if payload.get("url") else [])
    items = STORE.add_links([u for u in urls if u])
    _maybe_prefetch(items)
    return {"added": len(items), "total": len(STORE.inbox)}


# GET variant so a bookmarklet can hit it with an <img>/navigation and no CORS
@app.get("/api/capture", response_class=PlainTextResponse)
def capture(url: str = ""):
    items = STORE.add_links([url]) if url else []
    _maybe_prefetch(items)
    return f"ClipForge captured {len(items)} link. Inbox now has {len(STORE.inbox)}."


@app.post("/api/inbox/prefetch")
def prefetch_inbox():
    started = prefetch.prefetch_all(STORE, dl_dir(),
                                    CONFIG.get("cookies_browser", ""))
    return {"started": started}


@app.post("/api/inbox/{item_id}/download")
def download_item(item_id: str):
    for it in STORE.inbox:
        if it.id == item_id:
            if it.status not in ("downloading", "downloaded"):
                it.status = "new"
                _prefetch_one(it)
            return {"ok": True}
    return JSONResponse({"error": "not found"}, status_code=404)


@app.post("/api/inbox/{item_id}/quality")
def item_quality(item_id: str, payload: dict):
    h = payload.get("height")
    for it in STORE.inbox:
        if it.id == item_id:
            it.quality = None if h in (None, "") else int(h)
            # re-download at the new quality: drop any cached/prior file
            STORE.cache_del(it.url)
            it.file = None
            it.progress = 0.0
            it.downloaded_bytes = 0
            it.status = "new"
            if CONFIG.get("auto_download", True):
                _prefetch_one(it)
            return {"ok": True, "quality": it.quality}
    return JSONResponse({"error": "not found"}, status_code=404)


@app.delete("/api/inbox/{item_id}")
def delete_item(item_id: str):
    STORE.remove_item(item_id)
    return {"ok": True}


@app.post("/api/inbox/clear")
def clear_inbox():
    STORE.clear_inbox()
    return {"ok": True}


# ---- one-click extension launcher -----------------------------------
@app.get("/api/browsers")
def list_browsers():
    return {"browsers": browsers.detect()}


@app.post("/api/reveal")
def reveal(payload: dict):
    """Open a relevant folder in the OS file manager."""
    what = payload.get("what", "extension")
    target = {
        "extension": EXT_DIR,
        "output": Path(CONFIG["output_dir"]),
        "downloads": Path(CONFIG["downloads_dir"]),
    }.get(what, EXT_DIR)
    target.mkdir(parents=True, exist_ok=True)
    try:
        os.startfile(str(target))  # Windows
        return {"ok": True, "path": str(target)}
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": str(e), "path": str(target)},
                            status_code=400)


@app.get("/api/ext-path", response_class=PlainTextResponse)
def ext_path():
    return str(EXT_DIR.resolve())


@app.post("/api/launch-extension")
def launch_extension(payload: dict):
    bid = payload.get("browser", "")
    if not EXT_DIR.exists():
        return JSONResponse({"ok": False, "error": "extension folder missing"},
                            status_code=400)
    res = browsers.launch_with_extension(bid, EXT_DIR, EXT_PROFILE, APP_URL)
    code = 200 if res.get("ok") else 400
    return JSONResponse(res, status_code=code)


# ---- settings / storage ---------------------------------------------
@app.get("/api/config")
def get_config():
    return CONFIG


@app.post("/api/config")
def set_config(payload: dict):
    for k in ("downloads_dir", "output_dir", "cleanup_after", "keep_downloads",
              "cookies_browser", "max_jobs", "idle_timeout_min",
              "download_max_height", "auto_download", "max_concurrent_downloads"):
        if k in payload:
            CONFIG[k] = payload[k]
    Path(CONFIG["downloads_dir"]).mkdir(parents=True, exist_ok=True)
    Path(CONFIG["output_dir"]).mkdir(parents=True, exist_ok=True)
    prefetch.set_download_slots(CONFIG.get("max_concurrent_downloads", 1))
    config.save(CONFIG)
    return {"ok": True, "config": CONFIG}


# ---- stream playlist (watch links back-to-back, no download) ---------
def _resolved_item(u: str):
    from clipforge import downloader
    import urllib.parse
    r = downloader.resolve_stream(u, CONFIG.get("cookies_browser", ""))
    if r["url"]:
        proxy = "/api/stream?src=" + urllib.parse.quote(r["url"], safe="") \
                + "&ref=" + urllib.parse.quote(r["referer"], safe="")
        return {"title": r["title"], "src": proxy, "page": u}
    return {"title": r["title"], "src": None, "page": u, "error": r.get("error")}


@app.get("/api/resolve")
def resolve_one(url: str):
    """Resolve ONE link to a playable stream, so the player can start on the
    first link immediately and resolve the rest in the background."""
    return _resolved_item(url)


@app.post("/api/playlist")
def resolve_playlist(payload: dict):
    """Resolve all links in parallel (faster than one at a time)."""
    from concurrent.futures import ThreadPoolExecutor
    links = [l.strip() for l in (payload.get("links") or []) if l and l.strip()]
    with ThreadPoolExecutor(max_workers=min(8, max(1, len(links)))) as ex:
        out = list(ex.map(_resolved_item, links))
    return {"items": out}


@app.get("/api/stream")
def stream(src: str, ref: str = "", request: Request = None):
    """Proxy a remote media URL to the player (with impersonation + Referer),
    forwarding Range so the video seeks. Streams through; nothing is saved."""
    from curl_cffi import requests as creq
    headers = {}
    if ref:
        headers["Referer"] = ref
    rng = request.headers.get("range") if request else None
    if rng:
        headers["Range"] = rng
    try:
        r = creq.get(src, impersonate="chrome", stream=True, timeout=30,
                     headers=headers)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": str(e)[:120]}, status_code=502)

    def gen():
        try:
            for chunk in r.iter_content(65536):
                if chunk:
                    yield chunk
        except Exception:
            pass

    passthru = {}
    for k in ("content-type", "content-length", "content-range", "accept-ranges"):
        if k in {kk.lower() for kk in r.headers}:
            for kk, vv in r.headers.items():
                if kk.lower() == k:
                    passthru[kk] = vv
    return StreamingResponse(
        gen(), status_code=r.status_code,
        headers=passthru,
        media_type=r.headers.get("content-type", "video/mp4"))


@app.get("/api/local")
def local_files(dir: str = ""):
    """List video/image files in a folder (default: the downloads folder) so
    the user can add already-saved clips to a compilation without downloading."""
    base = Path(dir) if dir else Path(CONFIG["downloads_dir"])
    vid_exts = {".mp4", ".webm", ".mkv", ".mov", ".m4v", ".avi", ".gif", ".flv"}
    exts = vid_exts | media.IMAGE_EXTS
    files = []
    if base.exists() and base.is_dir():
        for f in sorted(base.iterdir()):
            if f.is_file() and f.suffix.lower() in exts:
                files.append({"name": f.name, "path": str(f),
                              "mb": round(f.stat().st_size / 1e6, 1)})
    return {"dir": str(base), "files": files}


@app.get("/api/projects")
def list_projects():
    out = Path(CONFIG["output_dir"])
    projs = []
    if out.exists():
        for d in sorted(out.iterdir()):
            if d.is_dir():
                vids = list(d.glob("*.mp4"))
                projs.append({"name": d.name, "count": len(vids)})
    return {"projects": projs, "output_dir": str(out)}


@app.post("/api/cleanup")
def cleanup(payload: dict):
    """Delete downloaded source files to free space. scope: all|inbox."""
    freed = 0
    removed = 0
    base = Path(CONFIG["downloads_dir"])
    if base.exists():
        for f in base.rglob("*"):
            if f.is_file():
                try:
                    freed += f.stat().st_size
                    f.unlink()
                    removed += 1
                except Exception:
                    pass
    STORE.cache.clear()
    return {"ok": True, "removed": removed, "freed_mb": round(freed / 1e6, 1)}


# ---- music upload ---------------------------------------------------
@app.post("/api/upload-music")
async def upload_music(file: UploadFile = File(...)):
    dst = UPLOADS / file.filename
    dst.write_bytes(await file.read())
    return {"path": str(dst), "name": file.filename}


# ---- jobs -----------------------------------------------------------
def _find_duplicates(links: list[str]) -> dict:
    seen: dict[str, int] = {}
    dupes: list[dict] = []
    for i, u in enumerate(links):
        key = u.strip().rstrip("/").lower()
        if key in seen:
            dupes.append({"url": u, "index": i, "first_index": seen[key]})
        else:
            seen[key] = i
    return {"count": len(dupes), "items": dupes}


def _dedupe(links: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for u in links:
        key = u.strip().rstrip("/").lower()
        if key not in seen:
            seen.add(key)
            out.append(u)
    return out


@app.post("/api/jobs")
async def create_job(payload: dict):
    # inputs: structured items [{url,start,end}] (editing) or plain links
    items = payload.get("items")
    if items:
        items = [it for it in items if it.get("url", "").strip()]
        links = [it["url"].strip() for it in items]
    else:
        links = [l.strip() for l in (payload.get("links") or []) if l and l.strip()]
    if not links:
        return JSONResponse({"error": "No links provided."}, status_code=400)

    # duplicate handling: warn (default) | drop | keep
    dupe_mode = payload.get("duplicates", "warn")
    dupes = _find_duplicates(links)
    if dupes["count"] > 0 and dupe_mode == "warn":
        return {
            "needs_confirmation": True,
            "duplicates": dupes,
            "unique_count": len(_dedupe(links)),
            "total": len(links),
        }
    if dupe_mode == "drop":
        links = _dedupe(links)
        if items:
            seen = set()
            deduped = []
            for it in items:
                k = it["url"].strip().rstrip("/").lower()
                if k not in seen:
                    seen.add(k)
                    deduped.append(it)
            items = deduped

    project = payload.get("project", "")
    out_dir = config.resolve_output_dir(
        CONFIG, project=project, override=payload.get("output_dir", ""))
    cleanup_after = payload.get("cleanup_after", CONFIG.get("cleanup_after", False))

    params = {
        "links": links,
        "items": items,
        "mode": payload.get("mode", "stitch"),
        "height": payload.get("height", 1080),
        "aspect": payload.get("aspect", "16:9"),
        "fit": payload.get("fit", "pad"),
        "fps": payload.get("fps", 30),
        "order": payload.get("order", "as_listed"),
        "max_clip_seconds": payload.get("max_clip_seconds", 0),
        "music_path": payload.get("music_path"),
        "keep_original_audio": payload.get("keep_original_audio", False),
        "normalize_audio": payload.get("normalize_audio", True),
        "beats_per_cut": payload.get("beats_per_cut", 2),
        "sync_source": payload.get("sync_source", "auto"),
        "transition": payload.get("transition", "none"),
        "transition_seconds": payload.get("transition_seconds", 0.5),
        "image_seconds": payload.get("image_seconds", 4),
        "target_length": payload.get("target_length", 0),
        "cookies_browser": payload.get("cookies_browser",
                                       CONFIG.get("cookies_browser", "")),
        "download_max_height": payload.get("download_max_height",
                                           CONFIG.get("download_max_height", 1080)),
        "project": config.safe_name(project),
        "cleanup_after": bool(cleanup_after),
    }
    job = STORE.create(params)
    EXECUTOR.submit(run_job, job, STORE, WORK, out_dir)
    return {"id": job.id}


@app.post("/api/jobs/{jid}/cancel")
def cancel_job(jid: str):
    job = STORE.get(jid)
    if not job:
        return JSONResponse({"error": "not found"}, status_code=404)
    job.cancel = True
    return {"ok": True}


@app.get("/api/jobs/{jid}")
def job_status(jid: str):
    job = STORE.get(jid)
    if not job:
        return JSONResponse({"error": "not found"}, status_code=404)
    return job.to_dict()


@app.get("/api/jobs/{jid}/download")
def job_download(jid: str):
    job = STORE.get(jid)
    if not job or not job.result or not Path(job.result).exists():
        return JSONResponse({"error": "not ready"}, status_code=404)
    return FileResponse(job.result, filename=Path(job.result).name,
                        media_type="video/mp4")


@app.get("/api/jobs/{jid}/preview")
def job_preview(jid: str):
    job = STORE.get(jid)
    if not job or not job.result or not Path(job.result).exists():
        return JSONResponse({"error": "not ready"}, status_code=404)
    return FileResponse(job.result, media_type="video/mp4")


app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")


def _port_in_use(host: str, port: int) -> bool:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.4)
        return s.connect_ex((host, port)) == 0


def _kill_port_holder(port: int) -> None:
    """Terminate whatever is squatting on our port, so THIS freshly launched
    (correct) instance wins. run.bat launches exactly one, so this is safe."""
    import subprocess
    try:
        out = subprocess.run(["netstat", "-ano"], capture_output=True,
                             text=True).stdout
        pids = set()
        for line in out.splitlines():
            if f":{port} " in line and "LISTENING" in line:
                pids.add(line.split()[-1])
        for pid in pids:
            if pid and pid != "0" and int(pid) != os.getpid():
                subprocess.run(["taskkill", "/F", "/PID", pid],
                               capture_output=True)
    except Exception:
        pass


def main():
    host = os.environ.get("CLIPFORGE_HOST", "127.0.0.1")
    port = int(os.environ.get("CLIPFORGE_PORT", "8765"))
    url = f"http://{host}:{port}"

    # if anything is squatting on the port, take it over (we are the instance
    # the user just launched, on the correct venv Python).
    if _port_in_use(host, port):
        print("\n  Port busy, taking over from a previous instance...\n")
        _kill_port_holder(port)
        time.sleep(1.5)

    t = tools_status()
    if not (t["ffmpeg"] and t["ffprobe"]):
        print("\n  [!] FFmpeg not found on PATH. ClipForge needs ffmpeg + ffprobe.")
        print("      Install from https://ffmpeg.org/download.html and reopen.\n")

    # optional idle auto-shutdown: exit after N idle minutes (0 = never)
    idle_min = float(CONFIG.get("idle_timeout_min", 0) or 0)
    if idle_min > 0:
        def _watchdog():
            while True:
                time.sleep(30)
                if time.time() - LAST_ACTIVITY > idle_min * 60:
                    running = any(j.status == "running"
                                  for j in STORE._jobs.values())
                    if not running:
                        print(f"\n  ClipForge idle {idle_min:g} min, exiting.\n")
                        os._exit(0)
        threading.Thread(target=_watchdog, daemon=True).start()
        print(f"  Idle auto-shutdown: {idle_min:g} min")

    if os.environ.get("CLIPFORGE_NO_BROWSER") != "1":
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    print("\n  ClipForge running -> " + url)
    print("  Data dir: " + str(DATA) + "\n")
    try:
        uvicorn.run(app, host=host, port=port, log_level="warning")
    except OSError:
        print("\n  Could not start on " + url + ". It may already be running.\n")
        if os.environ.get("CLIPFORGE_NO_BROWSER") != "1":
            webbrowser.open(url)


if __name__ == "__main__":
    main()
