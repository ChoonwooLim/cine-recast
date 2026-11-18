"""Elements that appear in the shots, and the swap table that says which of them are replaced."""

import re
from pathlib import Path

from cine_recast.analyze import load_shots
from cine_recast.errors import CineRecastError
from cine_recast.jsonio import read_project_json, write_json
from cine_recast.paths import ProjectPaths
from cine_recast.project import load_project
from cine_recast.textrules import han_chars, is_english_prompt

ELEMENT_KINDS = ("person", "creature", "prop", "background")
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")
ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,23}$")
_STILL_LABELS = ("first", "mid", "last")


def write_template(root: Path) -> Path:
    """Write casting.template.json: the shots with their stills, and empty element and swap lists."""
    root = Path(root)
    pp = ProjectPaths(root)
    shots = []
    for shot in load_shots(root):
        folder = pp.shot_dir(shot.index)
        shots.append({
            "shot": shot.index, "start": shot.start, "end": shot.end,
            "stills": [(folder / "stills" / f"{label}.png").relative_to(root).as_posix()
                       for label in _STILL_LABELS],
        })
    path = pp.root / "casting.template.json"
    write_json(path, {"shots": shots, "elements": [], "swaps": []})
    return path


def load(root: Path):
    """casting.json as written. Its shape is not checked here: check() reports it problem by problem."""
    pp = ProjectPaths(Path(root))
    if not pp.casting_file.is_file():
        raise CineRecastError(f"casting.json not found: {pp.casting_file}")
    return read_project_json(pp.root, pp.casting_file)


def _is_shot_list(value) -> bool:
    return isinstance(value, list) and all(isinstance(s, int) and not isinstance(s, bool) for s in value)


def _strings(value, where: str):
    """Yield (location, text) for every string value, recursively."""
    if isinstance(value, str):
        yield where, value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield from _strings(item, f"{where}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _strings(item, f"{where}[{index}]")


def answer(rights: dict, item: str) -> str | None:
    """The recorded answer of one rights item of project.json's rights, or None when unanswered."""
    entry = rights.get(item)
    return entry.get("answer") if isinstance(entry, dict) else None


def _virtual_person_problem(root: Path, images: list[tuple[str, object]]) -> str | None:
    """Why likeness_consent "not_applicable" does not hold for these (element, image) pairs of person
    swaps, or None when every image is a generated file of its element made without reference images.

    When assets.json or the ledger cannot be read, the cause is returned as the problem instead of raised.
    """
    from cine_recast.assets import generated_files, ref_free_files  # assets imports this module

    known: dict[str, tuple[set[Path], set[Path]]] = {}
    for element, image in images:
        if element not in known:
            try:
                known[element] = ({(root / name).resolve() for name in generated_files(root, element)},
                                  {(root / name).resolve() for name in ref_free_files(root, element)})
            except (CineRecastError, OSError) as exc:
                cause = exc.message if isinstance(exc, CineRecastError) else str(exc)
                return (f"A person is replaced and likeness_consent is 'not_applicable', but the generated files "
                        f"cannot be checked: {cause}; repair it, or record consent with: "
                        "rights set --item likeness_consent --answer yes")
        generated, ref_free = known[element]
        path = (root / image).resolve() if isinstance(image, str) else None
        if path in ref_free:
            continue
        if path in generated:
            return (f"A person is replaced with an image generated from reference images (swap for {element!r}: "
                    f"{image!r}); a person generated from reference images needs likeness_consent 'yes', "
                    "record it with: rights set --item likeness_consent --answer yes")
        return (f"A person is replaced with an image this project did not generate (swap for {element!r}: "
                f"{image!r}), so likeness_consent 'not_applicable' does not hold (it covers only a person "
                "generated without reference images); record consent with: "
                "rights set --item likeness_consent --answer yes")
    return None


def check(root: Path) -> list[str]:
    """Problems found in casting.json as English sentences. An empty list means it is valid.

    The shape comes first, each fault one problem: the top level is an object, elements and swaps
    are lists of objects, an element's id, kind, name and look are strings and its shots a list of
    whole numbers, a swap's element is a string and its images a list of strings.

    A person swap needs likeness_consent "yes", or "not_applicable" when every image of every
    person swap is a file this project generated for that element from an asset without reference
    images (a virtual person).
    """
    root = Path(root)
    data = load(root)
    rights = load_project(root).get("rights", {})
    shot_numbers = {shot.index for shot in load_shots(root)}
    problems: list[str] = []

    if not isinstance(data, dict):
        return ["casting.json must hold a JSON object at the top level, with the lists 'elements' and 'swaps'."]
    elements, swaps = data.get("elements"), data.get("swaps")
    for key, value in (("elements", elements), ("swaps", swaps)):
        if not isinstance(value, list):
            problems.append(f"casting.json must hold the list {key!r} (a list of objects).")
    if problems:
        return problems

    for where, text in _strings(data, "casting"):
        found = han_chars(text)
        if found:
            problems.append(f"Han characters are not allowed ({where}): {''.join(found)}")

    ids: dict[str, dict] = {}
    for position, element in enumerate(elements):
        if not isinstance(element, dict):
            problems.append(f"elements[{position}] must be an object.")
            continue
        eid = element.get("id")
        label = f"element {eid!r}" if isinstance(eid, str) and eid else f"elements[{position}]"
        if not isinstance(eid, str) or not ID_PATTERN.fullmatch(eid):
            problems.append(f"{label}: id must be a string matching ^[a-z][a-z0-9_]{{1,23}}$.")
        elif eid in ids:
            problems.append(f"{label}: duplicate element id.")
        else:
            ids[eid] = element
        if not isinstance(element.get("kind"), str) or element.get("kind") not in ELEMENT_KINDS:
            problems.append(f"{label}: kind must be one of {', '.join(ELEMENT_KINDS)}.")
        if not isinstance(element.get("name"), str):
            problems.append(f"{label}: name must be a string.")
        shots = element.get("shots")
        if not _is_shot_list(shots) or not shots:
            problems.append(f"{label}: shots must be a non-empty list of shot numbers (whole numbers).")
        else:
            unknown = [s for s in shots if s not in shot_numbers]
            if unknown:
                problems.append(f"{label}: unknown shot numbers {unknown}; the video has shots "
                                f"{min(shot_numbers)} to {max(shot_numbers)}.")
        look = element.get("look")
        if not isinstance(look, str) or not is_english_prompt(look):
            problems.append(f"{label}: look must be a non-empty English description "
                            "(no Han, Hangul or Kana characters).")

    swapped: set[str] = set()
    swapped_kinds: set[str] = set()
    person_images: list[tuple[str, object]] = []  # (element, image) for every image of a person swap
    for position, swap in enumerate(swaps):
        if not isinstance(swap, dict):
            problems.append(f"swaps[{position}] must be an object.")
            continue
        name = swap.get("element")
        label = f"swap for {name!r}" if isinstance(name, str) else f"swaps[{position}]"
        if not isinstance(name, str):
            problems.append(f"{label}: element must be the id of an element (a string).")
        elif name not in ids:
            problems.append(f"{label}: element does not exist in the element list.")
        else:
            swapped_kinds.add(str(ids[name].get("kind")))
        if isinstance(name, str):
            if name in swapped:
                problems.append(f"{label}: duplicate swap entry; keep one per element.")
            swapped.add(name)
        images = swap.get("images")
        if not isinstance(images, list) or not images:
            problems.append(f"{label}: images must list at least one image file.")
            continue
        if not all(isinstance(image, str) for image in images):
            problems.append(f"{label}: images must be a list of file paths (strings).")
            continue
        if isinstance(name, str) and name in ids and ids[name].get("kind") == "person":
            person_images += [(name, image) for image in images]
        for image in images:
            path = Path(image) if isinstance(image, str) else None
            if path is None or path.suffix.lower() not in IMAGE_SUFFIXES:
                problems.append(f"{label}: image {image!r} must be a .jpg, .jpeg, .png or .webp file.")
            elif not (root / path).is_file():  # a relative path is relative to the project folder
                problems.append(f"{label}: image file not found: {image}")

    consent = answer(rights, "likeness_consent")
    if "person" in swapped_kinds and consent == "not_applicable":
        problem = _virtual_person_problem(root, person_images)
        if problem:
            problems.append(problem)
    elif "person" in swapped_kinds and consent != "yes":
        problems.append("A person is replaced but likeness_consent is not answered 'yes'; "
                        "record it with: rights set --item likeness_consent --answer yes")
    if "background" in swapped_kinds and answer(rights, "background_rights") is None:
        problems.append("A background is replaced but background_rights is not answered; "
                        "record it with: rights set --item background_rights")
    if answer(rights, "reference_rights") is None:
        problems.append("reference_rights is not answered; "
                        "record it with: rights set --item reference_rights")
    return problems


def swapped_shots(root: Path, kind: str) -> set[int]:
    """The shots of the elements of `kind` that have a swap in casting.json as it is now.

    Read leniently: entries that are not well formed are passed over (cast check reports them).
    """
    data = load(root)
    if not isinstance(data, dict) or not isinstance(data.get("elements"), list) or not isinstance(data.get("swaps"), list):
        return set()
    swapped = {swap.get("element") for swap in data["swaps"]
               if isinstance(swap, dict) and isinstance(swap.get("element"), str)}
    return {shot for element in data["elements"]
            if isinstance(element, dict) and element.get("kind") == kind and element.get("id") in swapped
            and _is_shot_list(element.get("shots")) for shot in element["shots"]}


def swaps_for_shots(root: Path, shots: list[int]) -> list[dict]:
    """Swaps of the elements that appear in any of the shots, in element order."""
    data = load(root)
    wanted = set(shots)
    by_element = {swap.get("element"): swap for swap in data.get("swaps", [])}
    result = []
    for element in data.get("elements", []):
        swap = by_element.get(element.get("id"))
        if swap is not None and wanted & set(element.get("shots", [])):
            result.append({"element": element["id"], "kind": element["kind"],
                           "images": list(swap["images"])})
    return result
