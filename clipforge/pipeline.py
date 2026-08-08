"""Pipeline: turn a list of links (videos and/or images) into a compilation.

Modes:
  stitch    - download, normalize, join in chosen order.
  smartcut  - stitch + remove dead air from each clip first.
  montage   - fast cuts, beat-synced to music or content-synced to each clip's
              own audio onsets; native audio kept.

Extras: still images -> Ken Burns clips; crossfade/fade transitions; music bed
with original audio preserved; aspect (16:9/9:16/1:1) + pad/crop; loudness
normalize; per-clip trim + explicit order; browser cookies; cancel; failed list.
"""
from __future__ import annotations

import shutil
import time
from pathlib import Path

from . import beat, downloader, media, smartcut
from .jobs import Job, JobStore


class JobCancelled(Exception):
    pass


def _ck(job: Job):
    if getattr(job, "cancel", False):
        raise JobCancelled()


def _order_clips(clips, order):
    if order == "random":
        return sorted(clips, key=lambda c: hash(str(c["path"])) & 0xFFFF)
    if order == "shortest_first":
        return sorted(clips, key=lambda c: c["dur"])
    if order == "longest_first":
        return sorted(clips, key=lambda c: -c["dur"])
    return clips  # as_listed / explicit items order


def run_job(job: Job, store: JobStore, work_root: Path, out_dir: Path) -> None:
    p = job.params
    mode: str = p.get("mode", "stitch")
    fps: int = int(p.get("fps", 30))
    order: str = p.get("order", "as_listed")
    max_clip: float = float(p.get("max_clip_seconds", 0) or 0)
    music_path: str | None = p.get("music_path") or None
    keep_orig_audio: bool = bool(p.get("keep_original_audio", False))
    beats_per_cut: int = int(p.get("beats_per_cut", 2))
    sync_source: str = p.get("sync_source", "auto")
    transition: str = p.get("transition", "none")
    trans_dur: float = float(p.get("transition_seconds", 0.5) or 0.5)
    image_seconds: float = float(p.get("image_seconds", 4) or 4)
    target_length: float = float(p.get("target_length", 0) or 0)
    cookies_browser: str = p.get("cookies_browser", "") or ""
    normalize_audio: bool = bool(p.get("normalize_audio", True))
    dl_max_height: int = int(p.get("download_max_height", 1080) or 0)

    aspect: str = p.get("aspect", "16:9")
    fit: str = p.get("fit", "pad")
    quality = int(p.get("height", 1080))
    W, H = media.dims(aspect, quality)

    # inputs: structured items [{url,start,end}] or plain links
    items = p.get("items")
    if not items:
        items = [{"url": u, "start": 0, "end": 0} for u in p["links"]]

    work = work_root / job.id
    raw = work / "raw"
    norm = work / "norm"
    for d in (raw, norm):
        d.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        store.update(job, status="running", stage="download", progress=0.02,
                     msg=f"Starting: {len(items)} item(s), mode={mode}, {aspect} {fit}")

        clips: list[dict] = []
        n = len(items)
        for i, it in enumerate(items):
            _ck(job)
            url = it["url"]
            t_start = float(it.get("start", 0) or 0)
            t_end = float(it.get("end", 0) or 0)

            # local file already on disk? use it directly, no download.
            local = url[7:] if url.lower().startswith("file://") else url
            if not url.lower().startswith(("http://", "https://")) and Path(local).exists():
                lp = Path(local)
                if lp.suffix.lower() in media.IMAGE_EXTS and lp.suffix.lower() != ".gif":
                    kb = raw / f"kb_{i:03d}.mp4"
                    if media.ken_burns_image(lp, kb, duration=image_seconds,
                                             width=W, height=H, fit=fit, fps=fps,
                                             direction=i % 4):
                        clips.append({"path": kb, "dur": image_seconds,
                                      "image": True, "normed": True})
                        store.update(job, msg=f"  local image: {lp.name}")
                        continue
                dur = media.probe_duration(lp)
                clip = {"path": lp, "dur": dur, "image": False, "normed": False}
                if t_start > 0 or t_end > 0:
                    s = max(0.0, t_start)
                    d = (t_end - s) if t_end > s else (dur - s)
                    if d > 0.1:
                        tclip = raw / f"trim_{i:03d}.mp4"
                        if media.trim(lp, tclip, s, d):
                            clip = {"path": tclip, "dur": media.probe_duration(tclip),
                                    "image": False, "normed": False}
                clips.append(clip)
                store.update(job, stage="download",
                             msg=f"Local [{i+1}/{n}] {lp.name} ({clip['dur']:.1f}s)")
                continue

            if downloader.is_image_url(url):
                store.update(job, stage="download", msg=f"Image [{i+1}/{n}] {url}")
                cached = store.cache_get(url)
                img = Path(cached) if (cached and Path(cached).exists()) \
                    else downloader.download_image(url, raw, i)
                if img and Path(img).exists():
                    kb = raw / f"kb_{i:03d}.mp4"
                    if media.ken_burns_image(img, kb, duration=image_seconds,
                                             width=W, height=H, fit=fit, fps=fps,
                                             direction=i % 4):
                        clips.append({"path": kb, "dur": image_seconds,
                                      "image": True, "normed": True})
                        store.update(job, msg=f"  ok (Ken Burns): {Path(img).name}")
                        continue
                job.failed.append(url)
                store.update(job, msg=f"  FAILED image: {url}")
                continue

            # video: reuse prefetch cache if present
            cached = store.cache_get(url)
            if cached and Path(cached).exists():
                src = Path(cached)
                store.update(job, stage="download",
                             msg=f"Cached [{i+1}/{n}] {src.name}")
            else:
                store.update(job, stage="download",
                             msg=f"Downloading [{i+1}/{n}] {url}")

                def prog(frac, *_a, i=i):
                    base = 0.02 + (i / max(1, n)) * 0.34
                    store.update(job, progress=base + frac * (0.34 / max(1, n)))

                src, err, hused, fb = downloader.download_smart(
                    url, raw, i, progress=prog,
                    cookies_browser=cookies_browser, start_height=dl_max_height,
                    cancel_check=lambda: getattr(job, "cancel", False),
                    note_cb=lambda m: store.update(job, msg="  " + m))
                if not (src and Path(src).exists()):
                    reason = err or "download failed"
                    job.failed.append(f"{url}  ({reason})")
                    store.update(job, msg=f"  FAILED: {url} -- {reason}")
                    continue
                if fb:
                    store.update(job, msg=f"  downloaded at {downloader.hlabel(hused)} (higher quality failed: {fb[:50]})")
            if not (src and Path(src).exists()):
                job.failed.append(f"{url}  (file missing)")
                store.update(job, msg=f"  FAILED: {url}")
                continue

            # per-clip trim (pre-cut to native size so montage/linear reuse it)
            if t_start > 0 or t_end > 0:
                dur_full = media.probe_duration(src)
                s = max(0.0, t_start)
                d = (t_end - s) if t_end > s else (dur_full - s)
                if d > 0.1:
                    tclip = raw / f"trim_{i:03d}.mp4"
                    if media.trim(src, tclip, s, d):
                        src = tclip
                        store.update(job, msg=f"  trimmed to {s:.1f}-{s+d:.1f}s")

            clips.append({"path": Path(src), "dur": media.probe_duration(src),
                          "image": False, "normed": False})
            store.update(job, msg=f"  ok: {Path(src).name} ({clips[-1]['dur']:.1f}s)")

        if not clips:
            raise RuntimeError("Nothing could be downloaded.")

        _ck(job)
        store.update(job, progress=0.40, stage="process",
                     msg=f"Got {len(clips)}/{n} item(s)"
                         + (f", {len(job.failed)} failed" if job.failed else ""))
        clips = _order_clips(clips, order)

        if mode == "montage":
            body = _build_montage(job, store, clips, norm, work, W, H, fit, fps,
                                  music_path, keep_orig_audio, beats_per_cut,
                                  sync_source, target_length)
            music_already = True
        else:
            body = _build_linear(job, store, clips, norm, work, W, H, fit, fps,
                                 max_clip, transition, trans_dur,
                                 smart=(mode == "smartcut"))
            music_already = False

        _ck(job)
        ts = time.strftime("%Y%m%d_%H%M%S")
        staged = work / "staged.mp4"
        if music_path and not music_already:
            store.update(job, stage="music", progress=0.90,
                         msg="Adding music" + (" (original kept under)" if keep_orig_audio else ""))
            if not media.mux_music(body, music_path, staged,
                                   keep_original=keep_orig_audio):
                shutil.copy(body, staged)
        else:
            shutil.copy(body, staged)

        out_file = out_dir / f"compilation_{mode}_{ts}.mp4"
        if normalize_audio and media.has_audio(staged):
            store.update(job, stage="loudness", progress=0.95,
                         msg="Normalizing loudness")
            if not media.loudnorm(staged, out_file):
                shutil.copy(staged, out_file)
        else:
            shutil.copy(staged, out_file)

        if p.get("cleanup_after"):
            freed = 0
            for it in items:
                f = store.cache_get(it["url"])
                if f and Path(f).exists():
                    try:
                        freed += Path(f).stat().st_size
                        Path(f).unlink(missing_ok=True)
                    except Exception:
                        pass
                store.cache_del(it["url"])
            store.update(job, msg=f"Cleaned sources, freed {freed/1e6:.1f} MB")

        note = f" ({len(job.failed)} link(s) failed)" if job.failed else ""
        store.update(job, status="done", stage="done", progress=1.0,
                     msg=f"Done -> {out_file.name}{note}")
        job.result = str(out_file)

    except JobCancelled:
        store.update(job, status="cancelled", stage="cancelled",
                     msg="Cancelled by user")
        job.error = "cancelled"
    except Exception as e:  # noqa: BLE001
        store.update(job, status="error", stage="error", msg=f"ERROR: {e}")
        job.error = str(e)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _prep_clip(clip, idx, norm, work, W, H, fit, fps, max_clip, smart):
    src = clip["path"]
    if clip.get("normed"):
        return src
    cur = src
    if smart and not clip["image"]:
        cut = work / f"cut_{idx:03d}.mp4"
        if smartcut.smartcut(src, cut):
            cur = cut
    dst = norm / f"n_{idx:03d}.mp4"
    seg = max_clip if (max_clip and max_clip < media.probe_duration(cur)) else None
    ok = media.normalize_clip(cur, dst, width=W, height=H, fit=fit, fps=fps,
                              start=0.0 if seg else None, duration=seg)
    return dst if ok else None


def _build_linear(job, store, clips, norm, work, W, H, fit, fps,
                  max_clip, transition, trans_dur, smart):
    processed: list[Path] = []
    m = len(clips)
    for i, clip in enumerate(clips):
        _ck(job)
        stage = "smartcut" if (smart and not clip["image"]) else "normalize"
        store.update(job, stage=stage, progress=0.40 + (i / max(1, m)) * 0.45,
                     msg=f"Processing [{i+1}/{m}] {Path(clip['path']).name}")
        out = _prep_clip(clip, i, norm, work, W, H, fit, fps, max_clip, smart)
        if out:
            processed.append(out)
        else:
            store.update(job, msg=f"  skip (failed): {Path(clip['path']).name}")

    if not processed:
        raise RuntimeError("All items failed processing.")

    if transition in ("crossfade", "fade") and len(processed) > 1:
        store.update(job, stage="transition", progress=0.88,
                     msg=f"Stitching with {transition} ({trans_dur}s)")
        body = work / "body.mp4"
        if media.crossfade_concat(processed, body, xdur=trans_dur, fps=fps):
            return body
        store.update(job, msg="  crossfade failed, hard-cut fallback")

    store.update(job, stage="concat", progress=0.88, msg="Stitching clips")
    body = work / "body.mp4"
    if not media.concat_demux(processed, body):
        raise RuntimeError("Concat failed.")
    return body


def _build_montage(job, store, clips, norm, work, W, H, fit, fps,
                   music_path, keep_orig_audio, beats_per_cut,
                   sync_source, target_length):
    use_music_beats = bool(music_path) and sync_source != "content"
    if sync_source == "music" and not music_path:
        use_music_beats = False
    if use_music_beats:
        return _montage_music(job, store, clips, norm, work, W, H, fit, fps,
                              music_path, keep_orig_audio, beats_per_cut)
    return _montage_content(job, store, clips, norm, work, W, H, fit, fps,
                            music_path, target_length)


def _montage_music(job, store, clips, norm, work, W, H, fit, fps,
                   music_path, keep_orig_audio, beats_per_cut):
    store.update(job, stage="beats", progress=0.44, msg="Detecting music beats")
    times = beat.beat_times(music_path)
    segs = beat.cut_points(times, beats_per_cut=beats_per_cut)
    if not segs:
        raise RuntimeError("Could not build a beat plan.")
    store.update(job, stage="cut", msg=f"{len(segs)} beat segments planned")

    pieces: list[Path] = []
    ns = len(segs)
    for i, (mstart, seg_dur) in enumerate(segs):
        _ck(job)
        clip = clips[i % len(clips)]
        src, dur = clip["path"], clip["dur"]
        usable = max(0.0, dur - seg_dur)
        start = ((i * 0.6180339) % 1.0) * usable if usable > 0 else 0.0
        dst = norm / f"seg_{i:04d}.mp4"
        if media.extract_segment(src, dst, start, seg_dur,
                                 width=W, height=H, fit=fit, fps=fps):
            pieces.append(dst)
        if i % 5 == 0:
            store.update(job, progress=0.45 + (i / max(1, ns)) * 0.40,
                         msg=f"Cut {i+1}/{ns}")
    if not pieces:
        raise RuntimeError("No montage segments produced.")

    store.update(job, stage="concat", progress=0.87, msg="Assembling montage")
    body = work / "montage.mp4"
    if not media.concat_demux(pieces, body):
        raise RuntimeError("Montage concat failed.")

    store.update(job, stage="music", progress=0.9, msg="Overlaying music")
    withmusic = work / "montage_music.mp4"
    if media.mux_music(body, music_path, withmusic, keep_original=keep_orig_audio):
        return withmusic
    return body


def _montage_content(job, store, clips, norm, work, W, H, fit, fps,
                     music_path, target_length):
    store.update(job, stage="onsets", progress=0.44,
                 msg="Detecting content beats (impacts/speech/claps)")
    plans: list[list[tuple[float, float]]] = []
    for clip in clips:
        _ck(job)
        segs = beat.content_segments(clip["path"], work)
        plans.append(segs)
        store.update(job, msg=f"  {Path(clip['path']).name}: {len(segs)} cut(s)")

    order: list[tuple[int, float, float]] = []
    idxs = [0] * len(clips)
    remaining = sum(len(pl) for pl in plans)
    while remaining > 0:
        for ci, pl in enumerate(plans):
            if idxs[ci] < len(pl):
                start, dur = pl[idxs[ci]]
                order.append((ci, start, dur))
                idxs[ci] += 1
                remaining -= 1

    pieces: list[Path] = []
    acc = 0.0
    ns = len(order)
    for i, (ci, start, dur) in enumerate(order):
        _ck(job)
        if target_length and acc >= target_length:
            break
        dst = norm / f"cseg_{i:04d}.mp4"
        if media.extract_segment(clips[ci]["path"], dst, start, dur,
                                 width=W, height=H, fit=fit, fps=fps):
            pieces.append(dst)
            acc += dur
        if i % 5 == 0:
            store.update(job, progress=0.45 + (i / max(1, ns)) * 0.40,
                         msg=f"Cut {i+1}/{ns} ({acc:.1f}s)")

    if not pieces:
        raise RuntimeError("No content segments produced.")

    store.update(job, stage="concat", progress=0.87,
                 msg="Assembling content montage (native audio kept)")
    body = work / "montage.mp4"
    if not media.concat_demux(pieces, body):
        raise RuntimeError("Montage concat failed.")

    if music_path:
        store.update(job, stage="music", progress=0.9,
                     msg="Adding music under original audio")
        withmusic = work / "montage_music.mp4"
        if media.mux_music(body, music_path, withmusic, keep_original=True,
                           original_gain=1.0):
            return withmusic
    return body
