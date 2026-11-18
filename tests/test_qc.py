import json
import math

import pytest
from conftest import fake_take, make_motion_clip

from cine_recast import plan
from cine_recast.cli import main
from cine_recast.conform import conform
from cine_recast.errors import CineRecastError
from cine_recast.ffmpeg import run
from cine_recast.jsonio import read_json, write_json
from cine_recast.picture import PictureBox
from cine_recast.qc import DEFAULT_THRESHOLDS, SKIPPABLE, score_shot, score_take
from cine_recast.report import write_report
from cine_recast.takes import select

T = dict(DEFAULT_THRESHOLDS)

def test_identical_take_passes(tmp_path):
    ref = make_motion_clip(tmp_path / "r.mp4", "pan")
    s = score_shot(ref, ref, PictureBox(0, 0, 384, 216), T)
    assert s["timing_ok"] and s["camera"] >= 0.99 and s["motion"] >= 0.99 and s["color"] >= 0.99
    assert abs(s["flicker_ratio"] - 1.0) < 0.2 and s["overlap_ratio"] >= 0.95
    assert s["verdict"] == "pass" and s["failed"] == []

def test_opposite_pan_fails_camera(tmp_path):
    ref = make_motion_clip(tmp_path / "r.mp4", "pan", rate=4.0)
    take = make_motion_clip(tmp_path / "t.mp4", "pan", rate=-4.0)
    s = score_shot(ref, take, PictureBox(0, 0, 384, 216), T)
    assert s["camera"] < 0.3 and s["verdict"] == "retry" and "camera" in s["failed"]

def test_static_reference_and_moving_take(tmp_path):
    ref = make_motion_clip(tmp_path / "r.mp4", "static")
    take = make_motion_clip(tmp_path / "t.mp4", "pan")
    assert score_shot(ref, take, PictureBox(0, 0, 384, 216), T)["camera"] < 0.2

def test_recoloured_take(tmp_path):
    ref = make_motion_clip(tmp_path / "r.mp4", "pan")
    take = tmp_path / "t.mp4"
    run(["-i", str(ref), "-vf", "hue=h=120:s=2,eq=brightness=0.3", "-c:v", "libx264", "-crf", "14", str(take)])
    s = score_shot(ref, take, PictureBox(0, 0, 384, 216), T)
    assert s["color"] < 0.6 and s["overlap_ratio"] < 0.2 and s["camera"] >= 0.8 and "color" in s["failed"]
    assert score_shot(ref, take, PictureBox(0, 0, 384, 216), T, skip=["color"])["verdict"] == "pass"

def test_frame_count_mismatch(tmp_path):
    ref = make_motion_clip(tmp_path / "r.mp4", "pan", frames=24)
    take = make_motion_clip(tmp_path / "t.mp4", "pan", frames=20)
    s = score_shot(ref, take, PictureBox(0, 0, 384, 216), T)
    assert s["timing_ok"] is False and s["failed"] == ["timing"] and s["camera"] is None

def test_score_take_writes_file(conformed3):     # planned3 + fake_take + conform, built once per session
    t = conformed3.take_id
    scores = score_take(conformed3.root, "u01", t)
    assert set(scores["shots"]) == {"1", "2", "3"} and scores["verdict"] == "pass"
    assert read_json(conformed3.take_dir("u01", t) / "scores.json") == scores


# --- extra guards beyond the brief's list ---

def test_cli_qc_prints_the_scores(monkeypatch, tmp_path, capsys):
    calls = []

    def fake_score_take(root, unit_id, take_id):
        calls.append((unit_id, take_id))
        return {"verdict": "pass", "shots": {}}

    monkeypatch.setattr("cine_recast.qc.score_take", fake_score_take)
    assert main(["qc", str(tmp_path), "--unit", "u01", "--take", "t001"]) == 0
    assert json.loads(capsys.readouterr().out) == {"verdict": "pass", "shots": {}}
    assert calls == [("u01", "t001")]

# --- final-review wave: the colour score of a background-swapped shot is left out of the verdict ---

RECOLOUR = "lutyuv=u=val-60:v=val+60"      # a strong tint that leaves the luma, and so camera and motion, alone


def test_background_swapped_shots_leave_colour_out(planned3, tmp_path):
    root = planned3.root                       # sky_battle (background) is swapped in shots 1 and 3, not in 2
    conform(root, "u01", fake_take(root, "u01", tmp_path, filters=RECOLOUR))
    scores = score_take(root, "u01", "t001")
    shots = scores["shots"]
    assert all(shots[s]["color"] < T["color_min"] for s in ("1", "2", "3"))
    for s in ("1", "3"):
        assert (shots[s]["verdict"], shots[s]["failed"], shots[s]["skipped"]) == ("pass", [], ["color"])
    assert (shots["2"]["verdict"], shots["2"]["failed"], shots["2"]["skipped"]) == ("retry", ["color"], [])
    assert scores["verdict"] == "retry" and read_json(planned3.take_dir("u01", "t001") / "scores.json") == scores

    plan = read_json(planned3.plan_file)
    plan["units"][0]["qc_skip"] = ["color"]                                     # the manual addition on top
    write_json(planned3.plan_file, plan)
    shots = score_take(root, "u01", "t001")["shots"]
    assert all((shots[s]["verdict"], shots[s]["skipped"]) == ("pass", ["color"]) for s in ("1", "2", "3"))

    plan["units"][0]["qc_skip"] = ["colour"]
    write_json(planned3.plan_file, plan)
    with pytest.raises(CineRecastError, match="qc_skip"):
        score_take(root, "u01", "t001")


def test_thresholds_come_from_the_project(conformed3):
    root = conformed3.root
    assert score_take(root, "u01", "t001")["verdict"] == "pass"
    project = read_json(conformed3.project_file)
    project["thresholds"]["camera_min"] = 1.01
    write_json(conformed3.project_file, project)
    shots = score_take(root, "u01", "t001")["shots"]
    assert all(s["verdict"] == "retry" and s["failed"] == ["camera"] for s in shots.values())


def test_skip_names_are_never_failed(tmp_path):
    ref = make_motion_clip(tmp_path / "r.mp4", "pan", rate=4.0)
    take = make_motion_clip(tmp_path / "t.mp4", "pan", rate=-4.0)
    s = score_shot(ref, take, PictureBox(0, 0, 384, 216), T, skip=["camera", "color"])
    assert s["skipped"] == ["camera", "color"] and "camera" not in s["failed"]


def test_report_marks_a_skipped_score(planned3, tmp_path):
    root = planned3.root
    conform(root, "u01", fake_take(root, "u01", tmp_path, filters=RECOLOUR))
    scores = score_take(root, "u01", "t001")
    select(root, "u01", "t001")
    text = write_report(root).read_text(encoding="utf-8")
    rows = {line.split("|")[1].strip(): line for line in text.splitlines() if line.startswith("| ") and "| u01 |" in line}
    marked = f"{scores['shots']['1']['color']:.3f} (\uc81c\uc678)"
    assert marked in rows["1"] and "(\uc81c\uc678)" not in rows["2"]
    legend = text.split("## \uc20f\ubcc4 \uacb0\uacfc")[1].split("## ")[0]
    assert legend.count("(\uc81c\uc678)") == 3                       # shots 1 and 3, and the line under the table


def test_plan_check_and_qc_know_the_same_score_names():
    assert plan.QC_SCORES == SKIPPABLE


def test_single_frame_shot_gives_finite_scores(tmp_path):      # no frame pair: empty camera and motion series
    ref = make_motion_clip(tmp_path / "r.mp4", "static", frames=1)
    s = score_shot(ref, ref, PictureBox(0, 0, 384, 216), T)
    values = [s[k] for k in ("camera", "motion", "color", "flicker_ratio", "overlap_ratio")]
    assert all(math.isfinite(v) for v in values) and s["verdict"] == "pass"
