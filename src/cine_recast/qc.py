"""Similarity scores of a conformed take against the reference, shot by shot.

The take's shot clips are full frames with the picture at the reference's picture box. Each is
cropped to the box (and scaled to the reference clip size if that differs) into a bit-exact h264
scratch clip in the pixel format of the reference shot clips (yuv420p), so the reference and the
take are decoded by the same decoder through the same filters and measured by the same functions.
A lossy scratch encode is not used: re-encoding alone moves the optical-flow maps of an otherwise
identical clip enough to cost several hundredths of the motion score.

Scores, each rounded to DECIMALS; the verdict is computed from the rounded values:

- camera: per frame pair the vector v = (dx, dy, ds * ANALYSIS_WIDTH / 2), all in pixels at the
  analysis width (the scale term is the displacement it causes at the frame edge), smoothed by a
  moving average of SMOOTH_WINDOW. With D the mean length of (ref - take) and A, B the mean
  lengths of ref and take: clamp(1 - max(0, D - CAMERA_TOLERANCE) / (A + B + CAMERA_FLOOR), 0, 1).
- motion: mean over frame pairs of the cosine similarity of the two motion maps; a pair where
  each map's mean is below MOTION_QUIET counts as 1.0.
- color: 1 - d / COLOR_SCALE clamped to [0, 1], d the mean per-frame Euclidean distance of mean Lab.
- flicker_ratio: max(flicker(take), FLICKER_FLOOR) / max(flicker(ref), FLICKER_FLOOR).
- overlap_ratio: share of BLOCK x BLOCK blocks whose RGB mean absolute difference is below
  OVERLAP_LEVEL, per frame, averaged over frames. Reported only, never judged.
"""

import math
import tempfile
from contextlib import closing
from pathlib import Path

import numpy as np

from cine_recast import plan
from cine_recast.analyze import load_video
from cine_recast.errors import CineRecastError
from cine_recast.ffmpeg import SCALE_FLAGS, iter_frames, probe, probe_size, run
from cine_recast.jsonio import write_json
from cine_recast.measure import ANALYSIS_WIDTH, camera_series, color_series, flicker, motion_maps
from cine_recast.paths import ProjectPaths
from cine_recast.picture import PictureBox
from cine_recast.project import DEFAULT_THRESHOLDS, load_project

SMOOTH_WINDOW = 5         # moving average length for the camera vectors
CAMERA_TOLERANCE = 0.25   # px: a mean camera difference up to this is tracker noise and costs nothing
CAMERA_FLOOR = 0.5        # px added to A + B, so two almost-still shots are not judged on noise alone
MOTION_QUIET = 0.01       # px: a motion map whose mean magnitude is below this shows no motion
COLOR_SCALE = 40.0        # mean Lab distance at which the colour score reaches 0
FLICKER_FLOOR = 0.05      # flicker below this counts as this, on both sides of the ratio
BLOCK = 16                # overlap block size, pixels at the reference clip size
OVERLAP_LEVEL = 6.0       # RGB mean absolute difference (0-255) below which a block is overlap
DECIMALS = 3              # stored scores are rounded to this many decimals

# Judged scores: the name used in `failed` and `skip`, the threshold key, and the direction.
CHECKS = (("camera", "camera_min", "min"), ("motion", "motion_min", "min"),
          ("color", "color_min", "min"), ("flicker_ratio", "flicker_ratio_max", "max"))
SKIPPABLE = tuple(name for name, _, _ in CHECKS)


def _round(value: float) -> float:
    return round(float(value), DECIMALS) + 0.0  # + 0.0 turns -0.0 into 0.0


def _smooth(series: np.ndarray) -> np.ndarray:
    """Moving average of length SMOOTH_WINDOW over each column (shorter when the series is)."""
    series = np.asarray(series, dtype=np.float64).reshape(-1, 3)
    if len(series) == 0:
        return series
    k = min(SMOOTH_WINDOW, len(series))
    kernel = np.full(k, 1.0 / k)
    return np.column_stack([np.convolve(series[:, c], kernel, mode="valid") for c in range(3)])


def _camera_vectors(series: np.ndarray) -> np.ndarray:
    """Smoothed (dx, dy, ds * ANALYSIS_WIDTH / 2) per frame pair, every term in pixels."""
    v = np.asarray(series, dtype=np.float64).reshape(-1, 3).copy()
    v[:, 2] *= ANALYSIS_WIDTH / 2
    return _smooth(v)


def _mean_length(vectors: np.ndarray) -> float:
    return float(np.linalg.norm(vectors, axis=1).mean()) if len(vectors) else 0.0


def _camera_terms(ref: np.ndarray, take: np.ndarray) -> tuple[float, float, float]:
    """(D, A, B): mean length of the difference, of the reference and of the take vectors."""
    a, b = _camera_vectors(ref), _camera_vectors(take)
    if a.shape != b.shape:
        raise CineRecastError(f"camera series differ in length: {len(ref)} and {len(take)} frame pairs")
    return _mean_length(a - b), _mean_length(a), _mean_length(b)


def _camera_score(ref: np.ndarray, take: np.ndarray) -> float:
    d, big_a, big_b = _camera_terms(ref, take)
    return min(1.0, max(0.0, 1.0 - max(0.0, d - CAMERA_TOLERANCE) / (big_a + big_b + CAMERA_FLOOR)))


def _motion_score(ref: np.ndarray, take: np.ndarray) -> float:
    pairs = []
    for a, b in zip(np.asarray(ref, dtype=np.float64), np.asarray(take, dtype=np.float64)):
        a, b = a.ravel(), b.ravel()
        if a.mean() < MOTION_QUIET and b.mean() < MOTION_QUIET:
            pairs.append(1.0)
            continue
        den = math.sqrt(float(a @ a) * float(b @ b))
        pairs.append(float(a @ b) / den if den > 0.0 else 0.0)  # motion on one side only
    if not pairs:
        return 1.0  # single-frame clips: no frame pair, no motion on either side
    return min(1.0, max(0.0, float(np.mean(pairs))))


def _color_score(ref_lab: np.ndarray, take_lab: np.ndarray) -> float:
    d = float(np.linalg.norm(np.asarray(ref_lab) - np.asarray(take_lab), axis=1).mean())
    return min(1.0, max(0.0, 1.0 - d / COLOR_SCALE))


def _frame_overlap(ref: np.ndarray, take: np.ndarray) -> float:
    """Share of the frame in BLOCK x BLOCK blocks whose RGB mean absolute difference is below
    OVERLAP_LEVEL. Edge blocks cut short by the frame size count by their area."""
    diff = np.abs(ref.astype(np.int16) - take.astype(np.int16)).astype(np.float64).sum(axis=2)
    h, w = diff.shape
    rows, cols = np.arange(0, h, BLOCK), np.arange(0, w, BLOCK)
    sums = np.add.reduceat(np.add.reduceat(diff, rows, axis=0), cols, axis=1)
    area = np.outer(np.diff(np.append(rows, h)), np.diff(np.append(cols, w)))
    close = sums / (area * 3) < OVERLAP_LEVEL
    return float((area * close).sum()) / (h * w)


def _overlap_ratio(ref_clip: Path, take_clip: Path, size: tuple[int, int]) -> float:
    """Frame mean of _frame_overlap; both clips decoded at `size`, the reference clip size."""
    ratios = []
    with closing(iter_frames(ref_clip, size=size)) as refs, closing(iter_frames(take_clip, size=size)) as takes:
        for ref, take in zip(refs, takes):
            ratios.append(_frame_overlap(ref, take))
    if not ratios:
        raise CineRecastError(f"no frames could be compared: {ref_clip} and {take_clip}")
    return float(np.mean(ratios))


def _picture_clip(take_clip: Path, box: PictureBox, size: tuple[int, int], out: Path) -> None:
    """The take's picture area at `size` as a bit-exact yuv420p h264 clip, frame for frame."""
    x, y, w, h = box.as_crop()
    filters = [f"crop={w}:{h}:{x}:{y}"]
    if (w, h) != tuple(size):
        filters.append(f"scale={size[0]}:{size[1]}:{SCALE_FLAGS}")
    run(["-i", str(take_clip), "-map", "0:v:0", "-vf", ",".join(filters), "-fps_mode", "passthrough",
         "-an", "-c:v", "libx264", "-qp", "0", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(out)])


def _check_inputs(thresholds: dict, skip) -> list[str]:
    for _, key, _ in CHECKS:
        value = thresholds.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise CineRecastError(f"threshold {key} must be a finite number, not {value!r}")
    skip = list(skip or [])
    unknown = [name for name in skip if name not in SKIPPABLE]
    if unknown:
        raise CineRecastError(f"unknown qc_skip names {unknown}; expected some of {', '.join(SKIPPABLE)}")
    return skip


def score_shot(ref_clip: Path, take_clip: Path, box: PictureBox, thresholds: dict,
               skip: list[str] = ()) -> dict:
    """Scores and verdict of one shot.

    ref_clip holds the reference picture area only; take_clip is the conformed full-frame shot,
    with the picture at `box`. Names in `skip` are left out of the verdict.
    """
    ref_clip, take_clip = Path(ref_clip), Path(take_clip)
    skip = _check_inputs(thresholds, skip)
    frames_ref, frames_take = probe(ref_clip).frames, probe(take_clip).frames
    result = {"frames_ref": frames_ref, "frames_take": frames_take, "timing_ok": frames_ref == frames_take,
              "camera": None, "motion": None, "color": None, "flicker_ratio": None, "overlap_ratio": None}
    if not result["timing_ok"]:
        return {**result, "verdict": "retry", "failed": ["timing"]}

    with tempfile.TemporaryDirectory(prefix="cine-recast-qc-", ignore_cleanup_errors=True) as work:
        picture = Path(work) / "take.mp4"
        size = probe_size(ref_clip)
        _picture_clip(take_clip, box, size, picture)
        ref_lab, take_lab = color_series(ref_clip), color_series(picture)
        scores = {
            "camera": _camera_score(camera_series(ref_clip), camera_series(picture)),
            "motion": _motion_score(motion_maps(ref_clip), motion_maps(picture)),
            "color": _color_score(ref_lab, take_lab),
            "flicker_ratio": max(flicker(take_lab), FLICKER_FLOOR) / max(flicker(ref_lab), FLICKER_FLOOR),
            "overlap_ratio": _overlap_ratio(ref_clip, picture, size),
        }
    for name, value in scores.items():
        if not math.isfinite(value):
            raise CineRecastError(f"{name} score is not a finite number for {take_clip}")
        result[name] = _round(value)

    failed = []
    for name, key, direction in CHECKS:
        ok = result[name] >= thresholds[key] if direction == "min" else result[name] <= thresholds[key]
        if not ok and name not in skip:
            failed.append(name)
    return {**result, "verdict": "retry" if failed else "pass", "failed": failed}


def score_take(root: Path, unit_id: str, take_id: str) -> dict:
    """Score every shot of a conformed take, write takes/<unit>/<take>/scores.json and return it.

    Thresholds come from project.json (DEFAULT_THRESHOLDS fill missing keys), skipped scores
    from the unit's qc_skip. The take passes when every shot passes.
    """
    root = Path(root)
    pp = ProjectPaths(root)
    item = plan.unit(root, unit_id)
    thresholds = {**DEFAULT_THRESHOLDS, **(load_project(root).get("thresholds") or {})}
    skip = item.get("qc_skip") or []
    _, box = load_video(root)
    folder = pp.take_dir(unit_id, take_id)
    if not (folder / "conform.json").is_file():
        raise CineRecastError(f"take {unit_id}/{take_id} has not been conformed; run conform first")
    shots: dict[str, dict] = {}
    for index in item.get("shots") or []:
        clip = folder / "shots" / f"{index:04d}.mov"
        if not clip.is_file():
            raise CineRecastError(f"conformed shot clip missing: {clip}; run conform again")
        shots[str(index)] = score_shot(pp.shot_dir(index) / "ref.mp4", clip, box, thresholds, skip)
    if not shots:
        raise CineRecastError(f"unit {unit_id} has no shots to score")
    data = {"verdict": "pass" if all(s["verdict"] == "pass" for s in shots.values()) else "retry",
            "shots": shots}
    write_json(folder / "scores.json", data)
    return data
