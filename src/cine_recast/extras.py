"""Extra shots: video-generation jobs for shots that are not in the reference (an opening, a bridge,
an ending), made to join an existing take.

An extra is one video-generation call. To lead into or out of a shot it either extends a take
(kind "extend": the take's raw file is the video input, the result continues it before its first
frame, "backward", or after its last frame, "forward") or starts or ends on a given picture (kind
"shot": a frame of a take, or an image under assets/ or swap/, written as extras/<id>/start.png or
end.png when the extra is added); reference images from assets/ or swap/ may be added. Extras live
in extras.json and follow the life cycle of the other paid jobs (lifecycle.py) through the same
ledger, as sound items do:

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
- Every input is checked again at add, set_cost and issue_next, by role (_input_problems): the
  video is the raw file of a take of a planned unit, a start or end image is the file extra add
  wrote (with the content it wrote), and every reference image and every picture a start or end
  image was copied from passes the rule all input images share (inputs.image_problems).
- A take whose shot does not reach the end of its raw file is never extended forward: what follows
  is held picture or spare frames, not part of the film. A conformed take is judged by its own
  conform.json, an unconformed one by its unit's pad_frames. A start or end frame named
  U/T:shot-first or U/T:shot-last is the first or last frame conform kept, so a new clip joins the
  shot the user confirmed.
- A frame of the reference video, or a copy of one at another size or compression, is never a
  start or end image (add compares the picture with every frame of the reference).
- Extras are free cuts for the user: assemble, which rebuilds the reference shot for shot, never
  uses them, and their sound is neither checked nor used.
"""

import math
import re
import shutil
from fractions import Fraction
from pathlib import Path

import numpy as np
from PIL import Image

from cine_recast import casting, inputs, ledger, plan
from cine_recast.analyze import load_video
from cine_recast.casting import IMAGE_SUFFIXES
from cine_recast.conform import _raw_file
from cine_recast.cuts import HOLD_DIFF, picture_signature
from cine_recast.errors import CineRecastError
from cine_recast.ffmpeg import ToolNotFound, extract_still, iter_frames, probe
from cine_recast.jsonio import read_project_json, write_json
from cine_recast.lifecycle import IssueRefused  # noqa: F401  (callers catch extras.IssueRefused)
from cine_recast.lifecycle import (SENT, batch_problem, fail, finish, highest, is_text, issue_in_order, quote,
                                   reconcile, require_issued, require_result, resolve, stale_quote)
from cine_recast.paths import ProjectPaths, inside_folder, refuse_project_material, same_bytes, sha256_of
from cine_recast.project import load_project
from cine_recast.textrules import han_problem, is_english_prompt

TOOL = "generate_video"
DIRECTIONS = ("backward", "forward")
ROLES = ("video", "start_image", "end_image", "image")
IMAGE_ROLES = ("start_image", "end_image", "image")
VIDEO_SUFFIXES = (".mp4", ".mov", ".mkv", ".webm")
_EXTRA_ID = re.compile(r"^x(\d+)$")
_TAKE = re.compile(r"^(u\d+)/(t\d{3,})$")
_FRAME = re.compile(r"^(u\d+)/(t\d{3,}):(first|last|shot-first|shot-last|\d+)$")
_RAW = re.compile(r"^takes/(u\d+)/(t\d{3,})/raw\.[A-Za-z0-9]+$")
_NOUN, _QUOTE_COMMAND = "extra", "extra set-cost"
# A start or end image is a frame of the reference when its signature (cuts.picture_signature) is
# within REFERENCE_COPY_LIMIT grey levels of a reference frame's. HOLD_DIFF (0.25) is the limit for
# held copies inside one decoded video and lets resized or recompressed copies through. Measured in
# the scoped re-review of final review 2 on 1280x536 frames: exact PNG copies 0.00, JPEG q95 to q40
# 0.04 to 0.60, copies at 1.5 times, three quarter and half size (also a 640-wide thumbnail and half
# size plus JPEG q80) 0.36 to 0.88, with nearest-neighbour resampling up to 1.36; the next frame of
# a slow shot 0.00 to 0.91; unrelated pictures 44 and up (the cut limit is 5.0). Not caught: a
# frame cropped or mirrored (11 to 37), and not reliably a regraded one (brightness +3 percent,
# contrast +5 percent: 0.95 to 1.96).
REFERENCE_COPY_LIMIT = 1.5
# A reference frame whose signature is flat (black, or one grey of a fade) keeps HOLD_DIFF, so a
# dark start picture is not refused for resembling a black frame (levels 0 and 1 are 1.0 apart).
FLAT_SPREAD = 2.0          # standard deviation of a flat signature, in grey levels
_NOT_A_REFERENCE_FRAME = ("a frame of the reference video is never a start or end image: the model carries it into "
                          "the new clip almost unchanged")


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


def _source(item: dict) -> dict:
    return item.get("source") if isinstance(item.get("source"), dict) else {}


def image_inputs(item: dict) -> list[str]:
    """The images whose rights an extra needs: its reference images (role image) and the pictures
    its start and end images were copied from (a frame of a take shows the swapped people, which
    the cast check covers)."""
    source = _source(item)
    copied = [source[name] for name in ("start", "end")
              if isinstance(source.get(name), str) and _FRAME.fullmatch(source[name]) is None]
    return copied + [entry["path"] for entry in _inputs(item)
                     if entry.get("role") == "image" and isinstance(entry.get("path"), str)]


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


def _image_problem(path: Path, what: str) -> str | None:
    """Why the file is not a readable image, or None."""
    try:
        with Image.open(path) as image:
            image.load()
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError) as exc:
        return (f"{what}: the file cannot be read as an image ({type(exc).__name__}: {exc}); give a .jpg, .jpeg, .png "
                "or .webp picture that opens")
    return None


def _ref_problem(root: Path, path_text: str, what: str) -> str | None:
    """Why a reference image (or a picture a start or end image was copied from) cannot be used:
    not under the project's assets/ or swap/, not an image file, missing, unreadable."""
    pp = ProjectPaths(Path(root))
    path = resolve(root, path_text)
    if inside_folder(path, [pp.assets_dir, pp.swap_dir]) is None:
        return (f"{what}: images must be generated asset files under assets/ or images under swap/ in the project "
                "folder")
    if path.suffix.lower() not in IMAGE_SUFFIXES or not path.is_file():
        return f"{what}: must be an existing .jpg, .jpeg, .png or .webp file"
    return _image_problem(path, what)


def _unit_item(root: Path, unit_id: str) -> dict | None:
    """The unit of the current plan with this id, else of the newest archived plan that has it."""
    plans = []
    if ProjectPaths(Path(root)).plan_file.is_file():
        plans.append(plan.load(root))
    plans += reversed(plan._archived_plans(root))
    for data in plans:
        units = data.get("units") if isinstance(data, dict) and isinstance(data.get("units"), list) else []
        for item in units:
            if isinstance(item, dict) and item.get("id") == unit_id:
                return item
    return None


def _padded(root: Path, unit_id: str) -> int:
    """The unit's pad_frames (0 when it has none or is unknown)."""
    item = _unit_item(root, unit_id)
    pad = item.get("pad_frames", 0) if isinstance(item, dict) else 0
    return pad if isinstance(pad, int) and not isinstance(pad, bool) and pad > 0 else 0


def _forward_problem(root: Path, label: str, unit_id: str, take_id: str, raw: Path) -> str | None:
    """Why the take is not extended forward, or None. A conformed take is judged by its own
    conform.json: when the frames conform kept end before the end of the raw file (the held tail of
    a padded unit, or frames the take ran over), the end of the raw file is not the end of the shot.
    This holds whatever the unit's padding is now (plan unit may have moved it to a model without
    padding). A take that was never conformed is judged by the unit's pad_frames."""
    remedy = (f"start the new clip on the last frame of the shot instead: extra add --start "
              f"{unit_id}/{take_id}:shot-last (backward extension is fine)")
    if (raw.parent / "conform.json").is_file():
        try:
            first, last, total = _kept_frames(root, raw, f"{label}: take {unit_id}/{take_id}")
        except CineRecastError as exc:
            return f"{exc.message}; until then it is not extended forward"
        if last >= total - 1:
            return None
        return (f"{label}: take {unit_id}/{take_id} is not extended forward: conform kept its frames {first} to {last} "
                f"of {total} (counted after the frame-rate conversion), so its raw file goes on past the end of the "
                f"shot (held picture of a padded unit, or frames the take ran over) and the model would continue from "
                f"there; {remedy}")
    pad = _padded(root, unit_id)
    if not pad:
        return None
    return (f"{label}: take {unit_id}/{take_id} is not extended forward: unit {unit_id} was padded (its reference "
            f"clip ends with {pad} copies of its last frame), so the end of the take's raw file is held picture that "
            f"is not part of the film, and the model would continue from there; {remedy}")


def _video_problems(root: Path, label: str, item: dict, path: str) -> list[str]:
    """The video input of an extension is the raw file of a take of a planned unit, the take its
    source names, and a padded take is not extended forward."""
    match = _RAW.fullmatch(path)
    redo = "add the extra again with extra add --extend U/T"
    if match is None or match.group(1) not in _plan_units(root):
        return [f"{label}: the video input {path} is not the raw file of a take of a planned unit "
                f"(takes/<unit>/<take>/raw.*); only a take is extended, {redo}"]
    unit_id, take_id = match.groups()
    try:
        raw = _raw_file(root, unit_id, take_id)
    except CineRecastError as exc:
        return [f"{label}: {exc.message}; {redo}"]
    problems = []
    if resolve(root, path) != raw.resolve():
        problems.append(f"{label}: the video input {path} is not the raw file of take {unit_id}/{take_id}; {redo}")
    if _source(item).get("extend") != f"{unit_id}/{take_id}":
        problems.append(f"{label}: the video input {path} is not the take its source names "
                        f"({_source(item).get('extend')!r}); {redo}")
    params = item.get("params") if isinstance(item.get("params"), dict) else {}
    if params.get("direction") == "forward":
        problem = _forward_problem(root, label, unit_id, take_id, raw)
        if problem:
            problems.append(problem)
    return problems


def _frame_file_problems(root: Path, label: str, item: dict, entry: dict, name: str, *, written: bool) -> list[str]:
    """A start or end image is extras/<own id>/<name>.png, the file extra add wrote, with the content
    it wrote (sha256 kept in the input), and its source says where it was copied from."""
    redo = "add the extra again with extra add (a start or end frame is written only by extra add)"
    expected = f"extras/{item.get('id')}/{name}.png"
    source = _source(item).get(name)
    problems = []
    if not is_text(source):
        problems.append(f"{label}: its {name} image has no source in extras.json; {redo}")
    elif _FRAME.fullmatch(source) is None:
        problem = _ref_problem(root, source, f"{label}: {source}, the picture its {name} image was copied from")
        if problem:
            problems.append(f"{problem}; {redo}")
    if entry.get("path") != expected:
        problems.append(f"{label}: its {name} image must be {expected}, the file extra add wrote, not "
                        f"{entry.get('path')}; {redo}")
    elif written:
        file = resolve(root, expected)
        if not file.is_file():
            problems.append(f"{label}: input file not found: {expected}; {redo}")
        elif not isinstance(entry.get("sha256"), str):
            problems.append(f"{label}: extras.json does not record what extra add wrote as {expected} (the extra was "
                            f"added before that record existed); {redo}")
        elif entry.get("sha256") != sha256_of(file):
            problems.append(f"{label}: {expected} is not the picture extra add wrote (its content changed); {redo} "
                            "- quoting it again would send the changed picture")
    return problems


def _input_problems(root: Path, label: str, item: dict, *, written: bool = True) -> list[str]:
    """What is wrong with an extra's inputs, by role, and with the rights its images need."""
    problems: list[str] = []
    inputs_ = item.get("inputs")
    if not isinstance(inputs_, list) or len(_inputs(item)) != len(inputs_):
        problems.append(f"{label}: inputs must be a list of {{role, path}} objects.")
    roles = [entry.get("role") for entry in _inputs(item)]
    for entry in _inputs(item):
        role, path = entry.get("role"), entry.get("path")
        if role not in ROLES or not is_text(path):
            problems.append(f"{label}: every input needs a role among {', '.join(ROLES)} and a path.")
            continue
        if han_problem(f"input path {path}", path):
            problems.append(f"{label}: {han_problem(f'input path {path}', path)}; rename the file without them.")
            continue
        if role == "video":
            problems += _video_problems(root, label, item, path)
        elif role in ("start_image", "end_image"):
            problems += _frame_file_problems(root, label, item, entry, role.split("_")[0], written=written)
        else:
            problem = _ref_problem(root, path, f"{label}: reference image {path}")
            if problem:
                problems.append(problem)
    for name in ("start", "end"):
        if roles.count(f"{name}_image") > 1:
            problems.append(f"{label}: more than one {name} image; add the extra again with extra add")
        if f"{name}_image" not in roles and name in _source(item):
            problems.append(f"{label}: its source names a {name} image but no {name}_image input exists; add the extra "
                            "again with extra add")
    for name, value in _source(item).items():
        if han_problem(f"source {name}", value):
            problems.append(f"{label}: {han_problem(f'source {name}', value)}; rename the file without them.")
    if not problems:
        problems += inputs.image_problems(root, image_inputs(item), label=label)
    return problems


def _problems(root: Path, item: dict, models: dict[str, dict], *, written: bool = True) -> list[str]:
    """What keeps an extra from being quoted or issued, as English sentences. With written False
    (extra add, before it writes them) the files of the start and end images are not checked."""
    label = f"extra {item['id']}" if is_text(item.get("id")) else "extra"
    problems: list[str] = []
    name = item.get("label")
    if not is_text(name):
        problems.append(f"{label}: label must be a non-empty text.")
    elif han_problem("label", name):
        problems.append(f"{label}: {han_problem('label', name)}.")
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
    problems += _input_problems(root, label, item, written=written)
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
    problem = batch_problem(label, item.get("batch"))
    if problem:
        problems.append(problem)
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


def raw_frame(fitted: int, raw_rate: Fraction, film_rate: Fraction) -> int:
    """The frame of a take's raw file that conform shows as frame `fitted` of the take fitted to the
    film's frame rate. conform's fps filter puts raw frame k at fitted time round(k * film_rate /
    raw_rate) (half away from zero) and shows at each fitted frame the last raw frame at or before
    it, so fitted frame j shows the largest k with k * film_rate / raw_rate < j + 1/2. The same frame
    when the rates are equal (a raw file of constant frame rate is assumed)."""
    if raw_rate == film_rate:
        return fitted
    return math.ceil((fitted + Fraction(1, 2)) * raw_rate / film_rate) - 1


def _kept_frames(root: Path, raw: Path, name: str) -> tuple[int, int, int]:
    """(first, last, source_frames) of the frames conform kept for the take whose raw file this is,
    counted after the frame-rate conversion, from its conform.json: with take_cuts the first value
    and the last value minus 1, else from start_frame for unit_frames frames, or to the end of the
    take when it was retimed. `name` begins the refusal of a record that does not describe the take."""
    record = read_project_json(Path(root), raw.parent / "conform.json",
                               {"start_frame": int, "source_frames": int, "unit_frames": int})
    cuts, start, total = record.get("take_cuts"), record["start_frame"], record["source_frames"]
    if cuts is not None:
        whole = isinstance(cuts, list) and len(cuts) >= 2 and all(
            isinstance(v, int) and not isinstance(v, bool) for v in cuts)
        first, last = (cuts[0], cuts[-1] - 1) if whole else (-1, -1)
    elif record.get("retimed") is True:
        first, last = start, total - 1
    else:
        first, last = start, start + record["unit_frames"] - 1
    if not 0 <= first <= last < total:
        raise CineRecastError(f"{name}: its conform.json does not describe frames inside the take; run conform for the "
                              "take again")
    return first, last, total


def _shot_frame(root: Path, flag: str, unit_id: str, take_id: str, which: str, raw: Path, facts) -> int:
    """The raw frame of U/T:shot-first or U/T:shot-last: the first or last frame conform kept for the
    take (_kept_frames; for a unit of several shots the first kept frame of its first shot and the
    last kept frame of its last), converted to a frame of the raw file when the frame rates differ."""
    name = f"{flag} {unit_id}/{take_id}:{which}"
    if not (raw.parent / "conform.json").is_file():
        raise CineRecastError(f"{name}: take {unit_id}/{take_id} has not been conformed, so the tool does not know "
                              "where the shot lies in its raw file; run conform for the take first (for a padded unit "
                              f"with --take-cuts as takes cuts shows), or give a frame of the raw file as "
                              f"{unit_id}/{take_id}:<n>")
    first, last, _ = _kept_frames(root, raw, f"{name}: take {unit_id}/{take_id}")
    fitted = first if which == "shot-first" else last
    info, _ = load_video(root)
    index = raw_frame(fitted, Fraction(facts.fps_num, facts.fps_den), Fraction(info.fps_num, info.fps_den))
    return min(max(index, 0), facts.frames - 1)


def _rgb(path: Path, what: str) -> np.ndarray:
    """The image as an RGB array; refused with a sentence when it cannot be read."""
    problem = _image_problem(path, what)
    if problem:
        raise CineRecastError(problem)
    with Image.open(path) as image:
        return np.asarray(image.convert("RGB"))


def _picture_areas(rgb: np.ndarray, info, box) -> list[np.ndarray]:
    """The picture as it is and, when it has the full frame's aspect (any size) and the reference has
    bars, its picture area cut at the same proportions, so a scaled full frame loses its bars too."""
    areas = [rgb]
    height, width = rgb.shape[:2]
    letterboxed = (box.w, box.h) != (info.width, info.height)
    if letterboxed and abs(width * info.height - height * info.width) <= 0.01 * width * info.height:
        sx, sy = width / info.width, height / info.height
        x0, y0 = round(box.x * sx), round(box.y * sy)
        x1, y1 = round((box.x + box.w) * sx), round((box.y + box.h) * sy)
        if x1 - x0 >= 2 and y1 - y0 >= 2:
            areas.append(rgb[y0:y1, x0:x1])
    return areas


def _reference_frame_match(root: Path, rgb: np.ndarray) -> tuple[int, float, float] | None:
    """(frame, difference, limit) of the first reference frame whose signature is within the limit of
    the picture's, or None: REFERENCE_COPY_LIMIT, or HOLD_DIFF for a flat reference frame
    (FLAT_SPREAD). The picture is compared as it is and as its picture area (_picture_areas).
    Refused (fail closed) when the reference cannot be read whole."""
    info, box = load_video(root)
    candidates = [picture_signature(area) for area in _picture_areas(rgb, info, box)]
    reference = Path(root) / load_project(root)["reference"]
    read = 0
    try:
        for index, frame in enumerate(iter_frames(reference, crop=box.as_crop())):
            read += 1
            signature = picture_signature(frame)
            limit = HOLD_DIFF if float(signature.std()) < FLAT_SPREAD else REFERENCE_COPY_LIMIT
            difference = min(float(np.abs(candidate - signature).mean()) for candidate in candidates)
            if difference < limit:
                return index, difference, limit
    except ToolNotFound:
        raise
    except CineRecastError as exc:
        raise CineRecastError(f"the reference video cannot be read to check that the picture is not one of its frames "
                              f"({exc.message}); nothing written") from exc
    if read != info.frames:
        raise CineRecastError(f"the reference video cannot be read to check that the picture is not one of its frames "
                              f"(read {read} of its {info.frames} frames); nothing written")
    return None


def _frame_choice(root: Path, flag: str, text: str) -> tuple[str, Path, int | None]:
    """What --start or --end names: ("frame", the take's raw file, the frame index) for U/T:FRAME,
    else ("image", the image file, None) for an image under assets/ or swap/ that is readable and is
    not a frame of the reference."""
    match = _FRAME.fullmatch(text) if isinstance(text, str) else None
    if match is None:
        pp = ProjectPaths(Path(root))
        path = resolve(root, str(text))
        if (inside_folder(path, [pp.assets_dir, pp.swap_dir]) is None
                or path.suffix.lower() not in IMAGE_SUFFIXES or not path.is_file()):
            raise CineRecastError(f"{flag} {text}: give a take frame as U/T:FRAME (for example u01/t001:shot-first, "
                                  "u01/t001:shot-last or u01/t001:12) or an existing .jpg, .jpeg, .png or .webp image; "
                                  "images must be generated asset files under assets/ or images under swap/ in the "
                                  f"project folder, and {_NOT_A_REFERENCE_FRAME}")
        found = _reference_frame_match(root, _rgb(path, f"{flag} {text}"))
        if found is not None:
            raise CineRecastError(f"{flag} {text}: this picture is frame {found[0]} of the reference video, or a copy "
                                  f"of it at another size or compression (its 64x36 signature differs by "
                                  f"{found[1]:.2f}, under the limit of {found[2]} for a copy); "
                                  f"{_NOT_A_REFERENCE_FRAME}. Generate the key picture as an "
                                  "asset (assets add, then give the stored file under assets/), or start from a take "
                                  "with U/T:shot-first or U/T:shot-last")
        return "image", path, None
    unit_id, take_id, choice = match.groups()
    raw = _take_raw(root, unit_id, take_id)
    facts = probe(raw)
    frames = facts.frames
    if choice in ("shot-first", "shot-last"):
        return "frame", raw, _shot_frame(root, flag, unit_id, take_id, choice, raw, facts)
    index = 0 if choice == "first" else frames - 1 if choice == "last" else int(choice)
    if not 0 <= index < frames:
        raise CineRecastError(f"{flag}: frame {index} is outside take {unit_id}/{take_id} (frames 0 to {frames - 1})")
    return "frame", raw, index


def _write_input_image(kind: str, source: Path, index: int | None, out: Path) -> None:
    """Write extras/<id>/start.png or end.png: the exact frame of a take, or a copy of an image (a
    PNG as it is, another type converted to PNG)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    if kind == "frame":
        extract_still(source, index, out)
    elif source.suffix.lower() == ".png":
        shutil.copyfile(source, out)
    else:
        with Image.open(source) as image:
            image.convert("RGB").save(out, format="PNG")


def _ref_path(root: Path, ref) -> str:
    """A reference image from the project's assets/ or swap/ folder, as a project-relative path."""
    problem = _ref_problem(root, str(ref), f"--ref {ref}")
    if problem:
        raise CineRecastError(problem)
    return _relative(root, resolve(root, str(ref)))


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
    inputs_: list[dict] = []
    source: dict = {}
    if extend is not None:
        match = _TAKE.fullmatch(extend) if isinstance(extend, str) else None
        if match is None:
            raise CineRecastError(f"--extend must be U/T (for example u01/t001), not {extend!r}")
        inputs_.append({"role": "video", "path": _relative(root, _take_raw(root, match.group(1), match.group(2)))})
        source = {"extend": extend, "direction": direction}
    frames = {}
    for flag, name, value in (("--start", "start", start), ("--end", "end", end)):
        if value is not None:
            value = str(value)                  # a path object from a caller is kept as the text it spells
            problem = han_problem(flag, value)
            if problem:
                raise CineRecastError(f"{problem}; rename the file without them")
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
        inputs_.append({"role": f"{name}_image", "path": f"extras/{xid}/{name}.png"})
    inputs_ += [{"role": "image", "path": path} for path in ref_paths]
    item = {"id": xid, "label": label, "kind": "extend" if extend is not None else "shot", "batch": batch,
            "state": "planned", "est_credits": None, "quote_digest": None, "params": params, "inputs": inputs_,
            "source": source, "result": None}
    problems = _problems(root, item, models, written=False)
    if problems:
        raise CineRecastError("; ".join(problems))
    made_extras_dir = not pp.extras_dir.exists()
    try:
        for name, (kind, path, index) in frames.items():
            _write_input_image(kind, path, index, folder / f"{name}.png")
            entry = next(e for e in inputs_ if e["role"] == f"{name}_image")
            entry["sha256"] = sha256_of(folder / f"{name}.png")
        data["extras"].append(item)
        _save(root, data)
    except BaseException:
        shutil.rmtree(folder, ignore_errors=True)
        if made_extras_dir:
            shutil.rmtree(pp.extras_dir, ignore_errors=True)
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

    Every queued extra must pass the checks of add again (casting and inputs included) and still
    have the params and input files its quote priced, or nothing is issued; an extra whose own
    checks fail is not also told to be quoted again (a changed start or end image is added again,
    never quoted again). What its images carry is kept with it (sent_likeness). Stops with
    IssueRefused at the first extra the ledger refuses; the extras issued before it stay issued and
    travel on the exception.
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
            own = _problems(root, item, models)
            stale = None if own else stale_quote(item, _priced(root, item), noun=_NOUN, command=_QUOTE_COMMAND)
            problems += own + ([stale] if stale else [])
        if problems:
            raise CineRecastError("extra check failed, no extra issued: " + "; ".join(problems))
        for item in queue:
            item["sent_likeness"] = inputs.likeness_of(root, image_inputs(item))
    runs = {item["id"]: _payload(root, item) for item in queue}
    try:
        issued = issue_in_order(root, batch, queue, lambda item: _priced(root, item), lambda: _save(root, data),
                                noun=_NOUN, command=_QUOTE_COMMAND)
    except IssueRefused as exc:
        raise IssueRefused(exc.message, [runs[p["job_id"]] for p in exc.issued]) from exc
    return [runs[p["job_id"]] for p in issued]


def _check_result_file(root: Path, extra_id: str, source: Path) -> None:
    """Refuse a result file that is not a downloaded generation result with a readable video."""
    if source.suffix.lower() not in VIDEO_SUFFIXES:
        raise CineRecastError(f"extra {extra_id}: result file {source} must be a .mp4, .mov, .mkv or .webm file")
    if not source.is_file():
        raise CineRecastError(f"extra {extra_id}: result file not found: {source}")
    refuse_project_material(root, source, f"extra {extra_id}")
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
