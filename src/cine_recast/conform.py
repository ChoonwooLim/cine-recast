"""Conform a take: one clip per shot with the reference's frame size, frame rate and frame counts.

The take is fitted as a whole into a bit-exact scratch file (_fit) with the filters, in this order,
fps=<reference rate> -> scale (fill the picture area) -> crop (to the picture area) -> pad (to the
full frame, bars black); its frame count is the take's source_frames, and take frame numbers count
its frames. Each shot is then cut from it by frame range as ProRes 422 HQ.

- Without take_cuts the take lines up with the unit frame for frame from start_frame. A take with
  fewer than the unit's frames after start_frame is refused, or, with allow_retime, resampled evenly
  to exactly the unit's frames first.
- With take_cuts (the take frame where each shot starts, then the frame after the last shot), each
  shot is cut from its own segment, resampled evenly to the shot's frame count when the counts differ.

The raw file and meta.json of the take are only read; the shots/ folder and conform.json are rebuilt
each run. scan_take reports what helps to choose take_cuts and writes nothing into the take folder.
"""

import shutil
import tempfile
from pathlib import Path

from cine_recast import plan
from cine_recast.analyze import load_shots, load_video
from cine_recast.cuts import Shot, detect_cuts, held_tail_start
from cine_recast.errors import CineRecastError
from cine_recast.ffmpeg import VideoInfo, cut_frames, probe, run
from cine_recast.jsonio import write_json
from cine_recast.paths import ProjectPaths
from cine_recast.picture import PictureBox
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


def _fit(raw: Path, out: Path, info: VideoInfo, box: PictureBox) -> VideoInfo:
    """Fit the take into the scratch file `out` with the reference's frame rate, picture area and
    frame size, and return the facts of `out`. Take frame numbers count the frames of this file."""
    rate = f"{info.fps_num}/{info.fps_den}"
    run(["-i", str(raw), "-map", "0:v:0", "-vf", ",".join([
        f"fps={rate}",
        f"scale={box.w}:{box.h}:force_original_aspect_ratio=increase",
        f"crop={box.w}:{box.h}",
        f"pad={info.width}:{info.height}:{box.x}:{box.y}:black",
    ]), *_lossless(out)])
    return probe(out)


def _retime_filters(rate: str, start: int, have: int, need: int, *, bounded: bool = False) -> list[str]:
    """Filters that resample frames [start, start + have) evenly to exactly `need` frames.

    The first fps makes one time-base tick one frame. setpts puts source frame k at
    ceil(k * need / have - 1/2); the second fps shows at output frame j the last source frame at
    or before it, which is frame floor((j + 1/2) * have / need): centre sampling, so the first and
    the last source frames are both used. tpad and trim make the count exactly `need`. With bounded,
    the frames from start + have on (the next shot of a take) are cut away before the resampling.
    """
    trim = f"trim=start_frame={start}" + (f":end_frame={start + have}" if bounded else "")
    return [f"fps={rate}", trim,
            f"setpts=ceil((2*N*{need}-{have})/(2*{have}))", f"fps={rate}",
            f"tpad=stop_mode=clone:stop={need}", f"trim=end_frame={need}"]


def _cut_shot(source: Path, first: int, shot: Shot, out: Path) -> None:
    """Cut source frames [first, first + shot.frames) as the shot's ProRes clip and check its count."""
    cut_frames(source, first, first + shot.frames, out, codec="prores")
    got = probe(out).frames
    if got != shot.frames:
        raise CineRecastError(f"shot {shot.index}: the conformed clip has {got} frames, "
                              f"the reference shot has {shot.frames}")


def _checked_take_cuts(take_cuts, shots: list[Shot], unit_id: str) -> list[int]:
    """take_cuts as a list, refused unless it holds one value per shot and one more, whole numbers in
    strictly ascending order from 0 or more, and every segment is within 25 percent of its shot's
    frame count (a larger difference means wrong boundaries or a take that changed the shot)."""
    values = list(take_cuts) if isinstance(take_cuts, (list, tuple)) else take_cuts
    if not isinstance(values, list):
        raise CineRecastError(f"--take-cuts must be a list of whole frame numbers, not {take_cuts!r}")
    if len(values) != len(shots) + 1:
        raise CineRecastError(
            f"--take-cuts needs {len(shots) + 1} values for the {len(shots)} shots of unit {unit_id} (the take "
            f"frame where each shot starts, then the frame after its last shot), got {len(values)}: {values}")
    whole = all(isinstance(v, int) and not isinstance(v, bool) for v in values)
    if not whole or values[0] < 0 or any(b <= a for a, b in zip(values, values[1:])):
        raise CineRecastError(f"--take-cuts must be whole numbers in strictly ascending order starting at 0 or "
                              f"more: {values}")
    for shot, a, b in zip(shots, values, values[1:]):
        have, need = b - a, shot.frames
        if 4 * abs(have - need) > need:
            raise CineRecastError(
                f"--take-cuts: shot {shot.index}: its segment {a}-{b} holds {have} take frames, the reference "
                f"shot has {need} frames, more than 25 percent apart; the boundaries are probably wrong (look at "
                "the take frames around them; takes cuts lists the cuts it finds), or the take must be generated "
                "again")
    return values


def conform(root: Path, unit_id: str, take_id: str, *, start_frame: int = 0,
            allow_retime: bool = False, take_cuts: list[int] | None = None) -> list[Path]:
    """Write takes/<unit>/<take>/shots/NNNN.mov per shot of the unit and conform.json.

    start_frame is the take frame, counted after the frame-rate conversion, that becomes the
    unit's first frame. take_cuts, counted the same way, gives each shot its own take segment
    [take_cuts[i], take_cuts[i + 1]); it is not combined with start_frame or allow_retime. Returns
    the shot files in shot order.
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
    if take_cuts is not None:
        if start_frame != 0 or allow_retime:
            raise CineRecastError("--take-cuts cannot be combined with --start-frame or --allow-retime: its first "
                                  "value is where the unit starts, and each segment is resampled to its shot's "
                                  "frame count when the counts differ")
        take_cuts = _checked_take_cuts(take_cuts, shots, unit_id)

    work = Path(tempfile.mkdtemp(prefix=".conform-", dir=folder))
    try:
        fitted = work / "fitted.mov"
        source_frames = _fit(raw, fitted, info, box).frames
        staged = work / "shots"
        if take_cuts is None:
            have = source_frames - start_frame
            source, offset, retimed = fitted, start_frame, False
            if have < unit_frames:
                if not allow_retime or have <= 0:
                    raise CineRecastError(f"take too short: have {max(have, 0)} frames, need {unit_frames}")
                source, offset, retimed = work / "retimed.mov", 0, True
                run(["-i", str(fitted), "-vf", ",".join(_retime_filters(rate, start_frame, have, unit_frames)),
                     *_lossless(source)])
            for shot in shots:
                _cut_shot(source, offset + shot.start - item["start"], shot, staged / f"{shot.index:04d}.mov")
            record = {"start_frame": start_frame, "retimed": retimed,
                      "source_frames": source_frames, "unit_frames": unit_frames}
        else:
            if take_cuts[-1] > source_frames:
                raise CineRecastError(f"--take-cuts: the last value {take_cuts[-1]} is larger than the take's "
                                      f"{source_frames} frames (counted after the frame-rate conversion); give "
                                      f"boundaries inside the take")
            segments = []
            for shot, first, end in zip(shots, take_cuts, take_cuts[1:]):
                have, resampled = end - first, end - first != shot.frames
                source, offset = fitted, first
                if resampled:
                    source, offset = work / f"segment_{shot.index:04d}.mov", 0
                    run(["-i", str(fitted), "-vf",
                         ",".join(_retime_filters(rate, first, have, shot.frames, bounded=True)), *_lossless(source)])
                _cut_shot(source, offset, shot, staged / f"{shot.index:04d}.mov")
                segments.append({"shot": shot.index, "take_start": first, "take_end": end, "frames": shot.frames,
                                 "retimed": resampled})
            record = {"start_frame": take_cuts[0], "retimed": any(s["retimed"] for s in segments),
                      "source_frames": source_frames, "unit_frames": unit_frames,
                      "take_cuts": take_cuts, "segments": segments}

        target, record_file = folder / "shots", folder / "conform.json"
        try:
            record_file.unlink(missing_ok=True)      # no record describes shots that are being replaced
            if target.exists():
                shutil.rmtree(target)
            staged.replace(target)
        except OSError as exc:
            raise CineRecastError(f"cannot replace {target} (is a shot file open in another program?): "
                                  f"{exc}") from exc
        write_json(record_file, record)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return [target / f"{shot.index:04d}.mov" for shot in shots]


def scan_take(root: Path, unit_id: str, take_id: str) -> dict:
    """What helps to choose take_cuts for a take, as printed by takes cuts.

    The take is fitted as conform fits it (_fit), into a temporary folder outside the project that
    is removed afterwards, so nothing is written into the take folder. Returns {"frames": the take's
    frames after the fit, "unit_frames", "pad_frames" (from the plan, 0 when absent), "cuts": the
    frames where detect_cuts finds a hard cut in the fitted take, "tail_static_from": the first frame
    of its held tail (held_tail_start), or None}.
    """
    root = Path(root)
    item = plan.unit(root, unit_id)
    pad = plan.pad_frames(item)
    raw = _raw_file(root, unit_id, take_id)
    info, box = load_video(root)
    _unit_shots(root, item)                     # the unit's start and end are the analysis's
    work = Path(tempfile.mkdtemp(prefix="cine-recast-scan-"))
    try:
        fitted = work / "fitted.mov"
        facts = _fit(raw, fitted, info, box)
        cuts = detect_cuts(fitted, facts, box)
        tail = held_tail_start(fitted, box)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return {"frames": facts.frames, "unit_frames": item["end"] - item["start"], "pad_frames": pad,
            "cuts": cuts, "tail_static_from": tail}
