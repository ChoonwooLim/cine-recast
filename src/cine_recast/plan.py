"""Generation units, jobs, and the hand-over of jobs to the agent.

A unit is a run of consecutive shots generated in one call. Every unit has jobs; a job moves
planned -> quoted (set_cost) -> issued (issue_next) -> done or failed (record_done, record_fail).
A job reaches the agent only through issue_next, which hands it to ledger.issue first and stops
at the first refusal. Model limits come only from the project's models.json.
"""

import math
import shutil
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path

from cine_recast import casting, ledger
from cine_recast.analyze import load_shots, load_video
from cine_recast.errors import CineRecastError
from cine_recast.ffmpeg import cut_frames
from cine_recast.jsonio import read_json, write_json
from cine_recast.paths import ProjectPaths
from cine_recast.project import load_project
from cine_recast.textrules import is_english_prompt

METHODS = ("edit", "regen")
QUALITIES = ("draft", "final")
MODEL_KINDS = ("video", "image")
TOOL = "generate_video"
_UNISSUED = ("planned", "quoted")  # states in which a job has not been handed to the ledger


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_text(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_number(value) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def _exact(value: float) -> Fraction:
    """The decimal value as written (2.5 -> 5/2), so float noise never moves a limit."""
    return Fraction(str(value))


def _whole(value: float) -> float:
    return int(value) if isinstance(value, float) and value.is_integer() else value


def _resolve(root: Path, path: str) -> Path:
    """Paths in plan.json are relative to the project folder unless absolute."""
    return (Path(root) / path).resolve()


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
    """Problems found in models.json as English sentences. An empty list means it is valid."""
    models, problems = _read_models(root)
    seen: set[str] = set()
    for position, model in enumerate(models):
        if not isinstance(model, dict):
            problems.append(f"models[{position}] must be an object.")
            continue
        mid = model.get("id")
        label = f"model {mid!r}" if _is_text(mid) else f"models[{position}]"
        if not _is_text(mid):
            problems.append(f"{label}: id must be a non-empty string.")
        elif mid in seen:
            problems.append(f"{label}: duplicate model id.")
        else:
            seen.add(mid)
        if model.get("kind") not in MODEL_KINDS:
            problems.append(f"{label}: kind must be one of {', '.join(MODEL_KINDS)}.")
        low, high = model.get("min_seconds"), model.get("max_seconds")
        for key, value in (("min_seconds", low), ("max_seconds", high)):
            if not _is_number(value) or value <= 0:
                problems.append(f"{label}: {key} must be a number greater than 0.")
        if _is_number(low) and _is_number(high) and low > high:
            problems.append(f"{label}: min_seconds ({low}) must not be longer than max_seconds ({high}).")
        for key in ("aspect_ratios", "resolutions"):
            value = model.get(key)
            if not isinstance(value, list) or not value or not all(_is_text(v) for v in value):
                problems.append(f"{label}: {key} must be a non-empty list of strings.")
        if not isinstance(model.get("video_ref"), bool):
            problems.append(f"{label}: video_ref must be true or false.")
        refs = model.get("image_refs")
        if isinstance(refs, bool) or not isinstance(refs, int) or refs < 0:
            problems.append(f"{label}: image_refs must be a whole number of at least 0.")
        if not _is_text(model.get("read_at")):
            problems.append(f"{label}: read_at must give the date the model list was read.")
    return problems


def _models_by_id(root: Path) -> dict[str, dict]:
    models, _ = _read_models(root)
    return {m["id"]: m for m in models if isinstance(m, dict) and _is_text(m.get("id"))}


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


def _closest_aspect(spec: dict, target: float) -> str:
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
    pp = ProjectPaths(Path(root))
    if not pp.plan_file.is_file():
        raise CineRecastError(f"plan.json not found: {pp.plan_file}; run plan build first")
    try:
        data = read_json(pp.plan_file)
    except ValueError as exc:
        raise CineRecastError(f"plan.json is not valid JSON: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("units"), list) or not isinstance(data.get("jobs"), list):
        raise CineRecastError("plan.json must hold the lists 'units' and 'jobs'")
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
    digits = jid[1:]
    return (int(digits) if jid.startswith("j") and digits.isdigit() else -1, jid)


def _new_job(job_id: str, unit_id: str, batch: str, quality: str) -> dict:
    return {"id": job_id, "unit": unit_id, "batch": batch, "quality": quality,
            "state": "planned", "est_credits": None}


def unit(root: Path, unit_id: str) -> dict:
    return _find_unit(load(root), unit_id)


# --- build ---------------------------------------------------------------------------------

def build(root: Path, *, model: str, method: str, batch: str, quality: str = "draft",
          max_unit_seconds: float | None = None, replace: bool = False) -> dict:
    """Pack the shots into units, write each unit's reference clip and plan.json with one
    planned job per unit. Returns the plan. Nothing is written when a check refuses."""
    root = Path(root)
    pp = ProjectPaths(root)
    project = load_project(root)
    if method not in METHODS:
        raise CineRecastError(f"unknown method {method!r}; expected one of {', '.join(METHODS)}")
    if quality not in QUALITIES:
        raise CineRecastError(f"unknown quality {quality!r}; expected one of {', '.join(QUALITIES)}")
    if not _is_text(batch):
        raise CineRecastError("batch must not be empty")
    if max_unit_seconds is not None and (not _is_number(max_unit_seconds) or max_unit_seconds <= 0):
        raise CineRecastError(f"max_unit_seconds must be a number greater than 0: {max_unit_seconds!r}")
    if pp.plan_file.exists():
        if not replace:
            raise CineRecastError("plan.json already exists; rebuild it with replace (--replace), "
                                  "which is allowed only while no job has been issued")
        issued = [str(j.get("id")) if isinstance(j, dict) else repr(j) for j in load(root)["jobs"]
                  if not isinstance(j, dict) or j.get("state") not in _UNISSUED]
        if issued:
            raise CineRecastError(f"cannot replace the plan: jobs already issued: {', '.join(issued)}")

    problems = casting.check(root)
    if problems:
        raise CineRecastError("casting is not ready: " + "; ".join(problems))
    problems = check_models(root)
    if problems:
        raise CineRecastError("models.json is not valid: " + "; ".join(problems))
    spec = _models_by_id(root).get(model)
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

    resolution = spec["resolutions"][0] if quality == "draft" else spec["resolutions"][-1]
    aspect_ratio = _closest_aspect(spec, box.aspect)
    units: list[dict] = []
    for number, group in enumerate(groups, start=1):
        uid = f"u{number:02d}"
        start, end = group[0].start, group[-1].end
        shots = [shot.index for shot in group]
        if method == "edit":    # the unit's reference clip, written below
            lead = {"role": "video", "path": (pp.unit_dir(uid) / "ref.mp4").relative_to(root).as_posix()}
        else:                   # the first still of the unit's first shot
            still = pp.shot_dir(group[0].index) / "stills" / "first.png"
            lead = {"role": "image", "path": still.relative_to(root).as_posix()}
        inputs = [lead]
        for swap in casting.swaps_for_shots(root, shots):
            inputs += [{"role": "image", "path": image, "element": swap["element"]} for image in swap["images"]]
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

    units_dir = pp.analysis_dir / "units"
    if units_dir.exists():
        shutil.rmtree(units_dir)
    reference = root / project["reference"]
    for item in units:
        cut_frames(reference, item["start"], item["end"], pp.unit_dir(item["id"]) / "ref.mp4", crop=box.as_crop())
    data = {"units": units,
            "jobs": [_new_job(f"j{n:04d}", item["id"], batch, quality) for n, item in enumerate(units, start=1)]}
    _save(root, data)
    return data


# --- check ---------------------------------------------------------------------------------

def check(root: Path) -> list[str]:
    """Problems that keep the plan's jobs from being issued. An empty list means it is ready."""
    root = Path(root)
    data = load(root)
    problems = check_models(root)
    models = {} if problems else _models_by_id(root)
    for position, item in enumerate(data["units"]):
        if not isinstance(item, dict):
            problems.append(f"units[{position}] must be an object.")
            continue
        label = f"unit {item.get('id')}" if _is_text(item.get("id")) else f"units[{position}]"
        prompt = item.get("prompt")
        if not isinstance(prompt, str) or not is_english_prompt(prompt):
            problems.append(f"{label}: prompt must be a non-empty English text (no Han, Hangul or Kana characters).")
        inputs = item.get("inputs")
        if not isinstance(inputs, list) or not inputs:
            problems.append(f"{label}: inputs must be a non-empty list.")
        else:
            for entry in inputs:
                path = entry.get("path") if isinstance(entry, dict) else None
                if not _is_text(path):
                    problems.append(f"{label}: every input needs a role and a path.")
                elif not _resolve(root, path).is_file():
                    problems.append(f"{label}: input file not found: {path}")
        if not models:
            continue  # models.json problems are already listed
        spec = models.get(item.get("model"))
        if spec is None:
            problems.append(f"{label}: model {item.get('model')!r} is not in models.json.")
            continue
        params = item.get("params") if isinstance(item.get("params"), dict) else {}
        duration, top = params.get("duration"), math.ceil(spec["max_seconds"])
        if not _is_number(duration) or not (spec["min_seconds"] <= duration <= top):
            problems.append(f"{label}: duration {duration!r} is outside the limits of model "
                            f"{spec['id']!r} ({spec['min_seconds']} to {top} s).")
        if params.get("aspect_ratio") not in spec["aspect_ratios"]:
            problems.append(f"{label}: aspect_ratio {params.get('aspect_ratio')!r} is not offered by model "
                            f"{spec['id']!r} ({', '.join(spec['aspect_ratios'])}).")
        if params.get("resolution") not in spec["resolutions"]:
            problems.append(f"{label}: resolution {params.get('resolution')!r} is not offered by model "
                            f"{spec['id']!r} ({', '.join(spec['resolutions'])}).")
    return problems


# --- jobs ----------------------------------------------------------------------------------

def add_job(root: Path, unit_id: str, batch: str, quality: str) -> dict:
    """Add a planned job for a unit (a retry or another quality) and return it."""
    data = load(root)
    _find_unit(data, unit_id)
    if quality not in QUALITIES:
        raise CineRecastError(f"unknown quality {quality!r}; expected one of {', '.join(QUALITIES)}")
    if not _is_text(batch):
        raise CineRecastError("batch must not be empty")
    number = max((_job_number(j)[0] for j in data["jobs"] if isinstance(j, dict)), default=0)
    job = _new_job(f"j{max(number, 0) + 1:04d}", unit_id, batch, quality)
    data["jobs"].append(job)
    _save(root, data)
    return job


def set_cost(root: Path, job_id: str, credits: float) -> dict:
    """Record the quoted cost of a planned or quoted job; the job becomes quoted."""
    data = load(root)
    job = _find_job(data, job_id)
    if job.get("state") not in _UNISSUED:
        raise CineRecastError(f"job {job_id} is {job.get('state')}; its quote can no longer change")
    if not _is_number(credits) or credits <= 0:
        raise CineRecastError(f"job {job_id}: credits must be a finite number greater than 0: {credits!r}")
    job["est_credits"] = float(credits)
    job["state"] = "quoted"
    _save(root, data)
    return job


def _execution(root: Path, data: dict, job: dict) -> dict:
    """What the agent needs to run one job: tool, parameters and absolute input paths."""
    try:
        item = _find_unit(data, job.get("unit"))
    except CineRecastError as exc:
        raise CineRecastError(f"job {job.get('id')} refers to an unknown unit: {job.get('unit')!r}") from exc
    params = item["params"]
    return {
        "job_id": job["id"], "unit": item["id"], "tool": TOOL,
        "params": {"model": item["model"], "prompt": item["prompt"], "duration": params["duration"],
                   "aspect_ratio": params["aspect_ratio"], "resolution": params["resolution"]},
        "medias": [{"role": entry.get("role"), "path": str(_resolve(root, entry["path"]))}
                   for entry in item["inputs"]],
    }


def issue_next(root: Path, batch: str) -> list[dict]:
    """Hand the batch's quoted jobs to the ledger in job order and return what to run for each.

    Requires a clean check. Stops with CineRecastError at the first job the ledger refuses;
    the jobs issued before it stay issued.
    """
    root = Path(root)
    problems = check(root)
    if problems:
        raise CineRecastError("plan check failed, no job issued: " + "; ".join(problems))
    data = load(root)
    queue = sorted((j for j in data["jobs"]
                    if isinstance(j, dict) and j.get("batch") == batch and j.get("state") == "quoted"),
                   key=_job_number)
    issued: list[dict] = []
    for job in queue:
        execution = _execution(root, data, job)
        try:
            ledger.issue(root, job["id"], batch, job.get("est_credits"))
        except CineRecastError as exc:
            before = f" (issued before it in this call: {', '.join(e['job_id'] for e in issued)})" if issued else ""
            raise CineRecastError(f"job {job['id']} was not issued: {exc.message}{before}") from exc
        job["state"] = "issued"
        _save(root, data)
        issued.append(execution)
    return issued


def _issued_job(data: dict, job_id: str) -> dict:
    job = _find_job(data, job_id)
    if job.get("state") != "issued":
        raise CineRecastError(f"job {job_id} is {job.get('state')}, not issued; only an issued job can be recorded")
    return job


def record_done(root: Path, job_id: str, provider_id: str, credits: float, file: Path) -> dict:
    """Record an issued job as done in the ledger, then store the returned file as a new take."""
    from cine_recast.takes import new_take  # takes imports this module

    root = Path(root)
    data = load(root)
    job = _issued_job(data, job_id)
    _find_unit(data, job.get("unit"))
    source = Path(file)
    if not source.is_file():
        raise CineRecastError(f"job {job_id}: result file not found: {source}")
    ledger.done(root, job_id, provider_id, credits)
    take = new_take(root, job["unit"], source, {
        "job": job_id, "provider_id": provider_id, "credits": float(credits),
        "quality": job.get("quality"), "recorded_at": _now(),
    })
    job["state"] = "done"
    _save(root, data)
    return {"unit": job["unit"], "take": take}


def record_fail(root: Path, job_id: str, reason: str, credits: float = 0.0) -> dict:
    """Record an issued job as failed in the ledger (credits spent, if any) and return the job."""
    root = Path(root)
    data = load(root)
    job = _issued_job(data, job_id)
    ledger.fail(root, job_id, reason, credits)
    job["state"] = "failed"
    _save(root, data)
    return job
