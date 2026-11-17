"""Create a project folder and keep its project.json."""

import shutil
from datetime import datetime, timezone
from pathlib import Path

from cine_recast.errors import CineRecastError
from cine_recast.jsonio import read_json, write_json
from cine_recast.paths import ProjectPaths

RIGHTS_ITEMS = ("reference_rights", "likeness_consent", "background_rights")
RIGHT_ANSWERS = ("yes", "no", "unknown", "not_applicable")

DEFAULT_THRESHOLDS = {"camera_min": 0.6, "motion_min": 0.5, "color_min": 0.6, "flicker_ratio_max": 2.0}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def init_project(root: Path, reference: Path, name: str) -> ProjectPaths:
    """Create the project folder, copy the reference video into it and write project.json."""
    root, reference = Path(root), Path(reference)
    if not reference.is_file():
        raise CineRecastError(f"reference video not found: {reference}")
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        raise CineRecastError(f"project folder exists and is not empty: {root}")
    pp = ProjectPaths(root)
    pp.reference_dir.mkdir(parents=True, exist_ok=True)
    target = pp.reference_dir / f"source{reference.suffix.lower()}"
    shutil.copyfile(reference, target)
    write_json(pp.project_file, {
        "schema": 1,
        "name": name,
        "created_at": _now(),
        "reference": target.relative_to(root).as_posix(),
        "rights": {item: None for item in RIGHTS_ITEMS},
        "thresholds": dict(DEFAULT_THRESHOLDS),
    })
    return pp


def load_project(root: Path) -> dict:
    pp = ProjectPaths(Path(root))
    if not pp.project_file.is_file():
        raise CineRecastError(f"not a project folder (project.json missing): {root}")
    return read_json(pp.project_file)


def set_right(root: Path, item: str, answer: str, note: str = "") -> None:
    if item not in RIGHTS_ITEMS:
        raise CineRecastError(f"unknown rights item: {item}; expected one of {', '.join(RIGHTS_ITEMS)}")
    if answer not in RIGHT_ANSWERS:
        raise CineRecastError(f"unknown answer: {answer}; expected one of {', '.join(RIGHT_ANSWERS)}")
    data = load_project(root)
    data["rights"][item] = {"answer": answer, "note": note, "answered_at": _now()}
    write_json(ProjectPaths(Path(root)).project_file, data)
