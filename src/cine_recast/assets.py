"""Generated swap assets: image-generation jobs for elements that have no material yet.

An asset is one image-generation call for one element of casting.json (a virtual person, a new
sky, a creature, a prop). Asset jobs live in assets.json, apart from plan.json, because a plan can
only be built once the swap table passes its check. They follow the life cycle of video jobs
(lifecycle.py) through the same ledger:

planned (add) -> quoted (set_cost) -> issued (issue_next) -> done or failed (record_done, record_fail)

- An asset reaches the agent only through issue_next, which hands it to ledger.issue first and
  stops at the first refusal.
- The ledger is the authority for state: every load moves an asset forward to the state the
  ledger records for it.
- A quote is bound to what it priced: the model, the params and the refs (not the prompt).
- Returned images are stored as assets/<element>/<id>_<n><suffix> and never overwritten; use puts
  one of them in the swap table.
- Asset ids (a0001, ...) share the ledger with video job ids (j0001, ...) and are never reused.
- Model limits come only from the project's models.json.
"""

import re
import shutil
from pathlib import Path

from cine_recast import casting, ledger
from cine_recast.analyze import load_video
from cine_recast.casting import ID_PATTERN, IMAGE_SUFFIXES
from cine_recast.errors import CineRecastError
from cine_recast.jsonio import read_project_json, write_json
from cine_recast.lifecycle import (SENT, digest, fail, finish, highest, is_text, issue_in_order, quote, reconcile,
                                   require_issued, require_result, resolve, stale_quote)
from cine_recast.paths import ProjectPaths, inside_folder
from cine_recast.plan import closest_aspect, valid_models
from cine_recast.project import load_project
from cine_recast.textrules import is_english_prompt

TOOL = "generate_image"
MAX_COUNT = 4
_PARAM_KEYS = ("aspect_ratio", "resolution", "count")
_ASSET_ID = re.compile(r"^a(\d+)$")
_NOUN, _QUOTE_COMMAND = "asset", "assets set-cost"


# --- assets.json ---------------------------------------------------------------------------

def _load(root: Path) -> dict:
    """assets.json with every asset's state reconciled from the ledger; no assets when it is missing."""
    pp = ProjectPaths(Path(root))
    if not pp.assets_file.exists():
        return {"assets": []}
    data = read_project_json(pp.root, pp.assets_file, {"assets": list})
    reconcile(root, data["assets"])
    return data


def _save(root: Path, data: dict) -> None:
    write_json(ProjectPaths(Path(root)).assets_file, data)


def _find(data: dict, asset_id: str) -> dict:
    for asset in data["assets"]:
        if isinstance(asset, dict) and asset.get("id") == asset_id:
            return asset
    raise CineRecastError(f"unknown asset: {asset_id}")


def _number(asset: dict) -> tuple[int, str]:
    aid = str(asset.get("id", ""))
    match = _ASSET_ID.match(aid)
    return (int(match.group(1)) if match else -1, aid)


def _next_number(root: Path, data: dict) -> int:
    """Above every asset id in the ledger and in assets.json (video job ids are not counted)."""
    ids = [a.get("id") for a in data["assets"] if isinstance(a, dict)]
    return highest(_ASSET_ID, [*ledger.job_states(root), *ids]) + 1


def _elements(root: Path) -> dict[str, dict]:
    """casting.json's elements that have a valid id, by id. The rest of the swap table need not be valid."""
    data = casting.load(root)
    elements = data.get("elements") if isinstance(data, dict) else None
    if not isinstance(elements, list):
        return {}
    return {e["id"]: e for e in elements
            if isinstance(e, dict) and isinstance(e.get("id"), str) and ID_PATTERN.fullmatch(e["id"])}


# --- payloads and checks -------------------------------------------------------------------

def _payload(root: Path, asset: dict) -> dict:
    """The exact call for one asset: tool, parameters and absolute paths of the reference images.

    The same payload is shown for quoting and sent at issue.
    """
    params = asset.get("params") if isinstance(asset.get("params"), dict) else {}
    refs = asset.get("refs") if isinstance(asset.get("refs"), list) else []
    return {
        "job_id": asset.get("id"), "element": asset.get("element"), "tool": TOOL,
        "params": {"model": asset.get("model"), "prompt": asset.get("prompt"),
                   **{key: params[key] for key in _PARAM_KEYS if key in params}},
        "medias": [{"role": "image", "path": str(resolve(root, ref))} for ref in refs if is_text(ref)],
    }


def _rights_problems(root: Path, label: str, element: dict | None) -> list[str]:
    """The rights an asset with reference images needs, read now: the element's kind comes from
    casting.json as it is at this moment, the answers from project.json. A person needs
    likeness_consent "yes" (the result carries the likeness of whoever is in the references); a
    background needs an answered background_rights, as a background swap does."""
    kind = element.get("kind") if isinstance(element, dict) else None
    rights = load_project(root).get("rights") or {}
    if kind == "person" and casting.answer(rights, "likeness_consent") != "yes":
        return [f"{label}: element {element['id']!r} is a person and the asset has reference images, so the result "
                "carries the likeness of the people in them; likeness_consent must be 'yes', record it with: "
                "rights set --item likeness_consent --answer yes"]
    if kind == "background" and casting.answer(rights, "background_rights") is None:
        return [f"{label}: element {element['id']!r} is a background and the asset has reference images, but "
                "background_rights is not answered; record it with: rights set --item background_rights"]
    return []


def _problems(root: Path, asset: dict, models: dict[str, dict], elements: dict[str, dict]) -> list[str]:
    """What keeps an asset from being quoted or issued, as English sentences.

    An asset with reference images also needs the rights _rights_problems names.
    """
    label = f"asset {asset['id']}" if is_text(asset.get("id")) else "asset"
    problems: list[str] = []
    element = asset.get("element")
    if not isinstance(element, str) or element not in elements:
        problems.append(f"{label}: element {element!r} is not in the element list of casting.json.")
    prompt = asset.get("prompt")
    if not isinstance(prompt, str) or not is_english_prompt(prompt):
        problems.append(f"{label}: prompt must be a non-empty English text (no Han, Hangul or Kana characters).")
    model = asset.get("model")
    spec = models.get(model) if isinstance(model, str) else None
    if spec is None:
        problems.append(f"{label}: model {model!r} is not in models.json.")
    elif spec["kind"] != "image":
        problems.append(f"{label}: model {model!r} has kind {spec['kind']!r}; assets need an image model.")
        spec = None
    params = asset.get("params") if isinstance(asset.get("params"), dict) else {}
    count = params.get("count")
    if isinstance(count, bool) or not isinstance(count, int) or not 1 <= count <= MAX_COUNT:
        problems.append(f"{label}: count must be a whole number from 1 to {MAX_COUNT}, not {count!r}.")
    if spec is not None:
        if params.get("aspect_ratio") not in spec["aspect_ratios"]:
            problems.append(f"{label}: aspect_ratio {params.get('aspect_ratio')!r} is not offered by model "
                            f"{model!r} ({', '.join(spec['aspect_ratios'])}).")
        if spec["resolutions"] and params.get("resolution") not in spec["resolutions"]:
            problems.append(f"{label}: resolution {params.get('resolution')!r} is not offered by model "
                            f"{model!r} ({', '.join(spec['resolutions'])}).")
        if not spec["resolutions"] and "resolution" in params:
            problems.append(f"{label}: model {model!r} lists no resolution, so params must not hold one.")
    refs = asset.get("refs")
    if not isinstance(refs, list):
        problems.append(f"{label}: refs must be a list of image files.")
    else:
        for ref in refs:
            if not is_text(ref) or Path(ref).suffix.lower() not in IMAGE_SUFFIXES:
                problems.append(f"{label}: ref {ref!r} must be a .jpg, .jpeg, .png or .webp file.")
            elif not resolve(root, ref).is_file():  # a relative path is relative to the project folder
                problems.append(f"{label}: ref image not found: {ref}")
        if spec is not None and len(refs) > spec["image_refs"]:
            problems.append(f"{label}: {len(refs)} ref images, but model {model!r} takes at most "
                            f"{spec['image_refs']} (image_refs).")
        if refs:
            problems += _rights_problems(root, label, elements.get(element) if isinstance(element, str) else None)
    if not is_text(asset.get("batch")):
        problems.append(f"{label}: batch must not be empty.")
    return problems


def _default_aspect(root: Path, element: dict, spec: dict) -> str:
    """A background takes the model's ratio nearest the picture area, anything else the model's first."""
    if element.get("kind") == "background":
        _, box = load_video(root)
        return closest_aspect(spec, box.aspect)
    return spec["aspect_ratios"][0]


# --- the life cycle ------------------------------------------------------------------------

def add(root: Path, *, element: str, prompt: str, model: str, batch: str, aspect_ratio: str | None = None,
        count: int = 1, refs: list[Path] = ()) -> dict:
    """Plan an image-generation job for an element of casting.json and return the planned asset.

    Without aspect_ratio a background takes the model's ratio nearest the picture area, any other
    element the model's first; the resolution is the model's first, left out when it lists none.
    A relative ref is relative to the project folder. Nothing is written when a check refuses.
    """
    root = Path(root)
    load_project(root)
    elements = _elements(root)
    models = valid_models(root)
    spec = models.get(model)
    if aspect_ratio is None and element in elements and spec is not None and spec["kind"] == "image":
        aspect_ratio = _default_aspect(root, elements[element], spec)
    params: dict = {"aspect_ratio": aspect_ratio}
    if spec is not None and spec["kind"] == "image" and spec["resolutions"]:
        params["resolution"] = spec["resolutions"][0]
    params["count"] = count
    asset = {"id": None, "element": element, "kind": elements.get(element, {}).get("kind"), "model": model,
             "params": params, "prompt": prompt, "refs": [Path(ref).as_posix() for ref in refs],
             "batch": batch, "state": "planned", "est_credits": None, "quote_digest": None, "files": []}
    problems = _problems(root, asset, models, elements)
    if problems:
        raise CineRecastError("; ".join(problems))
    data = _load(root)
    asset["id"] = f"a{_next_number(root, data):04d}"
    data["assets"].append(asset)
    _save(root, data)
    return asset


def list_assets(root: Path) -> list[dict]:
    """Every asset with its element, state, batch, estimate and files, and its params: the params it
    was sent with once issued, the params it would be sent with before that."""
    root = Path(root)
    return [{"id": a.get("id"), "element": a.get("element"), "kind": a.get("kind"), "batch": a.get("batch"),
             "state": a.get("state"), "est_credits": a.get("est_credits"),
             "params": a.get("sent_params") if a.get("state") in SENT else _payload(root, a)["params"],
             "files": a.get("files", [])}
            for a in _load(root)["assets"] if isinstance(a, dict)]


def set_cost(root: Path, asset_id: str, credits: float) -> dict:
    """Record the quoted cost of a planned or quoted asset and bind it to the asset's model, params
    and refs. The asset becomes quoted; a new quote replaces the estimate and the binding at any
    time before the asset is issued."""
    root = Path(root)
    data = _load(root)
    asset = _find(data, asset_id)

    def checked_payload() -> dict:
        problems = _problems(root, asset, valid_models(root), _elements(root))
        if problems:
            raise CineRecastError("; ".join(problems))
        return _payload(root, asset)

    quote(asset, credits, checked_payload, noun=_NOUN)
    _save(root, data)
    return asset


def issue_next(root: Path, batch: str) -> list[dict]:
    """Hand the batch's quoted assets to the ledger in id order and return what to run for each.

    Every queued asset must pass the checks of add again and still have the payload its quote
    priced, or nothing is issued. Stops with IssueRefused at the first asset the ledger refuses;
    the assets issued before it stay issued and travel on the exception.
    """
    root = Path(root)
    data = _load(root)
    queue = sorted((a for a in data["assets"]
                    if isinstance(a, dict) and a.get("batch") == batch and a.get("state") == "quoted"),
                   key=_number)
    if queue:
        models, elements = valid_models(root), _elements(root)
        problems: list[str] = []
        for asset in queue:
            problems += _problems(root, asset, models, elements)
            stale = stale_quote(asset, _payload(root, asset), noun=_NOUN, command=_QUOTE_COMMAND)
            if stale:
                problems.append(stale)
        if problems:
            raise CineRecastError("asset check failed, no asset issued: " + "; ".join(problems))
    return issue_in_order(root, batch, queue, lambda asset: _payload(root, asset), lambda: _save(root, data),
                          noun=_NOUN, command=_QUOTE_COMMAND)


def record_done(root: Path, asset_id: str, provider_id: str, credits: float, files: list[Path]) -> dict:
    """Store the returned images as assets/<element>/<id>_<n><suffix>, record the asset as done in
    the ledger, then save. Returns {"asset", "files"} with the stored files relative to the project.

    A name that exists already is never overwritten: the call fails. A file inside the project's
    reference/, analysis/, takes/, out/ or assets/ folder is refused: it is project material (a frame
    of the reference, or an image already stored under another asset), not a generation result.
    The stored files are listed in assets.json before the ledger records the asset as done, so a
    crash between the two leaves them reachable. If the ledger refuses, the files stored by this call
    are removed and taken off the list again, so a refused call leaves nothing behind.
    """
    root = Path(root)
    pp = ProjectPaths(root)
    data = _load(root)
    asset = require_issued(_find(data, asset_id), noun=_NOUN)
    element = asset.get("element")
    if not _ASSET_ID.fullmatch(asset_id) or not isinstance(element, str) or not ID_PATTERN.fullmatch(element):
        raise CineRecastError(f"asset {asset_id}: its id and element {element!r} must be valid ids to name its files")
    require_result(asset, provider_id, credits, noun=_NOUN)
    sources = [Path(file) for file in files]
    if not sources:
        raise CineRecastError(f"asset {asset_id}: give at least one result file")
    for source in sources:
        if source.suffix.lower() not in IMAGE_SUFFIXES:
            raise CineRecastError(f"asset {asset_id}: result file {source} must be a .jpg, .jpeg, .png or .webp file")
        if not source.is_file():
            raise CineRecastError(f"asset {asset_id}: result file not found: {source}")
        folder = inside_folder(source, pp.material_folders())
        if folder is not None:
            raise CineRecastError(f"asset {asset_id}: result file {source} is project material (inside the project's "
                                  f"{folder.name} folder), not a generation result; register only files downloaded "
                                  "from the generation job")
    folder = pp.asset_dir(element)
    targets = [folder / f"{asset_id}_{n}{source.suffix.lower()}" for n, source in enumerate(sources, start=1)]
    for target in targets:
        if target.exists():
            raise CineRecastError(f"asset {asset_id}: {target.relative_to(root).as_posix()} exists already; "
                                  "stored files are never overwritten")
    folder.mkdir(parents=True, exist_ok=True)
    stored: list[Path] = []

    def undo() -> None:
        for target in stored:  # only the files made by this call
            target.unlink(missing_ok=True)

    try:
        for source, target in zip(sources, targets):
            with source.open("rb") as reader, target.open("xb") as writer:  # "x": never overwrite
                stored.append(target)
                shutil.copyfileobj(reader, writer)
    except BaseException:
        undo()
        raise
    names = [target.relative_to(root).as_posix() for target in targets]
    previous = asset.get("files", [])
    asset["files"] = names
    try:
        _save(root, data)  # listed before the ledger records done, so a crash after that strands no paid file
    except BaseException:
        undo()
        raise

    def take_back() -> None:
        undo()
        asset["files"] = previous
        _save(root, data)

    finish(root, asset, provider_id, credits, take_back, lambda: _save(root, data))
    return {"asset": asset_id, "files": names}


def record_fail(root: Path, asset_id: str, reason: str, credits: float = 0.0) -> dict:
    """Record an issued asset as failed in the ledger (credits spent, if any) and return the asset."""
    root = Path(root)
    data = _load(root)
    return fail(root, _find(data, asset_id), reason, credits, lambda: _save(root, data), noun=_NOUN)


# --- generated files and the swap table ----------------------------------------------------

def _sent_without_refs(asset: dict) -> bool:
    """True when the asset lists no refs and was sent with none.

    The quote digest binds what was sent at issue (the sent params and the medias), so it must match
    the sent params with no medias; editing refs in assets.json afterwards does not change that.
    """
    sent = asset.get("sent_params")
    return (asset.get("refs") == [] and isinstance(sent, dict)
            and asset.get("quote_digest") == digest({"params": sent, "medias": []}))


def _stored_files(root: Path, element: str, *, ref_free: bool) -> list[str]:
    """Files of the element's assets that the ledger records as done (a "done" written into
    assets.json alone does not count), optionally only of assets sent without refs."""
    root = Path(root)
    folder = ProjectPaths(root).asset_dir(element).relative_to(root).as_posix()
    suffixes = "|".join(re.escape(suffix) for suffix in IMAGE_SUFFIXES)
    found: list[str] = []
    states = ledger.job_states(root)
    done = [a for a in _load(root)["assets"]
            if isinstance(a, dict) and a.get("element") == element and states.get(a.get("id")) == "done"
            and (not ref_free or _sent_without_refs(a))]
    for asset in sorted(done, key=_number):
        aid = asset.get("id")
        if not isinstance(aid, str) or not _ASSET_ID.fullmatch(aid) or not isinstance(asset.get("files"), list):
            continue
        name = re.compile(rf"{re.escape(folder)}/{re.escape(aid)}_[1-9][0-9]*({suffixes})")
        found += [f for f in asset["files"] if isinstance(f, str) and name.fullmatch(f) and (root / f).is_file()]
    return found


def generated_files(root: Path, element: str) -> list[str]:
    """The stored files of the element's done assets, relative to the project folder, in asset order.

    Only files that exist under the names record_done gives (assets/<element>/<id>_<n><suffix>) count.
    """
    return _stored_files(root, element, ref_free=False)


def ref_free_files(root: Path, element: str) -> list[str]:
    """The element's generated files that came from assets made without reference images.

    Only these can show a virtual person: an outside photo used as a ref can carry a real person's
    likeness into the result.
    """
    return _stored_files(root, element, ref_free=True)


def use(root: Path, element: str, image: str) -> None:
    """Make image, one of the element's generated files, the element's swap in casting.json.

    The swap entry becomes {"element", "images": [image], "note": "generated: <asset id>"}, written
    in place of the element's swap or added when it has none.
    """
    root = Path(root)
    data = casting.load(root)
    swaps = data.get("swaps", []) if isinstance(data, dict) else None
    if not isinstance(swaps, list):
        raise CineRecastError("casting.json must hold the lists 'elements' and 'swaps'")
    if element not in _elements(root):
        raise CineRecastError(f"element {element!r} is not in the element list of casting.json")
    image = Path(image).as_posix()
    files = generated_files(root, element)
    if image not in files:
        raise CineRecastError(f"{image} is not a generated file of element {element!r}; "
                              f"its generated files: {', '.join(files) or 'none'}")
    asset_id = image.rsplit("/", 1)[-1].split("_", 1)[0]
    entry = {"element": element, "images": [image], "note": f"generated: {asset_id}"}
    for index, swap in enumerate(swaps):
        if isinstance(swap, dict) and swap.get("element") == element:
            swaps[index] = entry
            break
    else:
        swaps.append(entry)
    data["swaps"] = swaps
    write_json(ProjectPaths(root).casting_file, data)
