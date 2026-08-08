"""ffmpeg / ffprobe helpers: probe, normalize, trim, concat, mux."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def _find_binary(name: str) -> str | None:
    """Locate ffmpeg/ffprobe: env override -> bundled bin/ -> PATH -> imageio."""
    exe = name + (".exe" if os.name == "nt" else "")
    env = os.environ.get(f"CLIPFORGE_{name.upper()}")
    if env and Path(env).exists():
        return env

    bases: list[Path] = []
    if getattr(sys, "frozen", False):  # PyInstaller bundle
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            bases.append(Path(meipass) / "bin")
        bases.append(Path(sys.executable).parent / "bin")
    bases.append(Path(__file__).resolve().parent.parent / "bin")
    for b in bases:
        cand = b / exe
        if cand.exists():
            return str(cand)

    found = shutil.which(name)
    if found:
        return found

    if name == "ffmpeg":  # last-resort: pip-provided static ffmpeg
        try:
            import imageio_ffmpeg  # type: ignore
            return imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            return None
    return None


FFMPEG = _find_binary("ffmpeg") or "ffmpeg"
FFPROBE = _find_binary("ffprobe") or "ffprobe"


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


_VCODEC_ARGS = None


def video_codec_args() -> list[str]:
    """Pick the fastest working H.264 encoder once: GPU (NVENC/QSV/AMF) if
    available, else CPU libx264. GPU encoding is several times faster, which is
    the main lever on export speed."""
    global _VCODEC_ARGS
    if _VCODEC_ARGS is not None:
        return _VCODEC_ARGS
    candidates = [
        ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "23"],
        ["-c:v", "h264_qsv", "-global_quality", "23"],
        ["-c:v", "h264_amf", "-rc", "cqp", "-qp_i", "23", "-qp_p", "23"],
    ]
    for args in candidates:
        cp = _run([FFMPEG, "-hide_banner", "-f", "lavfi",
                   "-i", "testsrc=d=0.2:s=320x240", *args, "-f", "null", "-"])
        if cp.returncode == 0:
            _VCODEC_ARGS = args + ["-pix_fmt", "yuv420p"]
            return _VCODEC_ARGS
    _VCODEC_ARGS = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                    "-pix_fmt", "yuv420p"]
    return _VCODEC_ARGS


def probe_duration(path: str | Path) -> float:
    """Return media duration in seconds (0.0 on failure)."""
    cp = _run([
        FFPROBE, "-v", "error",
        "-show_entries", "format=duration",
        "-of", "json", str(path),
    ])
    if cp.returncode != 0:
        return 0.0
    try:
        return float(json.loads(cp.stdout)["format"]["duration"])
    except (KeyError, ValueError, json.JSONDecodeError):
        return 0.0


def has_audio(path: str | Path) -> bool:
    cp = _run([
        FFPROBE, "-v", "error",
        "-select_streams", "a",
        "-show_entries", "stream=index",
        "-of", "csv=p=0", str(path),
    ])
    return bool(cp.stdout.strip())


def dims(aspect: str, p: int) -> tuple[int, int]:
    """Output (width, height) for an aspect ratio and a quality 'p' (minor axis)."""
    p = int(p)
    if aspect == "9:16":
        w, h = p, round(p * 16 / 9)
    elif aspect == "1:1":
        w, h = p, p
    else:  # 16:9
        w, h = round(p * 16 / 9), p
    if w % 2:
        w += 1
    if h % 2:
        h += 1
    return w, h


def _scale_filter(w: int, h: int, fit: str, fps: int) -> str:
    if fit == "crop":  # fill frame, center-crop overflow (smart crop)
        return (f"scale={w}:{h}:force_original_aspect_ratio=increase,"
                f"crop={w}:{h},setsar=1,fps={fps},format=yuv420p")
    # pad (letterbox/pillarbox), no content lost
    return (f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,"
            f"setsar=1,fps={fps},format=yuv420p")


def normalize_clip(
    src: str | Path,
    dst: str | Path,
    width: int = 1920,
    height: int = 1080,
    fit: str = "pad",
    fps: int = 30,
    start: float | None = None,
    duration: float | None = None,
) -> bool:
    """Re-encode a clip to a canonical WxH/fps so clips concat cleanly.

    fit: 'pad' (letterbox, keep all) or 'crop' (fill + center-crop). Always
    produces an audio track so concat works. Optionally trims [start, start+dur].
    """
    src, dst = str(src), str(dst)
    w, h = int(width), int(height)
    if w % 2:
        w += 1
    if h % 2:
        h += 1
    vf = _scale_filter(w, h, fit, fps)

    cmd = [FFMPEG, "-y"]
    if start is not None:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", src]
    if duration is not None:
        cmd += ["-t", f"{duration:.3f}"]

    audio_ok = has_audio(src)
    if not audio_ok:
        # synthesize silent audio so every normalized clip has a track
        cmd += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
        cmd += ["-shortest"]

    cmd += [
        "-vf", vf,
        *video_codec_args(),
        "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
        "-map", "0:v:0",
        "-map", ("1:a:0" if not audio_ok else "0:a:0?"),
        dst,
    ]
    cp = _run(cmd)
    return cp.returncode == 0 and Path(dst).exists()


def extract_segment(
    src: str | Path, dst: str | Path, start: float, duration: float,
    width: int = 1920, height: int = 1080, fit: str = "pad", fps: int = 30,
) -> bool:
    """Cut an exact [start, start+duration] segment, normalized. For montage."""
    return normalize_clip(src, dst, width=width, height=height, fit=fit,
                          fps=fps, start=start, duration=duration)


def trim(src: str | Path, dst: str | Path, start: float,
         duration: float | None) -> bool:
    """Re-encode a native-size clip cut to [start, start+duration] (accurate)."""
    cmd = [FFMPEG, "-y", "-ss", f"{start:.3f}", "-i", str(src)]
    if duration is not None:
        cmd += ["-t", f"{duration:.3f}"]
    cmd += [
        *video_codec_args(),
        "-c:a", "aac", "-b:a", "160k", str(dst),
    ]
    cp = _run(cmd)
    return cp.returncode == 0 and Path(dst).exists()


def loudnorm(src: str | Path, dst: str | Path) -> bool:
    """Normalize perceived loudness (EBU R128) so clips match in volume."""
    cp = _run([
        FFMPEG, "-y", "-i", str(src),
        "-c:v", "copy",
        "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
        "-c:a", "aac", "-b:a", "192k",
        str(dst),
    ])
    return cp.returncode == 0 and Path(dst).exists()


def concat_demux(clips: list[str | Path], dst: str | Path) -> bool:
    """Concatenate pre-normalized clips (identical params) via demuxer copy."""
    clips = [str(c) for c in clips if Path(c).exists()]
    if not clips:
        return False
    listfile = Path(dst).with_suffix(".concat.txt")
    with open(listfile, "w", encoding="utf-8") as f:
        for c in clips:
            safe = str(Path(c).resolve()).replace("\\", "/").replace("'", "'\\''")
            f.write(f"file '{safe}'\n")
    cp = _run([
        FFMPEG, "-y", "-f", "concat", "-safe", "0",
        "-i", str(listfile), "-c", "copy", str(dst),
    ])
    listfile.unlink(missing_ok=True)
    if cp.returncode == 0 and Path(dst).exists():
        return True
    # fallback: re-encode concat (handles minor param drift)
    cp2 = _run([
        FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(listfile),
        *video_codec_args(),
        "-c:a", "aac", "-b:a", "160k", str(dst),
    ])
    return cp2.returncode == 0 and Path(dst).exists()


def extract_audio(src: str | Path, dst: str | Path) -> bool:
    """Extract audio to mono 22.05k wav (for onset/beat analysis)."""
    cp = _run([
        FFMPEG, "-y", "-i", str(src), "-vn",
        "-ac", "1", "-ar", "22050", "-f", "wav", str(dst),
    ])
    return cp.returncode == 0 and Path(dst).exists()


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tiff"}


def ken_burns_image(
    src: str | Path, dst: str | Path, duration: float = 4.0,
    width: int = 1920, height: int = 1080, fit: str = "pad",
    fps: int = 30, direction: int = 0,
) -> bool:
    """Turn a still image into a slow pan/zoom clip with silent audio.

    direction: 0 zoom-in center, 1 zoom-out, 2 pan-right, 3 pan-down.
    """
    src, dst = str(src), str(dst)
    w, h = int(width), int(height)
    if w % 2:
        w += 1
    if h % 2:
        h += 1
    frames = max(1, int(round(duration * fps)))
    # oversample so pan/zoom has room, then zoompan
    zoom_in = "min(zoom+0.0012,1.35)"
    if direction == 1:
        z = "if(eq(on,1),1.35,max(zoom-0.0012,1.0))"
        x, y = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    elif direction == 2:
        z = zoom_in
        x, y = "min(x+2,iw-(iw/zoom))", "ih/2-(ih/zoom/2)"
    elif direction == 3:
        z = zoom_in
        x, y = "iw/2-(iw/zoom/2)", "min(y+2,ih-(ih/zoom))"
    else:
        z = zoom_in
        x, y = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"

    vf = (
        f"scale={w*2}:{h*2}:force_original_aspect_ratio=increase,"
        f"crop={w*2}:{h*2},"
        f"zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={w}x{h}:fps={fps},"
        f"setsar=1,format=yuv420p"
    )
    cmd = [
        FFMPEG, "-y", "-loop", "1", "-i", src,
        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-t", f"{duration:.3f}",
        "-vf", vf,
        *video_codec_args(),
        "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
        "-shortest", dst,
    ]
    cp = _run(cmd)
    return cp.returncode == 0 and Path(dst).exists()


def crossfade_concat(
    clips: list[str | Path], dst: str | Path,
    xdur: float = 0.5, fps: int = 30,
) -> bool:
    """Concatenate normalized clips with a video+audio crossfade between each.

    Clips must already share resolution/fps/audio params. Falls back to plain
    concat if anything goes wrong.
    """
    clips = [str(c) for c in clips if Path(c).exists()]
    if len(clips) < 2:
        return concat_demux(clips, dst)

    durs = [probe_duration(c) for c in clips]
    if any(d <= xdur for d in durs):
        # clips too short to crossfade safely
        return concat_demux(clips, dst)

    inputs: list[str] = []
    for c in clips:
        inputs += ["-i", c]

    vparts, aparts = [], []
    # running offset where each successive xfade begins
    prev_v, prev_a = "0:v", "0:a"
    offset = durs[0] - xdur
    for i in range(1, len(clips)):
        vout = f"v{i}"
        aout = f"a{i}"
        vparts.append(
            f"[{prev_v}][{i}:v]xfade=transition=fade:duration={xdur}:"
            f"offset={offset:.3f}[{vout}]"
        )
        aparts.append(
            f"[{prev_a}][{i}:a]acrossfade=d={xdur}[{aout}]"
        )
        prev_v, prev_a = vout, aout
        offset += durs[i] - xdur

    filt = ";".join(vparts + aparts)
    cmd = [
        FFMPEG, "-y", *inputs, "-filter_complex", filt,
        "-map", f"[{prev_v}]", "-map", f"[{prev_a}]",
        *video_codec_args(),
        "-c:a", "aac", "-b:a", "160k", str(dst),
    ]
    cp = _run(cmd)
    if cp.returncode == 0 and Path(dst).exists():
        return True
    return concat_demux(clips, dst)


def mux_music(
    video: str | Path, music: str | Path, dst: str | Path,
    keep_original: bool = False, original_gain: float = 0.25,
) -> bool:
    """Replace/overlay audio track with music, trimmed to video length."""
    video, music, dst = str(video), str(music), str(dst)
    if keep_original and has_audio(video):
        filt = (
            f"[0:a]volume={original_gain}[a0];"
            f"[a0][1:a]amix=inputs=2:duration=first:dropout_transition=0[aout]"
        )
        cmd = [
            FFMPEG, "-y", "-i", video, "-i", music,
            "-filter_complex", filt,
            "-map", "0:v:0", "-map", "[aout]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-shortest", dst,
        ]
    else:
        cmd = [
            FFMPEG, "-y", "-i", video, "-i", music,
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-shortest", dst,
        ]
    cp = _run(cmd)
    return cp.returncode == 0 and Path(dst).exists()
