"""Create a project folder and keep its project.json."""

import shutil
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path

from cine_recast.errors import CineRecastError
from cine_recast.ffmpeg import ToolNotFound, stream_facts
from cine_recast.jsonio import read_project_json, write_json
from cine_recast.paths import ProjectPaths
from cine_recast.textrules import refuse_han

RIGHTS_ITEMS = ("reference_rights", "likeness_consent", "background_rights")
RIGHT_ANSWERS = ("yes", "no", "unknown", "not_applicable")

DEFAULT_THRESHOLDS = {"camera_min": 0.6, "motion_min": 0.5, "color_min": 0.6, "flicker_ratio_max": 2.0}

# ffprobe format_name tokens of the containers a reference may come in: the mov/mp4 family,
# matroska/webm, avi, mpegts and mxf.
REFERENCE_CONTAINERS = frozenset({"mov", "mp4", "m4a", "3gp", "3g2", "mj2", "matroska", "webm", "avi", "mpegts",
                                  "mxf"})
MAX_RATE_SPREAD = Fraction(2, 100)   # avg_frame_rate further than this from r_frame_rate: a variable rate


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def reference_problem(facts: dict) -> str | None:
    """Why a file with these ffmpeg.stream_facts cannot be a reference, or None when it can.

    It needs a video stream in one of REFERENCE_CONTAINERS, no rotation, square (or unstated)
    pixels and a constant frame rate: avg_frame_rate within 2 percent of r_frame_rate (not judged
    when either is unknown).
    """
    if not facts["video"]:
        return "it has no video stream"
    if not set(facts["formats"]) & REFERENCE_CONTAINERS:
        return f"its container ({','.join(facts['formats']) or 'unknown'}) is not mp4/mov, mkv/webm, avi, mpegts or mxf"
    if facts["rotation"]:
        return f"it carries rotation metadata ({facts['rotation']:g} degrees)"
    if facts["sar"] is not None and facts["sar"] != 1:
        return f"its pixels are not square (sample aspect ratio {facts['sar'].numerator}:{facts['sar'].denominator})"
    avg, rate = facts["avg_rate"], facts["r_rate"]
    if avg > 0 and rate > 0 and abs(avg - rate) / rate > MAX_RATE_SPREAD:
        return f"its frame rate is variable (average {float(avg):.3f} fps, stream rate {float(rate):.3f} fps)"
    return None


def _check_reference(reference: Path) -> None:
    """Refuse a reference that reference_problem rejects or ffprobe cannot read. A missing ffprobe is
    passed through as it is: re-encoding the video would not help."""
    try:
        facts = stream_facts(reference)
    except ToolNotFound:
        raise
    except CineRecastError:
        problem = "ffprobe cannot read it"
    else:
        problem = reference_problem(facts)
    if problem:
        raise CineRecastError(f"reference video {reference} cannot be used: {problem}; re-encode it to a plain "
                              "constant-frame-rate mp4 with square pixels and no rotation, then run init again")


def init_project(root: Path, reference: Path, name: str) -> ProjectPaths:
    """Create the project folder, copy the reference video into it and write project.json.

    The reference is probed first (reference_problem); a refusal leaves nothing behind.
    """
    root, reference = Path(root), Path(reference)
    refuse_han("name", name)
    if not reference.is_file():
        raise CineRecastError(f"reference video not found: {reference}")
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        raise CineRecastError(f"project folder exists and is not empty: {root}")
    _check_reference(reference)
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
    return read_project_json(pp.root, pp.project_file, {"reference": str, "rights": dict})


def set_right(root: Path, item: str, answer: str, note: str = "") -> None:
    if item not in RIGHTS_ITEMS:
        raise CineRecastError(f"unknown rights item: {item}; expected one of {', '.join(RIGHTS_ITEMS)}")
    if answer not in RIGHT_ANSWERS:
        raise CineRecastError(f"unknown answer: {answer}; expected one of {', '.join(RIGHT_ANSWERS)}")
    refuse_han("note", note)
    data = load_project(root)
    data["rights"][item] = {"answer": answer, "note": note, "answered_at": _now()}
    write_json(ProjectPaths(Path(root)).project_file, data)
