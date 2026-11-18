import pytest
from conftest import fake_take, make_clip, planned_project

from cine_recast.cli import main
from cine_recast.conform import conform
from cine_recast.errors import CineRecastError
from cine_recast.ffmpeg import iter_frames, probe
from cine_recast.jsonio import read_json


def shot_frames(paths):
    return [probe(p).frames for p in paths]

def test_conform_matches_reference_exactly(planned3, tmp_path):        # one unit u01 covering shots 1-3
    t = fake_take(planned3.root, "u01", tmp_path)
    out = conform(planned3.root, "u01", t)
    assert [p.name for p in out] == ["0001.mov", "0002.mov", "0003.mov"] and shot_frames(out) == [30, 24, 36]
    info = probe(out[0])
    assert (info.width, info.height, info.fps_num, info.fps_den, info.pix_fmt) == (384, 216, 24, 1, "yuv422p10le")
    assert read_json(planned3.take_dir("u01", t) / "conform.json")["retimed"] is False

def test_short_take_fails_loudly(planned3, tmp_path):
    t = fake_take(planned3.root, "u01", tmp_path, extra_frames=-20)
    with pytest.raises(CineRecastError, match="too short"):
        conform(planned3.root, "u01", t)
    assert shot_frames(conform(planned3.root, "u01", t, allow_retime=True)) == [30, 24, 36]

def test_other_aspect_is_cropped_to_fill(planned3, tmp_path):
    t = fake_take(planned3.root, "u01", tmp_path, size=(360, 360))
    info = probe(conform(planned3.root, "u01", t)[0])
    assert (info.width, info.height) == (384, 216)

def test_start_frame_offset(planned3, tmp_path):
    t = fake_take(planned3.root, "u01", tmp_path, extra_frames=12)
    conform(planned3.root, "u01", t, start_frame=10)
    assert read_json(planned3.take_dir("u01", t) / "conform.json")["start_frame"] == 10
    with pytest.raises(CineRecastError, match="too short"):
        conform(planned3.root, "u01", t, start_frame=40)

def test_letterboxed_ntsc_reference(tmp_path):
    clip = make_clip(tmp_path / "r.mp4", [("testsrc2", 30), ("smptebars", 18)], fps="24000/1001", bars=28)
    pp = planned_project(tmp_path / "p", clip)                          # helper: init..plan with one unit
    t = fake_take(pp.root, "u01", tmp_path, fps="25")
    out = conform(pp.root, "u01", t)
    assert shot_frames(out) == [30, 18]
    info = probe(out[0])
    assert (info.fps_num, info.fps_den, info.height) == (24000, 1001, 216)
    top = next(iter_frames(out[0], gray=True))[:28]
    assert top.max() <= 20                                              # bar stays black


# --- extra guards beyond the brief's list ---

def test_retimed_record_and_take_left_untouched(planned3, tmp_path):
    t = fake_take(planned3.root, "u01", tmp_path, extra_frames=-20)
    folder = planned3.take_dir("u01", t)
    before = {p.name: p.read_bytes() for p in folder.iterdir()}
    conform(planned3.root, "u01", t, start_frame=5, allow_retime=True)
    assert read_json(folder / "conform.json") == {
        "start_frame": 5, "retimed": True, "source_frames": 70, "unit_frames": 90}
    assert sorted(p.name for p in folder.iterdir()) == ["conform.json", "meta.json", "raw.mp4", "shots"]
    assert {name: (folder / name).read_bytes() for name in before} == before

def test_cli_conform_passes_its_options(monkeypatch, tmp_path, capsys):
    calls = []

    def fake_conform(root, unit_id, take_id, **options):
        calls.append((root, unit_id, take_id, options))
        if not options["allow_retime"]:
            raise CineRecastError("take too short: have 65 frames, need 90")
        return [tmp_path / "0001.mov"]

    monkeypatch.setattr("cine_recast.conform.conform", fake_conform)
    args = ["conform", str(tmp_path), "--unit", "u01", "--take", "t001"]
    assert main([*args, "--start-frame", "5"]) == 2 and "too short" in capsys.readouterr().err
    assert main([*args, "--allow-retime"]) == 0 and "0001.mov" in capsys.readouterr().out
    assert [c[1:] for c in calls] == [("u01", "t001", {"start_frame": 5, "allow_retime": False}),
                                      ("u01", "t001", {"start_frame": 0, "allow_retime": True})]
