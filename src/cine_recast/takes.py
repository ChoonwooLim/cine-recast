"""Takes: every result returned for a unit, kept in its own numbered folder and never overwritten."""

import re
import shutil
from pathlib import Path

from cine_recast import plan
from cine_recast.errors import CineRecastError
from cine_recast.jsonio import write_json
from cine_recast.paths import ProjectPaths

_TAKE_ID = re.compile(r"^t(\d{3,})$")


def _unit_folder(root: Path, unit_id: str) -> Path:
    """takes/<unit_id>, the parent of every ProjectPaths.take_dir of the unit."""
    return ProjectPaths(Path(root)).take_dir(unit_id, "t001").parent


def _numbered(folder: Path) -> dict[int, str]:
    """Take folders by number."""
    if not folder.is_dir():
        return {}
    found: dict[int, str] = {}
    for path in folder.iterdir():
        match = _TAKE_ID.match(path.name)
        if match and path.is_dir():
            found[int(match.group(1))] = path.name
    return found


def list_takes(root: Path, unit_id: str) -> list[str]:
    found = _numbered(_unit_folder(root, unit_id))
    return [found[number] for number in sorted(found)]


def new_take(root: Path, unit_id: str, source_file: Path, meta: dict) -> str:
    """Store a copy of source_file as a new take of the unit and return its id ("t001", "t002", ...).

    The number follows the highest existing take, and the folder is created exclusively, so an
    existing take is never written to.
    """
    root = Path(root)
    plan.unit(root, unit_id)
    source = Path(source_file)
    if not source.is_file():
        raise CineRecastError(f"take source file not found for unit {unit_id}: {source}")
    folder = _unit_folder(root, unit_id)
    folder.mkdir(parents=True, exist_ok=True)
    number = max(_numbered(folder), default=0) + 1
    while True:
        take_id = f"t{number:03d}"
        target = folder / take_id
        try:
            target.mkdir()
            break
        except FileExistsError:
            number += 1
    try:
        shutil.copyfile(source, target / f"raw{source.suffix.lower()}")
        write_json(target / "meta.json", meta)
    except BaseException:
        shutil.rmtree(target, ignore_errors=True)  # only the incomplete folder made by this call
        raise
    return take_id


def select(root: Path, unit_id: str, take_id: str) -> None:
    """Record take_id as the unit's selected take in plan.json."""
    root = Path(root)
    data = plan.load(root)
    target = next((u for u in data["units"] if isinstance(u, dict) and u.get("id") == unit_id), None)
    if target is None:
        raise CineRecastError(f"unknown unit: {unit_id}")
    takes = list_takes(root, unit_id)
    if take_id not in takes:
        raise CineRecastError(f"unit {unit_id} has no take {take_id}; its takes: {', '.join(takes) or 'none'}")
    target["selected_take"] = take_id
    write_json(ProjectPaths(root).plan_file, data)
