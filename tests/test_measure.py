import math

import numpy as np

from conftest import make_clip, make_motion_clip

from cine_recast.analyze import analyze
from cine_recast.jsonio import read_json
from cine_recast.measure import camera_series, color_series, flicker, motion_maps


def test_static_clip_has_no_camera_motion(tmp_path):
    s = camera_series(make_motion_clip(tmp_path / "s.mp4", "static"))
    assert s.shape == (23, 3) and np.abs(np.median(s, axis=0)).max() < 0.2


def test_pan_direction_and_speed(tmp_path):
    s = camera_series(make_motion_clip(tmp_path / "p.mp4", "pan", rate=4.0))
    assert abs(np.median(s[:, 0]) - 5.0) < 0.6          # 4 px at width 384 = 5 px at width 480
    assert abs(np.median(s[:, 1])) < 0.3


def test_zoom_rate(tmp_path):
    s = camera_series(make_motion_clip(tmp_path / "z.mp4", "zoom", rate=1.01))
    assert abs(np.median(s[:, 2]) - math.log(1.01)) < 0.003
    assert abs(np.median(s[:, 0])) < 0.6 and abs(np.median(s[:, 1])) < 0.6   # a centred zoom is not a pan


def test_motion_maps_shape_and_energy(tmp_path):
    still = motion_maps(make_motion_clip(tmp_path / "s.mp4", "static"))
    moving = motion_maps(make_motion_clip(tmp_path / "p.mp4", "pan"))
    assert still.shape == (23, 18, 32) and still.dtype == np.float32
    assert moving.mean() > 10 * max(still.mean(), 1e-3)


def test_grey_is_neutral_lab(tmp_path):
    lab = color_series(make_clip(tmp_path / "g.mp4", [("color=c=#808080", 6)]))
    assert lab.shape == (6, 3) and abs(lab[:, 0].mean() - 53.6) < 1.5 and np.abs(lab[:, 1:]).max() < 2.0


def test_flicker():
    steady = np.column_stack([np.full(10, 50.0), np.zeros(10), np.zeros(10)])
    jumpy = steady.copy(); jumpy[::2, 0] = 60.0
    assert flicker(steady) == 0.0 and flicker(jumpy) > 15.0 and flicker(steady[:2]) == 0.0


def test_sheet_gets_measures(project3):
    analyze(project3.root)
    sheet = read_json(project3.sheet_file(1))
    assert len(sheet["camera"]["series"]) == 29 and len(sheet["color"]["lab_mean"]) == 3
    assert isinstance(sheet["flicker"], float)


def test_non_finite_fit_becomes_zero_row(tmp_path, monkeypatch):
    clip = make_motion_clip(tmp_path / "p.mp4", "pan")
    import cine_recast.measure as m
    real = m.cv2.estimateAffinePartial2D

    def fake(a, b, **kw):
        _, inliers = real(a, b, **kw)
        return np.full((2, 3), np.nan), inliers
    monkeypatch.setattr(m.cv2, "estimateAffinePartial2D", fake)
    s = camera_series(clip)
    assert np.isfinite(s).all() and not s.any()
