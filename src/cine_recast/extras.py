"""Extra shots: video-generation jobs for shots that are not in the reference (an opening, a bridge,
an ending), made to join an existing take.

An extra is one video-generation call. To lead into or out of a shot it either extends a take
(kind "extend": the take's raw file is the video input, the result continues it before its first
frame, "backward", or after its last frame, "forward") or starts or ends on a frame of a take (kind
"shot": the frame is written as extras/<id>/start.png or end.png when the extra is added); reference
images from assets/ or swap/ may be added. Extras live in extras.json and follow the life cycle of
the other paid jobs (lifecycle.py) through the same ledger, as sound items do:

planned (add) -> quoted (set_cost) -> issued (issue_next) -> done or failed (record_done, record_fail)

- An extra reaches the agent only through issue_next, which hands it to ledger.issue first and
  stops at the first refusal.
- The ledger is the authority for state: every load moves an extra forward to the state the ledger
  records for it.
- A quote is bound to what it priced: the params (not the prompt) and the content of every input
  file, so a changed input file must be quoted again.
- The returned file is stored as extras/<id>/take<suffix> and never replaced once the ledger
  records the extra as done.
- Extra ids (x0001, ...) share the ledger with the other job ids and are never reused.
- A take shows the swapped people, so add, set_cost and issue_next require a clean cast check.
- Extras are free cuts for the user: assemble, which rebuilds the reference shot for shot, never
  uses them.
"""

import re
import shutil
from pathlib import Path

from cine_recast import casting, ledger, plan
from cine_recast.analyze import load_video
from cine_recast.casting import IMAGE_SUFFIXES
from cine_recast.conform import _raw_file
from cine_recast.errors import CineRecastError
from cine_recast.ffmpeg import ToolNotFound, extract_still, probe
from cine_recast.jsonio import read_project_json, write_json
from cine_recast.lifecycle import IssueRefused  # noqa: F401  (callers catch extras.IssueRefused)
from cine_recast.lifecycle import (SENT, fail, finish, highest, is_text, issue_in_order, quote, reconcile,
                                   require_issued, require_result, resolve, stale_quote)
from cine_recast.paths import ProjectPaths, inside_folder, same_bytes, sha256_of
from cine_recast.project import load_project
from cine_recast.textrules import han_chars, is_english_prompt

TOOL = "generate_video"
DIRECTIONS = ("backward", "forward")
ROLES = ("video", "start_image", "end_image", "image")
IMAGE_ROLES = ("start_image", "end_image", "image")
VIDEO_SUFFIXES = (".mp4", ".mov", ".mkv", ".webm")
_EXTRA_ID = re.compile(r"^x(\d+)$")
_TAKE = re.compile(r"^(u\d+)/(t\d{3,})$")
_FRAME = re.compile(r"^(u\d+)/(t\d{3,}):(first|last|\d+)$")
_NOUN, _QUOTE_COMMAND = "extra", "extra set-cost"


# --- extras.json ---------------------------------------------------------------------------

def _load(root: Path) -> dict:
    """extras.json with every extra's state reconciled from the ledger; no extras when it is missing."""
    pp = ProjectPaths(Path(root))
    if not pp.extras_file.exists():
        return {"extras": []}
    data = read_project_json(pp.root, pp.extras_file, {"extras": list})
    reconcile(root, data["extras"])
    return data


def _save(root: Path, data: dict) -> None:
    write_json(ProjectPaths(Path(root)).extras_file, data)


def _find(data: dict, extra_id: str) -> dict:
    for item in data["extras"]:
        if isinstance(item, dict) and item.get("id") == extra_id:
            return item
    raise CineRecastError(f"unknown extra: {extra_id}")


def _number(item: dict) -> tuple[int, str]:
    xid = str(item.get("id", ""))
    match = _EXTRA_ID.match(xid)
    return (int(match.group(1)) if match else -1, xid)


def _next_number(root: Path, data: dict) -> int:
    """Above every extra id in the ledger and in extras.json (other job ids are not counted)."""
    ids = [i.get("id") for i in data["extras"] if isinstance(i, dict)]
    return highest(_EXTRA_ID, [*ledger.job_states(root), *ids]) + 1


def load_items(root: Path) -> list[dict]:
    """The items of extras.json (reconciled from the ledger) that are objects, in id order."""
    return sorted((item for item in _load(root)["extras"] if isinstance(item, dict)), key=_number)


# --- payloads and checks -------------------------------------------------------------------

def _inputs(item: dict) -> list[dict]:
    inputs = item.get("inputs")
    return [entry for entry in inputs if isinstance(entry, dict)] if isinstance(inputs, list) else []


def _payload(root: Path, item: dict) -> dict:
    """The exact call for one extra: tool, parameters and absolute input paths."""
    params = item.get("params") if isinstance(item.get("params"), dict) else {}
    return {"job_id": item.get("id"), "kind": item.get("kind"), "tool": TOOL, "params": dict(params),
            "medias": [{"role": entry.get("role"), "path": str(resolve(root, entry["path"]))}
                       for entry in _inputs(item) if is_text(entry.get("path"))]}


def _priced(root: Path, item: dict) -> dict:
    """What a quote is bound to: the params and every input as (role, path and content hash)."""
    medias = []
    for entry in _inputs(item):
        path = resolve(root, entry["path"]) if is_text(entry.get("path")) else None
        content = sha256_of(path) if path is not None and path.is_file() else "missing"
        medias.append({"role": entry.get("role"), "path": f"{entry.get('path')}#{content}"})
    params = item.get("params") if isinstance(item.get("params"), dict) else {}
    return {"job_id": item.get("id"), "params": dict(params), "medias": medias}


def _cast_problems(root: Path, label: str) -> list[str]:
    """A take shows the swapped people, so an extra needs the swap table and the rights to pass."""
    problems = casting.check(root)
    if not problems:
        return []
    return [f"{label}: casting is not ready (a take shows the swapped people, so the same rights answers apply): "
            + "; ".join(problems)]


def _problems(root: Path, item: dict, models: dict[str, dict]) -> list[str]:
    """What keeps an extra from being quoted or issued, as English sentences."""
    label = f"extra {item['id']}" if is_text(item.get("id")) else "extra"
    problems: list[str] = []
    name = item.get("label")
    if not is_text(name):
        problems.append(f"{label}: label must be a non-empty text.")
    elif han_chars(name):
        problems.append(f"{label}: label must not contain Han characters ({''.join(han_chars(name))}).")
    params = item.get("params") if isinstance(item.get("params"), dict) else {}
    prompt = params.get("prompt")
    if not isinstance(prompt, str) or not is_english_prompt(prompt):
        problems.append(f"{label}: prompt must be a non-empty English text (no Han, Hangul or Kana characters).")
    model = params.get("model")
    spec = models.get(model) if isinstance(model, str) else None
    if spec is None:
        problems.append(f"{label}: model {model!r} is not in models.json.")
    elif spec["kind"] != "video":
        problems.append(f"{label}: model {model!r} has kind {spec['kind']!r}; extras need a video model.")
        spec = None
    duration = params.get("duration")
    if isinstance(duration, bool) or not isinstance(duration, int):
        problems.append(f"{label}: seconds must be a whole number, not {duration!r}.")
    elif spec is not None and not spec["min_seconds"] <= duration <= spec["max_seconds"]:
        problems.append(f"{label}: seconds {duration} is outside the limits of model {model!r} "
                        f"({spec['min_seconds']} to {spec['max_seconds']} s).")
    if spec is not None:
        if params.get("aspect_ratio") not in spec["aspect_ratios"]:
            problems.append(f"{label}: aspect_ratio {params.get('aspect_ratio')!r} is not offered by model "
                            f"{model!r} ({', '.join(spec['aspect_ratios'])}).")
        if params.get("resolution") not in spec["resolutions"]:
            problems.append(f"{label}: resolution {params.get('resolution')!r} is not offered by model "
                            f"{model!r} ({', '.join(spec['resolutions'])}).")
    if item.get("kind") == "extend":
        if params.get("direction") not in DIRECTIONS:
            problems.append(f"{label}: direction must be backward or forward, not {params.get('direction')!r}.")
    elif item.get("kind") != "shot" or "direction" in params:
        problems.append(f"{label}: kind must be 'extend' (with a direction) or 'shot' (without one).")
    inputs = item.get("inputs")
    if not isinstance(inputs, list) or len(_inputs(item)) != len(inputs):
        problems.append(f"{label}: inputs must be a list of {{role, path}} objects.")
    for entry in _inputs(item):
        if entry.get("role") not in ROLES or not is_text(entry.get("path")):
            problems.append(f"{label}: every input needs a role among {', '.join(ROLES)} and a path.")
        elif not resolve(root, entry["path"]).is_file():
            problems.append(f"{label}: input file not found: {entry['path']}")
    videos = sum(1 for entry in _inputs(item) if entry.get("role") == "video")
    images = sum(1 for entry in _inputs(item) if entry.get("role") in IMAGE_ROLES)
    if videos != (1 if item.get("kind") == "extend" else 0):
        problems.append(f"{label}: an extension has exactly one video input (the take) and a shot none.")
    if spec is not None:
        if videos and not spec["video_ref"]:
            problems.append(f"{label}: model {model!r} does not take a video input (video_ref is false), so it "
                            "cannot extend a take.")
        if images > spec["image_refs"]:
            problems.append(f"{label}: {images} input images, but model {model!r} takes at most "
                            f"{spec['image_refs']} (image_refs).")
    if not is_text(item.get("batch")):
        problems.append(f"{label}: batch must not be empty.")
    return problems


# --- inputs given at add -------------------------------------------------------------------

def _plan_units(root: Path) -> set[str]:
    """Unit ids of the current plan and of the archived plans."""
    plans = plan._archived_plans(root)
    if ProjectPaths(Path(root)).plan_file.is_file():
        plans.append(plan.load(root))
    return {u.get("id") for data in plans if isinstance(data.get("units"), list)
            for u in data["units"] if isinstance(u, dict) and isinstance(u.get("id"), str)}


def _take_raw(root: Path, unit_id: str, take_id: str) -> Path:
    if unit_id not in _plan_units(root):
        raise CineRecastError(f"unit {unit_id} is not in the current plan or an archived plan")
    return _raw_file(root, unit_id, take_id)


def _relative(root: Path, path: Path) -> str:
    return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()


def _frame_choice(root: Path, flag: str, text: str) -> tuple[Path, int]:
    """(the take's raw file, the frame index) for --start or --end U/T:FRAME."""
    match = _FRAME.fullmatch(text) if isinstance(text, str) else None
    if match is None:
        raise CineRecastError(f"{flag} must be U/T:FRAME (for example u01/t001:first, u01/t001:last or "
                              f"u01/t001:12), not {text!r}")
    raw = _take_raw(root, match.group(1), match.group(2))
    frames = probe(raw).frames
    choice = match.group(3)
    index = 0 if choice == "first" else frames - 1 if choice == "last" else int(choice)
    if not 0 <= index < frames:
        raise CineRecastError(f"{flag}: frame {index} is outside take {match.group(1)}/{match.group(2)} "
                              f"(frames 0 to {frames - 1})")
    return raw, index


def _ref_path(root: Path, ref) -> str:
    """A reference image from the project's assets/ or swap/ folder, as a project-relative path."""
    pp = ProjectPaths(Path(root))
    path = resolve(root, str(ref))
    if inside_folder(path, [pp.assets_dir, pp.root / "swap"]) is None:
        raise CineRecastError(f"--ref {ref}: reference images must be generated asset files under assets/ or images "
                              "under swap/ in the project folder")
    if path.suffix.lower() not in IMAGE_SUFFIXES or not path.is_file():
        raise CineRecastError(f"--ref {ref}: must be an existing .jpg, .jpeg, .png or .webp file")
    return _relative(root, path)


# --- the life cycle ------------------------------------------------------------------------

def add(root: Path, *, label: str, prompt: str, model: str, seconds: int, batch: str, extend: str | None = None,
        direction: str | None = None, start: str | None = None, end: str | None = None, refs=(),
        quality: str = "draft", aspect_ratio: str | None = None) -> dict:
    """Plan an extra shot and return it. The frames of --start and --end are written as
    extras/<id>/start.png and end.png. Nothing is written when a check refuses."""
    root = Path(root)
    pp = ProjectPaths(root)
    load_project(root)
    if extend is not None and (start is not None or end is not None):
        raise CineRecastError("--extend cannot be combined with --start or --end: an extension continues the take itself")
    if extend is not None and direction is None:
        raise CineRecastError("--extend needs --direction backward (before the take's first frame) or forward "
                              "(after its last frame)")
    if extend is None and direction is not None:
        raise CineRecastError("--direction belongs to --extend; give the take to extend with --extend U/T")
    if direction is not None and direction not in DIRECTIONS:
        raise CineRecastError(f"direction must be backward or forward, not {direction!r}")
    if quality not in plan.QUALITIES:
        raise CineRecastError(f"unknown quality {quality!r}; expected one of {', '.join(plan.QUALITIES)}")
    if isinstance(seconds, float) and seconds.is_integer():
        seconds = int(seconds)
    problems = _cast_problems(root, "extra")
    if problems:
        raise CineRecastError("; ".join(problems))
    models = plan.valid_models(root)
    spec = models.get(model)
    inputs: list[dict] = []
    source: dict = {}
    if extend is not None:
        match = _TAKE.fullmatch(extend) if isinstance(extend, str) else None
        if match is None:
            raise CineRecastError(f"--extend must be U/T (for example u01/t001), not {extend!r}")
        inputs.append({"role": "video", "path": _relative(root, _take_raw(root, match.group(1), match.group(2)))})
        source = {"extend": extend, "direction": direction}
    frames = {}
    for flag, name, value in (("--start", "start", start), ("--end", "end", end)):
        if value is not None:
            frames[name] = _frame_choice(root, flag, value)
            source[name] = value
    ref_paths = [_ref_path(root, ref) for ref in refs]
    params: dict = {"model": model, "prompt": prompt, "duration": seconds}
    if spec is not None and spec["kind"] == "video":
        params["aspect_ratio"] = aspect_ratio or plan.closest_aspect(spec, load_video(root)[1].aspect)
        params["resolution"] = plan._resolution(spec, quality)
    else:
        params["aspect_ratio"], params["resolution"] = aspect_ratio, None
    if direction is not None:
        params["direction"] = direction
    data = _load(root)
    xid = f"x{_next_number(root, data):04d}"
    folder = pp.extras_dir / xid
    for name in frames:
        inputs.append({"role": f"{name}_image", "path": f"extras/{xid}/{name}.png"})
    inputs += [{"role": "image", "path": path} for path in ref_paths]
    item = {"id": xid, "label": label, "kind": "extend" if extend is not None else "shot", "batch": batch,
            "state": "planned", "est_credits": None, "quote_digest": None, "params": params, "inputs": inputs,
            "source": source, "result": None}
    problems = [p for p in _problems(root, item, models) if "input file not found: extras/" not in p]
    if problems:
        raise CineRecastError("; ".join(problems))
    try:
        for name, (raw, index) in frames.items():
            extract_still(raw, index, folder / f"{name}.png")
        data["extras"].append(item)
        _save(root, data)
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    return item


def list_extras(root: Path) -> list[dict]:
    """Every extra with its state, batch, estimate, source and result, and the params and inputs it
    is sent with (the params it was sent with once issued)."""
    root = Path(root)
    found = []
    for item in load_items(root):
        payload = _payload(root, item)
        found.append({"id": item.get("id"), "label": item.get("label"), "kind": item.get("kind"),
                      "batch": item.get("batch"), "state": item.get("state"), "est_credits": item.get("est_credits"),
                      "params": item.get("sent_params") if item.get("state") in SENT else payload["params"],
                      "medias": payload["medias"], "source": item.get("source"), "result": item.get("result")})
    return found


def set_cost(root: Path, extra_id: str, credits: float) -> dict:
    """Record the quoted cost of a planned or quoted extra and bind it to its params and the content
    of its input files. The extra becomes quoted; a new quote replaces the old one until it is issued."""
    root = Path(root)
    data = _load(root)
    item = _find(data, extra_id)

    def checked_payload() -> dict:
        problems = _cast_problems(root, f"extra {extra_id}") or _problems(root, item, plan.valid_models(root))
        if problems:
            raise CineRecastError("; ".join(problems))
        return _priced(root, item)

    quote(item, credits, checked_payload, noun=_NOUN)
    _save(root, data)
    return item


def issue_next(root: Path, batch: str) -> list[dict]:
    """Hand the batch's quoted extras to the ledger in id order and return what to run for each.

    Every queued extra must pass the checks of add again (casting included) and still have the
    params and input files its quote priced, or nothing is issued. Stops with IssueRefused at the
    first extra the ledger refuses; the extras issued before it stay issued and travel on the
    exception.
    """
    root = Path(root)
    data = _load(root)
    queue = sorted((i for i in data["extras"]
                    if isinstance(i, dict) and i.get("batch") == batch and i.get("state") == "quoted"), key=_number)
    if queue:
        problems = _cast_problems(root, "extra")
        if problems:
            raise CineRecastError("no extra issued: " + "; ".join(problems))
        models = plan.valid_models(root)
        for item in queue:
            problems += _problems(root, item, models)
            stale = stale_quote(item, _priced(root, item), noun=_NOUN, command=_QUOTE_COMMAND)
            if stale:
                problems.append(stale)
        if problems:
            raise CineRecastError("extra check failed, no extra issued: " + "; ".join(problems))
    runs = {item["id"]: _payload(root, item) for item in queue}
    try:
        issued = issue_in_order(root, batch, queue, lambda item: _priced(root, item), lambda: _save(root, data),
                                noun=_NOUN, command=_QUOTE_COMMAND)
    except IssueRefused as exc:
        raise IssueRefused(exc.message, [runs[p["job_id"]] for p in exc.issued]) from exc
    return [runs[p["job_id"]] for p in issued]


def _check_result_file(root: Path, extra_id: str, source: Path) -> None:
    """Refuse a result file that is not a downloaded generation result with a readable video."""
    pp = ProjectPaths(root)
    if source.suffix.lower() not in VIDEO_SUFFIXES:
        raise CineRecastError(f"extra {extra_id}: result file {source} must be a .mp4, .mov, .mkv or .webm file")
    if not source.is_file():
        raise CineRecastError(f"extra {extra_id}: result file not found: {source}")
    folder = inside_folder(source, [*pp.material_folders(), pp.sounds_dir, pp.extras_dir])
    if folder is not None:
        raise CineRecastError(f"extra {extra_id}: {source} is project material (inside the project's {folder.name} "
                              "folder), not a generation result; register only files downloaded from the "
                              "generation job")
    if same_bytes(source, root / load_project(root)["reference"]):
        raise CineRecastError(f"extra {extra_id}: {source} is the reference video (its bytes equal the project's "
                              "reference copy), not a generation result")
    try:
        probe(source)
    except ToolNotFound:
        raise
    except CineRecastError as exc:
        raise CineRecastError(f"extra {extra_id}: result file {source} is not a readable video ({exc.message})") from exc


def record_done(root: Path, extra_id: str, provider_id: str, credits: float, file: Path) -> dict:
    """Store the returned file as extras/<id>/take<suffix>, record the extra as done in the ledger,
    then save. Returns {"extra", "result"} with the stored file relative to the project.

    Guards as for jobs done (a readable video, not project material, not the reference). The file
    of an extra the ledger records as done is never replaced; leftovers of an interrupted call are
    removed; the file is listed before the ledger records done and taken back if it refuses.
    """
    root = Path(root)
    pp = ProjectPaths(root)
    data = _load(root)
    item = require_issued(_find(data, extra_id), noun=_NOUN)
    if not _EXTRA_ID.fullmatch(extra_id):
        raise CineRecastError(f"extra {extra_id}: its id must be 'x' followed by digits to name its folder")
    require_result(item, provider_id, credits, noun=_NOUN)
    source = Path(file)
    _check_result_file(root, extra_id, source)
    if ledger.job_states(root).get(extra_id) == "done":
        raise CineRecastError(f"extra {extra_id} is done in the ledger; its stored file is never replaced")
    folder = pp.extras_dir / extra_id
    for leftover in (folder.glob("take.*") if folder.is_dir() else []):   # an interrupted call stored it
        leftover.unlink()
    previous = item.get("result")
    target = folder / f"take{source.suffix.lower()}"
    folder.mkdir(parents=True, exist_ok=True)
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
    item["result"] = name
    try:
        _save(root, data)  # listed before the ledger records done, so a crash after that strands no paid file
    except BaseException:
        undo()
        raise

    def take_back() -> None:
        undo()
        item["result"] = previous if isinstance(previous, str) and (root / previous).is_file() else None
        _save(root, data)

    finish(root, item, provider_id, credits, take_back, lambda: _save(root, data))
    return {"extra": extra_id, "result": name}


def record_fail(root: Path, extra_id: str, reason: str, credits: float = 0.0) -> dict:
    """Record an issued extra as failed in the ledger (credits spent, if any) and return it."""
    root = Path(root)
    data = _load(root)
    return fail(root, _find(data, extra_id), reason, credits, lambda: _save(root, data), noun=_NOUN)
