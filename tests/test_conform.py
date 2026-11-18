import pytest
from conftest import fake_take, make_clip, planned_project, write_models

from cine_recast.cli import main
from cine_recast.conform import conform
from cine_recast.errors import CineRecastError
from cine_recast.ffmpeg import iter_frames, probe
from cine_recast.jsonio import read_json
from cine_recast.plan import build
from cine_recast.takes import new_take


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
    assert read_json(folder / "conform.json") == {                     # the unit clip is padded to 4 s, 96 frames
        "start_frame": 5, "retimed": True, "source_frames": 76, "unit_frames": 90}
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


# --- task 19: conform --take-cuts, each shot cut from the take by its own boundaries ---

def _gray(path):
    import numpy as np
    return [frame.astype(np.int16) for frame in iter_frames(path, size=(64, 36), gray=True)]


def _mad(a, b):
    import numpy as np
    return float(np.abs(a - b).mean())


def _closest_shot(frame, refs):
    """The reference shot (number) holding the frame most like `frame`."""
    return min(refs, key=lambda n: min(_mad(frame, r) for r in refs[n]))


def _shifted_take(pp, tmp_path):
    """A take of u01 (shots of 30, 24 and 36 frames) made from the same sources, with its cuts at 26
    and 50 instead of 30 and 54 and 91 frames instead of 90."""
    clip = make_clip(tmp_path / "shifted.mp4", [("testsrc2", 26), ("smptebars", 24), ("mandelbrot", 41)],
                     size=(640, 360))
    return new_take(pp.root, "u01", clip, {"job": "fake"})


SHIFTED_CUTS = [0, 26, 50, 91]


def test_take_cuts_cut_every_shot_by_its_own_boundaries(planned3, tmp_path):
    t = _shifted_take(planned3, tmp_path)
    refs = {n: _gray(planned3.shot_dir(n) / "ref.mp4") for n in (1, 2, 3)}
    naive = conform(planned3.root, "u01", t)                            # frame for frame from the unit's start
    assert _closest_shot(_gray(naive[0])[-1], refs) == 2                # shot 1 ends in shot 2: the test can tell
    out = conform(planned3.root, "u01", t, take_cuts=SHIFTED_CUTS)
    assert shot_frames(out) == [30, 24, 36]
    for number, path in zip((1, 2, 3), out):
        frames = _gray(path)
        assert [_closest_shot(f, refs) for f in frames] == [number] * len(frames), f"shot {number}"
    take = _gray(planned3.take_dir("u01", t) / "raw.mp4")
    first = _gray(out[0])                                               # 26 take frames spread over 30
    assert [min(range(26), key=lambda k: _mad(f, take[k])) for f in first] == [
        int((j + 0.5) * 26 / 30) for j in range(30)]


def test_take_cuts_record(planned3, tmp_path):
    t = _shifted_take(planned3, tmp_path)
    folder = planned3.take_dir("u01", t)
    before = {p.name: p.read_bytes() for p in folder.iterdir()}
    conform(planned3.root, "u01", t, take_cuts=SHIFTED_CUTS)
    assert read_json(folder / "conform.json") == {
        "start_frame": 0, "retimed": True, "source_frames": 91, "unit_frames": 90, "take_cuts": SHIFTED_CUTS,
        "segments": [{"shot": 1, "take_start": 0, "take_end": 26, "frames": 30, "retimed": True},
                     {"shot": 2, "take_start": 26, "take_end": 50, "frames": 24, "retimed": False},
                     {"shot": 3, "take_start": 50, "take_end": 91, "frames": 36, "retimed": True}]}
    assert {name: (folder / name).read_bytes() for name in before} == before
    conform(planned3.root, "u01", t, take_cuts=[1, 26, 50, 86])        # starts later; shot 3 fits exactly
    record = read_json(folder / "conform.json")
    assert (record["start_frame"], record["retimed"], record["take_cuts"]) == (1, True, [1, 26, 50, 86])
    assert [(s["take_start"], s["take_end"], s["retimed"]) for s in record["segments"]] == [
        (1, 26, True), (26, 50, False), (50, 86, False)]


@pytest.mark.parametrize("options, fragment", [
    ({"take_cuts": [0, 26, 91]}, "4 values"),                          # three shots need four values
    ({"take_cuts": [0, 26.0, 50, 91]}, "whole numbers"),
    ({"take_cuts": [0, True, 50, 91]}, "whole numbers"),
    ({"take_cuts": [0, 50, 26, 91]}, "ascending"),
    ({"take_cuts": [0, 26, 26, 91]}, "ascending"),
    ({"take_cuts": [-1, 26, 50, 91]}, "0 or more"),
    ({"take_cuts": [0, 26, 50, 92]}, "larger than the take's 91 frames"),
    ({"take_cuts": [0, 20, 50, 91]}, "shot 1: .*20 take frames.* 30 frames.*boundaries are probably wrong.*generated again"),
    ({"take_cuts": [0, 26, 57, 91]}, "shot 2: .*31 take frames.* 24 frames"),
    ({"take_cuts": SHIFTED_CUTS, "start_frame": 3}, "--start-frame"),
    ({"take_cuts": SHIFTED_CUTS, "allow_retime": True}, "--allow-retime"),
])
def test_take_cuts_refusals(planned3, tmp_path, options, fragment):
    t = _shifted_take(planned3, tmp_path)
    folder = planned3.take_dir("u01", t)
    with pytest.raises(CineRecastError, match=fragment):
        conform(planned3.root, "u01", t, **options)
    assert sorted(p.name for p in folder.iterdir()) == ["meta.json", "raw.mp4"]


def test_take_cuts_on_a_padded_one_shot_take(cast3, tmp_path):
    write_models(cast3.root)                                           # min_seconds 4: shot 1 padded by 66 frames
    build(cast3.root, model="seedance_2_5", method="edit", batch="b", per_shot=True)
    t = fake_take(cast3.root, "u01", tmp_path)                          # the padded clip and 12 more held frames
    [clip] = conform(cast3.root, "u01", t, take_cuts=[0, 30])
    assert probe(clip).frames == 30
    assert read_json(cast3.take_dir("u01", t) / "conform.json") == {
        "start_frame": 0, "retimed": False, "source_frames": 108, "unit_frames": 30, "take_cuts": [0, 30],
        "segments": [{"shot": 1, "take_start": 0, "take_end": 30, "frames": 30, "retimed": False}]}
    ref = _gray(cast3.shot_dir(1) / "ref.mp4")
    assert max(_mad(a, b) for a, b in zip(_gray(clip), ref)) < 3       # the shot frame for frame, no held frame
    [clip] = conform(cast3.root, "u01", t, take_cuts=[0, 29])          # ending where the picture freezes
    assert probe(clip).frames == 30 and read_json(cast3.take_dir("u01", t) / "conform.json")["retimed"] is True


def test_cli_conform_passes_take_cuts(monkeypatch, tmp_path, capsys):
    calls = []

    def fake_conform(root, unit_id, take_id, **options):
        calls.append(options)
        return [tmp_path / "0001.mov"]

    monkeypatch.setattr("cine_recast.conform.conform", fake_conform)
    args = ["conform", str(tmp_path), "--unit", "u01", "--take", "t001"]
    assert main([*args, "--take-cuts", "0,26, 50,91"]) == 0
    assert calls == [{"start_frame": 0, "allow_retime": False, "take_cuts": [0, 26, 50, 91]}]
    capsys.readouterr()
    for text in ("0,a,50", "0,26,,91", "0,2.5"):
        assert main([*args, "--take-cuts", text]) == 2
        assert "--take-cuts" in capsys.readouterr().err
    assert len(calls) == 1
