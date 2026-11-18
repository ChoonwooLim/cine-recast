import json
import shutil
from pathlib import Path

import pytest
from conftest import GOOD_PROMPT, set_prompt, write_models

from cine_recast.cli import main
from cine_recast.errors import CineRecastError
from cine_recast.ffmpeg import probe
from cine_recast.jsonio import read_json, write_json
from cine_recast.ledger import approve, audit, batch_status
from cine_recast.ledger import done as ledger_done
from cine_recast.ledger import fail as ledger_fail
from cine_recast.ledger import issue as ledger_issue
from cine_recast.plan import (add_job, build, check, check_models, issue_next, list_jobs, load, record_done,
                              record_fail, set_cost)
from cine_recast.project import set_right
from cine_recast.takes import list_takes, select


def test_units_are_packed_by_model_limit(cast3):
    write_models(cast3.root, min_seconds=1, max_seconds=2.5)
    p = build(cast3.root, model="seedance_2_5", method="edit", batch="draft-1")
    assert [(u["id"], u["shots"], u["start"], u["end"]) for u in p["units"]] == [("u01", [1, 2], 0, 54), ("u02", [3], 54, 90)]
    assert [u["params"]["duration"] for u in p["units"]] == [3, 2]
    assert p["units"][0]["params"]["resolution"] == "480p" and p["units"][0]["params"]["aspect_ratio"] == "16:9"
    assert probe(cast3.unit_dir("u01") / "ref.mp4").frames == 54
    assert [j["state"] for j in p["jobs"]] == ["planned", "planned"]


def test_duration_is_raised_to_model_minimum(cast3):
    write_models(cast3.root, min_seconds=4, max_seconds=15)
    p = build(cast3.root, model="seedance_2_5", method="edit", batch="b")
    assert len(p["units"]) == 1 and p["units"][0]["params"]["duration"] == 4


def test_inputs_for_edit_and_regen(cast3):
    write_models(cast3.root)
    edit = build(cast3.root, model="seedance_2_5", method="edit", batch="b")["units"][0]["inputs"]
    assert [i["role"] for i in edit] == ["video", "image", "image"] and edit[1]["element"] == "hero"
    regen = build(cast3.root, model="seedance_2_5", method="regen", batch="b", replace=True)["units"][0]["inputs"]
    assert regen[0] == {"role": "image", "path": "analysis/shots/0001/stills/first.png"}


@pytest.mark.parametrize("setup, fragment", [
    (lambda r: write_models(r, video_ref=False), "video"),
    (lambda r: write_models(r, max_seconds=1.0), "longer"),
    (lambda r: write_models(r, image_refs=1), "image"),
    (lambda r: set_right(r, "likeness_consent", "no"), "likeness_consent"),
])
def test_build_refusals(cast3, setup, fragment):
    write_models(cast3.root); setup(cast3.root)
    with pytest.raises(CineRecastError, match=fragment):
        build(cast3.root, model="seedance_2_5", method="edit", batch="b")


def test_check_requires_english_prompts(cast3):
    write_models(cast3.root); build(cast3.root, model="seedance_2_5", method="edit", batch="b")
    assert any("prompt" in p for p in check(cast3.root))
    set_prompt(cast3.root, "u01", "\uc18c\ub144\uc774 \ub2ec\ub9b0\ub2e4")     # helper in conftest
    assert any("prompt" in p for p in check(cast3.root))
    set_prompt(cast3.root, "u01", "Replace the rider with the person in image 1.")
    assert check(cast3.root) == []


def test_issue_and_record(planned3, tmp_path, result_clip):               # planned3: built plan with valid prompts
    root = planned3.root
    with pytest.raises(CineRecastError):
        set_cost(root, "j0009", 5.0)                                 # unknown job
    assert issue_next(root, "b") == []                               # nothing quoted yet
    set_cost(root, "j0001", 40.0)
    with pytest.raises(CineRecastError, match="approval"):
        issue_next(root, "b")
    approve(root, "b", 50.0, "owner")
    jobs = issue_next(root, "b")
    assert [j["job_id"] for j in jobs] == ["j0001"] and jobs[0]["tool"] == "generate_video"
    assert all(Path(m["path"]).is_absolute() and Path(m["path"]).is_file() for m in jobs[0]["medias"])
    first = record_done(root, "j0001", "prov-1", 38.0, result_clip)
    assert first == {"unit": "u01", "take": "t001"} and (planned3.take_dir("u01", "t001") / "raw.mp4").is_file()
    retry = add_job(root, "u01", "b", "draft"); set_cost(root, retry["id"], 10.0); issue_next(root, "b")
    assert record_done(root, retry["id"], "prov-2", 10.0, result_clip)["take"] == "t002"
    assert load(root)["jobs"][0]["state"] == "done" and audit(root)["ok"] is True


# --- extra guards beyond the brief's list ---

def test_shot_longer_than_the_unit_limit_is_refused(cast3):
    write_models(cast3.root, min_seconds=1, max_seconds=1.0)          # valid model, shot 1 is 1.25 s
    with pytest.raises(CineRecastError, match="shot 1 .*longer"):
        build(cast3.root, model="seedance_2_5", method="edit", batch="b")


def test_max_unit_seconds_tightens_the_limit(cast3):
    write_models(cast3.root, min_seconds=1, max_seconds=15)
    p = build(cast3.root, model="seedance_2_5", method="edit", batch="b", max_unit_seconds=2.5)
    assert [u["shots"] for u in p["units"]] == [[1, 2], [3]]
    with pytest.raises(CineRecastError, match="longer"):
        build(cast3.root, model="seedance_2_5", method="edit", batch="b", max_unit_seconds=1.3, replace=True)


def test_final_quality_and_closest_aspect(cast3):
    write_models(cast3.root, aspect_ratios=["9:16", "1:1", "21:9"])    # picture area is 16:9
    u = build(cast3.root, model="seedance_2_5", method="regen", batch="b", quality="final")["units"][0]
    assert u["params"] == {"duration": 4, "aspect_ratio": "21:9", "resolution": "4k"}
    assert [i["role"] for i in u["inputs"]] == ["image", "image", "image"]
    assert u["inputs"][1:] == [{"role": "image", "path": i["path"], "element": i["element"]} for i in u["inputs"][1:]]
    assert u["prompt"] == "" and u["qc_skip"] == [] and u["selected_take"] is None
    assert load(cast3.root)["jobs"] == [{"id": "j0001", "unit": "u01", "batch": "b", "quality": "final",
                                         "state": "planned", "est_credits": None}]
    assert probe(cast3.unit_dir("u01") / "ref.mp4").frames == 90       # the unit clip is made for regen too


def test_refused_build_writes_nothing(cast3):
    write_models(cast3.root, image_refs=1)
    with pytest.raises(CineRecastError, match="u01"):
        build(cast3.root, model="seedance_2_5", method="edit", batch="b")
    assert not cast3.plan_file.exists() and not cast3.unit_dir("u01").exists()


@pytest.mark.parametrize("kwargs, fragment", [
    ({"model": "missing_model"}, "missing_model"),
    ({"method": "paint"}, "method"),
    ({"quality": "best"}, "quality"),
    ({"batch": " "}, "batch"),
    ({"max_unit_seconds": 0.0}, "max_unit_seconds"),
])
def test_build_argument_refusals(cast3, kwargs, fragment):
    write_models(cast3.root)
    args = {"model": "seedance_2_5", "method": "edit", "batch": "b", **kwargs}
    with pytest.raises(CineRecastError, match=fragment):
        build(cast3.root, **args)


def test_image_model_is_refused(cast3):
    write_models(cast3.root, kind="image")
    with pytest.raises(CineRecastError, match="video model"):
        build(cast3.root, model="seedance_2_5", method="regen", batch="b")


def test_plan_is_replaced_only_before_any_issue(planned3):
    root = planned3.root
    with pytest.raises(CineRecastError, match="replace"):
        build(root, model="seedance_2_5", method="edit", batch="b")
    set_cost(root, "j0001", 10.0); approve(root, "b", 10.0, "owner"); issue_next(root, "b")
    with pytest.raises(CineRecastError, match="j0001"):
        build(root, model="seedance_2_5", method="regen", batch="b", replace=True)
    assert load(root)["jobs"][0]["state"] == "issued"


@pytest.mark.parametrize("mutate, fragment", [
    (lambda m: m.pop("read_at"), "read_at"),
    (lambda m: m.update(kind="audio"), "kind"),
    (lambda m: m.update(min_seconds=20), "min_seconds"),
    (lambda m: m.update(aspect_ratios=[]), "aspect_ratios"),
    (lambda m: m.update(resolutions="480p"), "resolutions"),
    (lambda m: m.update(video_ref="yes"), "video_ref"),
    (lambda m: m.update(image_refs=-1), "image_refs"),
    (lambda m: m.update(image_refs=True), "image_refs"),
    (lambda m: m.update(id=""), "id"),
])
def test_check_models_problems(cast3, mutate, fragment):
    write_models(cast3.root)
    data = read_json(cast3.models_file)
    mutate(data["models"][0])
    write_json(cast3.models_file, data)
    assert any(fragment in p for p in check_models(cast3.root))


def test_check_models_valid_and_missing(cast3):
    assert any("models.json" in p for p in check_models(cast3.root))
    write_models(cast3.root)
    assert check_models(cast3.root) == []


@pytest.mark.parametrize("mutate, fragment", [
    (lambda u: u["params"].update(duration=16), "duration"),
    (lambda u: u["params"].update(duration=3), "duration"),
    (lambda u: u["params"].update(aspect_ratio="4:3"), "aspect_ratio"),
    (lambda u: u["params"].update(resolution="8k"), "resolution"),
    (lambda u: u["inputs"].append({"role": "image", "path": "Z:/missing/face.png"}), "not found"),
    (lambda u: u.update(model="other_model"), "other_model"),
])
def test_check_reports_unit_problems(planned3, mutate, fragment):
    assert check(planned3.root) == []
    data = read_json(planned3.plan_file)
    mutate(data["units"][0])
    write_json(planned3.plan_file, data)
    problems = check(planned3.root)
    assert any(fragment in p and "u01" in p for p in problems)


def test_issue_next_needs_a_clean_check(planned3):
    root = planned3.root
    set_cost(root, "j0001", 10.0); approve(root, "b", 50.0, "owner")
    set_prompt(root, "u01", "")
    with pytest.raises(CineRecastError, match="prompt"):
        issue_next(root, "b")
    assert batch_status(root, "b")["committed"] == 0.0 and load(root)["jobs"][0]["state"] == "quoted"


def test_issue_stops_at_the_first_refusal_and_keeps_earlier_jobs(planned3):
    root = planned3.root
    j2 = add_job(root, "u01", "b", "draft")["id"]
    other = add_job(root, "u01", "other", "draft")["id"]
    for job in ("j0001", j2, other):
        set_cost(root, job, 30.0)
    approve(root, "b", 50.0, "owner"); approve(root, "other", 50.0, "owner")
    with pytest.raises(CineRecastError, match=f"{j2}.*limit"):
        issue_next(root, "b")
    states = {j["id"]: j["state"] for j in load(root)["jobs"]}
    assert states == {"j0001": "issued", j2: "quoted", other: "quoted"}
    assert batch_status(root, "b")["committed"] == 30.0 and batch_status(root, "other")["committed"] == 0.0


@pytest.mark.parametrize("credits", [0.0, -1.0, float("nan"), float("inf")])
def test_set_cost_needs_a_positive_amount(planned3, credits):
    with pytest.raises(CineRecastError, match="j0001"):
        set_cost(planned3.root, "j0001", credits)
    assert load(planned3.root)["jobs"][0] == {"id": "j0001", "unit": "u01", "batch": "b", "quality": "draft",
                                              "state": "planned", "est_credits": None}


def test_job_state_rules(planned3, result_clip, tmp_path):
    root = planned3.root
    with pytest.raises(CineRecastError, match="j0001"):
        record_done(root, "j0001", "prov-1", 1.0, result_clip)              # planned, never issued
    with pytest.raises(CineRecastError, match="j0001"):
        record_fail(root, "j0001", "timeout")
    set_cost(root, "j0001", 20.0); set_cost(root, "j0001", 25.0)      # a quote may be revised before issue
    approve(root, "b", 30.0, "owner")
    assert issue_next(root, "b")[0]["job_id"] == "j0001"
    with pytest.raises(CineRecastError, match="j0001"):
        set_cost(root, "j0001", 5.0)                                  # issued jobs keep their quote
    with pytest.raises(CineRecastError, match="not found"):
        record_done(root, "j0001", "prov-1", 1.0, tmp_path / "missing.mp4")
    assert record_fail(root, "j0001", "provider timeout", 2.0)["state"] == "failed"
    with pytest.raises(CineRecastError):
        record_done(root, "j0001", "prov-1", 1.0, result_clip)              # already finished
    assert batch_status(root, "b") == {"limit": 30.0, "committed": 2.0, "remaining": 28.0}
    assert not (root / "takes").exists() and audit(root)["ok"] is True


@pytest.mark.parametrize("args, fragment", [
    (("u09", "b", "draft"), "u09"),
    (("u01", "b", "best"), "quality"),
    (("u01", "", "draft"), "batch"),
])
def test_add_job_refusals(planned3, args, fragment):
    with pytest.raises(CineRecastError, match=fragment):
        add_job(planned3.root, *args)


def test_issued_job_carries_the_unit_settings(planned3):
    root = planned3.root
    set_cost(root, "j0001", 10.0); approve(root, "b", 10.0, "owner")
    [job] = issue_next(root, "b")
    unit = load(root)["units"][0]
    assert job["unit"] == "u01" and job["params"] == {"model": "seedance_2_5", "prompt": unit["prompt"], **unit["params"]}
    assert [m["role"] for m in job["medias"]] == ["video", "image", "image"]
    assert Path(job["medias"][0]["path"]) == (planned3.unit_dir("u01") / "ref.mp4").resolve()
    assert Path(job["medias"][1]["path"]) == Path(unit["inputs"][1]["path"]).resolve()


def test_record_done_writes_take_meta(planned3, result_clip):
    root = planned3.root
    set_cost(root, "j0001", 10.0); approve(root, "b", 10.0, "owner"); issue_next(root, "b")
    record_done(root, "j0001", "prov-1", 9.5, result_clip)
    meta = read_json(planned3.take_dir("u01", "t001") / "meta.json")
    assert set(meta) == {"job", "provider_id", "credits", "quality", "recorded_at"}
    assert (meta["job"], meta["provider_id"], meta["credits"], meta["quality"]) == ("j0001", "prov-1", 9.5, "draft")
    assert audit(root)["provider_ids"] == ["prov-1"]


def test_cli_plan_build(cast3, capsys):
    root = str(cast3.root)
    write_models(cast3.root, min_seconds=1)
    assert main(["models", "check", root]) == 0
    build_args = ["plan", "build", root, "--model", "seedance_2_5", "--method", "edit", "--batch", "draft-1"]
    assert main(build_args + ["--quality", "final", "--max-unit-seconds", "2.5"]) == 0
    assert main(build_args) == 2                                      # plan exists, no --replace
    assert main(build_args + ["--replace"]) == 0
    assert main(["plan", "check", root]) == 2                         # prompts are still empty
    capsys.readouterr()
    assert main(["plan", "show", root]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert [u["shots"] for u in shown["units"]] == [[1, 2, 3]] and shown["units"][0]["params"]["resolution"] == "480p"


def test_cli_job_flow(planned3, result_clip, capsys):
    root = str(planned3.root)
    assert main(["plan", "check", root]) == 0
    assert main(["jobs", "set-cost", root, "--job", "j0001", "--credits", "40"]) == 0
    assert main(["jobs", "next", root, "--batch", "b"]) == 2           # no approval yet
    assert main(["approve", root, "--batch", "b", "--limit", "50", "--by", "owner", "--note", "first drafts"]) == 0
    capsys.readouterr()
    assert main(["jobs", "next", root, "--batch", "b"]) == 0
    issued = json.loads(capsys.readouterr().out)
    assert [j["job_id"] for j in issued] == ["j0001"] and issued[0]["params"]["model"] == "seedance_2_5"
    assert main(["jobs", "done", root, "--job", "j0001", "--provider-id", "prov-1", "--credits", "38",
                 "--file", str(result_clip)]) == 0
    assert main(["jobs", "add", root, "--unit", "u01", "--batch", "b", "--quality", "draft"]) == 0
    assert main(["jobs", "set-cost", root, "--job", "j0002", "--credits", "5"]) == 0
    assert main(["jobs", "next", root, "--batch", "b"]) == 0
    assert main(["jobs", "fail", root, "--job", "j0002", "--reason", "timeout", "--credits", "1"]) == 0
    assert main(["takes", "select", root, "--unit", "u01", "--take", "t001"]) == 0
    assert main(["takes", "select", root, "--unit", "u01", "--take", "t002"]) == 2
    capsys.readouterr()
    assert main(["jobs", "list", root]) == 0
    assert [(j["id"], j["state"]) for j in json.loads(capsys.readouterr().out)] == [("j0001", "done"), ("j0002", "failed")]
    assert main(["audit", root]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is True and report["batches"]["b"]["committed"] == 39.0
    with planned3.ledger_file.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write('{"type": "job_done", "time": "x", "job_id": "j0777", "provider_id": "p", "credits": 1.0}\n')
    assert main(["audit", root]) == 2
    assert json.loads(capsys.readouterr().out)["ok"] is False


# --- rulings round: resolution by job quality, rights re-check, ledger-backed job state ---

def test_resolution_follows_the_job_quality(planned3, capsys):
    root = planned3.root
    assert main(["jobs", "add", str(root), "--unit", "u01", "--batch", "b", "--quality", "final"]) == 0
    capsys.readouterr()
    assert main(["jobs", "list", str(root)]) == 0
    listed = json.loads(capsys.readouterr().out)
    draft, final = listed[0]["params"], listed[1]["params"]
    assert draft["resolution"] == "480p" and final["resolution"] == "4k"
    assert {**final, "resolution": "480p"} == draft
    assert load(root)["units"][0]["params"]["resolution"] == "480p"          # build's default stays in the unit
    assert [(j["id"], j["state"], j["batch"], j["quality"], j["est_credits"]) for j in listed] == [
        ("j0001", "planned", "b", "draft", None), ("j0002", "planned", "b", "final", None)]
    for job in ("j0001", "j0002"):
        set_cost(root, job, 10.0)
    approve(root, "b", 20.0, "owner")
    sent = issue_next(root, "b")
    assert [p["params"] for p in sent] == [draft, final]                    # what is quoted is what is sent
    assert sent[1]["medias"] == sent[0]["medias"]


@pytest.mark.parametrize("mutate, fragment", [
    (lambda j: j.update(quality="best"), "quality"),
    (lambda j: j.update(unit="u09"), "u09"),
])
def test_check_validates_every_job(planned3, mutate, fragment):
    data = read_json(planned3.plan_file)
    mutate(data["jobs"][0])
    write_json(planned3.plan_file, data)
    assert any("j0001" in p and fragment in p for p in check(planned3.root))


def test_issue_rechecks_rights_at_hand_over(planned3):
    root = planned3.root
    set_cost(root, "j0001", 10.0); approve(root, "b", 50.0, "owner")
    before = planned3.ledger_file.read_text(encoding="utf-8")
    set_right(root, "likeness_consent", "no")
    with pytest.raises(CineRecastError, match="likeness_consent"):
        issue_next(root, "b")
    assert planned3.ledger_file.read_text(encoding="utf-8") == before
    assert load(root)["jobs"][0]["state"] == "quoted"


def test_ledger_decides_job_state_after_a_crash(planned3):
    root = planned3.root
    set_cost(root, "j0001", 10.0); approve(root, "b", 50.0, "owner")
    ledger_issue(root, "j0001", "b", 10.0)                    # issued, but the plan save never happened
    assert read_json(planned3.plan_file)["jobs"][0]["state"] == "quoted"
    assert load(root)["jobs"][0]["state"] == "issued"
    assert issue_next(root, "b") == [] and audit(root)["ok"] is True
    j2 = add_job(root, "u01", "b", "draft")["id"]              # the next save persists the reconciled state
    assert read_json(planned3.plan_file)["jobs"][0]["state"] == "issued"
    ledger_issue(root, j2, "b", 5.0)                           # a planned job: no estimate is invented
    assert load(root)["jobs"][1] == {"id": j2, "unit": "u01", "batch": "b", "quality": "draft",
                                     "state": "issued", "est_credits": None}
    ledger_done(root, "j0001", "prov-1", 9.0); ledger_fail(root, j2, "timeout")
    assert [j["state"] for j in load(root)["jobs"]] == ["done", "failed"]


def test_reconciliation_never_moves_a_job_backwards(planned3):
    root = planned3.root
    set_cost(root, "j0001", 10.0); approve(root, "b", 50.0, "owner"); issue_next(root, "b")
    data = read_json(planned3.plan_file)
    data["jobs"][0]["state"] = "done"
    write_json(planned3.plan_file, data)
    assert load(root)["jobs"][0]["state"] == "done"


@pytest.mark.parametrize("setup, fragment", [
    ("never_issued", "never issued"),
    ("damaged_ledger", "damaged"),
])
def test_refused_record_done_leaves_no_take(planned3, result_clip, setup, fragment):
    root = planned3.root
    if setup == "never_issued":                                # the plan claims issued, the ledger does not
        data = read_json(planned3.plan_file)
        data["jobs"][0]["state"] = "issued"
        write_json(planned3.plan_file, data)
    else:
        set_cost(root, "j0001", 10.0); approve(root, "b", 50.0, "owner"); issue_next(root, "b")
        with planned3.ledger_file.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write('{"type": "job_done", "time": "x", "job_id": "j0001", ')
    with pytest.raises(CineRecastError, match=fragment):
        record_done(root, "j0001", "prov-1", 5.0, result_clip)
    assert list_takes(root, "u01") == []


def test_done_job_without_a_take_points_to_a_new_job(planned3, result_clip):
    root = planned3.root
    set_cost(root, "j0001", 10.0); approve(root, "b", 50.0, "owner"); issue_next(root, "b")
    ledger_done(root, "j0001", "prov-1", 9.0)                  # recorded as done, but no take was stored
    with pytest.raises(CineRecastError, match="jobs add"):
        record_done(root, "j0001", "prov-1", 9.0, result_clip)
    assert list_takes(root, "u01") == []
    j2 = add_job(root, "u01", "b", "draft")["id"]; set_cost(root, j2, 10.0); issue_next(root, "b")
    assert record_done(root, j2, "prov-2", 9.0, result_clip)["take"] == "t001"
    with pytest.raises(CineRecastError) as refused:
        record_done(root, j2, "prov-2", 9.0, result_clip)            # done with a take: no hint, no new folder
    assert "jobs add" not in refused.value.message and list_takes(root, "u01") == ["t001"]


# --- fix round 1: quotes bound to payloads, ids never reused, small items ---

def _edit_unit(pp, mutate, index=0):
    data = read_json(pp.plan_file)
    mutate(data["units"][index])
    write_json(pp.plan_file, data)


@pytest.mark.parametrize("mutate", [
    lambda u: u["params"].update(duration=15),                          # still within the model limits
    lambda u: u["params"].update(aspect_ratio="21:9"),
])
def test_quote_is_bound_to_the_priced_payload(planned3, mutate):
    root = planned3.root
    set_cost(root, "j0001", 10.0); approve(root, "b", 50.0, "owner")
    _edit_unit(planned3, mutate)
    assert any("j0001" in p and "set-cost" in p for p in check(root))
    before = planned3.ledger_file.read_text(encoding="utf-8")
    with pytest.raises(CineRecastError, match="set-cost"):
        issue_next(root, "b")
    assert planned3.ledger_file.read_text(encoding="utf-8") == before
    set_cost(root, "j0001", 12.0)                                       # a new quote, after the approval
    assert issue_next(root, "b")[0]["job_id"] == "j0001" and batch_status(root, "b")["committed"] == 12.0


def test_prompt_change_keeps_the_quote(planned3):
    root = planned3.root
    set_cost(root, "j0001", 10.0); approve(root, "b", 50.0, "owner")
    set_prompt(root, "u01", "Replace the rider with the person in image 1.")
    assert check(root) == []
    assert issue_next(root, "b")[0]["params"]["prompt"] == "Replace the rider with the person in image 1."


def test_jobs_list_shows_sent_params_once_issued(planned3, result_clip):
    root = planned3.root
    set_cost(root, "j0001", 10.0); approve(root, "b", 50.0, "owner")
    [sent] = issue_next(root, "b")
    add_job(root, "u01", "b", "draft")
    _edit_unit(planned3, lambda u: u["params"].update(duration=15))
    listed = {j["id"]: j for j in list_jobs(root)}
    assert listed["j0001"]["params"] == sent["params"] and listed["j0002"]["params"]["duration"] == 15
    record_done(root, "j0001", "prov-1", 10.0, result_clip)
    assert {j["id"]: j for j in list_jobs(root)}["j0001"]["params"] == sent["params"]


def test_replan_archives_the_plan_and_never_reuses_ids(planned3, result_clip):
    root = planned3.root
    set_cost(root, "j0001", 10.0); approve(root, "b", 50.0, "owner"); issue_next(root, "b")
    record_done(root, "j0001", "prov-1", 10.0, result_clip)
    p = build(root, model="seedance_2_5", method="regen", batch="b", replace=True)
    assert [u["id"] for u in p["units"]] == ["u02"] and [j["id"] for j in p["jobs"]] == ["j0002"]
    assert read_json(root / "plans" / "plan-0001.json")["jobs"][0]["state"] == "done"
    assert (planned3.unit_dir("u01") / "ref.mp4").is_file() and (planned3.unit_dir("u02") / "ref.mp4").is_file()
    assert [(j["id"], j["state"], j["est_credits"]) for j in load(root)["jobs"]] == [("j0002", "planned", None)]
    with pytest.raises(CineRecastError, match="u01"):
        select(root, "u01", "t001")                                     # a take of a unit no longer planned
    assert add_job(root, "u02", "b", "final")["id"] == "j0003"
    build(root, model="seedance_2_5", method="edit", batch="b", replace=True)
    assert (root / "plans" / "plan-0002.json").is_file()
    assert [u["id"] for u in load(root)["units"]] == ["u03"] and [j["id"] for j in load(root)["jobs"]] == ["j0004"]


def test_ids_continue_after_a_hand_deleted_plan(planned3):
    root = planned3.root
    set_cost(root, "j0001", 10.0); approve(root, "b", 50.0, "owner"); issue_next(root, "b")
    planned3.plan_file.unlink()
    p = build(root, model="seedance_2_5", method="edit", batch="b")
    assert [u["id"] for u in p["units"]] == ["u02"]
    assert [(j["id"], j["state"]) for j in load(root)["jobs"]] == [("j0002", "planned")]
    assert set_cost(root, "j0002", 10.0)["state"] == "quoted"


def test_done_without_take_guard_reads_the_ledger(planned3, result_clip):
    root = planned3.root
    set_cost(root, "j0001", 10.0); approve(root, "b", 50.0, "owner"); issue_next(root, "b")
    data = read_json(planned3.plan_file)
    data["jobs"][0]["state"] = "done"                                   # the plan claims done, the ledger does not
    write_json(planned3.plan_file, data)
    with pytest.raises(CineRecastError) as refused:
        record_done(root, "j0001", "prov-1", 9.0, result_clip)
    assert "jobs add" not in refused.value.message and list_takes(root, "u01") == []


def _two_jobs_over_the_limit(root):
    j2 = add_job(root, "u01", "b", "draft")["id"]
    set_cost(root, "j0001", 30.0); set_cost(root, j2, 30.0); approve(root, "b", 50.0, "owner")
    return j2


def test_refusal_carries_the_jobs_already_issued(planned3):
    _two_jobs_over_the_limit(planned3.root)
    with pytest.raises(CineRecastError, match="limit") as refused:
        issue_next(planned3.root, "b")
    assert [p["job_id"] for p in refused.value.issued] == ["j0001"]


def test_cli_prints_jobs_issued_before_a_refusal(planned3, capsys):
    j2 = _two_jobs_over_the_limit(planned3.root)
    capsys.readouterr()
    assert main(["jobs", "next", str(planned3.root), "--batch", "b"]) == 2
    out = capsys.readouterr()
    assert [j["job_id"] for j in json.loads(out.out)] == ["j0001"] and j2 in out.err and "limit" in out.err


@pytest.mark.parametrize("mutate", [lambda e: e.update(role="audio"), lambda e: e.pop("role")])
def test_check_refuses_an_input_without_a_valid_role(planned3, mutate):
    _edit_unit(planned3, lambda u: mutate(u["inputs"][1]))
    assert any("u01" in p and "role" in p for p in check(planned3.root))


def test_relative_swap_image_resolves_against_the_project(cast3, tmp_path, monkeypatch):
    root = cast3.root
    casting = read_json(cast3.casting_file)
    local = root / "refs" / "hero.png"
    local.parent.mkdir()
    shutil.copyfile(casting["swaps"][0]["images"][0], local)
    casting["swaps"][0]["images"] = ["refs/hero.png"]
    write_json(cast3.casting_file, casting)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    write_models(root)
    unit = build(root, model="seedance_2_5", method="edit", batch="b")["units"][0]
    assert unit["inputs"][1] == {"role": "image", "path": "refs/hero.png", "element": "hero"}
    set_prompt(root, "u01", GOOD_PROMPT)
    set_cost(root, "j0001", 10.0); approve(root, "b", 10.0, "owner")
    [job] = issue_next(root, "b")
    assert Path(job["medias"][1]["path"]) == local.resolve()


def test_flow_in_a_folder_with_hangul_and_a_space(_cast3_template, tmp_path, result_clip, capsys):
    root = tmp_path / "\uc7ac\uc81c\uc791 \ud504\ub85c\uc81d\ud2b8"
    shutil.copytree(_cast3_template, root)
    write_models(root)
    r = str(root)
    assert main(["plan", "build", r, "--model", "seedance_2_5", "--method", "edit", "--batch", "b"]) == 0
    set_prompt(root, "u01", GOOD_PROMPT)
    assert main(["jobs", "set-cost", r, "--job", "j0001", "--credits", "10"]) == 0
    assert main(["approve", r, "--batch", "b", "--limit", "10", "--by", "owner"]) == 0
    capsys.readouterr()
    assert main(["jobs", "next", r, "--batch", "b"]) == 0
    [job] = json.loads(capsys.readouterr().out)
    assert "\ud504\ub85c\uc81d\ud2b8" in job["medias"][0]["path"] and all(Path(m["path"]).is_file() for m in job["medias"])
    assert main(["jobs", "done", r, "--job", "j0001", "--provider-id", "prov-1", "--credits", "10",
                 "--file", str(result_clip)]) == 0
    assert (root / "takes" / "u01" / "t001" / "raw.mp4").is_file() and audit(root)["ok"] is True


def test_issue_loop_refuses_a_stale_quote_on_its_own_inputs(planned3, monkeypatch):
    import cine_recast.plan as plan_module

    root = planned3.root
    set_cost(root, "j0001", 10.0); approve(root, "b", 50.0, "owner")
    _edit_unit(planned3, lambda u: u["inputs"].append(dict(u["inputs"][1])))   # one more input image
    monkeypatch.setattr(plan_module, "check", lambda r, **kw: [])      # the quote binds the inputs too
    with pytest.raises(CineRecastError, match="set-cost"):
        issue_next(root, "b")
    assert batch_status(root, "b")["committed"] == 0.0


# --- final-review wave: the inputs sent are the inputs the swap table implies ---

def _without_person_swap(pp):
    casting = read_json(pp.casting_file)
    casting["swaps"] = [s for s in casting["swaps"] if s["element"] != "hero"]
    write_json(pp.casting_file, casting)


def test_withdrawn_person_swap_stops_a_plan_built_before(planned3, capsys):
    root = planned3.root                                   # built with the hero photo and consent "yes"
    set_cost(root, "j0001", 10.0); approve(root, "b", 50.0, "owner")
    _without_person_swap(planned3)
    set_right(root, "likeness_consent", "no")              # consent withdrawn, the plan is not rebuilt
    before = planned3.ledger_file.read_text(encoding="utf-8")
    assert any("u01" in p and "plan build --replace" in p for p in check(root))
    capsys.readouterr()
    assert main(["jobs", "next", str(root), "--batch", "b"]) == 2
    out = capsys.readouterr()
    assert json.loads(out.out or "[]") == [] and "plan build --replace" in out.err
    assert planned3.ledger_file.read_text(encoding="utf-8") == before
    assert load(root)["jobs"][0]["state"] == "quoted"


def test_inputs_added_by_hand_are_refused(cast3, tmp_path, capsys):
    root = cast3.root
    _without_person_swap(cast3)
    set_right(root, "likeness_consent", "no")
    write_models(root, image_refs=1)
    build(root, model="seedance_2_5", method="edit", batch="b")
    set_prompt(root, "u01", GOOD_PROMPT)
    photo = (tmp_path / "someone.png")
    shutil.copyfile(read_json(cast3.casting_file)["swaps"][0]["images"][0], photo)
    _edit_unit(cast3, lambda u: u["inputs"].extend([{"role": "image", "path": photo.as_posix(), "element": "hero"},
                                                    {"role": "image", "path": photo.as_posix()}]))
    problems = check(root)
    assert any("u01" in p and "plan build --replace" in p for p in problems)
    assert any("u01" in p and "image_refs" in p for p in problems)
    assert main(["plan", "check", str(root)]) == 2
    assert main(["jobs", "set-cost", str(root), "--job", "j0001", "--credits", "40"]) == 0
    assert main(["approve", str(root), "--batch", "b", "--limit", "50", "--by", "owner"]) == 0
    before = cast3.ledger_file.read_text(encoding="utf-8")
    capsys.readouterr()
    assert main(["jobs", "next", str(root), "--batch", "b"]) == 2
    assert json.loads(capsys.readouterr().out or "[]") == []
    assert cast3.ledger_file.read_text(encoding="utf-8") == before


@pytest.mark.parametrize("mutate, fragment", [
    (lambda u: u.update(method="regen"), "plan build --replace"),          # the lead input follows the method
    (lambda u: u["inputs"].reverse(), "plan build --replace"),              # order matters
    (lambda u: u["inputs"][1].update(element="sky_battle"), "plan build --replace"),
    (lambda u: u["inputs"].pop(), "plan build --replace"),
    (lambda u: u.update(method="paint"), "method"),
])
def test_stored_inputs_must_match_the_derived_inputs(planned3, mutate, fragment):
    _edit_unit(planned3, mutate)
    assert any("u01" in p and fragment in p for p in check(planned3.root))


@pytest.mark.parametrize("change, fragment", [
    ({"image_refs": 1}, "image_refs"),
    ({"video_ref": False}, "video_ref"),
])
def test_check_reads_the_model_limits_as_they_are_now(planned3, change, fragment):
    write_models(planned3.root, **change)
    assert any("u01" in p and fragment in p for p in check(planned3.root))


# --- final-review wave: project material is never registered as a take ---

def _issued_job(pp):
    set_cost(pp.root, "j0001", 10.0); approve(pp.root, "b", 50.0, "owner"); issue_next(pp.root, "b")


@pytest.mark.parametrize("make", [
    lambda pp: pp.unit_dir("u01") / "ref.mp4",                                     # the unit's own reference clip
    lambda pp: pp.root / "reference" / "source.mp4",
    lambda pp: pp.root / "REFERENCE" / "source.mp4",                               # another spelling of the folder
    lambda pp: pp.root / "downloads" / ".." / "analysis" / "shots" / "0001" / "ref.mp4",
    lambda pp: _copy_into(pp, "takes"),
    lambda pp: _copy_into(pp, "out"),
    lambda pp: _copy_into(pp, "assets"),
])
def test_jobs_done_refuses_project_material(planned3, make, capsys):
    _issued_job(planned3)
    source = make(planned3)
    capsys.readouterr()
    assert main(["jobs", "done", str(planned3.root), "--job", "j0001", "--provider-id", "prov-1", "--credits", "9",
                 "--file", str(source)]) == 2
    assert "project material" in capsys.readouterr().err
    assert list_takes(planned3.root, "u01") == [] and load(planned3.root)["jobs"][0]["state"] == "issued"


def _copy_into(pp, folder):
    target = pp.root / folder / "x" / "clip.mp4"
    target.parent.mkdir(parents=True)
    shutil.copyfile(pp.root / "reference" / "source.mp4", target)
    return target


def test_jobs_done_refuses_the_reference_video_given_to_init(planned3, clip3, capsys):
    _issued_job(planned3)                                  # planned3 was made with init --reference clip3
    before = planned3.ledger_file.read_bytes()
    capsys.readouterr()
    assert main(["jobs", "done", str(planned3.root), "--job", "j0001", "--provider-id", "prov-1", "--credits", "9",
                 "--file", str(clip3)]) == 2
    assert "is the reference video" in capsys.readouterr().err
    assert list_takes(planned3.root, "u01") == [] and planned3.ledger_file.read_bytes() == before


@pytest.mark.parametrize("mutate", [
    lambda d: d["units"][0].update(shots="1, 2, 3"),
    lambda d: d["units"][0].update(qc_skip=["colour"]),
    lambda d: (d["units"][0].update(id="..\\..\\evil"), d["jobs"][0].update(unit="..\\..\\evil")),
])
def test_jobs_done_stops_at_a_damaged_unit(planned3, result_clip, capsys, mutate):
    _issued_job(planned3)
    data = read_json(planned3.plan_file)
    mutate(data)
    write_json(planned3.plan_file, data)
    before = planned3.ledger_file.read_bytes()
    capsys.readouterr()
    assert main(["jobs", "done", str(planned3.root), "--job", "j0001", "--provider-id", "prov-1", "--credits", "9",
                 "--file", str(result_clip)]) == 2
    err = capsys.readouterr().err
    assert err.startswith("error: ") and "plan check" in err
    assert not (planned3.root / "takes").exists() and planned3.ledger_file.read_bytes() == before


def test_an_unopenable_lock_file_refuses_cleanly(planned3, capsys):
    root = planned3.root
    set_cost(root, "j0001", 10.0); approve(root, "b", 50.0, "owner")
    planned3.ledger_lock.unlink()
    planned3.ledger_lock.mkdir()                           # a folder where the lock file belongs
    capsys.readouterr()
    assert main(["jobs", "next", str(root), "--batch", "b"]) == 2
    out = capsys.readouterr()
    assert "ledger.lock" in out.err and "defect" not in out.err and json.loads(out.out) == []
    job = read_json(planned3.plan_file)["jobs"][0]
    assert job["state"] == "quoted" and "sent_params" not in job


def test_jobs_done_takes_a_downloaded_file_inside_the_project(planned3, result_clip):
    _issued_job(planned3)
    downloaded = planned3.root / "downloads" / "j0001.mp4"
    downloaded.parent.mkdir()
    shutil.copyfile(result_clip, downloaded)
    assert record_done(planned3.root, "j0001", "prov-1", 9.0, downloaded)["take"] == "t001"


# --- final-review wave: hand-written plan.json and models.json ---

@pytest.mark.parametrize("mutate, fragment", [
    (lambda d: (d["units"][0].update(id="..\\..\\evil"), d["jobs"][0].update(unit="..\\..\\evil")), "id"),
    (lambda d: d["units"][0].update(id="u01\n"), "id"),
    (lambda d: d["jobs"][0].update(id="j0001/x"), "id"),
    (lambda d: d["jobs"][0].update(id=1), "id"),
    (lambda d: d["units"][0].update(shots="1, 2, 3"), "shots"),
    (lambda d: d["units"][0].update(shots=[]), "shots"),
    (lambda d: d["units"][0].update(shots=[1, "2"]), "shots"),
    (lambda d: d["units"][0].update(qc_skip="color"), "qc_skip"),
    (lambda d: d["units"][0].update(qc_skip=["colour"]), "qc_skip"),
])
def test_check_validates_ids_shots_and_qc_skip(planned3, capsys, mutate, fragment):
    data = read_json(planned3.plan_file)
    mutate(data)
    write_json(planned3.plan_file, data)
    assert any(fragment in p for p in check(planned3.root))
    capsys.readouterr()
    assert main(["plan", "check", str(planned3.root)]) == 2
    assert not capsys.readouterr().err.startswith("error: ")              # problem lines, not a refusal


@pytest.mark.parametrize("mutate", [
    lambda u: u.update(id="..\\..\\evil"),
    lambda u: u.update(shots="1, 2, 3"),
    lambda u: u.update(qc_skip=["colour"]),
])
def test_commands_refuse_a_damaged_unit_cleanly(finished3, capsys, mutate):
    _edit_unit(finished3, mutate)
    unit_id = read_json(finished3.plan_file)["units"][0]["id"]
    for command in (["assemble", str(finished3.root)], ["report", str(finished3.root)],
                    ["qc", str(finished3.root), "--unit", unit_id, "--take", "t001"],
                    ["conform", str(finished3.root), "--unit", unit_id, "--take", "t001"]):
        capsys.readouterr()
        assert main(command) == 2, command
        assert capsys.readouterr().err.startswith("error: ")
    assert not (finished3.root.parent / "evil").exists()


@pytest.mark.parametrize("text, fragment", [
    ("{", "line 1, column 2"),
    ("[]", "top level"),
    ('{"units": [], "jobs": {}}', "'jobs'"),
    ('{"jobs": []}', "'units'"),
])
def test_a_damaged_plan_file_is_named(planned3, capsys, text, fragment):
    planned3.plan_file.write_text(text, encoding="utf-8")
    capsys.readouterr()
    assert main(["plan", "check", str(planned3.root)]) == 2
    err = capsys.readouterr().err
    assert err.startswith("error: plan.json") and fragment in err


def test_byte_order_marks_are_accepted(planned3, capsys):
    for path in (planned3.plan_file, planned3.models_file, planned3.project_file, planned3.video_file):
        path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())
    capsys.readouterr()
    assert main(["models", "check", str(planned3.root)]) == 0
    assert main(["plan", "check", str(planned3.root)]) == 0


def test_models_file_errors_are_problem_lines(planned3):
    planned3.models_file.write_text('{"models": [}', encoding="utf-8")
    [problem] = check_models(planned3.root)
    assert "models.json" in problem and "line 1, column 13" in problem


# --- final-review wave: life cycle ---

def _boom(*args, **kwargs):
    raise OSError("simulated crash while saving")


def test_sent_params_survive_a_save_that_fails_after_the_ledger_issued(planned3, monkeypatch):
    import cine_recast.ledger as ledger_module
    import cine_recast.plan as plan_module

    root = planned3.root
    set_cost(root, "j0001", 10.0); approve(root, "b", 50.0, "owner")
    [before] = list_jobs(root)
    real_issue = ledger_module.issue

    def issue_then_crash(*args, **kwargs):
        record = real_issue(*args, **kwargs)
        monkeypatch.setattr(plan_module, "_save", _boom)       # the save after the ledger line fails
        return record

    monkeypatch.setattr(ledger_module, "issue", issue_then_crash)
    with pytest.raises(OSError):
        issue_next(root, "b")
    monkeypatch.undo()
    [after] = list_jobs(root)
    assert after["state"] == "issued" and after["params"] == before["params"]


def test_a_refused_issue_keeps_no_sent_params(planned3):
    j2 = _two_jobs_over_the_limit(planned3.root)
    with pytest.raises(CineRecastError, match="limit"):
        issue_next(planned3.root, "b")
    jobs = {j["id"]: j for j in read_json(planned3.plan_file)["jobs"]}
    assert "sent_params" in jobs["j0001"] and "sent_params" not in jobs[j2]


def test_jobs_next_checks_only_the_units_it_issues(cast3, capsys):
    root = cast3.root
    write_models(root, min_seconds=1)
    units = build(root, model="seedance_2_5", method="edit", batch="cmp", max_unit_seconds=1.6)["units"]
    assert [u["id"] for u in units] == ["u01", "u02", "u03"]
    set_prompt(root, "u01", GOOD_PROMPT)                               # the other prompts stay empty
    set_cost(root, "j0001", 10.0); approve(root, "cmp", 10.0, "owner")
    assert [j["job_id"] for j in issue_next(root, "cmp")] == ["j0001"]
    problems = check(root)                                             # plan check still checks every unit
    assert any("u02" in p and "prompt" in p for p in problems) and any("u03" in p for p in problems)
    capsys.readouterr()
    assert main(["plan", "check", str(root)]) == 2


def test_approve_prints_the_new_state_of_the_batch(planned3, capsys):
    root = str(planned3.root)
    set_cost(planned3.root, "j0001", 4.0)
    capsys.readouterr()
    assert main(["approve", root, "--batch", "b", "--limit", "10", "--by", "owner"]) == 0
    assert capsys.readouterr().out == ("approved batch b: +10.0 credits, limit now 10.0, committed 0.0, "
                                       "remaining 10.0 (by owner)\n")
    issue_next(planned3.root, "b")
    assert main(["approve", root, "--batch", "b", "--limit", "5", "--by", "owner"]) == 0
    assert capsys.readouterr().out == ("approved batch b: +5.0 credits, limit now 15.0, committed 4.0, "
                                       "remaining 11.0 (by owner)\n")


def test_issue_loop_refuses_a_stale_quote_on_its_own(planned3, monkeypatch):
    import cine_recast.plan as plan_module

    root = planned3.root
    set_cost(root, "j0001", 10.0); approve(root, "b", 50.0, "owner")
    _edit_unit(planned3, lambda u: u["params"].update(duration=15))
    monkeypatch.setattr(plan_module, "check", lambda r, **kw: [])      # the gate holds even without check
    with pytest.raises(CineRecastError, match="set-cost") as refused:
        issue_next(root, "b")
    assert refused.value.issued == [] and batch_status(root, "b")["committed"] == 0.0
