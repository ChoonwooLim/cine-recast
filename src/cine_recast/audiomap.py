"""The timing of the reference's sound: its level per film frame and the frames of its strongest onsets.

analyze() writes this as analysis/audio.json so that the agent can place generated effects on the
frames where something is heard in the reference (a door, a footstep, an impact). This is timing
data, not sound: one RMS level per film frame and a list of frame numbers. Nothing in it lets the
reference sound be rebuilt, and no sample of the reference is stored.

- rms_db: per film frame, the RMS level of that frame's samples (the reference's first audio stream,
  mixed to mono by ffmpeg) in dBFS, never below FLOOR_DB.
- hits: frames where the level jumps by at least HIT_JUMP_DB against the mean of the previous
  HIT_WINDOW frames (fewer at the start), at least HIT_SPACING frames apart (the stronger jump wins),
  strongest first, at most MAX_HITS.
- shots: per shot the RMS level of all its samples in dBFS (mean_db) and its hits, ascending.
"""

import math
from fractions import Fraction
from pathlib import Path

import numpy as np

from cine_recast.cuts import Shot
from cine_recast.ffmpeg import VideoInfo, align_audio, audio_offset, decode_audio

RATE = 16000            # the reference's sound is read mono at this rate
FLOOR_DB = -90.0
HIT_JUMP_DB = 9.0
HIT_WINDOW = 6
HIT_SPACING = 6
MAX_HITS = 40
DECIMALS = 2


def _db(mean_square: float) -> float:
    if mean_square <= 0.0:
        return FLOOR_DB
    return max(FLOOR_DB, 10.0 * math.log10(mean_square))


def _bounds(frame: int, info: VideoInfo) -> int:
    """The first sample of film frame `frame` at RATE."""
    return round(Fraction(frame * info.fps_den * RATE, info.fps_num))


def frame_levels(samples: np.ndarray, info: VideoInfo) -> list[float]:
    """RMS level in dBFS (floor FLOOR_DB) of each of the film's frames; frames past the end of the
    sound are FLOOR_DB."""
    x = np.asarray(samples, dtype=np.float64).ravel()
    squares = np.concatenate([[0.0], np.cumsum(x * x)])
    levels = []
    for frame in range(info.frames):
        a, b = min(_bounds(frame, info), len(x)), min(_bounds(frame + 1, info), len(x))
        levels.append(_db((squares[b] - squares[a]) / (b - a)) if b > a else FLOOR_DB)
    return levels


def onsets(levels: list[float]) -> list[int]:
    """The hit frames of a level series (see the module docstring), strongest first."""
    found = []
    for frame in range(1, len(levels)):
        before = levels[max(0, frame - HIT_WINDOW):frame]
        jump = levels[frame] - sum(before) / len(before)
        if jump >= HIT_JUMP_DB:
            found.append((jump, frame))
    hits: list[int] = []
    for _, frame in sorted(found, key=lambda pair: (-pair[0], pair[1])):
        if all(abs(frame - other) >= HIT_SPACING for other in hits):
            hits.append(frame)
            if len(hits) == MAX_HITS:
                break
    return hits


def audio_map(reference: Path, info: VideoInfo, shots: list[Shot]) -> dict:
    """The content of analysis/audio.json for a reference whose audio stream ffmpeg can decode. The
    samples are put on the video's clock first (ffmpeg.align_audio), so that a sound stream that
    starts after the picture gives its hits at the frames where they are heard."""
    samples = align_audio(decode_audio(reference, rate=RATE, channels=1)[:, 0], audio_offset(reference), RATE)
    levels = frame_levels(samples, info)
    hits = onsets(levels)
    x = np.asarray(samples, dtype=np.float64)
    per_shot = []
    for shot in shots:
        a, b = min(_bounds(shot.start, info), len(x)), min(_bounds(shot.end, info), len(x))
        mean = _db(float((x[a:b] ** 2).mean())) if b > a else FLOOR_DB
        per_shot.append({"shot": shot.index, "start": shot.start, "end": shot.end, "mean_db": round(mean, DECIMALS),
                         "hits": sorted(h for h in hits if shot.start <= h < shot.end)})
    return {"has_audio": True, "frames": info.frames, "rms_db": [round(v, DECIMALS) for v in levels],
            "hits": hits, "shots": per_shot}


def no_audio(info: VideoInfo, shots: list[Shot]) -> dict:
    """analysis/audio.json for a reference without an audio stream."""
    return {"has_audio": False, "frames": info.frames, "rms_db": [], "hits": [], "shots": []}
