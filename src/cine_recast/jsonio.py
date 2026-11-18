import json
import os
import tempfile
from pathlib import Path
from typing import Any

from cine_recast.errors import CineRecastError

_TYPE_NAMES = {dict: "an object", list: "a list", str: "a string", bool: "true or false", int: "a whole number",
               float: "a number"}


def read_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _kind(value: Any) -> str:
    """How a JSON value reads in a message: "a list", "null", ..."""
    if value is None:
        return "null"
    return next((name for t, name in _TYPE_NAMES.items() if isinstance(value, t)), type(value).__name__)


def _is(value: Any, types: tuple[type, ...]) -> bool:
    if isinstance(value, bool):          # bool is an int in Python, never in a JSON file
        return bool in types
    return isinstance(value, types)


def _expected(types: tuple[type, ...]) -> str:
    if float in types:
        types = tuple(t for t in types if t is not int)
    return " or ".join(_TYPE_NAMES[t] for t in types)


def read_project_json(root: Path, path: Path, required: dict[str, type | tuple[type, ...]] | None = None) -> Any:
    """Read one of the project's JSON files, refusing a damaged one with a message that names it.

    A UTF-8 byte order mark is accepted. Bytes that are not UTF-8 and invalid JSON raise
    CineRecastError naming the file relative to the project folder (with the line and column for
    invalid JSON). With `required` (key -> type or tuple of types), the top level must be an object
    holding each key with a value of that type, or CineRecastError names the file and the key.
    Without it any JSON value is returned and the caller checks the shape.
    """
    path = Path(path)
    try:
        name = path.relative_to(root).as_posix()
    except ValueError:
        name = str(path)
    try:
        raw = path.read_bytes()
    except FileNotFoundError as exc:
        raise CineRecastError(f"{name} not found") from exc
    except OSError as exc:
        raise CineRecastError(f"{name} cannot be read: {exc.strerror or exc}") from exc
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise CineRecastError(f"{name} is not UTF-8 text (byte {exc.start} cannot be read); save it as UTF-8") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CineRecastError(f"{name} is not valid JSON: {exc.msg} at line {exc.lineno}, column {exc.colno}") from exc
    if required is None:
        return data
    if not isinstance(data, dict):
        raise CineRecastError(f"{name} must hold a JSON object at the top level, not {_kind(data)}")
    for key, types in required.items():
        types = types if isinstance(types, tuple) else (types,)
        if key not in data:
            raise CineRecastError(f"{name} is missing the key {key!r}")
        if not _is(data[key], types):
            raise CineRecastError(f"{name}: the key {key!r} must be {_expected(types)}, not {_kind(data[key])}")
    return data


def write_json(path: Path, data: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
        os.replace(tmp_name, path)
    except BaseException:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise


def append_jsonl(path: Path, record: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    path = Path(path)
    if not path.exists():
        return []
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(json.loads(line))
    return records
