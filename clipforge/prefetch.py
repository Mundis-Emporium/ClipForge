"""Background prefetch: download inbox links to a shared cache while the user
keeps adding more, so the final render skips the download wait."""
from __future__ import annotations

import threading
from pathlib import Path

from . import downloader
from .jobs import JobStore, InboxItem

_lock = threading.Lock()
_active: set[str] = set()

# limit how many downloads run at once. Serial by default so a single site
# (which throttles parallel downloads from one IP) gives each its full speed,
# letting short-lived download links finish before they expire.
_slots = threading.BoundedSemaphore(1)


def set_download_slots(n: int) -> None:
    global _slots
    _slots = threading.BoundedSemaphore(max(1, int(n)))


def prefetch_item(item: InboxItem, store: JobStore, cache_dir: Path,
                  cookies_browser: str = "", max_height: int = 1080) -> None:
    if item.status in ("downloading", "downloaded"):
        return
    if item.file and Path(item.file).exists():
        store.set_item(item.id, status="downloaded")
        return
    if store.cache_get(item.url):
        cached = store.cache_get(item.url)
        if cached and Path(cached).exists():
            store.set_item(item.id, status="downloaded", file=cached,
                           note="cached")
            return

    with _lock:
        if item.id in _active:
            return
        _active.add(item.id)

    def worker():
        cache_dir.mkdir(parents=True, exist_ok=True)
        idx = abs(hash(item.id)) % 100000
        # wait for a free download slot (serialized to avoid IP throttling)
        if not _slots.acquire(blocking=False):
            store.set_item(item.id, status="new",
                           note="waiting for a free download slot")
            _slots.acquire()
        if getattr(item, "cancel", False):
            _slots.release()
            with _lock:
                _active.discard(item.id)
            return
        store.set_item(item.id, status="downloading", note="")
        try:
            err = None
            hused = None
            fb = None
            if item.kind == "image" or downloader.is_image_url(item.url):
                path = downloader.download_image(item.url, cache_dir, idx)
                if not path:
                    err = "could not fetch image"
            else:
                def prog(frac, done=0, total=0):
                    store.set_item(item.id, progress=min(0.99, frac),
                                   downloaded_bytes=int(done),
                                   total_bytes=int(total))

                mh = item.quality if item.quality is not None else max_height
                path, err, hused, fb = downloader.download_smart(
                    item.url, cache_dir, idx, progress=prog,
                    cookies_browser=cookies_browser, start_height=mh,
                    cancel_check=lambda: getattr(item, "cancel", False),
                    note_cb=lambda m: store.set_item(item.id, note=m))
            if path and Path(path).exists():
                store.cache_put(item.url, str(path))
                if hused is not None:
                    note = f"downloaded at {downloader.hlabel(hused)}"
                    if fb:
                        note += f" (higher failed: {fb[:50]})"
                    store.set_item(item.id, quality=hused)
                else:
                    note = "ready to forge"
                store.set_item(item.id, status="downloaded", file=str(path),
                               note=note, progress=1.0)
            else:
                store.set_item(item.id, status="failed",
                               note=(err or "couldn't download")[:90])
        except downloader.Cancelled:
            store.set_item(item.id, status="failed", note="cancelled")
        except Exception as e:  # noqa: BLE001
            store.set_item(item.id, status="failed", note=str(e)[:80])
        finally:
            _slots.release()
            with _lock:
                _active.discard(item.id)

    threading.Thread(target=worker, daemon=True).start()


def prefetch_all(store: JobStore, cache_dir: Path,
                 cookies_browser: str = "", max_height: int = 1080) -> int:
    started = 0
    for it in list(store.inbox):
        if it.status in ("new", "ready", "failed") and not it.file:
            if it.status == "failed" and it.note == "invalid URL":
                continue
            prefetch_item(it, store, cache_dir, cookies_browser, max_height)
            started += 1
    return started
