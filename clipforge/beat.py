"""Beat detection for beat-synced montage.

Uses librosa if available for real beat tracking. Otherwise falls back to
a fixed-BPM grid so montage mode still works without heavy deps.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from . import media
from .media import _run, FFMPEG, probe_duration


def beat_times(music_path: str | Path, fallback_bpm: float = 120.0) -> list[float]:
    """Return a sorted list of beat timestamps (seconds) for the track."""
    try:
        import librosa  # type: ignore
        import numpy as np  # noqa: F401

        y, sr = librosa.load(str(music_path), mono=True)
        tempo, beats = librosa.beat.beat_track(y=y, sr=sr, units="time")
        times = [float(t) for t in beats]
        if len(times) >= 4:
            return times
    except Exception:
        pass
    return _fixed_grid(music_path, fallback_bpm)


def _fixed_grid(music_path: str | Path, bpm: float) -> list[float]:
    dur = probe_duration(music_path)
    if dur <= 0:
        dur = 60.0
    step = 60.0 / max(1.0, bpm)
    n = int(dur / step)
    return [i * step for i in range(n + 1)]


def onset_times(media_path: str | Path, work_dir: str | Path) -> list[float]:
    """Detect audio onsets (hits, claps, speech starts, impacts) in a video or
    audio file. Returns onset timestamps in seconds. Empty list if unavailable.
    """
    from pathlib import Path as _P
    wav = _P(work_dir) / (_P(media_path).stem + ".onset.wav")
    if not media.extract_audio(media_path, wav):
        return []
    try:
        import librosa  # type: ignore
        y, sr = librosa.load(str(wav), mono=True)
        onsets = librosa.onset.onset_detect(
            y=y, sr=sr, units="time", backtrack=True,
        )
        wav.unlink(missing_ok=True)
        return [float(t) for t in onsets]
    except Exception:
        wav.unlink(missing_ok=True)
        return []


def content_segments(
    media_path: str | Path, work_dir: str | Path,
    min_len: float = 0.4, max_len: float = 2.2,
) -> list[tuple[float, float]]:
    """Build (start, duration) segments from a clip's own audio onsets so cuts
    land on the action (impacts, speech, claps, breaths). Preserves native audio
    downstream because callers extract each [start, start+dur] with its audio.
    """
    from .media import probe_duration
    total = probe_duration(media_path)
    ons = onset_times(media_path, work_dir)
    # boundaries: onsets plus start/end, sorted & de-duped
    marks = sorted({0.0, *[o for o in ons if 0 < o < total], total})
    if len(marks) < 2:
        # no onsets found, fall back to fixed 1.5s chunks
        step = 1.5
        marks = [i * step for i in range(int(total / step) + 1)] + [total]
        marks = sorted(set(marks))
    segs: list[tuple[float, float]] = []
    for i in range(len(marks) - 1):
        start = marks[i]
        dur = marks[i + 1] - start
        if dur < min_len:
            continue
        segs.append((start, min(dur, max_len)))
    return segs


def cut_points(times: list[float], beats_per_cut: int = 2,
               min_len: float = 0.35, max_len: float = 2.5) -> list[tuple[float, float]]:
    """Turn beat timestamps into (start, duration) segments, grouping N beats
    per cut and clamping segment length for a watchable rhythm."""
    if len(times) < 2:
        return []
    marks = times[::max(1, beats_per_cut)]
    segs: list[tuple[float, float]] = []
    for i in range(len(marks) - 1):
        start = marks[i]
        dur = marks[i + 1] - start
        if dur < min_len:
            continue
        dur = min(dur, max_len)
        segs.append((start, dur))
    return segs
