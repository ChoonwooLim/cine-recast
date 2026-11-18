"""Find hard cuts (the frames where a new shot starts) and the held tail of a clip."""

from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from cine_recast.errors import CineRecastError
from cine_recast.ffmpeg import VideoInfo, iter_frames
from cine_recast.picture import PictureBox

SIG_SIZE = (64, 36)     # (w, h) of the gray signature each frame is reduced to
MIN_DIFF = 5.0          # a cut changes the picture by at least this mean absolute difference (0-255)
PEAK_RATIO = 3.0        # ... and by at least this many times the median of its neighbours
WINDOW = 8              # neighbours taken on each side of a frame for that median
ISOLATION_FACTOR = 2.0  # a cut is a lone spike: rejects broad peaks such as a fast occluder sweep
FLASH_FACTOR = 0.5      # a change that returns this close to the frame before it is a flash
HOLD_DIFF = 0.25        # frames whose signatures differ by less than this are copies of one picture
                        # (held copies differ by about 0.05; a slow real shot moves 0.6 to 1.3 per frame)
HOLD_MIN_RUN = 6        # a held tail shorter than this many frames is not reported


@dataclass(frozen=True)
class Shot:
    index: int          # 1-based
    start: int          # first frame
    end: int            # one past the last frame

    @property
    def frames(self) -> int:
        return self.end - self.start


def _mad(a: np.ndarray, b: np.ndarray) -> float:
    """Mean absolute pixel difference of two signatures."""
    return float(np.abs(a - b).mean())


def _signatures(path: Path, box: PictureBox) -> Iterator[np.ndarray]:
    """Each frame's picture area as a SIG_SIZE gray signature (int16, so differences do not wrap)."""
    for frame in iter_frames(path, size=SIG_SIZE, crop=box.as_crop(), gray=True):
        yield frame.astype(np.int16)


def _measure(path: Path, box: PictureBox) -> tuple[list[float], list[float], list[float]]:
    """Per frame i: d[i] = mad(sig[i-1], sig[i]), back1[i] = mad(sig[i-1], sig[i+1]) and
    back2[i] = mad(sig[i-1], sig[i+2]). Values that do not exist are NaN.

    Only the last four signatures are kept, so memory does not grow with the clip.
    """
    d: list[float] = []
    back1: list[float] = []
    back2: list[float] = []
    recent: deque[np.ndarray] = deque(maxlen=4)
    for t, sig in enumerate(_signatures(path, box)):
        recent.append(sig)
        d.append(_mad(recent[-2], sig) if t >= 1 else float("nan"))
        back1.append(float("nan"))
        back2.append(float("nan"))
        if t >= 2:
            back1[t - 1] = _mad(recent[-3], sig)
        if t >= 3:
            back2[t - 2] = _mad(recent[-4], sig)
    return d, back1, back2


def detect_cuts(path: Path, info: VideoInfo, box: PictureBox) -> list[int]:
    """Frame numbers where a new shot starts, ascending, never 0. Hard cuts only.

    Each frame's picture area is reduced to a 64x36 gray signature. Frame i is a candidate when
    its difference from frame i-1 is at least MIN_DIFF and at least PEAK_RATIO times the median
    difference of the WINDOW frames on each side, and at least ISOLATION_FACTOR times the larger
    of the two adjacent differences (an adjacent difference that does not exist is ignored).
    A candidate whose picture returns, one or two frames later, to within FLASH_FACTOR of its
    change from frame i-1 is a flash: the candidates from i through that frame are dropped.
    """
    path = Path(path)
    inside = box.x >= 0 and box.y >= 0 and box.x + box.w <= info.width and box.y + box.h <= info.height
    if box.w <= 0 or box.h <= 0 or not inside:
        raise CineRecastError(f"picture box {box.as_crop()} is outside the {info.width}x{info.height} frame")
    d, back1, back2 = _measure(path, box)
    n = len(d)
    if n != info.frames:
        raise CineRecastError(f"read {n} of {info.frames} frames from {path}")

    candidates: list[int] = []
    for i in range(1, n):
        neighbours = [d[j] for j in range(max(1, i - WINDOW), min(n, i + WINDOW + 1)) if j != i]
        median = float(np.median(neighbours)) if neighbours else 0.0
        adjacent = max((d[j] for j in (i - 1, i + 1) if 1 <= j < n), default=0.0)
        if d[i] >= MIN_DIFF and d[i] >= PEAK_RATIO * median and d[i] >= ISOLATION_FACTOR * adjacent:
            candidates.append(i)

    flashes: set[int] = set()
    for i in candidates:
        for k, back in ((1, back1[i]), (2, back2[i])):
            if back < FLASH_FACTOR * d[i]:     # NaN (no frame i+k) never compares true
                flashes.update(range(i, i + k + 1))
    return [i for i in candidates if i not in flashes]


def held_tail_start(path: Path, box: PictureBox) -> int | None:
    """The first held copy of the run of near-identical frames that lasts to the end of the clip:
    the frame after the run's first frame, which is the last real frame. None when the run is
    shorter than HOLD_MIN_RUN frames (the real frame counted) or the clip has no frames.

    Walking back from the last frame, a frame stays in the run while the mean absolute difference
    of its signature (the gray signature detect_cuts uses) to the next frame's is below HOLD_DIFF.
    This finds the copies of one picture a model returns for the held end of a padded source; as
    the end boundary of take_cuts the value keeps the last real frame and drops every copy.
    """
    d: list[float] = []         # d[i] = mad(sig[i], sig[i+1])
    previous = None
    for sig in _signatures(path, box):
        if previous is not None:
            d.append(_mad(previous, sig))
        previous = sig
    if previous is None:
        return None
    start = len(d)              # the last frame
    while start > 0 and d[start - 1] < HOLD_DIFF:
        start -= 1
    return start + 1 if len(d) + 1 - start >= HOLD_MIN_RUN else None


def shots_from_cuts(cuts: list[int], total_frames: int) -> list[Shot]:
    """Split [0, total_frames) at the cut frames into 1-based shots."""
    if total_frames < 1:
        raise CineRecastError(f"a clip needs at least one frame, got {total_frames}")
    previous = 0
    for cut in cuts:
        if cut <= previous or cut >= total_frames:
            raise CineRecastError(
                f"cuts must be ascending, unique and inside (0, {total_frames}): {list(cuts)}")
        previous = cut
    bounds = [0, *cuts, total_frames]
    return [Shot(n, bounds[n - 1], bounds[n]) for n in range(1, len(bounds))]
