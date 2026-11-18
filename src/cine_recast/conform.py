"""Conform a take: one clip per shot with the reference's frame size, frame rate and frame counts.

The take is fitted as a whole into a bit-exact scratch file with the filters, in this order,
fps=<reference rate> -> scale (fill the picture area) -> crop (to the picture area) -> pad (to the
full frame, bars black); its frame count is the take's source_frames. Each shot is then cut from
it by frame range as ProRes 422 HQ. A take with fewer than the unit's frames after start_frame is
refused, or, with allow_retime, resampled evenly to exactly the unit's frames first. The raw file
and meta.json of the take are only read; the shots/ folder and conform.json are rebuilt each run.
"""

import shutil
import tempfile
from pathlib import Path

from cine_recast import plan
from cine_recast.analyze import load_shots, load_video
from cine_recast.cuts import Shot
from cine_recast.errors import CineRecastError
from cine_recast.ffmpeg import cut_frames, probe, run
from cine_recast.jsonio import write_json
from cine_recast.paths import ProjectPaths
from cine_recast.takes import list_takes


def _raw_file(root: Path, unit_id: str, take_id: str) -> Path:
    known = list_takes(root, unit_id)
    if take_id not in known:
        raise CineRecastError(f"unit {unit_id} has no take {take_id}; its takes: {', '.join(known) or 'none'}")
    folder = ProjectPaths(root).take_dir(unit_id, take_id)
    found = sorted(p for p in folder.glob("raw.*") if p.is_file())
    if len(found) != 1:
        raise CineRecastError(f"take {unit_id}/{take_id} must hold exactly one raw file, found "
                              f"{[p.name for p in found] or 'none'}")
    return found[0]


def _unit_shots(root: Path, item: dict) -> list[Shot]:
    """The unit's shots from the analysis, in order; they must cover the unit's frames exactly."""
    by_index = {shot.index: shot for shot in load_shots(root)}
    shots = [by_index.get(index) for index in item.get("shots") or []]
    covered = (bool(shots) and None not in shots
               and all(a.end == b.start for a, b in zip(shots, shots[1:]))
               and shots[0].start == item.get("start") and shots[-1].end == item.get("end"))
    if not covered:
        raise CineRecastError(f"unit {item.get('id')}: shots {item.get('shots')} do not cover frames "
                              f"{item.get('start')}-{item.get('end')} of the analysis")
    return shots


def _lossless(out: Path) -> list[str]:
    """Output arguments for a scratch file: x264 at qp 0 is bit-exact and decodes fast (FFV1 decodes
    several times slower, and every probe decodes the whole file); MOV keeps the exact frame rate."""
    return ["-an", "-c:v", "libx264", "-qp", "0", "-preset", "ultrafast", "-pix_fmt", "yuv422p10le",
            "-fps_mode", "passthrough", str(out)]


def _retime_filters(rate: str, start: int, have: int, need: int) -> list[str]:
    """Filters that resample frames [start, start + have) evenly to exactly `need` frames.

    The first fps makes one time-base tick one frame. setpts puts source frame k at
    ceil(k * need / have - 1/2); the second fps shows at output frame j the last source frame at
    or before it, which is frame floor((j + 1/2) * have / need): centre sampling, so the first and
    the last source frames are both used. tpad and trim make the count exactly `need`.
    """
    return [f"fps={rate}", f"trim=start_frame={start}",
            f"setpts=ceil((2*N*{need}-{have})/(2*{have}))", f"fps={rate}",
            f"tpad=stop_mode=clone:stop={need}", f"trim=end_frame={need}"]


def conform(root: Path, unit_id: str, take_id: str, *, start_frame: int = 0,
            allow_retime: bool = False) -> list[Path]:
    """Write takes/<unit>/<take>/shots/NNNN.mov per shot of the unit and conform.json.

    start_frame is the take frame, counted after the frame-rate conversion, that becomes the
    unit's first frame. Returns the shot files in shot order.
    """
    root = Path(root)
    item = plan.unit(root, unit_id)
    raw = _raw_file(root, unit_id, take_id)
    folder = raw.parent
    if isinstance(start_frame, bool) or not isinstance(start_frame, int) or start_frame < 0:
        raise CineRecastError(f"start_frame must be a whole number of at least 0: {start_frame!r}")
    info, box = load_video(root)
    shots = _unit_shots(root, item)
    unit_frames = item["end"] - item["start"]
    rate = f"{info.fps_num}/{info.fps_den}"

    work = Path(tempfile.mkdtemp(prefix=".conform-", dir=folder))
    try:
        fitted = work / "fitted.mov"
        run(["-i", str(raw), "-map", "0:v:0", "-vf", ",".join([
            f"fps={rate}",
            f"scale={box.w}:{box.h}:force_original_aspect_ratio=increase",
            f"crop={box.w}:{box.h}",
            f"pad={info.width}:{info.height}:{box.x}:{box.y}:black",
        ]), *_lossless(fitted)])
        source_frames = probe(fitted).frames
        have = source_frames - start_frame
        source, offset, retimed = fitted, start_frame, False
        if have < unit_frames:
            if not allow_retime or have <= 0:
                raise CineRecastError(f"take too short: have {max(have, 0)} frames, need {unit_frames}")
            source, offset, retimed = work / "retimed.mov", 0, True
            run(["-i", str(fitted), "-vf", ",".join(_retime_filters(rate, start_frame, have, unit_frames)),
                 *_lossless(source)])

        staged = work / "shots"
        for shot in shots:
            first = offset + shot.start - item["start"]
            out = staged / f"{shot.index:04d}.mov"
            cut_frames(source, first, first + shot.frames, out, codec="prores")
            got = probe(out).frames
            if got != shot.frames:
                raise CineRecastError(f"shot {shot.index}: the conformed clip has {got} frames, "
                                      f"the reference shot has {shot.frames}")

        target, record = folder / "shots", folder / "conform.json"
        try:
            record.unlink(missing_ok=True)      # no record describes shots that are being replaced
            if target.exists():
                shutil.rmtree(target)
            staged.replace(target)
        except OSError as exc:
            raise CineRecastError(f"cannot replace {target} (is a shot file open in another program?): "
                                  f"{exc}") from exc
        write_json(record, {"start_frame": start_frame, "retimed": retimed,
                            "source_frames": source_frames, "unit_frames": unit_frames})
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return [target / f"{shot.index:04d}.mov" for shot in shots]
