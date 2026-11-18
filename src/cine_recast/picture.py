"""Find the picture rectangle inside letterbox or pillarbox bars."""

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from cine_recast.errors import CineRecastError
from cine_recast.ffmpeg import VideoInfo, iter_frames

LIT_LEVEL = 24          # a pixel is lit when its brightest sampled value is above this
BAR_FRACTION = 0.20     # a row or column with at most this fraction of lit pixels is bar
MIN_BAR = 0.02          # a bar thinner than this fraction of its dimension is ignored
MIN_PICTURE = 0.20      # a picture smaller than this fraction on either side is not trusted


@dataclass(frozen=True)
class PictureBox:
    x: int
    y: int
    w: int
    h: int

    @property
    def aspect(self) -> float:
        return self.w / self.h

    def as_crop(self) -> tuple[int, int, int, int]:
        """The box in the (x, y, w, h) order that ffmpeg.iter_frames takes as `crop`."""
        return (self.x, self.y, self.w, self.h)

    def to_dict(self) -> dict:
        return {"x": self.x, "y": self.y, "w": self.w, "h": self.h}

    @classmethod
    def from_dict(cls, d: dict) -> "PictureBox":
        return cls(x=int(d["x"]), y=int(d["y"]), w=int(d["w"]), h=int(d["h"]))


def given_picture_box(values: tuple[int, int, int, int], width: int, height: int) -> PictureBox:
    """The picture area x, y, w, h given by hand, checked against a width x height frame: whole
    numbers, w and h greater than 0, every value even (the scale and crop filters keep even sizes),
    and the box inside the frame."""
    if len(values) != 4 or not all(isinstance(v, int) and not isinstance(v, bool) for v in values):
        raise CineRecastError(f"--picture must be four whole numbers x,y,w,h, not {values!r}")
    x, y, w, h = values
    text = f"--picture {x},{y},{w},{h}"
    if w <= 0 or h <= 0:
        raise CineRecastError(f"{text}: w and h must be greater than 0")
    if x < 0 or y < 0 or x + w > width or y + h > height:
        raise CineRecastError(f"{text}: the area must lie inside the {width}x{height} frame")
    if any(v % 2 for v in values):
        raise CineRecastError(f"{text}: x, y, w and h must all be even")
    return PictureBox(x, y, w, h)


def _sample_indices(frames: int, samples: int) -> list[int]:
    """`samples` frame indices spread evenly over the clip, one at the middle of each equal slice."""
    n = max(1, min(samples, frames))
    return sorted({int((i + 0.5) * frames / n) for i in range(n)})


def _brightest(path: Path, info: VideoInfo, samples: int) -> np.ndarray:
    """Per-pixel maximum over the sampled gray frames."""
    if info.frames <= 0:
        raise CineRecastError(f"no frames to sample in {path}")
    wanted = set(_sample_indices(info.frames, samples))
    last = max(wanted)
    ever = np.zeros((info.height, info.width), dtype=np.uint8)
    seen = 0
    # A full-frame crop gives iter_frames the frame size, so it does not probe the clip again.
    full = (0, 0, info.width, info.height)
    for index, frame in enumerate(iter_frames(path, crop=full, count=last + 1, gray=True)):
        if index in wanted:
            np.maximum(ever, frame, out=ever)
            seen += 1
    if seen == 0:
        raise CineRecastError(f"no frames could be read from {path}")
    return ever


def _span(fraction: np.ndarray) -> tuple[int, int] | None:
    """[start, end) from the first to the last index whose lit fraction is above BAR_FRACTION."""
    keep = np.flatnonzero(fraction > BAR_FRACTION)
    if keep.size == 0:
        return None
    return int(keep[0]), int(keep[-1]) + 1


def _drop_thin_bars(start: int, end: int, length: int) -> tuple[int, int]:
    if start < MIN_BAR * length:
        start = 0
    if length - end < MIN_BAR * length:
        end = length
    return start, end


def _even_inward(start: int, end: int) -> tuple[int, int]:
    return start + (start % 2), end - (end % 2)


def detect_picture_box(path: Path, info: VideoInfo, samples: int = 60) -> PictureBox:
    """The picture rectangle of a clip, excluding black bars at its edges.

    A pixel counts as picture if it is ever brighter than LIT_LEVEL in the sampled frames, so a
    dark picture still separates from bars, and a small logo inside a bar does not widen the box.
    Returns the whole frame when no believable picture area is found.
    """
    path = Path(path)
    full = PictureBox(0, 0, info.width, info.height)
    lit = _brightest(path, info, samples) > LIT_LEVEL

    rows = _span(lit.mean(axis=1))
    if rows is None:
        return full
    y0, y1 = rows
    cols = _span(lit[y0:y1].mean(axis=0))
    if cols is None:
        return full
    x0, x1 = cols

    y0, y1 = _drop_thin_bars(y0, y1, info.height)
    x0, x1 = _drop_thin_bars(x0, x1, info.width)
    x0, x1 = _even_inward(x0, x1)
    y0, y1 = _even_inward(y0, y1)

    w, h = x1 - x0, y1 - y0
    if w < MIN_PICTURE * info.width or h < MIN_PICTURE * info.height:
        return full
    return PictureBox(x0, y0, w, h)
