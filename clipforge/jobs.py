"""In-memory job registry + structured link 'inbox' with prefetch cache."""
from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from urllib.parse import urlparse
from typing import Any


def _norm_key(url: str) -> str:
    return url.strip().rstrip("/").lower()


@dataclass
class InboxItem:
    id: str
    url: str
    kind: str = "unknown"        # video|image|unknown
    status: str = "new"          # new|checking|ready|failed|downloading|downloaded
    note: str = ""
    file: str | None = None      # cached download path when downloaded
    progress: float = 0.0        # 0..1 while downloading
    downloaded_bytes: int = 0
    total_bytes: int = 0
    quality: int | None = None   # per-item download cap; None = global default
    cancel: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "url": self.url, "kind": self.kind,
            "status": self.status, "note": self.note,
            "downloaded": bool(self.file),
            "progress": round(self.progress, 3),
            "mb": round(self.downloaded_bytes / 1e6, 1),
            "total_mb": round(self.total_bytes / 1e6, 1),
            "quality": self.quality,
        }


@dataclass
class Job:
    id: str
    status: str = "queued"
    progress: float = 0.0
    stage: str = "queued"
    log: list[str] = field(default_factory=list)
    result: str | None = None
    error: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    cancel: bool = False
    failed: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "status": self.status,
            "progress": round(self.progress, 4), "stage": self.stage,
            "log": self.log[-200:], "result": self.result, "error": self.error,
            "failed": self.failed,
        }


class JobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self.inbox: list[InboxItem] = []
        # url(normalized) -> downloaded file path (prefetch cache, shared w/ jobs)
        self.cache: dict[str, str] = {}

    # ---- jobs -------------------------------------------------------
    def create(self, params: dict[str, Any]) -> Job:
        jid = uuid.uuid4().hex[:12]
        job = Job(id=jid, params=params)
        with self._lock:
            self._jobs[jid] = job
        return job

    def get(self, jid: str) -> Job | None:
        return self._jobs.get(jid)

    def update(self, job: Job, *, progress: float | None = None,
               stage: str | None = None, msg: str | None = None,
               status: str | None = None) -> None:
        with self._lock:
            if progress is not None:
                job.progress = progress
            if stage is not None:
                job.stage = stage
            if status is not None:
                job.status = status
            if msg is not None:
                job.log.append(msg)

    # ---- inbox ------------------------------------------------------
    def add_links(self, urls: list[str]) -> list[InboxItem]:
        added: list[InboxItem] = []
        with self._lock:
            existing = {_norm_key(it.url) for it in self.inbox}
            for u in urls:
                u = u.strip()
                if not u:
                    continue
                key = _norm_key(u)
                kind = _guess_kind(u)
                status = "new" if _valid_url(u) else "failed"
                note = "" if status == "new" else "invalid URL"
                if key in existing:
                    # mark duplicate but still surface it
                    it = InboxItem(id=uuid.uuid4().hex[:8], url=u, kind=kind,
                                   status="ready" if status == "new" else "failed",
                                   note="duplicate")
                else:
                    it = InboxItem(id=uuid.uuid4().hex[:8], url=u,
                                   kind=kind, status=status, note=note)
                    existing.add(key)
                self.inbox.append(it)
                added.append(it)
        return added

    def set_item(self, item_id: str, **kw) -> None:
        with self._lock:
            for it in self.inbox:
                if it.id == item_id:
                    for k, v in kw.items():
                        setattr(it, k, v)
                    return

    def cancel_item(self, item_id: str) -> None:
        with self._lock:
            for it in self.inbox:
                if it.id == item_id:
                    it.cancel = True
                    return

    def remove_item(self, item_id: str) -> None:
        with self._lock:
            for it in self.inbox:
                if it.id == item_id:
                    it.cancel = True  # stop an in-flight prefetch
            self.inbox = [it for it in self.inbox if it.id != item_id]

    def inbox_urls(self) -> list[str]:
        return [it.url for it in self.inbox if it.status != "failed"]

    def clear_inbox(self) -> None:
        with self._lock:
            self.inbox.clear()

    def cache_get(self, url: str) -> str | None:
        return self.cache.get(_norm_key(url))

    def cache_put(self, url: str, path: str) -> None:
        with self._lock:
            self.cache[_norm_key(url)] = path

    def cache_del(self, url: str) -> None:
        with self._lock:
            self.cache.pop(_norm_key(url), None)


def _valid_url(u: str) -> bool:
    try:
        p = urlparse(u)
        return p.scheme in ("http", "https") and bool(p.netloc)
    except Exception:
        return False


def _guess_kind(u: str) -> str:
    from .media import IMAGE_EXTS
    from pathlib import Path
    ext = Path(urlparse(u).path).suffix.lower()
    if ext in IMAGE_EXTS and ext != ".gif":
        return "image"
    if ext in {".mp4", ".webm", ".mkv", ".mov", ".m4v", ".gif"}:
        return "video"
    return "unknown"


STORE = JobStore()
