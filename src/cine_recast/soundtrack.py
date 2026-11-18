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
3. Reference check (fail closed, reference_problem): when the reference video has an audio stream,
   its sound is put on the video's clock (the stream's start offset) and compared with the mix, both
   mono at 8 kHz: the largest absolute normalised cross-correlation over the whole film, and the
   same measure in every WINDOW-second window (hop WINDOW_HOP, near-silent windows skipped), each
   over lags of plus or minus MAX_LAG. At REFERENCE_LIMIT or more, in either, the mix carries the
   reference's sound and is refused; each placed sound is then checked alone to name the ones that
   carry it.

   What the measure still cannot catch: a copy shifted by more than MAX_LAG from its place in the
   reference; a copy buried in every window under other sound with about four times its energy
   (6 dB louder); a copy changed in pitch, speed or spectrum; an excerpt much shorter than a window
   under other sound. Two unrelated sounds that hold the same steady pitch for a whole window (a
   drone or pad on the reference's note, a mains hum) also reach the limit, as similar as a copy
   would be; the refusal says so and leaves the judgement to the user's ears.

Both files are written to scratch names in out/ and moved into place only when everything passed. A
refused mix also removes the soundtrack an earlier mix left, so no soundtrack older than the refusal
remains; a file another program holds open is a refusal that says so, and the record goes first, so
a soundtrack that could not be removed no longer passes check(). check() is what assemble uses: it
returns the soundtrack only while its bytes are the ones mixed, and assemble runs reference_problem
on whatever audio goes into the deliverable.
"""

import contextlib
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
from cine_recast.ffmpeg import (ToolNotFound, align_audio, audio_offset, audio_streams, decode_audio, raw_audio_input,
                                run_piped)
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
WINDOW = 2.0                # seconds of each window of the windowed reference check
WINDOW_HOP = 1.0            # seconds between the starts of two windows
QUIET = 1e-4                # a window whose RMS is below this (about -80 dBFS) is skipped
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


def windowed_correlation(mix: np.ndarray, reference: np.ndarray, rate: int) -> float:
    """The largest absolute normalised cross-correlation of the two mono signals within any window of
    WINDOW seconds (hop WINDOW_HOP) of the mix, each over lags of plus or minus MAX_LAG against the
    reference around the same time. Windows where either side is near-silent are skipped; 0.0 when
    every window is."""
    x = np.asarray(mix, dtype=np.float64).ravel()
    y = np.asarray(reference, dtype=np.float64).ravel()
    size, hop, lag = round(WINDOW * rate), round(WINDOW_HOP * rate), round(MAX_LAG * rate)
    best = 0.0
    for begin in range(0, max(1, len(x) - size + hop), hop):
        part = x[begin:begin + size]
        lo, hi = max(0, begin - lag), min(len(y), begin + len(part) + lag)
        if len(part) < rate // 4 or hi - lo < len(part) // 2:
            continue
        if math.sqrt(float((part ** 2).mean())) < QUIET or math.sqrt(float((y[lo:hi] ** 2).mean())) < QUIET:
            continue
        padded = np.zeros(len(part) + 2 * lag)          # the reference around the window, zero outside it
        padded[lo - (begin - lag):hi - (begin - lag)] = y[lo:hi]
        best = max(best, _window_ncc(part, padded, lag))
    return best


def _window_ncc(part: np.ndarray, around: np.ndarray, lag: int) -> float:
    """Largest absolute NCC of `part` against the slices around[k:k + len(part)], k = 0 .. 2 * lag,
    each normalised by the energies of the two slices (means removed)."""
    x = part - part.mean()
    size = 1 << int(len(around) + len(x)).bit_length()
    corr = np.fft.irfft(np.fft.rfft(around, size) * np.conj(np.fft.rfft(x, size)), size)[:2 * lag + 1]
    sums = np.concatenate([[0.0], np.cumsum(around)])
    squares = np.concatenate([[0.0], np.cumsum(around * around)])
    k = np.arange(2 * lag + 1)
    n = len(x)
    slice_sum = sums[k + n] - sums[k]
    energy_y = squares[k + n] - squares[k] - slice_sum ** 2 / n
    energy_x = float((x * x).sum())
    usable = (energy_y > 1e-12 * max(energy_x, 1e-300)) & (energy_x > 0)
    if not usable.any():
        return 0.0
    return float(min(1.0, (np.abs(corr[usable]) / np.sqrt(energy_x * energy_y[usable])).max()))


def _reference_samples(root: Path) -> np.ndarray | None:
    """The reference's sound, mono at CHECK_RATE, on the video's clock; None when the reference has no
    audio stream. A reference whose streams cannot be read is refused (fail closed)."""
    reference = root / load_project(root)["reference"]
    try:
        if audio_streams(reference) == 0:
            return None
        offset = audio_offset(reference)
        samples = decode_audio(reference, rate=CHECK_RATE, channels=1)[:, 0]
    except ToolNotFound:
        raise
    except CineRecastError as exc:
        raise CineRecastError(f"the reference video cannot be read to check the sound against it ({exc.message}); "
                              "nothing written") from exc
    return align_audio(samples, offset, CHECK_RATE)


def _similarity(ours: np.ndarray, theirs: np.ndarray) -> tuple[float, float]:
    """(whole-film correlation, largest windowed correlation)."""
    return (max_correlation(ours, theirs, round(MAX_LAG * CHECK_RATE)),
            windowed_correlation(ours, theirs, CHECK_RATE))


def reference_problem(root: Path, audio: Path, what: str) -> tuple[float | None, str | None]:
    """(the whole-film correlation of `audio` with the reference's sound, or None without reference
    audio; the refusal sentence when the whole-film or a windowed correlation reaches
    REFERENCE_LIMIT, else None). `what` names the audio in the sentence."""
    theirs = _reference_samples(Path(root))
    if theirs is None:
        return None, None
    whole, window = _similarity(decode_audio(audio, rate=CHECK_RATE, channels=1)[:, 0], theirs)
    if max(whole, window) < REFERENCE_LIMIT:
        return whole, None
    return whole, (f"{what} is as similar to the sound of the reference video as a copy would be (correlation "
                   f"{whole:.3f} over the whole film, {window:.3f} in the most similar {WINDOW:g}-second window; the "
                   f"limit is {REFERENCE_LIMIT})")


def _to_check_rate(samples: np.ndarray) -> np.ndarray:
    """Soundtrack samples (RATE, stereo, float32) as mono at CHECK_RATE, resampled by ffmpeg."""
    out, _ = run_piped([*raw_audio_input(RATE, CHANNELS), "-ac", "1", "-ar", str(CHECK_RATE), "-f", "f32le",
                        "-acodec", "pcm_f32le", "pipe:1"], samples.astype("<f4").tobytes())
    return np.frombuffer(out[:len(out) - len(out) % 4], dtype="<f4").astype(np.float64)


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


def _place(buffer: np.ndarray, root: Path, item: dict, info) -> None:
    """Add the item, shaped, at its start frame into the film-long buffer; what runs past the end of
    the film is cut with an END_FADE fade."""
    total = len(buffer)
    clip = _shaped(root / item["file"], item)
    start = _samples(item["at_frame"], info.fps_num, info.fps_den)
    if start + len(clip) > total:                   # the film ends during this sound
        clip = clip[:total - start].copy()
        length = min(len(clip), round(END_FADE * RATE))
        if length > 0:
            clip[len(clip) - length:] *= _ramp(length, False)[:, None]
    buffer[start:start + len(clip)] += clip


def _carriers(root: Path, placed: list[dict], info, theirs: np.ndarray) -> list[tuple[str, float]]:
    """Each placed sound checked alone against the reference's sound: those at or above the limit, or
    the most similar one when none reaches it alone, as (id, correlation), most similar first."""
    scores = []
    for item in placed:
        alone = np.zeros((_samples(info.frames, info.fps_num, info.fps_den), CHANNELS), dtype=np.float32)
        _place(alone, root, item, info)
        scores.append((item["id"], max(_similarity(_to_check_rate(alone), theirs))))
    scores.sort(key=lambda pair: -pair[1])
    hits = [pair for pair in scores if pair[1] >= REFERENCE_LIMIT]
    return hits or scores[:1]


def _cut_list(root: Path, placed: list[dict]) -> list[dict]:
    return [{"frame": shot.start, "sounds": [item["id"] for item in placed
                                             if abs(item["at_frame"] - shot.start) <= CUT_WINDOW]}
            for shot in load_shots(root)[1:]]


def mix(root: Path) -> dict:
    """Write out/soundtrack.wav and out/soundtrack.json (see the module docstring) and return the
    record with "path" added. Refused, with nothing written and the soundtrack of an earlier mix
    removed: no sound done and unmuted, a sum that is digital silence or too quiet to measure, a mix
    that carries the reference's sound, and every other refusal."""
    root = Path(root)
    try:
        return _mix(root)
    except ToolNotFound:
        raise                                       # the machine lacks ffmpeg: not a refused mix
    except CineRecastError as exc:
        left = _remove_soundtrack(ProjectPaths(root))
        if left:
            raise CineRecastError(f"{exc.message}; {left}") from exc
        raise


def _remove_soundtrack(pp: ProjectPaths) -> str | None:
    """Remove the soundtrack an earlier mix left, the record first: a soundtrack.wav that cannot be
    removed (held open by another program) then has no record, and check() refuses it. Returns what
    could not be removed as a sentence, or None."""
    held = []
    for path in (pp.soundtrack_record, pp.soundtrack_file):
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            held.append(f"{path.relative_to(pp.root).as_posix()} ({exc.strerror or exc})")
    if not held:
        return None
    if not pp.soundtrack_file.exists():
        verdict = "no soundtrack is left"
    elif not pp.soundtrack_record.exists():
        verdict = "assemble refuses that soundtrack until sound mix runs again"
    else:
        verdict = "assemble would still take the earlier soundtrack, so do not assemble before sound mix succeeds"
    return (f"the earlier soundtrack could not be removed: {', '.join(held)} (is it open in another program?); close "
            f"it and run sound mix again; {verdict}")


def _mix(root: Path) -> dict:
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
        _place(mixed, root, item, info)
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
        correlation, problem = reference_problem(root, scratch, "the mix")
        if problem:
            carriers = _carriers(root, placed, info, _reference_samples(root))
            named = ", ".join(f"{sid} (correlation {value:.3f} alone)" for sid, value in carriers)
            raise CineRecastError(
                f"{problem}; it comes from sound(s) {named}; either a sound of the reference was registered as a "
                "generated sound, or the named sound holds a sustained tone on the same pitch as the reference (a "
                "drone or pad on the same note reaches the limit too); the tool cannot tell the two apart, so ask "
                "the user to listen to the named sound: if it is the reference's sound, mute it with sound set "
                "--mute; if it is not, generate the cue again in another key with sound add and mute this one; no "
                "soundtrack written, and an earlier one is removed")
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
        try:
            scratch.replace(pp.soundtrack_file)
            record_scratch.replace(pp.soundtrack_record)
        except OSError as exc:
            raise CineRecastError(f"cannot replace {pp.soundtrack_file} (is it open in another program?): "
                                  f"{exc.strerror or exc}; no soundtrack written") from exc
    finally:
        for leftover in (scratch, record_scratch):
            with contextlib.suppress(OSError):          # a scratch name of out/, never read again
                leftover.unlink(missing_ok=True)
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
