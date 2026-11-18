"""Thin, frame-accurate wrappers around ffmpeg and ffprobe."""

import json
import subprocess
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

import numpy as np

from cine_recast.errors import CineRecastError

Crop = tuple[int, int, int, int]


@dataclass(frozen=True)
class VideoInfo:
    width: int
    height: int
    fps_num: int
    fps_den: int
    frames: int
    duration: float
    has_audio: bool
    pix_fmt: str

    @property
    def fps(self) -> float:
        return self.fps_num / self.fps_den


def _tail(text: str) -> str:
    return text.strip()[-400:]


# Every file ffmpeg or ffprobe opens is a local file (or a pipe): a playlist or list file given as an
# input must never make them open a URL, since the tool makes no network calls.
PROTOCOLS = ["-protocol_whitelist", "file,pipe"]


def _guard_inputs(args: list[str]) -> list[str]:
    """args with PROTOCOLS put in front of every "-i" (an input option applies to the next input)."""
    guarded: list[str] = []
    for arg in args:
        if arg == "-i":
            guarded += PROTOCOLS
        guarded.append(arg)
    return guarded


def _ffprobe(path: Path, args: list[str]) -> dict:
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", *args, "-of", "json", *PROTOCOLS, str(path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
    except FileNotFoundError as exc:
        raise CineRecastError("ffprobe was not found on PATH") from exc
    if proc.returncode != 0:
        raise CineRecastError(f"ffprobe failed for {path}: {_tail(proc.stderr)}")
    return json.loads(proc.stdout or "{}")


def probe(path: Path) -> VideoInfo:
    """Read stream facts. `frames` is the number of frames actually decoded."""
    path = Path(path)
    video = _ffprobe(path, [
        "-count_frames", "-select_streams", "v:0",
        "-show_entries",
        "stream=width,height,r_frame_rate,avg_frame_rate,nb_read_frames,pix_fmt:format=duration",
    ])
    streams = video.get("streams") or []
    if not streams:
        raise CineRecastError(f"no video stream in {path}")
    s = streams[0]
    rate = Fraction(s.get("r_frame_rate") or "0/1") if s.get("r_frame_rate") not in (None, "0/0") else Fraction(0)
    if rate == 0:
        rate = Fraction(s.get("avg_frame_rate") or "0/1")
    if rate == 0:
        raise CineRecastError(f"cannot determine frame rate of {path}")
    audio = _ffprobe(path, ["-select_streams", "a", "-show_entries", "stream=index"])
    try:
        duration = float((video.get("format") or {}).get("duration") or 0.0)
    except ValueError:
        duration = 0.0
    return VideoInfo(
        width=int(s["width"]), height=int(s["height"]),
        fps_num=rate.numerator, fps_den=rate.denominator,
        frames=int(s["nb_read_frames"]), duration=duration,
        has_audio=bool(audio.get("streams")), pix_fmt=str(s.get("pix_fmt", "")),
    )


def _rate(text) -> Fraction:
    """An ffprobe rate such as "24000/1001"; 0 when absent or unknown ("0/0")."""
    num, _, den = str(text or "").partition("/")
    try:
        n, d = int(num), int(den or 1)
    except ValueError:
        return Fraction(0)
    return Fraction(n, d) if d else Fraction(0)


def stream_facts(path: Path) -> dict:
    """What decides whether a file can serve as a reference, without decoding a frame.

    Returns {"video": bool (a video stream exists), "formats": the container's format_name tokens,
    "rotation": degrees from the first video stream's display matrix or rotate tag (0.0 when none),
    "sar": its sample aspect ratio as a Fraction, or None when absent or unknown,
    "avg_rate", "r_rate": its avg_frame_rate and r_frame_rate as Fractions (0 when unknown)}.
    Raises CineRecastError when ffprobe cannot read the file.
    """
    path = Path(path)
    data = _ffprobe(path, ["-select_streams", "v:0", "-show_entries",
                           "stream=codec_type,r_frame_rate,avg_frame_rate,sample_aspect_ratio"
                           ":stream_side_data=rotation:stream_tags=rotate:format=format_name"])
    formats = [token for token in str((data.get("format") or {}).get("format_name") or "").split(",") if token]
    streams = data.get("streams") or []
    if not streams:
        return {"video": False, "formats": formats, "rotation": 0.0, "sar": None,
                "avg_rate": Fraction(0), "r_rate": Fraction(0)}
    s = streams[0]
    rotation = 0.0
    for value in [*(entry.get("rotation") for entry in s.get("side_data_list") or [] if isinstance(entry, dict)),
                  (s.get("tags") or {}).get("rotate")]:
        try:
            if value is not None and float(value) % 360 != 0:
                rotation = float(value)
        except (TypeError, ValueError):
            continue
    num, _, den = str(s.get("sample_aspect_ratio") or "").partition(":")
    sar = Fraction(int(num), int(den)) if num.isdigit() and den.isdigit() and int(num) and int(den) else None
    return {"video": True, "formats": formats, "rotation": rotation, "sar": sar,
            "avg_rate": _rate(s.get("avg_frame_rate")), "r_rate": _rate(s.get("r_frame_rate"))}


def probe_size(path: Path) -> tuple[int, int]:
    """(width, height) from stream metadata only; no frame is decoded."""
    path = Path(path)
    data = _ffprobe(path, ["-select_streams", "v:0", "-show_entries", "stream=width,height"])
    streams = data.get("streams") or []
    if not streams:
        raise CineRecastError(f"no video stream in {path}")
    return int(streams[0]["width"]), int(streams[0]["height"])


def run(args: list[str]) -> None:
    """Run ffmpeg with the given arguments. Raises CineRecastError with the stderr tail on failure.

    Every input ("-i") is opened with the file and pipe protocols only.
    """
    try:
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostdin", "-y", *_guard_inputs(args)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        )
    except FileNotFoundError as exc:
        raise CineRecastError("ffmpeg was not found on PATH") from exc
    if proc.returncode != 0:
        raise CineRecastError(f"ffmpeg failed: {_tail(proc.stderr)}")


SCALE_FLAGS = "flags=bicubic+accurate_rnd+full_chroma_int"   # keeps mid-grey at 128 when scaling


def _scale_filter(size: tuple[int, int]) -> str:
    return f"scale={size[0]}:{size[1]}:{SCALE_FLAGS}"


def _crop_filter(crop: Crop) -> str:
    x, y, w, h = crop
    return f"crop={w}:{h}:{x}:{y}"


def _trim_filter(start: int, end: int | None) -> str:
    spec = f"trim=start_frame={start}"
    if end is not None:
        spec += f":end_frame={end}"
    return spec + ",setpts=PTS-STARTPTS"


def iter_frames(path: Path, *, size: tuple[int, int] | None = None, crop: Crop | None = None,
                start: int = 0, count: int | None = None, gray: bool = False) -> Iterator[np.ndarray]:
    """Yield decoded frames as uint8 arrays: RGB (h, w, 3), or gray (h, w).

    Order of operations: frame window, crop (x, y, w, h), scale to size (w, h).
    """
    path = Path(path)
    filters = [_trim_filter(start, None if count is None else start + count)]
    if crop is not None:
        filters.append(_crop_filter(crop))
    if size is not None:
        filters.append(_scale_filter(size))
    if size is not None:
        out_w, out_h = size
    elif crop is not None:
        out_w, out_h = crop[2], crop[3]
    else:
        out_w, out_h = probe_size(path)
    pix = "gray" if gray else "rgb24"
    channels = 1 if gray else 3
    frame_bytes = out_w * out_h * channels
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", *PROTOCOLS, "-i", str(path),
           "-vf", ",".join(filters), "-fps_mode", "passthrough",
           "-f", "rawvideo", "-pix_fmt", pix, "pipe:1"]
    with tempfile.TemporaryFile() as err:
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err)
        except FileNotFoundError as exc:
            raise CineRecastError("ffmpeg was not found on PATH") from exc
        assert proc.stdout is not None
        finished = False
        try:
            while True:
                buf = proc.stdout.read(frame_bytes)
                if len(buf) < frame_bytes:
                    finished = True
                    break
                arr = np.frombuffer(buf, dtype=np.uint8)
                yield arr.reshape((out_h, out_w) if gray else (out_h, out_w, 3)).copy()
        finally:
            proc.stdout.close()
            if not finished and proc.poll() is None:
                proc.kill()
            proc.wait()
        if finished and proc.returncode != 0:
            err.seek(0)
            text = err.read().decode("utf-8", errors="replace")
            raise CineRecastError(f"ffmpeg failed while decoding {path}: {_tail(text)}")


def extract_still(path: Path, frame: int, out: Path, *, crop: Crop | None = None) -> None:
    """Write one frame (zero-based index) as a PNG."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    filters = [_trim_filter(frame, frame + 1)]
    if crop is not None:
        filters.append(_crop_filter(crop))
    run(["-i", str(path), "-vf", ",".join(filters), "-frames:v", "1",
         "-fps_mode", "passthrough", str(out)])
    if not out.exists():
        raise CineRecastError(f"frame {frame} is outside the clip: {path}")


def cut_frames(path: Path, start: int, end: int, out: Path, *, crop: Crop | None = None,
               codec: str = "h264", audio: bool = False) -> None:
    """Re-encode frames [start, end) of a clip. codec is "h264" or "prores"."""
    if end <= start:
        raise CineRecastError(f"empty frame range [{start}, {end})")
    if codec not in ("h264", "prores"):
        raise CineRecastError(f"unknown codec: {codec}")
    path, out = Path(path), Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    filters = [_trim_filter(start, end)]
    if crop is not None:
        filters.append(_crop_filter(crop))
    args = ["-i", str(path), "-vf", ",".join(filters), "-fps_mode", "passthrough"]
    if codec == "h264":
        args += ["-c:v", "libx264", "-crf", "14", "-preset", "veryfast", "-pix_fmt", "yuv420p"]
    else:
        args += ["-c:v", "prores_ks", "-profile:v", "3", "-pix_fmt", "yuv422p10le"]
    info = probe(path) if audio else None
    if info is not None and info.has_audio:
        t0 = Fraction(start * info.fps_den, info.fps_num)
        t1 = Fraction(end * info.fps_den, info.fps_num)
        args += ["-af", f"atrim=start={float(t0):.6f}:end={float(t1):.6f},asetpts=PTS-STARTPTS",
                 "-c:a", "aac" if codec == "h264" else "pcm_s16le"]
    else:
        args += ["-an"]
    run([*args, str(out)])
