"""Approval and job ledger: the only gate between a plan and paid generation.

State is recomputed from the append-only ledger.jsonl on every call. No cache file,
and existing lines are never rewritten.
"""

import math
from datetime import datetime, timezone
from pathlib import Path

from cine_recast.errors import CineRecastError
from cine_recast.jsonio import append_jsonl, read_jsonl
from cine_recast.paths import ProjectPaths

_DECIMALS = 6  # credit sums are rounded so float noise (0.1 + 0.2) never decides a refusal


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _r(value: float) -> float:
    return round(float(value), _DECIMALS)


def _events(root: Path) -> list[dict]:
    return read_jsonl(ProjectPaths(Path(root)).ledger_file)


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
            jobs.setdefault(ev["job_id"], {"batch": ev["batch"], "est": float(ev["est_credits"]), "actual": None})
        elif kind in ("job_done", "job_failed"):
            job = jobs.get(ev["job_id"])
            if job is not None and job["actual"] is None:
                job["actual"] = float(ev["credits"])
    return limits, jobs


def _status(limits: dict[str, float], jobs: dict[str, dict], batch: str) -> dict:
    limit = _r(limits.get(batch, 0.0))
    committed = _r(sum(j["est"] if j["actual"] is None else j["actual"]
                       for j in jobs.values() if j["batch"] == batch))
    return {"limit": limit, "committed": committed, "remaining": _r(limit - committed)}


def approve(root: Path, batch: str, limit_credits: float, by: str, note: str = "") -> dict:
    _check_text("batch", batch)
    _check_amount("limit_credits", limit_credits, positive=True)
    _check_text("by", by)
    return _write(root, {"type": "approval", "time": _now(), "batch": batch,
                         "limit_credits": float(limit_credits), "by": by, "note": note})


def issue(root: Path, job_id: str, batch: str, est_credits: float) -> dict:
    _check_text("job_id", job_id)
    _check_text("batch", batch)
    _check_amount("est_credits", est_credits, positive=True)
    limits, jobs = _state(_events(root))
    if batch not in limits:
        raise CineRecastError(f"no approval recorded for batch {batch!r}; approve a credit limit first")
    if job_id in jobs:
        raise CineRecastError(f"job already issued: {job_id}")
    status = _status(limits, jobs, batch)
    if _r(status["committed"] + est_credits) > status["limit"]:
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


def done(root: Path, job_id: str, provider_id: str, credits: float) -> dict:
    _check_text("job_id", job_id)
    _check_text("provider_id", provider_id)
    _check_amount("credits", credits, positive=False)
    _require_open_job(root, job_id)
    return _write(root, {"type": "job_done", "time": _now(), "job_id": job_id,
                         "provider_id": provider_id, "credits": float(credits)})


def fail(root: Path, job_id: str, reason: str, credits: float = 0.0) -> dict:
    _check_text("job_id", job_id)
    _check_text("reason", reason)
    _check_amount("credits", credits, positive=False)
    _require_open_job(root, job_id)
    return _write(root, {"type": "job_failed", "time": _now(), "job_id": job_id,
                         "reason": reason, "credits": float(credits)})


def batch_status(root: Path, batch: str) -> dict:
    limits, jobs = _state(_events(root))
    return _status(limits, jobs, batch)


def audit(root: Path) -> dict:
    events = _events(root)
    limits, jobs = _state(events)
    problems: list[str] = []
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
        if st["committed"] > st["limit"]:
            problems.append(f"batch {name!r} is over its approved limit: committed {st['committed']} > limit {st['limit']}")
    return {"ok": not problems, "problems": problems, "batches": batches, "provider_ids": provider_ids}
