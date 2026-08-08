"""yt-dlp wrapper: download any supported URL to a local mp4."""
from __future__ import annotations

import re
import urllib.request
from pathlib import Path
from typing import Callable
from urllib.parse import urljoin, urlparse

import yt_dlp

from .media import IMAGE_EXTS


def is_image_url(url: str) -> bool:
    ext = Path(urlparse(url).path).suffix.lower()
    return ext in IMAGE_EXTS and ext != ".gif"  # gif treated as video by yt-dlp


def download_image(url: str, out_dir: str | Path, index: int) -> Path | None:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(urlparse(url).path).suffix.lower() or ".jpg"
    dst = out_dir / f"img_{index:03d}{ext}"
    parts = urlparse(url)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/122.0 Safari/537.36",
        "Accept": "image/avif,image/webp,image/*,*/*;q=0.8",
        "Referer": f"{parts.scheme}://{parts.netloc}/",
    }
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as r, open(dst, "wb") as f:
            f.write(r.read())
        if dst.exists() and dst.stat().st_size > 0:
            return dst
    except Exception:
        pass
    return None

# Prefer a single progressive/merged mp4 up to 1080p; fall back to best.
def _format_for(max_height: int) -> str:
    """yt-dlp format string. Prefers H.264 video + AAC audio in mp4 so the file
    plays everywhere (Windows Media Player, phones, editors), then falls back to
    any codec (VP9/AV1/Opus) if H.264 isn't offered. max_height <= 0 = best."""
    if max_height and max_height > 0:
        h = int(max_height)
        return (
            f"bv*[vcodec^=avc1][height<={h}]+ba[acodec^=mp4a]/"   # H.264+AAC
            f"b[vcodec^=avc1][height<={h}]/"                       # progressive H.264
            f"bv*[ext=mp4][height<={h}]+ba[ext=m4a]/"
            f"bv*[height<={h}]+ba/b[height<={h}]/"                 # any codec
            f"best[height<={h}]/best"
        )
    return ("bv*[vcodec^=avc1]+ba[acodec^=mp4a]/b[vcodec^=avc1]/"
            "bv*[ext=mp4]+ba[ext=m4a]/bv*+ba/b/best")


class Cancelled(Exception):
    pass


class _QuietLogger:
    """Swallow yt-dlp's console output. We surface clean reasons ourselves, so
    the terminal stays readable instead of dumping raw yt-dlp ERROR lines."""
    def debug(self, msg): pass
    def info(self, msg): pass
    def warning(self, msg): pass
    def error(self, msg): pass


def _impersonation_available() -> bool:
    """True if yt-dlp can impersonate a real browser (needs curl_cffi).
    Lets us pass Cloudflare anti-bot challenges on generic/direct sites."""
    try:
        import yt_dlp.networking._curlcffi  # noqa: F401  (raises if unsupported)
        return True
    except Exception:
        return False


_IMPERSONATE = _impersonation_available()


def download(url: str, out_dir: str | Path, index: int,
             progress: Callable[[float], None] | None = None,
             cookies_browser: str = "", max_height: int = 1080,
             cancel_check: Callable[[], bool] | None = None
             ) -> tuple[Path | None, str | None]:
    """Download one URL.

    Returns (path, None) on success or (None, error_message) on failure, so the
    caller can show WHY a link failed instead of a bare "failed".

    cookies_browser: pull cookies from this browser (chrome/edge/brave/firefox)
    for login/age-gated content. cancel_check: abort mid-download if it returns True.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    outtmpl = str(out_dir / f"src_{index:03d}.%(ext)s")

    def hook(d):
        if cancel_check and cancel_check():
            raise Cancelled()
        if progress and d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes") or 0
            frac = min(0.99, done / total) if total else 0.0
            progress(frac, done, total)

    opts = {
        "format": _format_for(max_height),
        "outtmpl": outtmpl,
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "ignoreerrors": False,
        "logger": _QuietLogger(),
        # be resilient to slow servers with short-lived download tokens
        "retries": 10,
        "fragment_retries": 10,
        "file_access_retries": 5,
        "socket_timeout": 60,
        "concurrent_fragment_downloads": 4,
        "progress_hooks": [hook],
        # allow sites that serve video from .php (and similar) endpoints;
        # without this yt-dlp skips them as "unusual extension for safety".
        "compat_opts": {"allow-unsafe-ext"},
        "postprocessors": [
            {"key": "FFmpegVideoConvertor", "preferedformat": "mp4"},
        ],
    }
    if _IMPERSONATE:
        # impersonate Chrome on generic/direct sites to clear Cloudflare 403s.
        # scoped to the generic extractor so dedicated extractors are untouched.
        opts["extractor_args"] = {"generic": {"impersonate": ["chrome"]}}
    if cookies_browser:
        opts["cookiesfrombrowser"] = (cookies_browser,)

    def _attempt(o):
        with yt_dlp.YoutubeDL(o) as ydl:
            info = ydl.extract_info(url, download=True)
            path = Path(ydl.prepare_filename(info))
            if path.suffix.lower() != ".mp4":
                mp4 = path.with_suffix(".mp4")
                if mp4.exists():
                    path = mp4
            return path if path.exists() else _find_downloaded(out_dir, index)

    eff_cookies = cookies_browser
    try:
        got = _attempt(opts)
        if got:
            return got, None
    except Cancelled:
        raise
    except Exception as e:  # noqa: BLE001
        # cookie DB locked (browser open)? retry once without cookies.
        if cookies_browser and _is_cookie_db_error(e):
            opts.pop("cookiesfrombrowser", None)
            eff_cookies = ""
            try:
                got = _attempt(opts)
                if got:
                    return got, None
            except Cancelled:
                raise
            except Exception as e2:  # noqa: BLE001
                e = e2
        got = _find_downloaded(out_dir, index)
        if got:
            return got, None
        # yt-dlp couldn't find a video: scrape the page for a direct media URL
        fb, fberr = _fallback_scrape(url, out_dir, index, eff_cookies,
                                     max_height, cancel_check, progress)
        if fb:
            return fb, None
        return None, _clean_error(e, eff_cookies)
    # no exception but no file, try the scraper
    fb, _ = _fallback_scrape(url, out_dir, index, eff_cookies, max_height,
                             cancel_check, progress)
    return (fb, None) if fb else (None, "no video was found at this link")


def _is_cookie_db_error(e: Exception) -> bool:
    low = str(e).lower()
    return "cookie" in low and ("copy" in low or "database" in low
                                or "could not" in low or "locked" in low)


_THUMB_HINT = ("/cf-thumbs/", "/thumbs/", "/thumb/", "/preview/", "sprite")


def _fallback_scrape(page_url: str, out_dir: Path, index: int,
                     cookies_browser: str, max_height: int,
                     cancel_check, progress=None
                     ) -> tuple[Path | None, str | None]:
    """Fetch the page (with impersonation) and pull a direct video URL out of
    the HTML when yt-dlp's extractors find nothing. Handles many embed/player
    sites that expose an <source>/og:video/.m3u8/.mp4 link."""
    try:
        html = _fetch_html(page_url)
    except Exception:
        return None, None
    if not html:
        return None, None

    media = _find_media_url(html, page_url)
    if not media:
        return None, None

    # hand the direct media URL back to yt-dlp, with the page as Referer so the
    # CDN accepts the request; impersonation clears any Cloudflare on the media host.
    outtmpl = str(out_dir / f"src_{index:03d}.%(ext)s")
    opts = {
        "format": _format_for(max_height),
        "outtmpl": outtmpl,
        "merge_output_format": "mp4",
        "quiet": True, "no_warnings": True, "noprogress": True,
        "logger": _QuietLogger(),
        "retries": 10, "fragment_retries": 10, "file_access_retries": 5,
        "socket_timeout": 60, "concurrent_fragment_downloads": 4,
        "compat_opts": {"allow-unsafe-ext"},
        "http_headers": {"Referer": page_url},
        "postprocessors": [{"key": "FFmpegVideoConvertor", "preferedformat": "mp4"}],
    }

    def fbhook(d):
        if cancel_check and cancel_check():
            raise Cancelled()
        if progress and d.get("status") == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes") or 0
            progress(min(0.99, done / total) if total else 0.0, done, total)

    opts["progress_hooks"] = [fbhook]
    if _IMPERSONATE:
        opts["extractor_args"] = {"generic": {"impersonate": ["chrome"]}}
        opts["impersonate"] = _default_impersonate()
    if cookies_browser:
        opts["cookiesfrombrowser"] = (cookies_browser,)
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(media, download=True)
            path = Path(ydl.prepare_filename(info))
            got = path if path.exists() else _find_downloaded(out_dir, index)
            return (got, None) if got else (None, "found a video link but could not download it")
    except Cancelled:
        raise
    except Exception as e:  # noqa: BLE001
        got = _find_downloaded(out_dir, index)
        return (got, None) if got else (None, _clean_error(e, cookies_browser))


def resolve_stream(url: str, cookies_browser: str = "") -> dict:
    """Resolve a page URL to a single directly-playable media URL (no download),
    for the playlist/stream player. Returns {url, title, referer, error}."""
    opts = {
        "quiet": True, "no_warnings": True, "skip_download": True,
        "logger": _QuietLogger(),
        # a single progressive file (video+audio together) so <video> can play it
        "format": ("b[ext=mp4][vcodec!=none][acodec!=none]/"
                   "b[vcodec!=none][acodec!=none]/b"),
        "compat_opts": {"allow-unsafe-ext"},
    }
    if _IMPERSONATE:
        opts["extractor_args"] = {"generic": {"impersonate": ["chrome"]}}
    if cookies_browser:
        opts["cookiesfrombrowser"] = (cookies_browser,)

    def _extract(o):
        with yt_dlp.YoutubeDL(o) as ydl:
            info = ydl.extract_info(url, download=False)
        if info.get("entries"):
            info = info["entries"][0]
        return info

    try:
        info = _extract(opts)
    except Exception as e:  # noqa: BLE001
        if cookies_browser and _is_cookie_db_error(e):
            opts.pop("cookiesfrombrowser", None)
            try:
                info = _extract(opts)
            except Exception:
                info = None
        else:
            info = None
        if info is None:
            # last resort: scrape the page for a direct media URL
            html = _fetch_html(url)
            durl = _find_media_url(html, url) if html else None
            return {"url": durl, "title": url, "referer": url,
                    "error": None if durl else _clean_error(e, cookies_browser)}
    durl = info.get("url")
    if not durl:
        html = _fetch_html(url)
        durl = _find_media_url(html, url) if html else None
    return {"url": durl, "title": info.get("title") or url,
            "referer": info.get("webpage_url") or url,
            "error": None if durl else "no playable stream found"}


def hlabel(h: int | None) -> str:
    return "best" if not h else f"{h}p"


_RETRYABLE = ("expired", "slow", "timeout", "timed out", "403", "forbidden",
              "cloudflare", "unusual", "connection", "reset", "incomplete")


def _is_retryable(msg: str | None) -> bool:
    low = (msg or "").lower()
    return any(k in low for k in _RETRYABLE)


def _quality_ladder(start: int) -> list[int]:
    steps = [2160, 1440, 1080, 720, 480, 360]
    lad = ([0] + steps) if start == 0 else ([start] + [h for h in steps if h < start])
    out, seen = [], set()
    for h in lad:
        if h not in seen:
            seen.add(h)
            out.append(h)
    return out


def download_smart(url: str, out_dir: str | Path, index: int,
                   progress: Callable | None = None, cookies_browser: str = "",
                   start_height: int = 1080, cancel_check: Callable | None = None,
                   note_cb: Callable[[str], None] | None = None):
    """Download, auto-stepping DOWN quality on retryable failures (a slow server
    letting a short-lived link expire, Cloudflare, timeouts) until it succeeds.

    Returns (path|None, error|None, height_used, fellback_reason|None).
    fellback_reason is set when a higher quality failed first.
    """
    ladder = _quality_ladder(start_height)
    last_err = None
    for i, h in enumerate(ladder):
        if cancel_check and cancel_check():
            raise Cancelled()
        if i > 0 and note_cb:
            note_cb(f"retrying at {hlabel(h)} (higher was too slow)")
        p, e = download(url, out_dir, index, progress=progress,
                        cookies_browser=cookies_browser, max_height=h,
                        cancel_check=cancel_check)
        if p:
            return p, None, h, (last_err if i > 0 else None)
        last_err = e
        if not _is_retryable(e):
            return None, e, h, None          # a lower quality won't help
    return None, last_err, ladder[-1], None


def _default_impersonate():
    try:
        from yt_dlp.networking.impersonate import ImpersonateTarget
        return ImpersonateTarget.from_str("chrome")
    except Exception:
        return None


def _fetch_html(url: str) -> str:
    """Get page HTML, using curl_cffi impersonation if available (else urllib)."""
    if _IMPERSONATE:
        try:
            from curl_cffi import requests as creq
            r = creq.get(url, impersonate="chrome", timeout=30)
            if r.status_code == 200:
                return r.text
        except Exception:
            pass
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode("utf-8", "replace")


def _find_media_url(html: str, base: str) -> str | None:
    """Pick the best direct media URL from page HTML. Prefer HLS, then mp4,
    skipping obvious thumbnails/sprites."""
    def _clean(u: str) -> str:
        return urljoin(base, u.replace("&amp;", "&").strip())

    def _is_thumb(u: str) -> bool:
        low = u.lower()
        return low.endswith(".jpg") or low.endswith(".png") \
            or any(h in low for h in _THUMB_HINT)

    # 1) explicit player sources / video tags
    tagged = re.findall(r'<(?:source|video)[^>]+src=["\']([^"\']+)', html, re.I)
    tagged += re.findall(r'og:video(?::url|:secure_url)?["\'][^>]+content=["\']([^"\']+)', html, re.I)
    tagged += re.findall(r'"(?:file|video_url|videoUrl|src)"\s*:\s*"([^"]+)"', html, re.I)

    # 2) any m3u8 / mp4 anywhere in the markup or inline scripts
    hls = re.findall(r'https?:\\?/\\?/[^"\'\\ ]+\.m3u8[^"\'\\ ]*', html, re.I)
    mp4 = re.findall(r'https?:\\?/\\?/[^"\'\\ ]+\.mp4(?:\?[^"\'\\ ]*)?', html, re.I)

    def _norm(lst):
        out = []
        for u in lst:
            u = u.replace("\\/", "/")
            if u.lower().endswith((".m3u8", ".mp4")) or ".m3u8?" in u.lower() or ".mp4?" in u.lower():
                out.append(_clean(u))
        return out

    tagged_media = [_clean(u.replace("\\/", "/")) for u in tagged
                    if (".mp4" in u.lower() or ".m3u8" in u.lower())]

    # ranked candidates: tagged HLS, any HLS, tagged mp4, any mp4
    for group in (
        [u for u in tagged_media if ".m3u8" in u.lower()],
        _norm(hls),
        [u for u in tagged_media if ".mp4" in u.lower() and not _is_thumb(u)],
        [u for u in _norm(mp4) if not _is_thumb(u)],
    ):
        for u in group:
            if u.startswith("http"):
                return u
    return None


def _clean_error(e: Exception, cookies_browser: str) -> str:
    """Turn a yt-dlp exception into a short, human-readable reason."""
    msg = str(e)
    # strip yt-dlp's noisy prefixes and ansi
    msg = re.sub(r"\x1b\[[0-9;]*m", "", msg)          # strip ANSI colour codes
    msg = msg.replace("ERROR:", "").strip()
    low = msg.lower()
    if "unusual" in low and "extension" in low:
        return ("the download link expired before finishing (slow server); "
                "try again, or pick a lower Download quality")
    if "cookie" in low and ("copy" in low or "database" in low or "locked" in low):
        b = cookies_browser or "that browser"
        return (f"couldn't read {b} cookies (Windows locks/encrypts them; Chrome "
                f"especially). Close {b}, pick the browser you're logged into, "
                f"try Firefox, or set cookies to None")
    # order matters: check specific HTTP codes before the login heuristic
    if "http error 404" in low or "http error 410" in low:
        return "link not found (404)"
    if "cloudflare" in low or "anti-bot" in low or "challenge" in low:
        if cookies_browser:
            return "blocked by Cloudflare even with cookies; the site may be unsupported"
        return "blocked by Cloudflare; try 'Use cookies from browser' in Project & storage"
    if "http error 403" in low or "forbidden" in low:
        return "blocked by the site (403). Try cookies from your browser."
    if "geo" in low and "restrict" in low:
        return "geo-restricted in your region"
    if any(k in low for k in ("sign in", "log in", "login required",
                              "private video", "members-only", "members only",
                              "age-restricted", "age restricted", "confirm your age")):
        hint = "needs sign-in"
        if not cookies_browser:
            hint += " (turn on 'Use cookies from browser' in Project & storage)"
        return hint
    if "unsupported url" in low or "no video formats" in low \
            or "unable to extract" in low:
        return "this page has no downloadable video (or the site isn't supported)"
    if "requested format is not available" in low:
        return "no matching video format for the chosen quality"
    if "timed out" in low or "timeout" in low:
        return "the site timed out"
    # fallback: first line, trimmed
    first = msg.splitlines()[0] if msg else "download failed"
    return first[:160]


def _find_downloaded(out_dir: Path, index: int) -> Path | None:
    matches = sorted(out_dir.glob(f"src_{index:03d}.*"))
    matches = [m for m in matches if m.suffix.lower() != ".part"]
    return matches[0] if matches else None
