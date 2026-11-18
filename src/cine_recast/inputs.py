"""Input images of generation jobs: which images may be sent, and which rights answers they need.

One rule for every command that can send an image to a paid service, so the commands cannot drift
apart: the reference images of an asset (assets add, set-cost, next) and the reference images and
the pictures a start or end frame was copied from of an extra (extra add, set-cost, next).

1. A file under assets/ must be a file a done asset stored there (the ledger records the asset as
   done). A picture placed there by hand is not a generated file.
2. Any other image must be named now by a swap entry of casting.json, so that the rights answers
   cast check demands for its element cover it. The tool cannot tell who is in a picture no swap
   entry names, so such a picture is refused as unknown.
3. An image that carries a person's likeness needs likeness_consent "yes". The one exception
   (design 7.7) is a virtual person: a file generated for a person element without reference
   images, which also passes under "not_applicable".
4. The swap image of a background element needs an answered background_rights.

What an image carries is traced (trace): a person element's swap image that is not a generated
file carries a real person; a file generated for a person element carries a virtual person when it
was sent without reference images, else a real one; a file generated for any other element carries
what its reference images carried when it was issued (recorded then as sent_likeness, so a later
change of the swap table does not change the answer), or, for an asset issued before that record
existed, what its reference images carry now. Anything the trace cannot follow is "unknown" and
needs consent "yes" like a real person.
"""

import os
from pathlib import Path

from cine_recast import casting
from cine_recast.errors import CineRecastError
from cine_recast.paths import ProjectPaths, inside_folder
from cine_recast.project import load_project

VIRTUAL, REAL, UNKNOWN = "virtual", "real", "unknown"
TAGS = (VIRTUAL, REAL, UNKNOWN)


def _key(root: Path, image) -> str:
    """A comparable spelling of an image path (relative to the project folder unless absolute)."""
    return os.path.normcase(str((Path(root) / str(image)).resolve()))


class _Project:
    """What the rule reads from the project, read once per check."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.pp = ProjectPaths(self.root)
        try:
            data = casting.load(self.root)
        except CineRecastError:
            data = None
        elements = data.get("elements") if isinstance(data, dict) and isinstance(data.get("elements"), list) else []
        self.kinds = {e["id"]: e.get("kind") for e in elements if isinstance(e, dict) and isinstance(e.get("id"), str)}
        swaps = data.get("swaps") if isinstance(data, dict) and isinstance(data.get("swaps"), list) else []
        self.swap_images: dict[str, set[str]] = {}          # image key -> every element whose swap names it
        for swap in swaps:
            if isinstance(swap, dict) and isinstance(swap.get("element"), str) and isinstance(swap.get("images"), list):
                for image in swap["images"]:
                    if isinstance(image, str):
                        self.swap_images.setdefault(_key(self.root, image), set()).add(swap["element"])
        self.rights = load_project(self.root).get("rights") or {}
        self._stored: dict[str, dict] | None = None

    def stored(self) -> dict[str, dict]:
        """Image key -> done asset for every file a done asset stored (raises CineRecastError when
        assets.json or the ledger cannot be read)."""
        if self._stored is None:
            from cine_recast.assets import stored_assets  # assets imports this module

            self._stored = {_key(self.root, name): asset for name, asset in stored_assets(self.root).items()}
        return self._stored

    def naming(self, image, kind: str) -> list[str]:
        """The elements of this kind whose swap names the image, sorted (a picture two swap entries
        name, a rider on a horse, counts as a person's when either of them is a person)."""
        return sorted(e for e in self.swap_images.get(_key(self.root, image), ()) if self.kinds.get(e) == kind)

    def under_assets(self, image) -> bool:
        return inside_folder(self.root / str(image), [self.pp.assets_dir]) is not None

    def trace(self, image, seen: frozenset = frozenset()) -> dict[str, str]:
        """What the image carries, as {tag: reason}; {} when it shows no person the tool knows of."""
        key = _key(self.root, image)
        if self.under_assets(image):
            asset = self.stored().get(key)
            if asset is None:
                return {UNKNOWN: f"{image} is not a file a done asset stored"}
            return self._trace_asset(asset, key, seen)
        if key not in self.swap_images:
            return {UNKNOWN: f"{image} is neither a generated file nor an image the swap table names"}
        persons = self.naming(image, "person")
        if persons:
            return {REAL: f"{image} is an image of the person element {persons[0]!r} in the swap table"}
        return {}

    def _trace_asset(self, asset: dict, key: str, seen: frozenset) -> dict[str, str]:
        from cine_recast.assets import sent_without_refs  # assets imports this module

        aid, element = asset.get("id"), asset.get("element")
        if self.kinds.get(element) == "person" or asset.get("kind") == "person":
            if sent_without_refs(asset):
                return {VIRTUAL: f"asset {aid} is a virtual person generated for {element!r} without reference images"}
            return {REAL: f"asset {aid} was generated for the person element {element!r} from reference images"}
        if sent_without_refs(asset):
            return {}
        recorded = asset.get("sent_likeness")
        if isinstance(recorded, dict) and all(tag in TAGS and isinstance(why, str) for tag, why in recorded.items()):
            return {tag: f"asset {aid} was made from reference images where {why}" for tag, why in recorded.items()}
        refs = asset.get("refs")
        if key in seen or not isinstance(refs, list):
            return {UNKNOWN: f"the reference images of asset {aid} cannot be traced"}
        found: dict[str, str] = {}
        for ref in refs:
            for tag, why in self.trace(ref, seen | {key}).items():
                found.setdefault(tag, f"asset {aid} was made from reference images where {why}")
        return found

    def traced(self, images) -> dict[str, str]:
        """What a set of images carries together, as {tag: reason} (the first reason per tag)."""
        found: dict[str, str] = {}
        for image in images:
            for tag, why in self.trace(image).items():
                found.setdefault(tag, why)
        return found


def likeness_of(root: Path, images) -> dict[str, str]:
    """What these images carry together ({tag: reason}, tags among VIRTUAL, REAL and UNKNOWN), as
    recorded on an asset or an extra when it is issued (sent_likeness). Raises CineRecastError when
    assets.json or the ledger cannot be read."""
    return _Project(root).traced([image for image in images if isinstance(image, str)])


def image_problems(root: Path, images, *, label: str) -> list[str]:
    """Why these images may not be sent as inputs of a generation job (rules 1 to 4 of the module
    docstring), as English sentences, each with what to do next; [] when every image may be sent."""
    images = [image for image in images if isinstance(image, str)]
    if not images:
        return []
    project = _Project(root)
    consent = casting.answer(project.rights, "likeness_consent")
    problems: list[str] = []
    for image in images:
        try:
            if project.under_assets(image) and _key(root, image) not in project.stored():
                problems.append(
                    f"{label}: {image} lies under assets/ but is not a file a done asset stored there (assets list shows "
                    "the stored files); assets/ holds only generation results, so a picture placed there by hand is "
                    "not an input. Generate the picture as an asset with assets add, or use a stored file")
                continue
            if not project.under_assets(image) and _key(root, image) not in project.swap_images:
                problems.append(
                    f"{label}: {image} is not an image of the swap table; a picture that was not generated in this "
                    "project is an input only while swaps[].images in casting.json names it for the element it "
                    "shows, so that the rights answers cast check demands cover it. Name it there and run cast check, "
                    "or generate the picture as an asset with assets add")
                continue
            carried = project.trace(image)
        except CineRecastError as exc:
            problems.append(f"{label}: the generated files cannot be checked ({exc.message}); repair that file, then "
                            "run the command again")
            continue
        real = carried.get(REAL) or carried.get(UNKNOWN)
        if real and consent != "yes":
            problems.append(
                f"{label}: {image} may carry a real person's likeness ({real}), so the generation would carry it too; "
                f"likeness_consent must be 'yes' (it is {consent!r}). Ask the user; if the person consents, record it "
                "with: rights set --item likeness_consent --answer yes; otherwise leave this image out")
        elif VIRTUAL in carried and consent not in ("yes", "not_applicable"):
            problems.append(
                f"{label}: {image} shows a person ({carried[VIRTUAL]}); a person needs likeness_consent 'yes', or "
                f"'not_applicable' for a virtual person (it is {consent!r}). Ask the user and record the answer with "
                "rights set --item likeness_consent")
        backgrounds = project.naming(image, "background")
        if backgrounds and casting.answer(project.rights, "background_rights") is None:
            problems.append(f"{label}: {image} is the swap image of the background element {backgrounds[0]!r} and "
                            "background_rights is not answered; ask the user and record it with: rights set --item "
                            "background_rights")
    return problems


def likeness_verified(root: Path) -> bool:
    """True when no image the film or an extra was made from may carry a real person: no swap image
    of any element and no image input of an issued or done extra carries a real or unknown person
    (trace; an extra's record from its issue counts). False when something cannot be read."""
    from cine_recast import extras  # extras imports this module

    try:
        project = _Project(root)
        if REAL in project.traced(project.swap_images) or UNKNOWN in project.traced(project.swap_images):
            return False
        for item in extras.load_items(root):
            if item.get("state") not in ("issued", "done"):
                continue
            recorded = item.get("sent_likeness")
            carried = recorded if isinstance(recorded, dict) else project.traced(extras.image_inputs(item))
            if REAL in carried or UNKNOWN in carried:
                return False
    except CineRecastError:
        return False
    return True
