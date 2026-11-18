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


@pytest.mark.parametrize("make", [
    lambda pp, clip: pp.root / "reference" / "source.mp4",
    lambda pp, clip: pp.shot_dir(1) / "ref.mp4",
    lambda pp, clip: clip,                                      # the original file given to init
])
def test_assemble_refuses_the_reference_as_audio(finished3, clip3, make):
    with pytest.raises(CineRecastError, match="sound of the reference is never used"):
        assemble(finished3.root, audio=make(finished3, clip3))
    assert not (finished3.out_dir / "final.mp4").exists()


def test_assemble_needs_a_selected_take(planned3):
    with pytest.raises(CineRecastError, match="u01"):
        assemble(planned3.root)


def test_compare_is_side_by_side(finished3):
    assemble(finished3.root)
    info = probe(compare(finished3.root))
    assert info.height == 540 and info.width == 1920 and info.frames == 90
