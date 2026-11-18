import json
import threading
import time

import pytest

from cine_recast import ledger as ledger_module
from cine_recast.cli import main
from cine_recast.errors import CineRecastError
from cine_recast.jsonio import append_jsonl
from cine_recast.ledger import approve, audit, batch_status, done, fail, issue, job_states
from cine_recast.paths import ProjectPaths


def test_issue_requires_approval(tmp_path):
    with pytest.raises(CineRecastError, match="approval"):
        issue(tmp_path, "j0001", "draft-1", 10.0)


def test_budget_is_enforced(tmp_path):
    approve(tmp_path, "draft-1", 100.0, "owner")
    issue(tmp_path, "j0001", "draft-1", 60.0)
    with pytest.raises(CineRecastError, match="limit"):
        issue(tmp_path, "j0002", "draft-1", 41.0)
    issue(tmp_path, "j0002", "draft-1", 40.0)
    assert batch_status(tmp_path, "draft-1") == {"limit": 100.0, "committed": 100.0, "remaining": 0.0}


def test_actual_credits_replace_the_estimate(tmp_path):
    approve(tmp_path, "b", 100.0, "owner")
    issue(tmp_path, "j0001", "b", 60.0); done(tmp_path, "j0001", "prov-1", 45.0)
    assert batch_status(tmp_path, "b")["remaining"] == 55.0
    issue(tmp_path, "j0002", "b", 55.0); fail(tmp_path, "j0002", "timeout")
    assert batch_status(tmp_path, "b")["remaining"] == 55.0


def test_second_approval_raises_the_limit(tmp_path):
    approve(tmp_path, "b", 50.0, "owner"); approve(tmp_path, "b", 30.0, "owner")
    assert batch_status(tmp_path, "b")["limit"] == 80.0


@pytest.mark.parametrize("call", [
    lambda r: issue(r, "j0001", "b", 10.0),           # duplicate issue
    lambda r: issue(r, "j0009", "b", 0.0),            # non-positive estimate
    lambda r: done(r, "j0009", "p", 1.0),             # never issued
    lambda r: (done(r, "j0001", "p", 1.0), done(r, "j0001", "p", 1.0)),   # finished twice
    lambda r: approve(r, "b", 0.0, "owner"),
    lambda r: approve(r, "b", 10.0, ""),
])
def test_rejections(tmp_path, call):
    approve(tmp_path, "b", 100.0, "owner"); issue(tmp_path, "j0001", "b", 10.0)
    with pytest.raises(CineRecastError):
        call(tmp_path)


def test_audit(tmp_path):
    approve(tmp_path, "b", 100.0, "owner"); issue(tmp_path, "j0001", "b", 10.0); done(tmp_path, "j0001", "prov-1", 9.0)
    assert audit(tmp_path) == {"ok": True, "problems": [], "provider_ids": ["prov-1"],
                               "batches": {"b": {"limit": 100.0, "committed": 9.0, "remaining": 91.0}},
                               "totals": {"limit": 100.0, "committed": 9.0, "remaining": 91.0}}
    append_jsonl(ProjectPaths(tmp_path).ledger_file,
                 {"type": "job_done", "time": "x", "job_id": "j0777", "provider_id": "prov-x", "credits": 500.0})
    report = audit(tmp_path)
    assert report["ok"] is False and any("j0777" in p for p in report["problems"])


# --- extra guards beyond the brief's list ---

def test_float_sums_do_not_refuse_an_exact_fit(tmp_path):
    approve(tmp_path, "b", 0.3, "owner")
    issue(tmp_path, "j1", "b", 0.1)
    issue(tmp_path, "j2", "b", 0.2)
    assert batch_status(tmp_path, "b")["remaining"] == 0.0


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1.0])
def test_non_finite_or_negative_amounts_refused(tmp_path, bad):
    with pytest.raises(CineRecastError):
        approve(tmp_path, "b", bad, "owner")
    approve(tmp_path, "b", 100.0, "owner")
    with pytest.raises(CineRecastError):
        issue(tmp_path, "j1", "b", bad)
    issue(tmp_path, "j1", "b", 10.0)
    with pytest.raises(CineRecastError):
        done(tmp_path, "j1", "p", bad)
    with pytest.raises(CineRecastError):
        fail(tmp_path, "j1", "r", bad)


def test_refused_calls_write_nothing(tmp_path):
    approve(tmp_path, "b", 10.0, "owner")
    before = ProjectPaths(tmp_path).ledger_file.read_text(encoding="utf-8")
    with pytest.raises(CineRecastError):
        issue(tmp_path, "j1", "b", 11.0)
    assert ProjectPaths(tmp_path).ledger_file.read_text(encoding="utf-8") == before


def test_audit_flags_overspent_batch_and_unissued_failure(tmp_path):
    approve(tmp_path, "b", 10.0, "owner"); issue(tmp_path, "j1", "b", 10.0)
    led = ProjectPaths(tmp_path).ledger_file
    append_jsonl(led, {"type": "job_done", "time": "x", "job_id": "j1", "provider_id": "p", "credits": 25.0})
    append_jsonl(led, {"type": "job_failed", "time": "x", "job_id": "j9", "reason": "r", "credits": 0.0})
    problems = audit(tmp_path)["problems"]
    assert any("batch 'b' is over its approved limit" in p for p in problems)
    assert any("j9" in p for p in problems)


def test_overspent_job_shows_negative_remaining_and_blocks_issue(tmp_path):
    approve(tmp_path, "b", 100.0, "owner")
    issue(tmp_path, "j1", "b", 10.0)
    done(tmp_path, "j1", "p", 150.0)
    assert batch_status(tmp_path, "b") == {"limit": 100.0, "committed": 150.0, "remaining": -50.0}
    with pytest.raises(CineRecastError, match="limit"):
        issue(tmp_path, "j2", "b", 1.0)
    report = audit(tmp_path)
    assert report["ok"] is False
    assert any("batch 'b' is over its approved limit" in p for p in report["problems"])


def _raw(root, text):
    path = ProjectPaths(root).ledger_file
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


_BAD_LINES = {
    "nan_credits": '{"type": "job_done", "time": "x", "job_id": "j1", "provider_id": "p", "credits": NaN}\n',
    "infinite_limit": '{"type": "approval", "time": "x", "batch": "b", "limit_credits": Infinity, "by": "o", "note": ""}\n',
    "negative_credits": '{"type": "job_done", "time": "x", "job_id": "j1", "provider_id": "p", "credits": -500.0}\n',
    "misspelled_type": '{"type": "job_isued", "time": "x", "job_id": "j2", "batch": "b", "est_credits": 90.0}\n',
    "missing_key": '{"type": "job_issued", "time": "x", "job_id": "j2", "batch": "b"}\n',
    "string_amount": '{"type": "job_issued", "time": "x", "job_id": "j2", "batch": "b", "est_credits": "5"}\n',
    "bool_amount": '{"type": "job_failed", "time": "x", "job_id": "j1", "reason": "r", "credits": true}\n',
    "empty_id": '{"type": "job_issued", "time": "x", "job_id": "", "batch": "b", "est_credits": 5.0}\n',
    "not_an_object": '[1, 2]\n',
    "partial_last_line": '{"type": "job_issued", "time": "x", "job_id": "j2", "ba',
}


@pytest.mark.parametrize("bad", list(_BAD_LINES))
def test_damaged_ledger_fails_closed(tmp_path, bad):
    approve(tmp_path, "b", 100.0, "owner")
    issue(tmp_path, "j1", "b", 10.0)
    _raw(tmp_path, _BAD_LINES[bad])
    for call in (lambda: issue(tmp_path, "j9", "b", 1.0),
                 lambda: done(tmp_path, "j1", "p", 1.0),
                 lambda: fail(tmp_path, "j1", "r"),
                 lambda: batch_status(tmp_path, "b")):
        with pytest.raises(CineRecastError, match="line 3"):
            call()
    report = audit(tmp_path)
    assert report["ok"] is False
    assert any("line 3" in p for p in report["problems"])


@pytest.mark.parametrize("char", ["\u2028", "\u2029", "\u0085"])
def test_unicode_line_separators_in_text_do_not_break_the_ledger(tmp_path, char):
    approve(tmp_path, "b", 100.0, "owner", note=f"a{char}b")
    issue(tmp_path, "j1", "b", 10.0)
    fail(tmp_path, "j1", f"x{char}y")
    report = audit(tmp_path)
    assert report["ok"] is True and report["problems"] == []


def test_a_line_that_is_not_utf8_is_one_more_invalid_line(tmp_path):
    approve(tmp_path, "b", 100.0, "owner")
    with ProjectPaths(tmp_path).ledger_file.open("ab") as handle:
        handle.write(b'{"type": "job_failed", "time": "x", "job_id": "j1", "reason": "\xff\xfe", "credits": 0}\n')
    report = audit(tmp_path)
    assert report["ok"] is False and any(p.startswith("ledger line 2 is invalid") for p in report["problems"])
    with pytest.raises(CineRecastError, match="line 2"):
        batch_status(tmp_path, "b")


def test_cli_audit_and_approve_on_a_ledger_with_bytes_that_are_not_utf8(project3, capsys):
    root = str(project3.root)
    project3.ledger_file.write_bytes(b'{"type": "approval", "time": "x", "batch": "b", "limit_credits": 50, '
                                     b'"by": "o"}\n\xff\xfe broken\n')
    before = project3.ledger_file.read_bytes()
    capsys.readouterr()
    assert main(["audit", root]) == 2
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is False and report["problems"][0].startswith("ledger line 2 is invalid")
    assert main(["approve", root, "--batch", "b", "--limit", "5", "--by", "o"]) == 2
    assert "line 2" in capsys.readouterr().err and project3.ledger_file.read_bytes() == before


def test_approve_refuses_to_append_to_a_damaged_ledger(tmp_path):
    approve(tmp_path, "b", 100.0, "owner")
    _raw(tmp_path, _BAD_LINES["misspelled_type"])
    before = ProjectPaths(tmp_path).ledger_file.read_bytes()
    with pytest.raises(CineRecastError, match="line 2"):
        approve(tmp_path, "b", 10.0, "owner")
    assert ProjectPaths(tmp_path).ledger_file.read_bytes() == before


# --- final-review wave: the read-check-append is locked; audit totals ---

def test_a_writer_refuses_while_the_ledger_is_locked(tmp_path):
    approve(tmp_path, "b", 100.0, "owner")
    issue(tmp_path, "j0", "b", 1.0)
    before = ProjectPaths(tmp_path).ledger_file.read_bytes()
    with ledger_module.locked(tmp_path):
        for call in (lambda: approve(tmp_path, "b", 1.0, "owner", lock_wait=0.2),
                     lambda: issue(tmp_path, "j1", "b", 1.0, lock_wait=0.2),
                     lambda: done(tmp_path, "j0", "p", 1.0, lock_wait=0.2),
                     lambda: fail(tmp_path, "j0", "r", 0.0, lock_wait=0.2)):
            with pytest.raises(CineRecastError, match="another cine-recast command is writing the ledger"):
                call()
    assert ProjectPaths(tmp_path).ledger_file.read_bytes() == before
    issue(tmp_path, "j1", "b", 1.0)                                    # the lock is free again
    assert (tmp_path / "ledger.lock").is_file()


def test_two_threads_against_a_limit_for_one_issue_exactly_one(tmp_path, monkeypatch):
    approve(tmp_path, "b", 10.0, "owner")
    real_events = ledger_module._events

    def slow_events(root):
        events = real_events(root)
        time.sleep(0.3)                                                # widen the read-to-append window
        return events

    monkeypatch.setattr(ledger_module, "_events", slow_events)
    barrier = threading.Barrier(2)
    results: dict[str, str] = {}

    def run(job):
        barrier.wait()
        try:
            issue(tmp_path, job, "b", 6.0)
            results[job] = "issued"
        except CineRecastError as exc:
            results[job] = exc.message

    threads = [threading.Thread(target=run, args=(job,)) for job in ("j1", "j2")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert list(results.values()).count("issued") == 1
    assert any("limit" in r for r in results.values()) and batch_status(tmp_path, "b")["committed"] == 6.0


def test_audit_totals_over_batches(tmp_path):
    approve(tmp_path, "a", 10.0, "owner"); approve(tmp_path, "b", 5.5, "owner")
    issue(tmp_path, "j1", "a", 4.0); issue(tmp_path, "j2", "b", 2.0); done(tmp_path, "j2", "p", 1.5)
    assert audit(tmp_path)["totals"] == {"limit": 15.5, "committed": 5.5, "remaining": 10.0}


def test_job_states(tmp_path):
    assert job_states(tmp_path) == {}
    approve(tmp_path, "b", 100.0, "owner")
    for job in ("j1", "j2", "j3"):
        issue(tmp_path, job, "b", 10.0)
    done(tmp_path, "j1", "p", 1.0); fail(tmp_path, "j2", "r")
    assert job_states(tmp_path) == {"j1": "done", "j2": "failed", "j3": "issued"}
    _raw(tmp_path, '{"type": "job_done", "time": "x", "job_id": "j2", "provider_id": "p", "credits": 1.0}\n')
    assert job_states(tmp_path)["j2"] == "failed"                     # the first finish wins
    _raw(tmp_path, '[1, 2]\n')
    with pytest.raises(CineRecastError, match="damaged"):
        job_states(tmp_path)
