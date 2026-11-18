import json

import pytest
from conftest import fake_take, make_clip, planned_project

from cine_recast.cli import main
from cine_recast.conform import scan_take
from cine_recast.errors import CineRecastError
from cine_recast.jsonio import read_json
from cine_recast.plan import unit
from cine_recast.takes import list_takes, new_take, select


def test_takes_are_never_overwritten(planned3, clip3):
    a = new_take(planned3.root, "u01", clip3, {"job": "x"}); b = new_take(planned3.root, "u01", clip3, {"job": "y"})
    assert (a, b) == ("t001", "t002") and list_takes(planned3.root, "u01") == ["t001", "t002"]
    select(planned3.root, "u01", "t002")
    assert unit(planned3.root, "u01")["selected_take"] == "t002"
    with pytest.raises(CineRecastError):
        select(planned3.root, "u01", "t009")


# --- extra guards beyond the brief's list ---

def test_take_folder_holds_raw_file_and_meta(planned3, clip3):
    t = new_take(planned3.root, "u01", clip3, {"job": "x"})
    folder = planned3.take_dir("u01", t)
    assert sorted(p.name for p in folder.iterdir()) == ["meta.json", "raw.mp4"]
    assert (folder / "raw.mp4").read_bytes() == clip3.read_bytes()
    assert read_json(folder / "meta.json") == {"job": "x"}


def test_numbering_continues_after_the_highest_take(planned3, clip3):
    (planned3.root / "takes" / "u01" / "t004").mkdir(parents=True)     # a gap left by hand
    assert new_take(planned3.root, "u01", clip3, {}) == "t005"
    assert list_takes(planned3.root, "u01") == ["t004", "t005"]
    assert list_takes(planned3.root, "u02") == []


@pytest.mark.parametrize("call", [
    lambda r, clip, tmp: new_take(r, "u09", clip, {}),                     # unknown unit
    lambda r, clip, tmp: new_take(r, "u01", tmp / "missing.mp4", {}),      # missing source file
    lambda r, clip, tmp: select(r, "u09", "t001"),                         # unknown unit
])
def test_take_refusals(planned3, clip3, tmp_path, call):
    with pytest.raises(CineRecastError):
        call(planned3.root, clip3, tmp_path)
    assert not (planned3.root / "takes").exists()


# --- task 19: takes cuts, the boundaries found in a take after the frame-rate conversion ---

@pytest.fixture
def held(tmp_path):
    """A one-unit project: a static shot of 24 frames and a moving shot of 40. The unit lasts 64
    frames, and its clip is padded to the example model's 4 s (96 frames) with its last frame."""
    clip = make_clip(tmp_path / "ref.mp4", [("smptebars", 24), ("testsrc2", 40)])
    return planned_project(tmp_path / "p", clip)


def _snapshot(folder):
    return {p.relative_to(folder).as_posix(): p.read_bytes() for p in folder.rglob("*") if p.is_file()}


def test_takes_cuts_finds_a_shifted_cut_and_the_held_tail(held, tmp_path, capsys):
    root = held.root
    assert unit(root, "u01")["pad_frames"] == 32
    # The model's take starts 3 frames late: the cut moves from 24 to 21, the last moving frame from
    # 63 to 60, and the held copies of it follow from 61 up to the end.
    t = fake_take(root, "u01", tmp_path, filters="trim=start_frame=3,setpts=PTS-STARTPTS", extra_frames=0)
    folder = held.take_dir("u01", t)
    before = _snapshot(folder)
    capsys.readouterr()
    assert main(["takes", "cuts", str(root), "--unit", "u01", "--take", t]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "frames": 93, "unit_frames": 64, "pad_frames": 32, "cuts": [21], "tail_static_from": 61}
    assert _snapshot(folder) == before and sorted(p.name for p in folder.iterdir()) == ["meta.json", "raw.mp4"]


def test_takes_cuts_reports_a_take_without_a_held_tail(held, result_clip):
    t = new_take(held.root, "u01", result_clip, {"job": "x"})          # 48 moving frames, no cut
    assert scan_take(held.root, "u01", t) == {
        "frames": 48, "unit_frames": 64, "pad_frames": 32, "cuts": [], "tail_static_from": None}


@pytest.mark.parametrize("copies, expected", [(4, None), (5, 64)])
def test_a_held_tail_needs_six_frames(held, tmp_path, copies, expected):
    # the 64 moving frames, then `copies` copies of the last one: a run of copies + 1 frames; the
    # value is the first copy, so --take-cuts "0,<value>" keeps the last moving frame 63
    t = fake_take(held.root, "u01", tmp_path, filters="trim=end_frame=64", extra_frames=copies)
    assert scan_take(held.root, "u01", t)["tail_static_from"] == expected


def test_takes_cuts_refusals(held, capsys):
    root = str(held.root)
    for args in (["--unit", "u01", "--take", "t009"], ["--unit", "u07", "--take", "t001"]):
        capsys.readouterr()
        assert main(["takes", "cuts", root, *args]) == 2
        assert capsys.readouterr().err.startswith("error: ")
