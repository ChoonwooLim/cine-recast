"""The film's soundtrack: the mix of the generated sounds, its reference check, and its verification.

mix() writes out/soundtrack.wav (PCM 24-bit, 48 kHz, stereo, exactly as long as the film to the
sample) and out/soundtrack.json:

1. Every sound that the ledger records as done and that is not muted is decoded and resampled to
   48 kHz stereo by ffmpeg, cut to its `seconds`, faded in and out (linear ramps), gained by gain_db
   and placed at at_frame / fps. What extends past the end of the film is cut with a 50 ms fade.
   The sounds are added as they are, with no normalisation per input.
2. Loudness: the integrated loudness of the sum is measured with ffmpeg's loudnorm in measurement
   mode; one static gain brings it to TARGET_LUFS, then alimiter holds the peaks under CEILING_DBFS
   (latency compensated, so nothing moves in time).
3. Reference check (fail closed): when the reference video has an audio stream, the largest
   absolute normalised cross-correlation between the mix and the reference's sound, both mono at
   8 kHz, over lags of plus or minus 0.5 s, must stay below REFERENCE_LIMIT; otherwise the mix
   carries the reference's sound, is refused, and nothing is left behind.

Both files are written to scratch names in out/ and moved into place only when everything passed.
check() is what assemble uses: it returns the soundtrack only while its bytes are the ones mixed.
"""

import json
import math
import os
import re
import tempfile
from fractions import Fraction
from pathlib import Path

import numpy as np

from cine_recast import ledger, sound
from cine_recast.analyze import load_shots, load_video
from cine_recast.errors import CineRecastError
from cine_recast.ffmpeg import ToolNotFound, audio_streams, decode_audio, raw_audio_input, run_piped
from cine_recast.jsonio import read_project_json, write_json
from cine_recast.paths import ProjectPaths, sha256_of
from cine_recast.project import load_project

RATE = 48000                # soundtrack sample rate
CHANNELS = 2
TARGET_LUFS = -16.0
CEILING_DBFS = -1.0
END_FADE = 0.05             # seconds of fade where the film ends in the middle of a sound
SILENCE = 2.0 ** -23        # a sum whose peak is below one 24-bit step is digital silence
CHECK_RATE = 8000           # the reference check compares mono signals at this rate
MAX_LAG = 0.5               # seconds of lag searched either way
REFERENCE_LIMIT = 0.5       # a correlation at or above this means the mix carries the reference's sound
CUT_WINDOW = 2              # a sound starting within this many frames of a cut is listed at that cut
_BLOCK = 1 << 16            # samples of the mix correlated at a time


# --- samples -------------------------------------------------------------------------------

def _samples(frames: int, fps_num: int, fps_den: int) -> int:
    """The number of samples at RATE that last `frames` film frames, rounded to the nearest sample."""
    return round(Fraction(frames * fps_den * RATE, fps_num))


def _ramp(length: int, up: bool) -> np.ndarray:
    ramp = np.linspace(0.0, 1.0, length, endpoint=False, dtype=np.float64) + 1.0 / length
    return (ramp if up else ramp[::-1]).astype(np.float32)


def _shaped(path: Path, item: dict) -> np.ndarray:
    """The item's file at RATE stereo, cut to its seconds, faded and gained."""
    clip = decode_audio(path, rate=RATE, channels=CHANNELS, seconds=item["seconds"])
    clip = clip[:round(item["seconds"] * RATE)].copy()
    n = len(clip)
    for seconds, up in ((item["fade_in"], True), (item["fade_out"], False)):
        length = min(n, round(seconds * RATE))
        if length > 0:
            if up:
                clip[:length] *= _ramp(length, True)[:, None]
            else:
                clip[n - length:] *= _ramp(length, False)[:, None]
    clip *= np.float32(10.0 ** (item["gain_db"] / 20.0))
    return clip


def _loudness(args: list[str], data: bytes | None = None) -> float:
    """Integrated loudness (LUFS) of the input given by `args`, from loudnorm's JSON measurement."""
    _, err = run_piped([*args, "-af", f"loudnorm=I={TARGET_LUFS}:TP={CEILING_DBFS}:print_format=json",
                        "-f", "null", "-"], data)
    blocks = re.findall(r"\{[^{}]*\}", err)
    try:
        return float(json.loads(blocks[-1])["input_i"])
    except (IndexError, KeyError, ValueError) as exc:
        raise CineRecastError("the loudness measurement of the mix could not be read from ffmpeg's loudnorm "
                              "output") from exc


def max_correlation(mix: np.ndarray, reference: np.ndarray, max_lag: int) -> float:
    """The largest absolute normalised cross-correlation of two mono signals over lags of -max_lag to
    +max_lag samples. At lag L the pairs are mix[n] and reference[n + L]; each lag is normalised by
    the energies of the two overlapping parts (means removed first). 0.0 where a part is silent."""
    x = np.asarray(mix, dtype=np.float64).ravel()
    y = np.asarray(reference, dtype=np.float64).ravel()
    if not len(x) or not len(y):
        return 0.0
    x, y = x - x.mean(), y - y.mean()
    nx, ny, k = len(x), len(y), int(max_lag)
    sums = np.zeros(2 * k + 1)
    for start in range(0, nx, _BLOCK):
        part = x[start:start + _BLOCK]
        lo, hi = start - k, start + len(part) + k
        segment = np.zeros(hi - lo)
        a, b = max(lo, 0), min(hi, ny)
        if a < b:
            segment[a - lo:b - lo] = y[a:b]
        size = 1 << int(len(segment) + len(part)).bit_length()
        corr = np.fft.irfft(np.fft.rfft(segment, size) * np.conj(np.fft.rfft(part, size)), size)
        sums += corr[:2 * k + 1]                    # corr[m] = sum_i segment[i + m] * part[i], lag m - k
    lags = np.arange(-k, k + 1)
    cx = np.concatenate([[0.0], np.cumsum(x * x)])
    cy = np.concatenate([[0.0], np.cumsum(y * y)])
    first = np.maximum(0, -lags)                    # overlap: mix[first:last], reference[first + L:last + L]
    last = np.minimum(nx, ny - lags)
    valid = last > first
    first, last = np.where(valid, first, 0), np.where(valid, last, 0)
    ex = cx[last] - cx[first]
    ey = cy[np.clip(last + lags, 0, ny)] - cy[np.clip(first + lags, 0, ny)]
    floor = 1e-12 * max(cx[-1], cy[-1], 1e-300)
    usable = valid & (ex > floor) & (ey > floor)
    if not usable.any():
        return 0.0
    ncc = np.abs(sums[usable]) / np.sqrt(ex[usable] * ey[usable])
    return float(min(1.0, ncc.max()))


def _reference_correlation(root: Path, mixed: Path) -> float | None:
    """max_correlation of the mix and the reference's sound, or None when the reference has no audio
    stream. A reference whose streams cannot be read refuses the mix (fail closed)."""
    reference = root / load_project(root)["reference"]
    try:
        has_audio = audio_streams(reference) > 0
    except ToolNotFound:
        raise
    except CineRecastError as exc:
        raise CineRecastError(f"the reference video cannot be read to check the mix against its sound ({exc.message}); "
                              "no soundtrack written") from exc
    if not has_audio:
        return None
    ours = decode_audio(mixed, rate=CHECK_RATE, channels=1)[:, 0]
    theirs = decode_audio(reference, rate=CHECK_RATE, channels=1)[:, 0]
    return max_correlation(ours, theirs, round(MAX_LAG * CHECK_RATE))


# --- the mix -------------------------------------------------------------------------------

def _placed(root: Path, frames: int) -> list[dict]:
    """The done, unmuted sounds, in id order, each with its stored file; refused when a placement or
    a stored file is not usable."""
    states = ledger.job_states(root)
    placed, problems = [], []
    for item in sound.load_items(root):
        if states.get(item.get("id")) != "done" or item.get("muted") is True:
            continue
        problems += sound.placement_problems(item, frames)
        seconds = item.get("seconds")
        if not isinstance(seconds, (int, float)) or isinstance(seconds, bool) or not seconds > 0:
            problems.append(f"sound {item.get('id')}: seconds must be a number greater than 0, not {seconds!r}.")
        file = item.get("file")
        expected = re.compile(rf"sounds/{re.escape(str(item.get('id')))}\.(wav|mp3|m4a|ogg|flac)")
        if not isinstance(file, str) or not expected.fullmatch(file) or not (root / file).is_file():
            problems.append(f"sound {item.get('id')} is done but its stored file {file!r} is missing or not a file "
                            "that sound done stored; register the sound again with a new item")
        placed.append(item)
    if problems:
        raise CineRecastError("sounds.json: " + " ".join(problems) + " No soundtrack written.")
    return placed


def _cut_list(root: Path, placed: list[dict]) -> list[dict]:
    return [{"frame": shot.start, "sounds": [item["id"] for item in placed
                                             if abs(item["at_frame"] - shot.start) <= CUT_WINDOW]}
            for shot in load_shots(root)[1:]]


def mix(root: Path) -> dict:
    """Write out/soundtrack.wav and out/soundtrack.json (see the module docstring) and return the
    record with "path" added. Refused, with nothing written: no sound done and unmuted, a sum that
    is digital silence or too quiet to measure, a mix that carries the reference's sound."""
    root = Path(root)
    pp = ProjectPaths(root)
    load_project(root)
    info, _ = load_video(root)
    placed = _placed(root, info.frames)
    if not placed:
        raise CineRecastError("no sound is done and unmuted, so there is nothing to mix; generate sounds with sound "
                              "add, sound next and sound done, or unmute one with sound set --unmute")
    total = _samples(info.frames, info.fps_num, info.fps_den)
    fps = Fraction(info.fps_num, info.fps_den)
    mixed = np.zeros((total, CHANNELS), dtype=np.float32)
    for item in placed:
        clip = _shaped(root / item["file"], item)
        start = _samples(item["at_frame"], info.fps_num, info.fps_den)
        if start + len(clip) > total:                   # the film ends during this sound
            clip = clip[:total - start].copy()
            length = min(len(clip), round(END_FADE * RATE))
            if length > 0:
                clip[len(clip) - length:] *= _ramp(length, False)[:, None]
        mixed[start:start + len(clip)] += clip
    if float(np.abs(mixed).max()) < SILENCE:
        raise CineRecastError("the mix is digital silence (every placed sound is silent or gained down to nothing); "
                              "no soundtrack written")
    before = _loudness(raw_audio_input(RATE, CHANNELS), mixed.astype("<f4").tobytes())
    if not math.isfinite(before):
        raise CineRecastError("the mix is too quiet to measure its loudness (below the -70 LUFS gate of the "
                              "measurement); raise the gain of the sounds with sound set; no soundtrack written")
    gain = TARGET_LUFS - before
    mixed *= np.float32(10.0 ** (gain / 20.0))
    ceiling = 10.0 ** (CEILING_DBFS / 20.0)
    pp.out_dir.mkdir(parents=True, exist_ok=True)
    handle, scratch_name = tempfile.mkstemp(dir=pp.out_dir, prefix=".soundtrack-", suffix=".wav")
    scratch = Path(scratch_name)
    record_scratch = scratch.with_suffix(".json")
    try:
        os.close(handle)
        run_piped([*raw_audio_input(RATE, CHANNELS), "-af",
                   f"alimiter=limit={ceiling:.6f}:level=disabled:latency=1:attack=5:release=50,"
                   f"apad=whole_len={total},atrim=end_sample={total}",
                   "-ar", str(RATE), "-c:a", "pcm_s24le", "-f", "wav", str(scratch)], mixed.astype("<f4").tobytes())
        written = decode_audio(scratch, rate=RATE, channels=CHANNELS)
        if len(written) != total:
            raise CineRecastError(f"the written soundtrack has {len(written)} samples, the film needs {total}")
        peak = float(np.abs(written).max())
        after = _loudness(["-i", str(scratch)])
        correlation = _reference_correlation(root, scratch)
        if correlation is not None and correlation >= REFERENCE_LIMIT:
            raise CineRecastError(
                f"the mix carries the sound of the reference video (correlation {correlation:.3f} with the "
                f"reference's sound, the limit is {REFERENCE_LIMIT}); a sound of the reference was registered as a "
                "generated sound: mute it with sound set --mute or replace it with a newly generated one; no "
                "soundtrack written")
        record = {
            "sounds": [{"id": item["id"], "kind": item.get("kind"), "label": item.get("label"),
                        "start_seconds": round(float(item["at_frame"] / fps), 6), "at_frame": item["at_frame"],
                        "seconds": item["seconds"], "gain_db": item["gain_db"], "fade_in": item["fade_in"],
                        "fade_out": item["fade_out"]} for item in placed],
            "loudness_before": round(before, 2), "gain_applied_db": round(gain, 2),
            "loudness_after": round(after, 2) if math.isfinite(after) else None,
            "peak_dbfs": round(20 * math.log10(peak), 2) if peak > 0 else None,
            "duration_seconds": round(float(Fraction(info.frames) / fps), 6),
            "sha256": sha256_of(scratch),
            "reference_correlation": None if correlation is None else round(correlation, 4),
            "cuts": _cut_list(root, placed),
        }
        write_json(record_scratch, record)
        scratch.replace(pp.soundtrack_file)
        record_scratch.replace(pp.soundtrack_record)
    finally:
        scratch.unlink(missing_ok=True)
        record_scratch.unlink(missing_ok=True)
    return {**record, "path": pp.soundtrack_file}


# --- verification --------------------------------------------------------------------------

def check(root: Path, *, strict: bool = True) -> tuple[Path, dict] | None:
    """(out/soundtrack.wav, its record) when a soundtrack exists and its SHA-256 equals the one in
    out/soundtrack.json; None when there is no soundtrack.wav. A soundtrack that does not match its
    record (changed or replaced after the mix, or without a readable record) is refused with strict,
    and is None without."""
    pp = ProjectPaths(Path(root))
    if not pp.soundtrack_file.is_file():
        return None
    problem = None
    try:
        record = read_project_json(pp.root, pp.soundtrack_record, {"sha256": str, "sounds": list})
        if record["sha256"] != sha256_of(pp.soundtrack_file):
            problem = "out/soundtrack.wav is not the file sound mix wrote (its SHA-256 differs from out/soundtrack.json)"
    except CineRecastError as exc:
        record, problem = None, exc.message
    if problem is None:
        return pp.soundtrack_file, record
    if strict:
        raise CineRecastError(f"{problem}; run sound mix again (or assemble with --silent or --audio)")
    return None


def placement_of(item: dict) -> dict:
    """The keys that place a sound in the mix, as soundtrack.json records them (start_seconds aside)."""
    return {key: item.get(key) for key in ("id", "at_frame", "seconds", "gain_db", "fade_in", "fade_out")}


def stale(root: Path, record: dict) -> bool:
    """True when the done, unmuted sounds of sounds.json or their placement differ from the mixed ones."""
    states = ledger.job_states(root)
    now = [placement_of(item) for item in sound.load_items(root)
           if states.get(item.get("id")) == "done" and item.get("muted") is not True]
    mixed = [placement_of(entry) for entry in record.get("sounds") or [] if isinstance(entry, dict)]
    return now != mixed
