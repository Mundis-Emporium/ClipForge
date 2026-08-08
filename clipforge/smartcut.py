"""Smart auto-cut: remove silence / dead air from a clip.

Uses `auto-editor` CLI if installed (best results). Falls back to an
ffmpeg silencedetect + trim pipeline so the feature works without it.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from .media import _run, FFMPEG, probe_duration, has_audio


def _auto_editor_available() -> bool:
    return shutil.which("auto-editor") is not None


def smartcut(src: str | Path, dst: str | Path,
             silence_db: int = -30, margin: float = 0.2) -> bool:
    """Trim dead air. Returns True and writes `dst`; False if nothing done."""
    src, dst = str(src), str(dst)
    if not has_audio(src):
        return False
    if _auto_editor_available():
        return _via_auto_editor(src, dst, silence_db, margin)
    return _via_ffmpeg(src, dst, silence_db, margin)


def _via_auto_editor(src: str, dst: str, silence_db: int, margin: float) -> bool:
    cmd = [
        "auto-editor", src,
        "--edit", f"audio:threshold={_db_to_pct(silence_db)}%",
        "--margin", f"{margin}s",
        "--no-open",
        "-o", dst,
    ]
    cp = _run(cmd)
    return cp.returncode == 0 and Path(dst).exists()


def _db_to_pct(db: int) -> int:
    # crude mapping from dBFS threshold to auto-editor's 0-100 loudness %
    return max(1, min(20, int(round((db + 60) / 60 * 20))))


def _via_ffmpeg(src: str, dst: str, silence_db: int, margin: float) -> bool:
    """Detect loud (non-silent) spans, keep them, drop the rest."""
    total = probe_duration(src)
    cp = _run([
        FFMPEG, "-i", src, "-af",
        f"silencedetect=noise={silence_db}dB:d=0.4",
        "-f", "null", "-",
    ])
    log = cp.stderr
    starts = [float(m) for m in re.findall(r"silence_start:\s*([0-9.]+)", log)]
    ends = [float(m) for m in re.findall(r"silence_end:\s*([0-9.]+)", log)]

    # build keep spans = complement of silence spans
    silences = list(zip(starts, ends + [total] * (len(starts) - len(ends))))
    keeps: list[tuple[float, float]] = []
    cur = 0.0
    for s, e in silences:
        s = max(0.0, s - margin)
        e = min(total, e + margin)
        if s > cur:
            keeps.append((cur, s))
        cur = max(cur, e)
    if cur < total:
        keeps.append((cur, total))
    keeps = [(a, b) for a, b in keeps if b - a > 0.15]
    if not keeps or (len(keeps) == 1 and keeps[0] == (0.0, total)):
        return False

    select = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in keeps)
    vf = f"select='{select}',setpts=N/FRAME_RATE/TB"
    af = f"aselect='{select}',asetpts=N/SR/TB"
    cp2 = _run([
        FFMPEG, "-y", "-i", src,
        "-vf", vf, "-af", af,
        *media.video_codec_args(),
        "-c:a", "aac", "-b:a", "160k", dst,
    ])
    return cp2.returncode == 0 and Path(dst).exists()
