from conftest import make_clip

from cine_recast.ffmpeg import probe
from cine_recast.picture import PictureBox, detect_picture_box

SEG = [("testsrc2", 24), ("smptebars", 24)]


def box_of(path):
    return detect_picture_box(path, probe(path))


def test_full_frame_when_no_bars(tmp_path):
    assert box_of(make_clip(tmp_path / "a.mp4", SEG)) == PictureBox(0, 0, 384, 216)


def test_letterbox(tmp_path):
    assert box_of(make_clip(tmp_path / "b.mp4", SEG, bars=28)) == PictureBox(0, 28, 384, 160)


def test_dark_low_contrast_picture_is_not_mistaken_for_bars(tmp_path):
    assert box_of(make_clip(tmp_path / "c.mp4", SEG, bars=28, dark=True)) == PictureBox(0, 28, 384, 160)


def test_logo_in_bar_is_ignored(tmp_path):
    assert box_of(make_clip(tmp_path / "d.mp4", SEG, bars=28, logo=True)) == PictureBox(0, 28, 384, 160)


def test_pillarbox(tmp_path):
    assert box_of(make_clip(tmp_path / "e.mp4", SEG, side_bars=48)) == PictureBox(48, 0, 288, 216)


def test_dict_roundtrip_and_aspect():
    b = PictureBox(0, 140, 1920, 800)
    assert PictureBox.from_dict(b.to_dict()) == b and abs(b.aspect - 2.4) < 1e-9
