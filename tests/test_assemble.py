import shutil

import pytest

from cine_recast.assemble import assemble, compare
from cine_recast.errors import CineRecastError
from cine_recast.ffmpeg import probe, run


def test_assemble_matches_reference_length(finished3):
    final = assemble(finished3.root)
    assert probe(finished3.out_dir / "master.mov").frames == 90
    info = probe(final)
    assert info.frames == 90 and info.has_audio is False and (info.width, info.height) == (384, 216)


def test_assemble_with_supplied_audio(finished3, tmp_path):
    tone = tmp_path / "tone.wav"
    run(["-f", "lavfi", "-i", "sine=f=440:d=10", str(tone)])
    info = probe(assemble(finished3.root, audio=tone))
    assert info.has_audio is True and abs(info.duration - 3.75) < 0.1


def _in_out_folder(pp):
    """A file in out/, as compare.mp4 is (it carries the reference sound)."""
    pp.out_dir.mkdir(parents=True, exist_ok=True)
    target = pp.out_dir / "compare.mp4"
    shutil.copyfile(pp.take_dir("u01", "t001") / "raw.mp4", target)
    return target


@pytest.mark.parametrize("make", [
    lambda pp, clip: pp.root / "reference" / "source.mp4",
    lambda pp, clip: pp.shot_dir(1) / "ref.mp4",
    lambda pp, clip: clip,                                      # the original file given to init
    lambda pp, clip: pp.take_dir("u01", "t001") / "raw.mp4",    # a take may carry the reference sound
    lambda pp, clip: _in_out_folder(pp),
])
def test_assemble_refuses_reference_material_as_audio(finished3, clip3, make):
    audio = make(finished3, clip3)
    with pytest.raises(CineRecastError, match="must be a file made for the film"):
        assemble(finished3.root, audio=audio)
    assert not (finished3.out_dir / "final.mp4").exists()


def test_assemble_needs_a_selected_take(planned3):
    with pytest.raises(CineRecastError, match="u01"):
        assemble(planned3.root)


def test_compare_is_side_by_side(finished3):
    assemble(finished3.root)
    info = probe(compare(finished3.root))
    assert info.height == 540 and info.width == 1920 and info.frames == 90
