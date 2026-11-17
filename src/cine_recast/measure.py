"""Per-shot measurements: camera motion, motion maps, colour and flicker.

Every input clip holds the picture area only (no letterbox bars). The same measurements are
taken on a reference shot and on its recreation, so units and signs here are fixed:

- camera: one row per frame pair, (dx, dy, ds). dx, dy are pixels at ANALYSIS_WIDTH and are
  positive when the picture content moves right / down. They are the displacement of the frame
  centre, so a zoom about the centre does not show up as a pan. ds is the natural log of the
  magnification (positive when the content grows).
- motion maps: optical flow magnitude per frame pair on a MOTION_GRID grid.
- colour: mean CIE Lab per frame, L in 0-100, a and b signed.
"""

import math
from pathlib import Path

import cv2
import numpy as np

from cine_recast.errors import CineRecastError
from cine_recast.ffmpeg import iter_frames, probe

ANALYSIS_WIDTH = 480        # camera and colour are measured at this width, aspect ratio kept
MAX_CORNERS = 400           # features tracked per frame pair
CORNER_QUALITY = 0.01       # goodFeaturesToTrack quality level
CORNER_DISTANCE = 8         # minimum distance between features, pixels at ANALYSIS_WIDTH
LK_WINDOW = (21, 21)        # Lucas-Kanade search window
LK_LEVELS = 3               # Lucas-Kanade pyramid levels
MIN_INLIERS = 12            # fewer RANSAC inliers than this: the pair counts as no motion
MOTION_SIZE = (160, 90)     # (w, h) of the gray frames the motion maps are computed on
MOTION_GRID = (32, 18)      # (w, h) the flow magnitude is averaged down to
DECIMALS = 3                # stored values are rounded to this many decimals


def _analysis_size(path: Path) -> tuple[int, int]:
    info = probe(path)
    height = max(2, round(ANALYSIS_WIDTH * info.height / info.width / 2) * 2)
    return ANALYSIS_WIDTH, height


def _pair_motion(prev: np.ndarray, cur: np.ndarray) -> tuple[float, float, float]:
    """(dx, dy, ds) of the similarity transform taking gray frame prev to gray frame cur."""
    pts = cv2.goodFeaturesToTrack(prev, maxCorners=MAX_CORNERS, qualityLevel=CORNER_QUALITY,
                                  minDistance=CORNER_DISTANCE)
    if pts is None or len(pts) < MIN_INLIERS:
        return 0.0, 0.0, 0.0
    nxt, status, _ = cv2.calcOpticalFlowPyrLK(prev, cur, pts, None, winSize=LK_WINDOW, maxLevel=LK_LEVELS)
    ok = status.reshape(-1) == 1
    if int(ok.sum()) < MIN_INLIERS:
        return 0.0, 0.0, 0.0
    m, inliers = cv2.estimateAffinePartial2D(pts[ok], nxt[ok], method=cv2.RANSAC)
    if m is None or inliers is None or int(inliers.sum()) < MIN_INLIERS:
        return 0.0, 0.0, 0.0
    scale = math.hypot(m[0, 0], m[1, 0])
    if scale <= 0.0:
        return 0.0, 0.0, 0.0
    h, w = prev.shape
    cx, cy = (w - 1) / 2, (h - 1) / 2
    dx = m[0, 0] * cx + m[0, 1] * cy + m[0, 2] - cx
    dy = m[1, 0] * cx + m[1, 1] * cy + m[1, 2] - cy
    return float(dx), float(dy), math.log(scale)


def _frame_lab(rgb: np.ndarray) -> np.ndarray:
    """Mean CIE Lab of one RGB uint8 frame (float input makes OpenCV return L 0-100, signed a, b)."""
    scaled = rgb.astype(np.float32)
    scaled *= np.float32(1.0 / 255.0)
    return np.array(cv2.mean(cv2.cvtColor(scaled, cv2.COLOR_RGB2Lab))[:3], dtype=np.float64)


def _scan(path: Path, *, camera: bool, color: bool) -> tuple[np.ndarray, np.ndarray]:
    """One decoding pass at the analysis size. Returns (camera series, colour series)."""
    path = Path(path)
    cam: list[tuple[float, float, float]] = []
    lab: list[np.ndarray] = []
    prev: np.ndarray | None = None
    count = 0
    for rgb in iter_frames(path, size=_analysis_size(path)):
        count += 1
        if camera:
            gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
            if prev is not None:
                cam.append(_pair_motion(prev, gray))
            prev = gray
        if color:
            lab.append(_frame_lab(rgb))
    if count == 0:
        raise CineRecastError(f"no frames could be read from {path}")
    return (np.array(cam, dtype=np.float64).reshape(-1, 3),
            np.array(lab, dtype=np.float64).reshape(-1, 3))


def camera_series(path: Path) -> np.ndarray:
    """Shape (frames-1, 3): dx, dy, ds per frame pair (see the module docstring for units)."""
    return _scan(path, camera=True, color=False)[0]


def motion_maps(path: Path) -> np.ndarray:
    """Shape (frames-1, 18, 32), float32: mean optical flow magnitude per grid cell."""
    path = Path(path)
    fw, fh = MOTION_SIZE
    gw, gh = MOTION_GRID
    maps: list[np.ndarray] = []
    prev: np.ndarray | None = None
    count = 0
    for gray in iter_frames(path, size=MOTION_SIZE, gray=True):
        count += 1
        if prev is not None:
            flow = cv2.calcOpticalFlowFarneback(prev, gray, None, 0.5, 3, 15, 3, 5, 1.2, 0)
            mag = np.hypot(flow[..., 0], flow[..., 1])
            maps.append(mag.reshape(gh, fh // gh, gw, fw // gw).mean(axis=(1, 3)))
        prev = gray
    if count == 0:
        raise CineRecastError(f"no frames could be read from {path}")
    return np.array(maps, dtype=np.float32).reshape(-1, gh, gw)


def color_series(path: Path) -> np.ndarray:
    """Shape (frames, 3): mean CIE Lab per frame, L in 0-100, a and b signed."""
    return _scan(path, camera=False, color=True)[1]


def flicker(lab: np.ndarray) -> float:
    """Mean absolute second difference of L over frames; 0.0 for fewer than 3 frames."""
    lab = np.asarray(lab, dtype=np.float64)
    if lab.shape[0] < 3:
        return 0.0
    return float(np.abs(np.diff(lab[:, 0], n=2)).mean())


def _round(value: float) -> float:
    return round(float(value), DECIMALS) + 0.0  # + 0.0 turns -0.0 into 0.0


def measure_clip(path: Path) -> dict:
    """Camera, colour and flicker of one picture-area clip, rounded for a shot sheet."""
    series, lab = _scan(path, camera=True, color=True)
    if len(series):
        med = np.median(series, axis=0)
        pan, zoom = math.hypot(med[0], med[1]), med[2]
    else:
        pan, zoom = 0.0, 0.0
    return {
        "camera": {
            "series": [[_round(v) for v in row] for row in series],
            "pan_px_per_frame": _round(pan),
            "zoom_per_frame": _round(zoom),
        },
        "color": {
            "lab_mean": [_round(v) for v in lab.mean(axis=0)],
            "lab_std": [_round(v) for v in lab.std(axis=0)],
        },
        "flicker": _round(flicker(lab)),
    }
