import pytest
from conftest import make_clip

from cine_recast.cuts import Shot, detect_cuts, shots_from_cuts
from cine_recast.errors import CineRecastError
from cine_recast.ffmpeg import probe
from cine_recast.picture import PictureBox


def cuts_of(path):
    info = probe(path)
    return detect_cuts(path, info, PictureBox(0, 0, info.width, info.height))


def test_three_segments(clip3):
    assert cuts_of(clip3) == [30, 54]


def test_fast_motion_without_cuts(tmp_path):
    assert cuts_of(make_clip(tmp_path / "m.mp4", [("mandelbrot", 72)])) == []


def test_single_white_flash_is_not_a_cut(tmp_path):
    p = make_clip(tmp_path / "f.mp4", [("smptebars", 20), ("color=c=white", 1), ("smptebars", 20)])
    assert cuts_of(p) == []


def test_three_frame_insert_gives_two_cuts(tmp_path):
    p = make_clip(tmp_path / "i.mp4", [("smptebars", 20), ("rgbtestsrc", 3), ("smptebars", 20)])
    assert cuts_of(p) == [20, 23]


def test_cuts_inside_letterbox(tmp_path):
    p = make_clip(tmp_path / "l.mp4", [("testsrc2", 30), ("smptebars", 24)], bars=28, dark=True)
    assert detect_cuts(p, probe(p), PictureBox(0, 28, 384, 160)) == [30]


def test_shots_from_cuts():
    assert shots_from_cuts([30, 54], 90) == [Shot(1, 0, 30), Shot(2, 30, 54), Shot(3, 54, 90)]
    assert shots_from_cuts([], 90) == [Shot(1, 0, 90)]
    for bad in ([54, 30], [0, 30], [30, 90], [30, 30]):
        with pytest.raises(CineRecastError):
            shots_from_cuts(bad, 90)
