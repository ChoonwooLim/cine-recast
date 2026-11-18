"""Sound for the film: generated music, sound effects and ambience, each placed on a film frame.

A sound item is one audio-generation call (one prompt, one clip of `seconds`). Generation services
take text and a duration and know nothing of the film, so the tool keeps the synchronisation: every
item starts at a film frame (at_frame), and the mix (soundtrack.py) puts it there. Sound items live
in sounds.json and follow the life cycle of video jobs and asset jobs (lifecycle.py) through the
same ledger:

planned (add) -> quoted (set_cost) -> issued (issue_next) -> done or failed (record_done, record_fail)

- A sound reaches the agent only through issue_next, which hands it to ledger.issue first and
  stops at the first refusal.
- The ledger is the authority for state: every load moves an item forward to the state the ledger
  records for it.
- A quote is bound to what it priced: the model and the duration (not the prompt). The placement
  (at_frame, gain_db, fade_in, fade_out, muted) is not part of the quote and changes at any time.
- The returned file is stored as sounds/<id><suffix> and never replaced once the ledger records the
  item as done.
- Sound ids (s0001, ...) share the ledger with video job ids (j0001, ...) and asset ids (a0001, ...)
  and are never reused.
- The sound of the reference video is never an input: record_done refuses project material and a
  copy of the reference, and the mix compares its result with the reference's sound.
- Model limits come only from the project's models.json.
"""

import math
import re
import shutil
from pathlib import Path

from cine_recast import ledger
from cine_recast.analyze import load_shots, load_video
from cine_recast.errors import CineRecastError
from cine_recast.ffmpeg import ToolNotFound, audio_streams
from cine_recast.jsonio import read_project_json, write_json
from cine_recast.lifecycle import IssueRefused  # noqa: F401  (callers catch sound.IssueRefused)
from cine_recast.lifecycle import (SENT, fail, finish, highest, is_number, is_text, issue_in_order, quote, reconcile,
                                   require_issued, require_result, stale_quote)
from cine_recast.paths import ProjectPaths, inside_folder, same_bytes
from cine_recast.plan import valid_models
from cine_recast.project import load_project
from cine_recast.textrules import han_chars, is_english_prompt

TOOL = "generate_audio"
KINDS = ("music", "sfx", "ambience")
SUFFIXES = (".wav", ".mp3", ".m4a", ".ogg", ".flac")
GAIN_MIN, GAIN_MAX = -60.0, 12.0
DEFAULT_FADE_OUT = 0.2
_SOUND_ID = re.compile(r"^s(\d+)$")
_NOUN, _QUOTE_COMMAND = "sound", "sound set-cost"


# --- sounds.json ---------------------------------------------------------------------------

def _load(root: Path) -> dict:
    """sounds.json with every item's state reconciled from the ledger; no items when it is missing."""
    pp = ProjectPaths(Path(root))
    if not pp.sounds_file.exists():
        return {"sounds": []}
    data = read_project_json(pp.root, pp.sounds_file, {"sounds": list})
    reconcile(root, data["sounds"])
    return data


def _save(root: Path, data: dict) -> None:
    write_json(ProjectPaths(Path(root)).sounds_file, data)


def _find(data: dict, sound_id: str) -> dict:
    for item in data["sounds"]:
        if isinstance(item, dict) and item.get("id") == sound_id:
            return item
    raise CineRecastError(f"unknown sound: {sound_id}")


def _number(item: dict) -> tuple[int, str]:
    sid = str(item.get("id", ""))
    match = _SOUND_ID.match(sid)
    return (int(match.group(1)) if match else -1, sid)


def _next_number(root: Path, data: dict) -> int:
    """Above every sound id in the ledger and in sounds.json (video job and asset ids are not counted)."""
    ids = [i.get("id") for i in data["sounds"] if isinstance(i, dict)]
    return highest(_SOUND_ID, [*ledger.job_states(root), *ids]) + 1


def load_items(root: Path) -> list[dict]:
    """The items of sounds.json (reconciled from the ledger) that are objects, in id order."""
    return sorted((item for item in _load(root)["sounds"] if isinstance(item, dict)), key=_number)


# --- payloads and checks -------------------------------------------------------------------

def _whole(value: float):
    return int(value) if isinstance(value, float) and value.is_integer() else value


def _payload(item: dict) -> dict:
    """The exact call for one sound. The same payload is shown for quoting and sent at issue."""
    return {"job_id": item.get("id"), "kind": item.get("kind"), "tool": TOOL,
            "params": {"model": item.get("model"), "prompt": item.get("prompt"), "duration": item.get("seconds")},
            "medias": []}


def _problems(item: dict, models: dict[str, dict]) -> list[str]:
    """What keeps a sound from being quoted or issued, as English sentences."""
    label = f"sound {item['id']}" if is_text(item.get("id")) else "sound"
    problems: list[str] = []
    if item.get("kind") not in KINDS:
        problems.append(f"{label}: kind must be one of {', '.join(KINDS)}, not {item.get('kind')!r}.")
    name = item.get("label")
    if not is_text(name):
        problems.append(f"{label}: label must be a non-empty text.")
    elif han_chars(name):
        problems.append(f"{label}: label must not contain Han characters ({''.join(han_chars(name))}).")
    prompt = item.get("prompt")
    if not isinstance(prompt, str) or not is_english_prompt(prompt):
        problems.append(f"{label}: prompt must be a non-empty English text (no Han, Hangul or Kana characters).")
    model = item.get("model")
    spec = models.get(model) if isinstance(model, str) else None
    if spec is None:
        problems.append(f"{label}: model {model!r} is not in models.json.")
    elif spec["kind"] != "audio":
        problems.append(f"{label}: model {model!r} has kind {spec['kind']!r}; sounds need an audio model.")
        spec = None
    seconds = item.get("seconds")
    if not is_number(seconds) or seconds <= 0:
        problems.append(f"{label}: seconds must be a number greater than 0, not {seconds!r}.")
    elif spec is not None and not spec["min_seconds"] <= seconds <= spec["max_seconds"]:
        problems.append(f"{label}: seconds {seconds} is outside the limits of model {model!r} "
                        f"({spec['min_seconds']} to {spec['max_seconds']} s).")
    if not is_text(item.get("batch")):
        problems.append(f"{label}: batch must not be empty.")
    return problems


def _film(root: Path):
    """(VideoInfo, shots) of the analysed reference: the film's frame count, frame rate and shots."""
    info, _ = load_video(root)
    return info, load_shots(root)


def _start_frame(root: Path, frames: int, *, at_frame=None, shot=None, offset_frames=None) -> int | None:
    """The start frame given by at_frame, or by the first frame of shot plus offset_frames; None when
    neither is given. Refused unless it is a whole number from 0 to the film's last frame."""
    if at_frame is not None and shot is not None:
        raise CineRecastError("give the start either as a frame (--at-frame) or as a shot (--shot), not both")
    if offset_frames is not None and shot is None:
        raise CineRecastError("--offset-frames counts from the first frame of a shot; give the shot with --shot")
    for name, value in (("at_frame", at_frame), ("shot", shot), ("offset_frames", offset_frames)):
        if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
            raise CineRecastError(f"{name} must be a whole number, not {value!r}")
    if shot is not None:
        shots = load_shots(root)
        found = next((s for s in shots if s.index == shot), None)
        if found is None:
            raise CineRecastError(f"unknown shot {shot}; the film has shots 1 to {len(shots)}")
        at_frame = found.start + (offset_frames or 0)
    if at_frame is None:
        return None
    if at_frame < 0:
        raise CineRecastError(f"the start frame must be at least 0, not {at_frame}")
    if at_frame >= frames:
        raise CineRecastError(f"the start frame {at_frame} is not inside the film's {frames} frames (0 to {frames - 1})")
    return at_frame


def _mix_value(name: str, value, *, low: float | None = None, high: float | None = None) -> float:
    if not is_number(value) or (low is not None and value < low) or (high is not None and value > high):
        bounds = f"from {low:g} to {high:g}" if high is not None else f"of at least {low:g}"
        raise CineRecastError(f"{name} must be a finite number {bounds}, not {value!r}")
    return float(value)


def placement_problems(item: dict, frames: int) -> list[str]:
    """What is wrong with an item's placement and mix values (a sounds.json edited by hand), as sentences."""
    label = f"sound {item.get('id')}"
    problems = []
    at = item.get("at_frame")
    if isinstance(at, bool) or not isinstance(at, int) or not 0 <= at < frames:
        problems.append(f"{label}: at_frame must be a whole number from 0 to {frames - 1}, not {at!r}.")
    for name, low, high in (("gain_db", GAIN_MIN, GAIN_MAX), ("fade_in", 0.0, None), ("fade_out", 0.0, None)):
        value = item.get(name)
        if not is_number(value) or value < low or (high is not None and value > high):
            problems.append(f"{label}: {name} must be a finite number "
                            f"{f'from {low:g} to {high:g}' if high is not None else f'of at least {low:g}'}, "
                            f"not {value!r}.")
    if not isinstance(item.get("muted"), bool):
        problems.append(f"{label}: muted must be true or false, not {item.get('muted')!r}.")
    return problems


# --- the life cycle ------------------------------------------------------------------------

def add(root: Path, *, kind: str, label: str, prompt: str, model: str, seconds: float, batch: str,
        at_frame: int | None = None, shot: int | None = None, offset_frames: int | None = None,
        gain_db: float = 0.0, fade_in: float = 0.0, fade_out: float = DEFAULT_FADE_OUT) -> dict:
    """Plan an audio-generation job and return the planned item.

    The sound starts at at_frame, or at the first frame of shot plus offset_frames, or at frame 0.
    Nothing is written when a check refuses.
    """
    root = Path(root)
    load_project(root)
    info, _ = _film(root)
    start = _start_frame(root, info.frames, at_frame=at_frame, shot=shot, offset_frames=offset_frames)
    item = {"id": None, "kind": kind, "label": label, "prompt": prompt, "model": model,
            "seconds": _whole(float(seconds)) if is_number(seconds) else seconds, "batch": batch,
            "state": "planned", "est_credits": None, "quote_digest": None, "file": None,
            "at_frame": 0 if start is None else start,
            "gain_db": _mix_value("gain_db", gain_db, low=GAIN_MIN, high=GAIN_MAX),
            "fade_in": _mix_value("fade_in", fade_in, low=0.0), "fade_out": _mix_value("fade_out", fade_out, low=0.0),
            "muted": False}
    problems = _problems(item, valid_models(root))
    if problems:
        raise CineRecastError("; ".join(problems))
    data = _load(root)
    item["id"] = f"s{_next_number(root, data):04d}"
    data["sounds"].append(item)
    _save(root, data)
    return item


def list_sounds(root: Path) -> list[dict]:
    """Every item with its state, batch, estimate, placement and file, and its params: the params it
    was sent with once issued, the params it would be sent with before that."""
    return [{"id": i.get("id"), "kind": i.get("kind"), "label": i.get("label"), "batch": i.get("batch"),
             "state": i.get("state"), "est_credits": i.get("est_credits"),
             "params": i.get("sent_params") if i.get("state") in SENT else _payload(i)["params"],
             "at_frame": i.get("at_frame"), "gain_db": i.get("gain_db"), "fade_in": i.get("fade_in"),
             "fade_out": i.get("fade_out"), "muted": i.get("muted"), "file": i.get("file")}
            for i in load_items(root)]


def set_cost(root: Path, sound_id: str, credits: float) -> dict:
    """Record the quoted cost of a planned or quoted sound and bind it to its model and duration.
    The item becomes quoted; a new quote replaces the estimate and the binding until it is issued."""
    root = Path(root)
    data = _load(root)
    item = _find(data, sound_id)

    def checked_payload() -> dict:
        problems = _problems(item, valid_models(root))
        if problems:
            raise CineRecastError("; ".join(problems))
        return _payload(item)

    quote(item, credits, checked_payload, noun=_NOUN)
    _save(root, data)
    return item


def issue_next(root: Path, batch: str) -> list[dict]:
    """Hand the batch's quoted sounds to the ledger in id order and return what to run for each.

    Every queued item must pass the checks of add again and still have the payload its quote
    priced, or nothing is issued. Stops with IssueRefused at the first item the ledger refuses; the
    items issued before it stay issued and travel on the exception.
    """
    root = Path(root)
    data = _load(root)
    queue = sorted((i for i in data["sounds"]
                    if isinstance(i, dict) and i.get("batch") == batch and i.get("state") == "quoted"), key=_number)
    if queue:
        models = valid_models(root)
        problems: list[str] = []
        for item in queue:
            problems += _problems(item, models)
            stale = stale_quote(item, _payload(item), noun=_NOUN, command=_QUOTE_COMMAND)
            if stale:
                problems.append(stale)
        if problems:
            raise CineRecastError("sound check failed, no sound issued: " + "; ".join(problems))
    return issue_in_order(root, batch, queue, _payload, lambda: _save(root, data), noun=_NOUN, command=_QUOTE_COMMAND)


def _files_named_for(folder: Path, sound_id: str) -> list[Path]:
    """The files in folder named as record_done names the file of sound_id (<id><suffix>)."""
    if not folder.is_dir():
        return []
    return [path for path in folder.iterdir()
            if path.is_file() and path.stem == sound_id and path.suffix.lower() in SUFFIXES]


def _check_result_file(root: Path, sound_id: str, source: Path) -> None:
    """Refuse a result file that is not a downloaded generation result with an audio stream."""
    pp = ProjectPaths(root)
    if source.suffix.lower() not in SUFFIXES:
        raise CineRecastError(f"sound {sound_id}: result file {source} must be a .wav, .mp3, .m4a, .ogg or .flac file")
    if not source.is_file():
        raise CineRecastError(f"sound {sound_id}: result file not found: {source}")
    folder = inside_folder(source, [*pp.material_folders(), pp.sounds_dir, pp.extras_dir])
    if folder is not None:
        raise CineRecastError(f"sound {sound_id}: result file {source} is project material (inside the project's "
                              f"{folder.name} folder), not a generation result; register only files downloaded from "
                              "the generation job")
    if same_bytes(source, root / load_project(root)["reference"]):
        raise CineRecastError(f"sound {sound_id}: {source} is the reference video (its bytes equal the project's "
                              "reference copy); the film's sound is generated for the film, the reference's sound is "
                              "never used")
    try:
        streams = audio_streams(source)
    except ToolNotFound:
        raise
    except CineRecastError:
        streams = 0
    if streams == 0:
        raise CineRecastError(f"sound {sound_id}: result file {source} has no audio stream that ffprobe can read; "
                              "download the file again")


def record_done(root: Path, sound_id: str, provider_id: str, credits: float, file: Path) -> dict:
    """Store the returned file as sounds/<id><suffix>, record the item as done in the ledger, then
    save. Returns {"sound", "file"} with the stored file relative to the project.

    Refused: an item that is not issued; a file that is missing, of another type, without an audio
    stream, inside the project's reference/, analysis/, takes/, out/, assets/ or sounds/ folder, or
    with the bytes of the reference copy. The file of an item the ledger records as done is never
    replaced. While the ledger does not record it as done, files already named for this id are
    leftovers of an interrupted call and are removed. The stored file is listed in sounds.json
    before the ledger records done; if the ledger refuses, the stored file is removed and the list
    restored.
    """
    root = Path(root)
    pp = ProjectPaths(root)
    data = _load(root)
    item = require_issued(_find(data, sound_id), noun=_NOUN)
    if not _SOUND_ID.fullmatch(sound_id):
        raise CineRecastError(f"sound {sound_id}: its id must be 's' followed by digits to name its file")
    require_result(item, provider_id, credits, noun=_NOUN)
    source = Path(file)
    _check_result_file(root, sound_id, source)
    if ledger.job_states(root).get(sound_id) == "done":
        raise CineRecastError(f"sound {sound_id} is done in the ledger; its stored file is never replaced")
    for leftover in _files_named_for(pp.sounds_dir, sound_id):   # an interrupted call stored it, the ledger never heard
        leftover.unlink()
    previous = item.get("file") if isinstance(item.get("file"), str) and (root / item["file"]).is_file() else None
    target = pp.sounds_dir / f"{sound_id}{source.suffix.lower()}"
    pp.sounds_dir.mkdir(parents=True, exist_ok=True)
    stored: list[Path] = []

    def undo() -> None:
        for path in stored:
            path.unlink(missing_ok=True)

    try:
        with source.open("rb") as reader, target.open("xb") as writer:   # "x": never overwrite
            stored.append(target)
            shutil.copyfileobj(reader, writer)
    except BaseException:
        undo()
        raise
    name = target.relative_to(root).as_posix()
    item["file"] = name
    try:
        _save(root, data)  # listed before the ledger records done, so a crash after that strands no paid file
    except BaseException:
        undo()
        raise

    def take_back() -> None:
        undo()
        item["file"] = previous
        _save(root, data)

    finish(root, item, provider_id, credits, take_back, lambda: _save(root, data))
    return {"sound": sound_id, "file": name}


def record_fail(root: Path, sound_id: str, reason: str, credits: float = 0.0) -> dict:
    """Record an issued sound as failed in the ledger (credits spent, if any) and return the item."""
    root = Path(root)
    data = _load(root)
    return fail(root, _find(data, sound_id), reason, credits, lambda: _save(root, data), noun=_NOUN)


# --- placement -----------------------------------------------------------------------------

def set_placement(root: Path, sound_id: str, *, at_frame: int | None = None, shot: int | None = None,
                  offset_frames: int | None = None, gain_db: float | None = None, fade_in: float | None = None,
                  fade_out: float | None = None, mute: bool = False, unmute: bool = False) -> dict:
    """Change the placement and mix values of an item in any state; values not given stay. Returns
    the item. Nothing is written when a value is refused."""
    root = Path(root)
    if mute and unmute:
        raise CineRecastError("give --mute or --unmute, not both")
    info, _ = _film(root)
    data = _load(root)
    item = _find(data, sound_id)
    changes: dict = {}
    start = _start_frame(root, info.frames, at_frame=at_frame, shot=shot, offset_frames=offset_frames)
    if start is not None:
        changes["at_frame"] = start
    if gain_db is not None:
        changes["gain_db"] = _mix_value("gain_db", gain_db, low=GAIN_MIN, high=GAIN_MAX)
    for name, value in (("fade_in", fade_in), ("fade_out", fade_out)):
        if value is not None:
            changes[name] = _mix_value(name, value, low=0.0)
    if mute or unmute:
        changes["muted"] = mute
    item.update(changes)
    _save(root, data)
    return item


def placement_line(root: Path, item: dict) -> str:
    """One line with the item's start frame (and its time), gain, fades, and whether it is muted."""
    info, _ = load_video(root)
    at = item.get("at_frame")
    seconds = at * info.fps_den / info.fps_num if isinstance(at, int) and not isinstance(at, bool) else math.nan
    line = (f"{item.get('id')}: frame {at} ({seconds:.3f} s), gain {item.get('gain_db')} dB, "
            f"fade in {item.get('fade_in')} s, fade out {item.get('fade_out')} s")
    return line + (", muted" if item.get("muted") is True else "")
