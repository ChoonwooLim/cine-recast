import json
import math

from conftest import make_motion_clip

from cine_recast.cli import main
from cine_recast.ffmpeg import run
from cine_recast.jsonio import read_json
from cine_recast.picture import PictureBox
from cine_recast.qc import DEFAULT_THRESHOLDS, score_shot, score_take

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

def test_single_frame_shot_gives_finite_scores(tmp_path):      # no frame pair: empty camera and motion series
    ref = make_motion_clip(tmp_path / "r.mp4", "static", frames=1)
    s = score_shot(ref, ref, PictureBox(0, 0, 384, 216), T)
    values = [s[k] for k in ("camera", "motion", "color", "flicker_ratio", "overlap_ratio")]
    assert all(math.isfinite(v) for v in values) and s["verdict"] == "pass"
