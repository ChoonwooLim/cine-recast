"""Generation units, jobs, and the hand-over of jobs to the agent.

A unit is a run of consecutive shots generated in one call. Every unit has jobs; a job moves
planned -> quoted (set_cost) -> issued (issue_next) -> done or failed (record_done, record_fail).
A job reaches the agent only through issue_next, which hands it to ledger.issue first and stops
at the first refusal.

- The ledger is the authority for job state: every load moves a job forward to the state the
  ledger records for it.
- A quote is bound to what it priced: set_cost stores a digest of the job's payload (params
  without the prompt, and the medias), and a job whose payload changed must be quoted again.
- The resolution sent follows the job's quality.
- Unit and job ids are never reused within a project: replacing a plan archives it under plans/.
- Model limits come only from the project's models.json.

The life cycle itself (ledger reconciliation, quote binding, the ordered issue) is shared with the
asset jobs of assets.py and lives in lifecycle.py.
"""

import math
import re
import shutil
from fractions import Fraction
from pathlib import Path

from cine_recast import casting, ledger
from cine_recast.analyze import load_shots, load_video
from cine_recast.errors import CineRecastError
from cine_recast.ffmpeg import cut_frames
from cine_recast.jsonio import read_json, write_json
from cine_recast.lifecycle import IssueRefused  # noqa: F401  (callers catch plan.IssueRefused)
from cine_recast.lifecycle import (SENT, UNISSUED, fail, finish, highest, is_number, is_text, issue_in_order, now,
                                   quote, reconcile, require_issued, require_result, resolve, stale_quote)
from cine_recast.paths import ProjectPaths, inside_folder
from cine_recast.project import load_project
from cine_recast.textrules import is_english_prompt

METHODS = ("edit", "regen")
QUALITIES = ("draft", "final")
MODEL_KINDS = ("video", "image")
ROLES = ("video", "image")
TOOL = "generate_video"
_UNIT_ID = re.compile(r"^u(\d+)$")
_JOB_ID = re.compile(r"^j(\d+)$")
_ARCHIVE = re.compile(r"^plan-(\d+)\.json$")


def _exact(value: float) -> Fraction:
    """The decimal value as written (2.5 -> 5/2), so float noise never moves a limit."""
    return Fraction(str(value))


def _whole(value: float) -> float:
    return int(value) if isinstance(value, float) and value.is_integer() else value


# --- models.json -------------------------------------------------------------------------

def _read_models(root: Path) -> tuple[list, list[str]]:
    path = ProjectPaths(Path(root)).models_file
    if not path.is_file():
        return [], [f"models.json not found: {path}; write it from the provider's model list"]
    try:
        data = read_json(path)
    except ValueError as exc:
        return [], [f"models.json is not valid JSON: {exc}"]
    models = data.get("models") if isinstance(data, dict) else None
    if not isinstance(models, list) or not models:
        return [], ["models.json must hold a non-empty list 'models'."]
    return models, []


def check_models(root: Path) -> list[str]:
    """Problems found in models.json as English sentences. An empty list means it is valid.

    An image model (kind "image") needs no min_seconds, max_seconds or video_ref, and its
    resolutions may be an empty list.
    """
    models, problems = _read_models(root)
    seen: set[str] = set()
    for position, model in enumerate(models):
        if not isinstance(model, dict):
            problems.append(f"models[{position}] must be an object.")
            continue
        mid = model.get("id")
        label = f"model {mid!r}" if is_text(mid) else f"models[{position}]"
        if not is_text(mid):
            problems.append(f"{label}: id must be a non-empty string.")
        elif mid in seen:
            problems.append(f"{label}: duplicate model id.")
        else:
            seen.add(mid)
        if model.get("kind") not in MODEL_KINDS:
            problems.append(f"{label}: kind must be one of {', '.join(MODEL_KINDS)}.")
        image = model.get("kind") == "image"
        if not image:
            low, high = model.get("min_seconds"), model.get("max_seconds")
            for key, value in (("min_seconds", low), ("max_seconds", high)):
                if not is_number(value) or value <= 0:
                    problems.append(f"{label}: {key} must be a number greater than 0.")
            if is_number(low) and is_number(high) and low > high:
                problems.append(f"{label}: min_seconds ({low}) must not be longer than max_seconds ({high}).")
        for key, may_be_empty in (("aspect_ratios", False), ("resolutions", image)):
            value = model.get(key)
            if not isinstance(value, list) or not (value or may_be_empty) or not all(is_text(v) for v in value):
                problems.append(f"{label}: {key} must be a {'' if may_be_empty else 'non-empty '}list of strings.")
        if not image and not isinstance(model.get("video_ref"), bool):
            problems.append(f"{label}: video_ref must be true or false.")
        refs = model.get("image_refs")
        if isinstance(refs, bool) or not isinstance(refs, int) or refs < 0:
            problems.append(f"{label}: image_refs must be a whole number of at least 0.")
        if not is_text(model.get("read_at")):
            problems.append(f"{label}: read_at must give the date the model list was read.")
    return problems


def _models_by_id(root: Path) -> dict[str, dict]:
    models, _ = _read_models(root)
    return {m["id"]: m for m in models if isinstance(m, dict) and is_text(m.get("id"))}


def valid_models(root: Path) -> dict[str, dict]:
    problems = check_models(root)
    if problems:
        raise CineRecastError("models.json is not valid: " + "; ".join(problems))
    return _models_by_id(root)


def _resolution(spec: dict, quality: str) -> str:
    """draft uses the model's first resolution, final its last."""
    return spec["resolutions"][0] if quality == "draft" else spec["resolutions"][-1]


def _ratio(text: str) -> float | None:
    parts = text.split(":")
    if len(parts) != 2:
        return None
    try:
        w, h = float(parts[0]), float(parts[1])
    except ValueError:
        return None
    if not (math.isfinite(w) and math.isfinite(h)) or w <= 0 or h <= 0:
        return None
    return w / h


def closest_aspect(spec: dict, target: float) -> str:
    """The model's W:H aspect ratio closest to target (by ratio, first wins a tie)."""
    best: tuple[float, str] | None = None
    for text in spec["aspect_ratios"]:
        ratio = _ratio(text)
        if ratio is None:
            continue
        distance = abs(math.log(ratio / target))
        if best is None or distance < best[0]:
            best = (distance, text)
    if best is None:
        raise CineRecastError(f"model {spec['id']!r} lists no aspect ratio in W:H form: {spec['aspect_ratios']}")
    return best[1]


# --- plan.json -----------------------------------------------------------------------------

def load(root: Path) -> dict:
    """plan.json with every job's state reconciled from the ledger (written on the next save)."""
    pp = ProjectPaths(Path(root))
    if not pp.plan_file.is_file():
        raise CineRecastError(f"plan.json not found: {pp.plan_file}; run plan build first")
    try:
        data = read_json(pp.plan_file)
    except ValueError as exc:
        raise CineRecastError(f"plan.json is not valid JSON: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("units"), list) or not isinstance(data.get("jobs"), list):
        raise CineRecastError("plan.json must hold the lists 'units' and 'jobs'")
    reconcile(root, data["jobs"])
    return data


def _save(root: Path, data: dict) -> None:
    write_json(ProjectPaths(Path(root)).plan_file, data)


def _find_unit(data: dict, unit_id: str) -> dict:
    for item in data["units"]:
        if isinstance(item, dict) and item.get("id") == unit_id:
            return item
    raise CineRecastError(f"unknown unit: {unit_id}")


def _find_job(data: dict, job_id: str) -> dict:
    for job in data["jobs"]:
        if isinstance(job, dict) and job.get("id") == job_id:
            return job
    raise CineRecastError(f"unknown job: {job_id}")


def _job_number(job: dict) -> tuple[int, str]:
    jid = str(job.get("id", ""))
    match = _JOB_ID.match(jid)
    return (int(match.group(1)) if match else -1, jid)


def _new_job(job_id: str, unit_id: str, batch: str, quality: str) -> dict:
    return {"id": job_id, "unit": unit_id, "batch": batch, "quality": quality,
            "state": "planned", "est_credits": None}


def unit(root: Path, unit_id: str) -> dict:
    return _find_unit(load(root), unit_id)


# --- ids that are never reused ---------------------------------------------------------------

def _archives(root: Path) -> list[tuple[int, Path]]:
    """Archived plans plans/plan-NNNN.json, by number."""
    folder = Path(root) / "plans"
    if not folder.is_dir():
        return []
    found = [(int(m.group(1)), p) for p in folder.iterdir() if p.is_file() and (m := _ARCHIVE.match(p.name))]
    return sorted(found)


def _archived_plans(root: Path) -> list[dict]:
    plans = []
    for _, path in _archives(root):
        try:
            data = read_json(path)
        except ValueError as exc:
            raise CineRecastError(f"archived plan is not valid JSON: {path}: {exc}") from exc
        plans.append(data if isinstance(data, dict) else {})
    return plans


def _ids(plans: list[dict], key: str) -> list:
    return [entry.get("id") for plan in plans if isinstance(plan.get(key), list)
            for entry in plan[key] if isinstance(entry, dict)]


def _next_job_number(root: Path, current: dict | None) -> int:
    """Above every job id in the ledger, the current plan and the archived plans."""
    plans = ([current] if current else []) + _archived_plans(root)
    return highest(_JOB_ID, [*ledger.job_states(root), *_ids(plans, "jobs")]) + 1


def _next_unit_number(root: Path, current: dict | None) -> int:
    """Above every unit id in the current and archived plans and under takes/ and analysis/units/."""
    pp = ProjectPaths(Path(root))
    plans = ([current] if current else []) + _archived_plans(root)
    folders = [name for base in (pp.root / "takes", pp.analysis_dir / "units") if base.is_dir()
               for name in (p.name for p in base.iterdir() if p.is_dir())]
    return highest(_UNIT_ID, [*_ids(plans, "units"), *folders]) + 1


def _archive(root: Path, data: dict) -> Path:
    number = max((n for n, _ in _archives(root)), default=0) + 1
    path = Path(root) / "plans" / f"plan-{number:04d}.json"
    while path.exists():
        number += 1
        path = Path(root) / "plans" / f"plan-{number:04d}.json"
    write_json(path, data)
    return path


# --- build ---------------------------------------------------------------------------------

def _unit_inputs(root: Path, unit_id: str, method: str, shots: list[int]) -> list[dict]:
    """The inputs a unit is sent with, derived from the project as it is now.

    First the lead input by method (edit: the unit's reference clip, role "video"; regen: the first
    still of the unit's first shot, role "image"), then the swap images of the elements that appear
    in its shots, in element order (casting.swaps_for_shots). plan build stores this list in the
    unit, and check refuses a unit whose stored inputs differ from it.
    """
    pp = ProjectPaths(Path(root))
    if method == "edit":
        lead = {"role": "video", "path": (pp.unit_dir(unit_id) / "ref.mp4").relative_to(pp.root).as_posix()}
    else:
        still = pp.shot_dir(shots[0]) / "stills" / "first.png"
        lead = {"role": "image", "path": still.relative_to(pp.root).as_posix()}
    inputs = [lead]
    for swap in casting.swaps_for_shots(root, shots):
        inputs += [{"role": "image", "path": image, "element": swap["element"]} for image in swap["images"]]
    return inputs


def build(root: Path, *, model: str, method: str, batch: str, quality: str = "draft",
          max_unit_seconds: float | None = None, replace: bool = False) -> dict:
    """Pack the shots into units, write each unit's reference clip and plan.json with one
    planned job per unit. Returns the plan. Nothing is written when a check refuses.

    With replace, the current plan is archived under plans/ and new unit and job ids follow every
    id used before; refused while a job of the current plan is issued and not finished.
    """
    root = Path(root)
    pp = ProjectPaths(root)
    project = load_project(root)
    if method not in METHODS:
        raise CineRecastError(f"unknown method {method!r}; expected one of {', '.join(METHODS)}")
    if quality not in QUALITIES:
        raise CineRecastError(f"unknown quality {quality!r}; expected one of {', '.join(QUALITIES)}")
    if not is_text(batch):
        raise CineRecastError("batch must not be empty")
    if max_unit_seconds is not None and (not is_number(max_unit_seconds) or max_unit_seconds <= 0):
        raise CineRecastError(f"max_unit_seconds must be a number greater than 0: {max_unit_seconds!r}")
    current = None
    if pp.plan_file.exists():
        if not replace:
            raise CineRecastError("plan.json already exists; rebuild it with replace (--replace), which "
                                  "archives the current plan under plans/")
        current = load(root)
        open_jobs = [str(j.get("id")) if isinstance(j, dict) else repr(j) for j in current["jobs"]
                     if not isinstance(j, dict) or j.get("state") not in (*UNISSUED, "done", "failed")]
        if open_jobs:
            raise CineRecastError(f"cannot replace the plan: jobs issued and not finished: {', '.join(open_jobs)}; "
                                  "record them with jobs done or jobs fail first")

    problems = casting.check(root)
    if problems:
        raise CineRecastError("casting is not ready: " + "; ".join(problems))
    spec = valid_models(root).get(model)
    if spec is None:
        raise CineRecastError(f"model {model!r} is not in models.json")
    if spec["kind"] != "video":
        raise CineRecastError(f"model {model!r} has kind {spec['kind']!r}; units need a video model")
    if method == "edit" and not spec["video_ref"]:
        raise CineRecastError(f"model {model!r} does not take a reference video (video_ref is false); "
                              "method 'edit' needs one, use method 'regen'")

    info, box = load_video(root)
    frame_seconds = Fraction(info.fps_den, info.fps_num)
    limit = _exact(spec["max_seconds"])
    if max_unit_seconds is not None:
        limit = min(limit, _exact(max_unit_seconds))
    source = f"model {model!r} max_seconds {spec['max_seconds']}"
    if max_unit_seconds is not None:
        source += f", max_unit_seconds {max_unit_seconds}"
    groups: list[list] = []
    for shot in load_shots(root):
        seconds = shot.frames * frame_seconds
        if seconds > limit:
            raise CineRecastError(f"shot {shot.index} is {float(seconds):.3f} s long, longer than the "
                                  f"unit limit of {float(limit):g} s ({source})")
        if groups and (groups[-1][-1].end - groups[-1][0].start + shot.frames) * frame_seconds <= limit:
            groups[-1].append(shot)
        else:
            groups.append([shot])

    resolution = _resolution(spec, quality)  # the unit's default; each job's quality decides what is sent
    aspect_ratio = closest_aspect(spec, box.aspect)
    first_unit = _next_unit_number(root, current)
    units: list[dict] = []
    for number, group in enumerate(groups, start=first_unit):
        uid = f"u{number:02d}"
        start, end = group[0].start, group[-1].end
        shots = [shot.index for shot in group]
        inputs = _unit_inputs(root, uid, method, shots)   # the edit lead, the unit's reference clip, is written below
        images = sum(1 for item in inputs if item["role"] == "image")
        if images > spec["image_refs"]:
            raise CineRecastError(
                f"unit {uid} (shots {shots}) needs {images} input images but model {model!r} "
                f"takes at most {spec['image_refs']} (image_refs)")
        units.append({
            "id": uid, "shots": shots, "start": start, "end": end, "method": method, "model": model,
            "params": {"duration": _whole(max(spec["min_seconds"], math.ceil((end - start) * frame_seconds))),
                       "aspect_ratio": aspect_ratio, "resolution": resolution},
            "prompt": "", "inputs": inputs, "qc_skip": [], "selected_take": None,
        })
    first_job = _next_job_number(root, current)
    jobs = [_new_job(f"j{n:04d}", item["id"], batch, quality) for n, item in enumerate(units, start=first_job)]

    reference = root / project["reference"]
    for item in units:          # new unit ids, so earlier reference clips are never touched
        cut_frames(reference, item["start"], item["end"], pp.unit_dir(item["id"]) / "ref.mp4", crop=box.as_crop())
    if current is not None:
        _archive(root, current)
    data = {"units": units, "jobs": jobs}
    _save(root, data)
    return data


# --- payloads and quotes -------------------------------------------------------------------

def _payload(root: Path, data: dict, models: dict[str, dict], job: dict) -> dict:
    """The exact call for one job: tool, parameters and absolute input paths.

    The unit gives the model, prompt, duration, aspect ratio and inputs; the job's quality gives
    the resolution. The same payload is shown for quoting and sent at issue.
    """
    jid = job.get("id")
    try:
        item = _find_unit(data, job.get("unit"))
    except CineRecastError as exc:
        raise CineRecastError(f"job {jid} refers to an unknown unit: {job.get('unit')!r}") from exc
    if job.get("quality") not in QUALITIES:
        raise CineRecastError(f"job {jid}: quality must be one of {', '.join(QUALITIES)}, "
                              f"not {job.get('quality')!r}")
    spec = models.get(item.get("model"))
    if spec is None:
        raise CineRecastError(f"job {jid}: model {item.get('model')!r} of unit {item['id']} is not in models.json")
    if spec["kind"] != "video":
        raise CineRecastError(f"job {jid}: model {item.get('model')!r} of unit {item['id']} has kind "
                              f"{spec['kind']!r}; units need a video model")
    params = item.get("params") if isinstance(item.get("params"), dict) else {}
    inputs = item.get("inputs") if isinstance(item.get("inputs"), list) else []
    return {
        "job_id": jid, "unit": item["id"], "tool": TOOL,
        "params": {"model": item["model"], "prompt": item.get("prompt"), "duration": params.get("duration"),
                   "aspect_ratio": params.get("aspect_ratio"), "resolution": _resolution(spec, job["quality"])},
        "medias": [{"role": entry.get("role"), "path": str(resolve(root, entry["path"]))}
                   for entry in inputs if isinstance(entry, dict) and is_text(entry.get("path"))],
    }


# --- check ---------------------------------------------------------------------------------

def _casting_problems(root: Path) -> list[str]:
    """casting.check, with a casting.json that cannot be read reported as a problem instead of raised."""
    try:
        return casting.check(root)
    except CineRecastError as exc:
        return [exc.message]


def check(root: Path) -> list[str]:
    """Problems that keep the plan's jobs from being issued. An empty list means it is ready.

    Each unit's stored inputs must be the inputs _unit_inputs derives from the swap table and the
    method now, and fit the model's image_refs and video_ref as models.json gives them now.
    """
    root = Path(root)
    data = load(root)
    problems = check_models(root)
    models = {} if problems else _models_by_id(root)
    cast_problems = _casting_problems(root)
    if cast_problems:
        problems.append("casting is not ready, so the unit inputs cannot be compared with the swap table: "
                        + "; ".join(cast_problems))
    for position, item in enumerate(data["units"]):
        if not isinstance(item, dict):
            problems.append(f"units[{position}] must be an object.")
            continue
        label = f"unit {item.get('id')}" if is_text(item.get("id")) else f"units[{position}]"
        prompt = item.get("prompt")
        if not isinstance(prompt, str) or not is_english_prompt(prompt):
            problems.append(f"{label}: prompt must be a non-empty English text (no Han, Hangul or Kana characters).")
        method, shots = item.get("method"), item.get("shots")
        if method not in METHODS:
            problems.append(f"{label}: method must be one of {', '.join(METHODS)}, not {method!r}.")
        inputs = item.get("inputs")
        if not isinstance(inputs, list) or not inputs:
            problems.append(f"{label}: inputs must be a non-empty list.")
        else:
            for entry in inputs:
                path = entry.get("path") if isinstance(entry, dict) else None
                if not is_text(path):
                    problems.append(f"{label}: every input needs a role and a path.")
                    continue
                if entry.get("role") not in ROLES:
                    problems.append(f"{label}: input {path} needs the role 'video' or 'image', "
                                    f"not {entry.get('role')!r}.")
                if not resolve(root, path).is_file():
                    problems.append(f"{label}: input file not found: {path}")
            derivable = (not cast_problems and method in METHODS and is_text(item.get("id"))
                         and isinstance(shots, list) and bool(shots)
                         and all(isinstance(s, int) and not isinstance(s, bool) for s in shots))
            if derivable:
                expected = _unit_inputs(root, item["id"], method, shots)
                if inputs != expected:
                    problems.append(f"{label}: its inputs are not the inputs the swap table and method {method!r} "
                                    f"give now ({', '.join(e['path'] for e in expected)}); rebuild the plan "
                                    "with plan build --replace")
        if not models:
            continue  # models.json problems are already listed
        spec = models.get(item.get("model"))
        if spec is None:
            problems.append(f"{label}: model {item.get('model')!r} is not in models.json.")
            continue
        if spec["kind"] != "video":
            problems.append(f"{label}: model {spec['id']!r} has kind {spec['kind']!r}; units need a video model.")
            continue
        if isinstance(inputs, list):
            images = sum(1 for entry in inputs if isinstance(entry, dict) and entry.get("role") == "image")
            if images > spec["image_refs"]:
                problems.append(f"{label}: {images} input images, but model {spec['id']!r} takes at most "
                                f"{spec['image_refs']} (image_refs).")
        if method == "edit" and not spec["video_ref"]:
            problems.append(f"{label}: method 'edit' sends a reference video, but model {spec['id']!r} has "
                            "video_ref false; rebuild the plan with method 'regen' or another model.")
        params = item.get("params") if isinstance(item.get("params"), dict) else {}
        duration, top = params.get("duration"), math.ceil(spec["max_seconds"])
        if not is_number(duration) or not (spec["min_seconds"] <= duration <= top):
            problems.append(f"{label}: duration {duration!r} is outside the limits of model "
                            f"{spec['id']!r} ({spec['min_seconds']} to {top} s).")
        if params.get("aspect_ratio") not in spec["aspect_ratios"]:
            problems.append(f"{label}: aspect_ratio {params.get('aspect_ratio')!r} is not offered by model "
                            f"{spec['id']!r} ({', '.join(spec['aspect_ratios'])}).")
        if params.get("resolution") not in spec["resolutions"]:
            problems.append(f"{label}: resolution {params.get('resolution')!r} is not offered by model "
                            f"{spec['id']!r} ({', '.join(spec['resolutions'])}).")
    for position, job in enumerate(data["jobs"]):
        if not isinstance(job, dict):
            problems.append(f"jobs[{position}] must be an object.")
            continue
        if not models:
            continue
        try:
            payload = _payload(root, data, models, job)
        except CineRecastError as exc:
            problems.append(exc.message)
            continue
        if job.get("state") == "quoted":
            stale = stale_quote(job, payload, noun="job", command="jobs set-cost")
            if stale:
                problems.append(stale)
    return problems


# --- jobs ----------------------------------------------------------------------------------

def add_job(root: Path, unit_id: str, batch: str, quality: str) -> dict:
    """Add a planned job for a unit (a retry or another quality) and return it."""
    root = Path(root)
    data = load(root)
    _find_unit(data, unit_id)
    if quality not in QUALITIES:
        raise CineRecastError(f"unknown quality {quality!r}; expected one of {', '.join(QUALITIES)}")
    if not is_text(batch):
        raise CineRecastError("batch must not be empty")
    job = _new_job(f"j{_next_job_number(root, data):04d}", unit_id, batch, quality)
    data["jobs"].append(job)
    _save(root, data)
    return job


def set_cost(root: Path, job_id: str, credits: float) -> dict:
    """Record the quoted cost of a planned or quoted job and bind it to the job's current payload.

    The job becomes quoted. A new quote replaces the estimate and the binding at any time before
    the job is issued.
    """
    root = Path(root)
    data = load(root)
    job = _find_job(data, job_id)
    quote(job, credits, lambda: _payload(root, data, valid_models(root), job), noun="job")
    _save(root, data)
    return job


def list_jobs(root: Path) -> list[dict]:
    """Every job with its state, batch, quality and estimate, and its params: the params it was
    sent with once issued, the params it would be sent with before that."""
    root = Path(root)
    data = load(root)
    models = valid_models(root)
    return [{"id": job.get("id"), "unit": job.get("unit"), "batch": job.get("batch"), "quality": job.get("quality"),
             "state": job.get("state"), "est_credits": job.get("est_credits"),
             "params": job.get("sent_params") if job.get("state") in SENT
             else _payload(root, data, models, job)["params"]}
            for job in data["jobs"] if isinstance(job, dict)]


def issue_next(root: Path, batch: str) -> list[dict]:
    """Hand the batch's quoted jobs to the ledger in job order and return what to run for each.

    Requires a clean casting check (rights are read again at the moment of spending) and a clean
    plan check. Each job must still have the payload its quote priced. Stops with IssueRefused at
    the first job refused; the jobs issued before it stay issued and travel on the exception.
    """
    root = Path(root)
    problems = casting.check(root)
    if problems:
        raise CineRecastError("casting is not ready, no job issued: " + "; ".join(problems))
    problems = check(root)
    if problems:
        raise CineRecastError("plan check failed, no job issued: " + "; ".join(problems))
    data = load(root)
    models = valid_models(root)
    queue = sorted((j for j in data["jobs"]
                    if isinstance(j, dict) and j.get("batch") == batch and j.get("state") == "quoted"),
                   key=_job_number)
    return issue_in_order(root, batch, queue, lambda job: _payload(root, data, models, job),
                          lambda: _save(root, data), noun="job", command="jobs set-cost")


def _job_has_take(root: Path, unit_id: str, job_id: str) -> bool:
    from cine_recast.takes import list_takes  # takes imports this module

    pp = ProjectPaths(Path(root))
    for take_id in list_takes(root, unit_id):
        try:
            if read_json(pp.take_dir(unit_id, take_id) / "meta.json").get("job") == job_id:
                return True
        except (OSError, ValueError, AttributeError):
            continue
    return False


def record_done(root: Path, job_id: str, provider_id: str, credits: float, file: Path) -> dict:
    """Store the returned file as a new take, record the job as done in the ledger, then save.

    A file inside the project's reference/, analysis/, takes/, out/ or assets/ folder is refused: it
    is project material, not a generation result. If the ledger refuses, the take folder made by
    this call is removed, so a refused call leaves nothing behind.
    """
    from cine_recast.takes import new_take  # takes imports this module

    root = Path(root)
    data = load(root)
    job = _find_job(data, job_id)
    unit_id = job.get("unit")
    in_plan = any(isinstance(u, dict) and u.get("id") == unit_id for u in data["units"])
    if ledger.job_states(root).get(job_id) == "done" and in_plan and not _job_has_take(root, unit_id, job_id):
        raise CineRecastError(
            f"job {job_id} is already done in the ledger but no take was stored for it; register the "
            "file again with a new job: jobs add, then jobs set-cost, jobs next and jobs done")
    require_issued(job, noun="job")
    _find_unit(data, unit_id)
    require_result(job, provider_id, credits, noun="job")
    source = Path(file)
    if not source.is_file():
        raise CineRecastError(f"job {job_id}: result file not found: {source}")
    folder = inside_folder(source, ProjectPaths(root).material_folders())
    if folder is not None:
        raise CineRecastError(f"job {job_id}: {source} is project material (inside the project's {folder.name} "
                              "folder), not a generation result; register only files downloaded from the "
                              "generation job")
    take = new_take(root, unit_id, source, {
        "job": job_id, "provider_id": provider_id, "credits": float(credits),
        "quality": job.get("quality"), "recorded_at": now(),
    })
    finish(root, job, provider_id, credits,
           lambda: shutil.rmtree(ProjectPaths(root).take_dir(unit_id, take), ignore_errors=True),
           lambda: _save(root, data))
    return {"unit": unit_id, "take": take}


def record_fail(root: Path, job_id: str, reason: str, credits: float = 0.0) -> dict:
    """Record an issued job as failed in the ledger (credits spent, if any) and return the job."""
    root = Path(root)
    data = load(root)
    return fail(root, _find_job(data, job_id), reason, credits, lambda: _save(root, data), noun="job")
