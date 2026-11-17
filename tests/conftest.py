"""Synthetic clip helpers shared by every test. Clips are generated with ffmpeg lavfi sources."""

import subprocess
from pathlib import Path

import pytest


def _encode_args(path: Path) -> list[str]:
    return ["-c:v", "libx264", "-crf", "14", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            "-fps_mode", "passthrough", str(path)]


def _run(args: list[str]) -> None:
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostdin", "-y", *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {proc.stderr[-400:]}")


def make_clip(path: Path, segments: list[tuple[str, int]], *, size: tuple[int, int] = (384, 216),
              fps: str = "24", bars: int = 0, side_bars: int = 0, logo: bool = False,
              dark: bool = False, audio: bool = False) -> Path:
    """Build a clip from lavfi sources. segments: (source name, frame count) pairs.

    A source may carry its own options, such as "color=c=white"; size and rate are appended.

    bars is the pixel height of black bands at the top and bottom (total size stays `size`).
    side_bars is the pixel width of black bands at the left and right.
    logo draws a 24x12 white box at the left of the top band.
    dark applies a low-contrast lifted-black grade.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    w, h = size
    args: list[str] = []
    chains: list[str] = []
    for i, (source, frames) in enumerate(segments):
        sep = ":" if "=" in source else "="
        args += ["-f", "lavfi", "-i", f"{source}{sep}size={w}x{h}:rate={fps}"]
        chains.append(f"[{i}:v]trim=end_frame={frames},setpts=PTS-STARTPTS,setsar=1,format=yuv420p[v{i}]")
    if audio:
        args += ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000"]
    joined = "".join(f"[v{i}]" for i in range(len(segments)))
    post: list[str] = []
    if dark:
        post.append("lutyuv=y=val*0.25+18")
    if bars > 0:
        post.append(f"drawbox=x=0:y=0:w=iw:h={bars}:color=black:t=fill")
        post.append(f"drawbox=x=0:y=ih-{bars}:w=iw:h={bars}:color=black:t=fill")
    if side_bars > 0:
        post.append(f"drawbox=x=0:y=0:w={side_bars}:h=ih:color=black:t=fill")
        post.append(f"drawbox=x=iw-{side_bars}:y=0:w={side_bars}:h=ih:color=black:t=fill")
    if logo:
        post.append(f"drawbox=x={side_bars + 8}:y={max(0, (bars - 12) // 2)}:w=24:h=12:color=white:t=fill")
    post.append("format=yuv420p")
    chains.append(f"{joined}concat=n={len(segments)}:v=1:a=0[c]")
    chains.append("[c]" + ",".join(post) + "[out]")
    args += ["-filter_complex", ";".join(chains), "-map", "[out]"]
    if audio:
        args += ["-map", f"{len(segments)}:a", "-c:a", "aac", "-shortest"]
    args += _encode_args(path)
    _run(args)
    return path


def make_motion_clip(path: Path, kind: str, *, frames: int = 24, rate: float = 4.0,
                     size: tuple[int, int] = (384, 216)) -> Path:
    """A clip whose content is one fixed textured image, moved per frame.

    kind: "static", "pan" (content moves right by `rate` pixels per frame) or
    "zoom" (content magnified by `rate` times per frame).
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    w, h = size
    if kind == "static":
        graph = (f"testsrc2=size={w}x{h}:rate=24,trim=end_frame=1,loop=loop={frames - 1}:size=1,"
                 "setpts=N/(24*TB),format=yuv420p")
    elif kind == "pan":
        pad = int(rate * frames) + 2
        graph = (f"testsrc2=size={w + pad}x{h}:rate=24,trim=end_frame=1,loop=loop={frames - 1}:size=1,"
                 f"setpts=N/(24*TB),crop=w={w}:h={h}:x='{pad}-2-n*{rate}':y=0,format=yuv420p")
    elif kind == "zoom":
        graph = (f"testsrc2=size={w}x{h}:rate=24,trim=end_frame=1,loop=loop={frames - 1}:size=1,"
                 f"setpts=N/(24*TB),scale=w='trunc(iw*pow({rate},n)/2)*2':h='trunc(ih*pow({rate},n)/2)*2':eval=frame,"
                 f"crop=w={w}:h={h}:x='(iw-{w})/2':y='(ih-{h})/2',format=yuv420p")
    else:
        raise ValueError(f"unknown motion kind: {kind}")
    _run(["-f", "lavfi", "-i", graph, "-frames:v", str(frames), *_encode_args(path)])
    return path


def make_sweep_clip(path: Path, *, frames: int = 40, sweep_start: int = 18,
                    size: tuple[int, int] = (384, 216)) -> Path:
    """One static smptebars shot, no cut, crossed by a fast occluder.

    A full-height black box 5/12 of the frame wide is just off the left edge at frame
    sweep_start and just off the right edge at frame sweep_start + 5, eased so it moves fastest
    in the middle. The frame differences form a broad peak instead of one isolated spike.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    w, h = size
    box_w = w * 5 // 12
    p = f"clip((t*24-{sweep_start})/5,0,1)"
    x = f"{w + box_w}*{p}*{p}*(3-2*{p})-{box_w}"
    graph = (f"[0:v]trim=end_frame={frames},setpts=PTS-STARTPTS,setsar=1,format=yuv420p[bg];"
             f"[1:v]setsar=1,format=yuv420p[box];"
             f"[bg][box]overlay=x='{x}':y=0:eval=frame:shortest=1,format=yuv420p[out]")
    _run(["-f", "lavfi", "-i", f"smptebars=size={w}x{h}:rate=24",
          "-f", "lavfi", "-i", f"color=c=black:size={box_w}x{h}:rate=24",
          "-filter_complex", graph, "-map", "[out]", "-frames:v", str(frames), *_encode_args(path)])
    return path


@pytest.fixture(scope="session")
def clip3(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("clips") / "clip3.mp4"
    return make_clip(path, [("testsrc2", 30), ("smptebars", 24), ("mandelbrot", 36)])


@pytest.fixture
def project3(tmp_path, clip3):
    from cine_recast.project import init_project
    return init_project(tmp_path / "p", clip3, "t")
