import numpy as np
import pytest
from conftest import make_clip

from cine_recast.errors import CineRecastError
from cine_recast.ffmpeg import cut_frames, iter_frames, probe, probe_size, run


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


def test_iter_frames_raises_on_a_broken_file(tmp_path):
    bad = tmp_path / "broken.mp4"
    bad.write_bytes(b"this is not a video file")
    with pytest.raises(CineRecastError):
        list(iter_frames(bad, size=(64, 36), gray=True))


def test_iter_frames_can_be_closed_early(clip3):
    frames = iter_frames(clip3, gray=True)
    first = next(frames)
    frames.close()                      # must not raise
    assert first.shape == (216, 384)


def test_scaling_keeps_mid_grey(tmp_path):
    p = make_clip(tmp_path / "g.mp4", [("color=c=#808080", 4)])
    unscaled = next(iter_frames(p))
    rgb = next(iter_frames(p, size=(96, 54)))
    assert abs(int(rgb.mean()) - 128) <= 1
    assert abs(float(rgb.mean()) - float(unscaled.mean())) <= 1


def _playlist(tmp_path):
    """A local playlist whose one segment is a URL (a closed port on this machine, never reached)."""
    path = tmp_path / "list.m3u8"
    path.write_text("#EXTM3U\n#EXT-X-TARGETDURATION:2\n#EXTINF:2.0,\nhttp://127.0.0.1:9/seg0.ts\n#EXT-X-ENDLIST\n",
                    encoding="ascii")
    return path


@pytest.mark.parametrize("call", [
    lambda path, out: probe(path),
    lambda path, out: list(iter_frames(path, size=(64, 36))),
])
def test_inputs_are_opened_with_the_file_and_pipe_protocols_only(tmp_path, call):
    with pytest.raises(CineRecastError, match="whitelist 'file,pipe'"):
        call(_playlist(tmp_path), tmp_path / "x.png")


def test_run_opens_its_inputs_with_the_file_and_pipe_protocols_only(tmp_path):
    with pytest.raises(CineRecastError, match="whitelist 'file,pipe'"):
        run(["-v", "error", "-i", str(_playlist(tmp_path)), "-f", "null", "-"])


def test_run_puts_the_protocol_whitelist_before_every_input():
    from cine_recast.ffmpeg import _guard_inputs

    assert _guard_inputs(["-f", "concat", "-i", "a.txt", "-i", "b.wav", "-map", "0:v", "out.mp4"]) == [
        "-f", "concat", "-protocol_whitelist", "file,pipe", "-i", "a.txt",
        "-protocol_whitelist", "file,pipe", "-i", "b.wav", "-map", "0:v", "out.mp4"]


def test_probe_size_matches_probe(clip3):
    info = probe(clip3)
    assert probe_size(clip3) == (info.width, info.height)
