import numpy as np
import pytest
from conftest import make_clip

from cine_recast.errors import CineRecastError
from cine_recast.ffmpeg import cut_frames, iter_frames, probe, run


def test_probe_counts_frames(clip3):
    info = probe(clip3)
    assert (info.width, info.height, info.fps_num, info.fps_den, info.frames) == (384, 216, 24, 1, 90)
    assert info.has_audio is False


def test_probe_ntsc_rate(tmp_path):
    p = make_clip(tmp_path / "n.mp4", [("testsrc2", 48)], fps="24000/1001")
    info = probe(p)
    assert (info.fps_num, info.fps_den, info.frames) == (24000, 1001, 48)


def test_iter_frames_window_matches_full_read(clip3):
    full = list(iter_frames(clip3, gray=True))
    part = list(iter_frames(clip3, gray=True, start=30, count=5))
    assert len(full) == 90 and len(part) == 5
    assert full[0].shape == (216, 384) and full[0].dtype == np.uint8
    assert np.array_equal(part[0], full[30]) and np.array_equal(part[4], full[34])


def test_iter_frames_crop_and_size(clip3):
    f = next(iter_frames(clip3, crop=(0, 28, 384, 160), size=(96, 40)))
    assert f.shape == (40, 96, 3)


def test_cut_frames_exact(clip3, tmp_path):
    out = tmp_path / "s.mp4"
    cut_frames(clip3, 30, 54, out)
    assert probe(out).frames == 24
    mov = tmp_path / "s.mov"
    cut_frames(clip3, 54, 90, mov, codec="prores")
    assert probe(mov).frames == 36 and probe(mov).pix_fmt == "yuv422p10le"


def test_korean_path_with_space(tmp_path):
    p = make_clip(tmp_path / "\ubc14\ud0d5 \ud654\uba74" / "\ud074\ub9bd.mp4", [("testsrc2", 12)])
    assert probe(p).frames == 12
    out = p.with_name("\uc798\ub77c\ub0b8 \ud074\ub9bd.mp4")
    cut_frames(p, 2, 8, out)
    assert probe(out).frames == 6


def test_run_failure_raises(tmp_path):
    with pytest.raises(CineRecastError):
        run(["-i", str(tmp_path / "missing.mp4"), str(tmp_path / "o.mp4")])
