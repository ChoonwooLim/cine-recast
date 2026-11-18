"""The life cycle shared by every paid job: video jobs in plan.json, asset jobs in assets.json and
sound jobs in sounds.json.

An entry moves planned -> quoted -> issued -> done or failed.

- The ledger is the authority for state: reconcile moves each entry forward to the state the
  ledger records for its id, never backwards.
- A quote is bound to what it priced: digest fingerprints a payload without its prompt, and an
  entry whose payload changed since it was quoted must be quoted again.
- An entry reaches the agent only through issue_in_order, which hands it to ledger.issue first and
  stops at the first refusal; the payloads issued before it travel on IssueRefused.
- Only an issued entry is recorded: finish records done in the ledger after the caller stored the
  returned files and undoes that storing when the ledger refuses; fail records the failure in the
  ledger first.

`noun` ("job", "asset", "sound") names the entry in every message.
"""

import hashlib
import json
import math
import re
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
from pathlib import Path

from cine_recast import ledger
from cine_recast.errors import CineRecastError
from cine_recast.textrules import han_problem

UNISSUED = ("planned", "quoted")          # states in which an entry has not been handed to the ledger
SENT = ("issued", "done", "failed")       # states in which an entry's payload has been sent
RANK = {"planned": 0, "quoted": 1, "issued": 2, "done": 3, "failed": 3}


class IssueRefused(CineRecastError):
    """issue_in_order stopped at an entry. `issued` holds the payloads it issued before that."""

    def __init__(self, message: str, issued: list[dict]) -> None:
        super().__init__(message)
        self.issued = issued


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def is_text(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def is_number(value) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)


def resolve(root: Path, path: str) -> Path:
    """Paths in project files are relative to the project folder unless absolute."""
    return (Path(root) / path).resolve()


def highest(pattern: re.Pattern, ids: Iterable) -> int:
    """The largest number captured by pattern among ids, or 0."""
    return max((int(m.group(1)) for i in ids if isinstance(i, str) and (m := pattern.match(i))), default=0)


def reconcile(root: Path, entries: list) -> None:
    """Move each entry forward to the state the ledger records; never backwards, estimates untouched.

    ledger.job_states fails closed, so a damaged ledger stops every read until it is repaired.
    """
    states = ledger.job_states(root)
    for entry in entries:
        proven = states.get(entry.get("id")) if isinstance(entry, dict) else None
        if proven is not None and RANK.get(entry.get("state"), -1) <= RANK[proven]:
            entry["state"] = proven


def digest(payload: dict) -> str:
    """Fingerprint of what a quote priced: every param but the prompt, and the medias as (role, path).

    The prompt is left out on purpose: it does not change the price.
    """
    priced = {"params": {k: v for k, v in payload["params"].items() if k != "prompt"},
              "medias": [[m["role"], m["path"]] for m in payload["medias"]]}
    return hashlib.sha256(json.dumps(priced, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def stale_quote(entry: dict, payload: dict, *, noun: str, command: str) -> str | None:
    """None while the entry still has the payload its quote priced, else the problem as a sentence."""
    if entry.get("quote_digest") == digest(payload):
        return None
    return (f"{noun} {entry.get('id')}: its parameters or inputs changed since it was quoted; "
            f"quote it again with {command}")


def quote(entry: dict, credits: float, payload_of: Callable[[], dict], *, noun: str) -> dict:
    """Record the quoted cost of a planned or quoted entry and bind it to the payload payload_of()
    returns (called only after the state and the credits are accepted). The entry becomes quoted;
    a new quote replaces the estimate and the binding at any time before it is issued. The caller saves.
    """
    if entry.get("state") not in UNISSUED:
        raise CineRecastError(f"{noun} {entry.get('id')} is {entry.get('state')}; its quote can no longer change")
    if not is_number(credits) or credits <= 0:
        raise CineRecastError(f"{noun} {entry.get('id')}: credits must be a finite number greater than 0: {credits!r}")
    payload = payload_of()
    entry["est_credits"] = float(credits)
    entry["quote_digest"] = digest(payload)
    entry["state"] = "quoted"
    return entry


def issue_in_order(root: Path, batch: str, queue: list[dict], payload_of: Callable[[dict], dict],
                   save: Callable[[], None], *, noun: str, command: str) -> list[dict]:
    """Hand each queued entry to ledger.issue in order and return the payloads to run.

    Each entry must still have the payload its quote priced. The params it will be sent with
    (sent_params) are saved before the ledger is asked, so an entry the ledger issued just before a
    save failed still shows them; if the ledger refuses, they are removed and saved again. An entry
    the ledger accepts becomes issued and is saved at once. Stops with IssueRefused at the first
    entry refused; the entries issued before it stay issued and travel on the exception.
    """
    issued: list[dict] = []

    def refused(entry: dict, reason: str) -> IssueRefused:
        before = f" (issued before it in this call: {', '.join(p['job_id'] for p in issued)})" if issued else ""
        return IssueRefused(f"{noun} {entry['id']} was not issued: {reason}{before}", issued)

    for entry in queue:
        payload = payload_of(entry)
        stale = stale_quote(entry, payload, noun=noun, command=command)
        if stale:
            raise refused(entry, stale)
        entry["sent_params"] = payload["params"]
        save()
        try:
            ledger.issue(root, entry["id"], batch, entry.get("est_credits"))
        except CineRecastError as exc:
            del entry["sent_params"]
            save()
            raise refused(entry, exc.message) from exc
        entry["state"] = "issued"
        save()
        issued.append(payload)
    return issued


def require_issued(entry: dict, *, noun: str) -> dict:
    """The entry, if it is issued; only an issued entry can be recorded as done or failed."""
    if entry.get("state") != "issued":
        raise CineRecastError(f"{noun} {entry.get('id')} is {entry.get('state')}, not issued; "
                              f"only an issued {noun} can be recorded")
    return entry


def require_result(entry: dict, provider_id: str, credits: float, *, noun: str) -> None:
    """Refuse a result without a provider id or with credits that are not a finite number of at least 0."""
    if not is_text(provider_id):
        raise CineRecastError(f"{noun} {entry.get('id')}: provider_id must not be empty")
    problem = han_problem("provider_id", provider_id)
    if problem:
        raise CineRecastError(f"{noun} {entry.get('id')}: {problem}")
    if not is_number(credits) or credits < 0:
        raise CineRecastError(f"{noun} {entry.get('id')}: credits must be a finite number of at least 0: {credits!r}")


def finish(root: Path, entry: dict, provider_id: str, credits: float, undo: Callable[[], None],
           save: Callable[[], None]) -> None:
    """Record an issued entry as done in the ledger once the caller has stored its returned files.

    If the ledger refuses, undo() removes what the caller stored, so a refused call leaves nothing
    behind. Otherwise the entry becomes done and save() is called.
    """
    try:
        ledger.done(root, entry["id"], provider_id, credits)
    except BaseException:
        undo()
        raise
    entry["state"] = "done"
    save()


def batch_problem(label: str, batch) -> str | None:
    """Why an entry's batch cannot be used (empty, or holding Han characters), or None."""
    if not is_text(batch):
        return f"{label}: batch must not be empty."
    problem = han_problem("batch", batch)
    return f"{label}: {problem}." if problem else None


def fail(root: Path, entry: dict, reason: str, credits: float, save: Callable[[], None], *, noun: str) -> dict:
    """Record an issued entry as failed in the ledger (credits spent, if any), save, and return it."""
    require_issued(entry, noun=noun)
    ledger.fail(root, entry["id"], reason, credits)
    entry["state"] = "failed"
    save()
    return entry
