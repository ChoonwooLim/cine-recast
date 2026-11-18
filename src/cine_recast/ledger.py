"""Approval and job ledger: the only gate between a plan and paid generation.

State is recomputed from the append-only ledger.jsonl on every call. No cache file,
and existing lines are never rewritten.

Every writer (approve, issue, done, fail) holds an exclusive OS lock on the sidecar file
ledger.lock for its whole read-check-append, so two commands at the same moment cannot both pass
a limit or issue one id twice. The operating system releases the lock when the process ends, so
a killed command leaves no stale lock behind.
"""

import json
import math
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from cine_recast.errors import CineRecastError
from cine_recast.jsonio import append_jsonl
from cine_recast.paths import ProjectPaths

_DECIMALS = 6  # credit sums are rounded so float noise (0.1 + 0.2) never decides a refusal
LOCK_WAIT = 10.0  # seconds a writer waits for ledger.lock before it refuses

if os.name == "nt":
    import msvcrt

    def _try_lock(handle) -> bool:
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True

    def _unlock(handle) -> None:
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
else:
    import fcntl

    def _try_lock(handle) -> bool:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        return True

    def _unlock(handle) -> None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


@contextmanager
def locked(root: Path, wait: float = LOCK_WAIT) -> Iterator[None]:
    """Hold the exclusive lock on the project's ledger.lock, waiting up to `wait` seconds for it.

    Refuses with CineRecastError when another process (or another handle) holds it that long, and
    when the lock file cannot be opened at all (a folder in its place, no write permission).
    """
    try:
        opened = ProjectPaths(Path(root)).ledger_lock.open("a+b")
    except OSError as exc:
        raise CineRecastError(f"the ledger lock file ledger.lock cannot be opened ({exc.strerror or exc}); make sure "
                              "ledger.lock in the project folder is an ordinary file this user can write") from exc
    with opened as handle:
        deadline = time.monotonic() + wait
        while not _try_lock(handle):
            if time.monotonic() >= deadline:
                raise CineRecastError("another cine-recast command is writing the ledger; run one command at a time")
            time.sleep(0.05)
        try:
            yield
        finally:
            _unlock(handle)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _r(value: float) -> float:
    return round(float(value), _DECIMALS)


def _reject_constant(name: str):
    raise ValueError(f"non-standard JSON constant {name}")


def _is_text(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_amount(value, *, positive: bool) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return False
    return value > 0 if positive else value >= 0


# type -> list of (key, check) pairs; every key is required
_SCHEMA = {
    "approval": [("batch", _is_text), ("limit_credits", lambda v: _is_amount(v, positive=True)),
                 ("by", _is_text)],
    "job_issued": [("job_id", _is_text), ("batch", _is_text),
                   ("est_credits", lambda v: _is_amount(v, positive=True))],
    "job_done": [("job_id", _is_text), ("provider_id", _is_text),
                 ("credits", lambda v: _is_amount(v, positive=False))],
    "job_failed": [("job_id", _is_text), ("reason", _is_text),
                   ("credits", lambda v: _is_amount(v, positive=False))],
}


def _invalid_reason(ev) -> str | None:
    if not isinstance(ev, dict):
        return "not a JSON object"
    schema = _SCHEMA.get(ev.get("type")) if isinstance(ev.get("type"), str) else None
    if schema is None:
        return f"unknown event type {ev.get('type')!r}"
    if not isinstance(ev.get("time"), str):
        return "missing or invalid key 'time'"
    for key, check in schema:
        if key not in ev:
            return f"missing key {key!r}"
        if not check(ev[key]):
            return f"invalid value for {key!r}: {ev[key]!r}"
    return None


def _load(root: Path) -> tuple[list[dict], list[str]]:
    """Read ledger.jsonl strictly. Returns (valid events, one problem string per invalid line).

    The file is read as bytes and each line decoded on its own, so a line that is not UTF-8 is one
    more invalid line and the others are still read.
    """
    path = ProjectPaths(Path(root)).ledger_file
    if not path.exists():
        return [], []
    events: list[dict] = []
    problems: list[str] = []
    for number, raw in enumerate(path.read_bytes().split(b"\n"), start=1):
        try:
            line = raw.removesuffix(b"\r").decode("utf-8")
        except UnicodeDecodeError as exc:
            problems.append(f"ledger line {number} is invalid: not UTF-8 text (byte {exc.start} cannot be read)")
            continue
        if not line.strip():
            continue
        try:
            ev = json.loads(line, parse_constant=_reject_constant)
        except ValueError as exc:
            problems.append(f"ledger line {number} is invalid: unreadable JSON ({exc})")
            continue
        reason = _invalid_reason(ev)
        if reason:
            problems.append(f"ledger line {number} is invalid: {reason}")
        else:
            events.append(ev)
    return events, problems


def _events(root: Path) -> list[dict]:
    """Valid events, or CineRecastError if any line is damaged (fail closed)."""
    events, problems = _load(root)
    if problems:
        extra = f" (and {len(problems) - 1} more)" if len(problems) > 1 else ""
        raise CineRecastError(f"{problems[0]}{extra}; the ledger is damaged, repair ledger.jsonl before continuing")
    return events


def _write(root: Path, record: dict) -> dict:
    append_jsonl(ProjectPaths(Path(root)).ledger_file, record)
    return record


def _check_amount(name: str, value: float, *, positive: bool) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise CineRecastError(f"{name} must be a finite number: {value!r}")
    if (value <= 0) if positive else (value < 0):
        raise CineRecastError(f"{name} must be {'greater than' if positive else 'at least'} 0: {value}")
    return float(value)


def _check_text(name: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CineRecastError(f"{name} must not be empty")
    return value


def _state(events: list[dict]) -> tuple[dict[str, float], dict[str, dict]]:
    """Return (limits by batch, jobs by id). The first issue and the first finish of a job win."""
    limits: dict[str, float] = {}
    jobs: dict[str, dict] = {}
    for ev in events:
        kind = ev.get("type")
        if kind == "approval":
            limits[ev["batch"]] = limits.get(ev["batch"], 0.0) + float(ev["limit_credits"])
        elif kind == "job_issued":
            jobs.setdefault(ev["job_id"], {"batch": ev["batch"], "est": float(ev["est_credits"]),
                                           "actual": None, "finish": None})
        elif kind in ("job_done", "job_failed"):
            job = jobs.get(ev["job_id"])
            if job is not None and job["actual"] is None:
                job["actual"] = float(ev["credits"])
                job["finish"] = "done" if kind == "job_done" else "failed"
    return limits, jobs


def _status(limits: dict[str, float], jobs: dict[str, dict], batch: str) -> dict:
    limit = _r(limits.get(batch, 0.0))
    committed = _r(sum(j["est"] if j["actual"] is None else j["actual"]
                       for j in jobs.values() if j["batch"] == batch))
    return {"limit": limit, "committed": committed, "remaining": _r(limit - committed)}


def approve(root: Path, batch: str, limit_credits: float, by: str, note: str = "", *,
            lock_wait: float = LOCK_WAIT) -> dict:
    """Record a credit approval for a batch. Refused while the ledger does not validate."""
    _check_text("batch", batch)
    _check_amount("limit_credits", limit_credits, positive=True)
    _check_text("by", by)
    with locked(root, lock_wait):
        _events(root)  # a damaged ledger is repaired before anything is appended to it
        return _write(root, {"type": "approval", "time": _now(), "batch": batch,
                             "limit_credits": float(limit_credits), "by": by, "note": note})


def issue(root: Path, job_id: str, batch: str, est_credits: float, *, lock_wait: float = LOCK_WAIT) -> dict:
    _check_text("job_id", job_id)
    _check_text("batch", batch)
    _check_amount("est_credits", est_credits, positive=True)
    with locked(root, lock_wait):
        limits, jobs = _state(_events(root))
        if batch not in limits:
            raise CineRecastError(f"no approval recorded for batch {batch!r}; approve a credit limit first")
        if job_id in jobs:
            raise CineRecastError(f"job already issued: {job_id}")
        status = _status(limits, jobs, batch)
        if not (_r(status["committed"] + est_credits) <= status["limit"]):
            raise CineRecastError(
                f"batch {batch!r} would exceed its approved limit: committed {status['committed']} "
                f"+ estimate {_r(est_credits)} > limit {status['limit']}")
        return _write(root, {"type": "job_issued", "time": _now(), "job_id": job_id, "batch": batch,
                             "est_credits": float(est_credits)})


def _require_open_job(root: Path, job_id: str) -> None:
    _, jobs = _state(_events(root))
    job = jobs.get(job_id)
    if job is None:
        raise CineRecastError(f"job was never issued: {job_id}")
    if job["actual"] is not None:
        raise CineRecastError(f"job already finished: {job_id}")


def done(root: Path, job_id: str, provider_id: str, credits: float, *, lock_wait: float = LOCK_WAIT) -> dict:
    _check_text("job_id", job_id)
    _check_text("provider_id", provider_id)
    _check_amount("credits", credits, positive=False)
    with locked(root, lock_wait):
        _require_open_job(root, job_id)
        return _write(root, {"type": "job_done", "time": _now(), "job_id": job_id,
                             "provider_id": provider_id, "credits": float(credits)})


def fail(root: Path, job_id: str, reason: str, credits: float = 0.0, *, lock_wait: float = LOCK_WAIT) -> dict:
    _check_text("job_id", job_id)
    _check_text("reason", reason)
    _check_amount("credits", credits, positive=False)
    with locked(root, lock_wait):
        _require_open_job(root, job_id)
        return _write(root, {"type": "job_failed", "time": _now(), "job_id": job_id,
                             "reason": reason, "credits": float(credits)})


def batch_status(root: Path, batch: str) -> dict:
    limits, jobs = _state(_events(root))
    return _status(limits, jobs, batch)


def job_states(root: Path) -> dict[str, str]:
    """Job id -> "issued", "done" or "failed" for every issued job (first issue and first finish win).

    Fails closed like batch_status: a damaged ledger raises CineRecastError.
    """
    _, jobs = _state(_events(root))
    return {job_id: job["finish"] or "issued" for job_id, job in jobs.items()}


def audit(root: Path) -> dict:
    events, problems = _load(root)
    limits, jobs = _state(events)
    seen_issued: set[str] = set()
    finished: set[str] = set()
    provider_ids: list[str] = []
    for ev in events:
        kind = ev.get("type")
        job_id = ev.get("job_id")
        if kind == "job_issued":
            if job_id in seen_issued:
                problems.append(f"job {job_id} was issued more than once")
            seen_issued.add(job_id)
            if ev.get("batch") not in limits:
                problems.append(f"job {job_id} was issued in batch {ev.get('batch')!r} without an approval")
        elif kind in ("job_done", "job_failed"):
            if job_id not in seen_issued:
                problems.append(f"job {job_id} finished without ever being issued")
            elif job_id in finished:
                problems.append(f"job {job_id} finished more than once")
            finished.add(job_id)
            if kind == "job_done":
                pid = ev.get("provider_id")
                if pid not in provider_ids:
                    provider_ids.append(pid)
    batches = {b: _status(limits, jobs, b) for b in sorted(set(limits) | {j["batch"] for j in jobs.values()})}
    for name, st in batches.items():
        if not (st["committed"] <= st["limit"]):
            problems.append(f"batch {name!r} is over its approved limit: committed {st['committed']} > limit {st['limit']}")
    totals = {key: _r(sum(st[key] for st in batches.values())) for key in ("limit", "committed", "remaining")}
    return {"ok": not problems, "problems": problems, "batches": batches, "totals": totals,
            "provider_ids": provider_ids}
