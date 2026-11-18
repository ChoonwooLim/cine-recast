import json
from pathlib import Path

import pytest
from conftest import set_prompt, write_models

from cine_recast.cli import main
from cine_recast.errors import CineRecastError
from cine_recast.ffmpeg import probe
from cine_recast.jsonio import read_json, write_json
from cine_recast.ledger import approve, audit, batch_status
from cine_recast.plan import add_job, build, check, check_models, issue_next, load, record_done, record_fail, set_cost
from cine_recast.project import set_right


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


def test_issue_and_record(planned3, tmp_path, clip3):               # planned3: built plan with valid prompts
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
    first = record_done(root, "j0001", "prov-1", 38.0, clip3)
    assert first == {"unit": "u01", "take": "t001"} and (planned3.take_dir("u01", "t001") / "raw.mp4").is_file()
    retry = add_job(root, "u01", "b", "draft"); set_cost(root, retry["id"], 10.0); issue_next(root, "b")
    assert record_done(root, retry["id"], "prov-2", 10.0, clip3)["take"] == "t002"
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


def test_job_state_rules(planned3, clip3, tmp_path):
    root = planned3.root
    with pytest.raises(CineRecastError, match="j0001"):
        record_done(root, "j0001", "prov-1", 1.0, clip3)              # planned, never issued
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
        record_done(root, "j0001", "prov-1", 1.0, clip3)              # already finished
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


def test_record_done_writes_take_meta(planned3, clip3):
    root = planned3.root
    set_cost(root, "j0001", 10.0); approve(root, "b", 10.0, "owner"); issue_next(root, "b")
    record_done(root, "j0001", "prov-1", 9.5, clip3)
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


def test_cli_job_flow(planned3, clip3, capsys):
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
                 "--file", str(clip3)]) == 0
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
