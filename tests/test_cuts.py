import pytest
from conftest import make_clip, make_sweep_clip

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


def test_fast_occluder_sweep_is_not_a_cut(tmp_path):
    assert cuts_of(make_sweep_clip(tmp_path / "s.mp4")) == []


def _slow_pan(path, *, frames=40, held=8):
    """A smooth pan of 0.5 px per frame (the content is drawn 4x larger, moved 2 px per frame and
    scaled down), then `held` copies of its last frame. On the held-tail measure the pan moves
    about 0.8 per frame, like a slow real shot; the copies differ by almost nothing."""
    from conftest import _encode_args, _run

    w, h = 384 * 4, 216 * 4
    graph = (f"testsrc2=size={w + 2 * frames + 8}x{h}:rate=24,trim=end_frame=1,loop=loop={frames - 1}:size=1,"
             f"setpts=N/(24*TB),crop=w={w}:h={h}:x='n*2':y=0,scale=384:216:flags=area,"
             f"tpad=stop_mode=clone:stop={held},format=yuv420p")
    _run(["-f", "lavfi", "-i", graph, "-frames:v", str(frames + held), *_encode_args(path)])
    return path


def test_a_slow_real_shot_is_not_a_held_tail(tmp_path):
    from cine_recast.cuts import held_tail_start

    path = _slow_pan(tmp_path / "slow.mp4")
    info = probe(path)
    assert held_tail_start(path, PictureBox(0, 0, info.width, info.height)) == 40   # the first held copy


def test_shots_from_cuts():
    assert shots_from_cuts([30, 54], 90) == [Shot(1, 0, 30), Shot(2, 30, 54), Shot(3, 54, 90)]
    assert shots_from_cuts([], 90) == [Shot(1, 0, 90)]
    for bad in ([54, 30], [0, 30], [30, 90], [30, 30]):
        with pytest.raises(CineRecastError):
            shots_from_cuts(bad, 90)
