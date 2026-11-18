import json
import shutil
import subprocess

import pytest
from conftest import fake_soundtrack

from cine_recast.assemble import assemble, compare
from cine_recast.cli import main
from cine_recast.errors import CineRecastError
from cine_recast.ffmpeg import probe, run
from cine_recast.jsonio import read_json, write_json


def _audio(path):
    """The audio streams of a file: codec, sample rate, channels, duration and bit rate."""
    proc = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
                           "stream=codec_name,sample_rate,channels,duration,bit_rate", "-of", "json", str(path)],
                          capture_output=True, text=True, check=True)
    return json.loads(proc.stdout).get("streams") or []


def test_assemble_matches_reference_length(finished3):
    result = assemble(finished3.root)
    assert result.path == finished3.out_dir / "final.mp4" and result.sound == "none"
    assert probe(finished3.out_dir / "master.mov").frames == 90
    info = probe(result.path)
    assert info.frames == 90 and info.has_audio is False and (info.width, info.height) == (384, 216)


def test_assemble_with_supplied_audio(finished3, tmp_path):
    tone = tmp_path / "tone.wav"
    run(["-f", "lavfi", "-i", "sine=f=440:d=10", str(tone)])
    result = assemble(finished3.root, audio=tone)
    info = probe(result.path)
    assert info.has_audio is True and abs(info.duration - 3.75) < 0.1 and result.sound == "file tone.wav"


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


# --- task 20: the soundtrack, --silent, and the length of the sound --------------------------

def test_the_soundtrack_goes_into_the_film(finished3, capsys):
    fake_soundtrack(finished3, items=3)
    capsys.readouterr()
    assert main(["assemble", str(finished3.root)]) == 0
    assert capsys.readouterr().out.splitlines() == [str(finished3.out_dir / "final.mp4"), "sound: soundtrack (3 items)"]
    [final] = _audio(finished3.out_dir / "final.mp4")
    assert (final["codec_name"], final["sample_rate"], final["channels"]) == ("aac", "48000", 2)
    assert abs(float(final["duration"]) - 3.75) < 0.05 and abs(int(final["bit_rate"]) - 256000) < 40000
    [master] = _audio(finished3.out_dir / "master.mov")
    assert master["codec_name"] == "pcm_s24le" and abs(float(master["duration"]) - 3.75) < 0.001
    assert probe(finished3.out_dir / "final.mp4").frames == 90 and probe(finished3.out_dir / "master.mov").frames == 90


@pytest.mark.parametrize("damage", [
    lambda pp: pp.soundtrack_file.write_bytes(pp.soundtrack_file.read_bytes()[:-3] + b"\x01\x02\x03"),
    lambda pp: pp.soundtrack_record.unlink(),
    lambda pp: write_json(pp.soundtrack_record, {**read_json(pp.soundtrack_record), "sha256": "0" * 64}),
])
def test_a_soundtrack_changed_after_the_mix_is_refused(finished3, damage):
    fake_soundtrack(finished3)
    damage(finished3)
    with pytest.raises(CineRecastError, match="sound mix again"):
        assemble(finished3.root)
    assert not (finished3.out_dir / "final.mp4").exists() and not (finished3.out_dir / "master.mov").exists()


def test_silent_leaves_the_soundtrack_out(finished3, tmp_path, capsys):
    fake_soundtrack(finished3)
    result = assemble(finished3.root, silent=True)
    assert result.sound == "none" and _audio(result.path) == [] and _audio(finished3.out_dir / "master.mov") == []
    with pytest.raises(CineRecastError, match="--silent"):
        assemble(finished3.root, silent=True, audio=tmp_path / "x.wav")
    capsys.readouterr()
    assert main(["assemble", str(finished3.root), "--silent", "--audio", str(tmp_path / "x.wav")]) == 2


def test_a_supplied_audio_file_wins_over_the_soundtrack_and_is_padded(finished3, tmp_path):
    fake_soundtrack(finished3)
    tone = tmp_path / "short.wav"
    run(["-f", "lavfi", "-i", "sine=f=440:d=2", str(tone)])                  # shorter than the film
    result = assemble(finished3.root, audio=tone)
    assert result.sound == "file short.wav"
    [final] = _audio(result.path)
    assert final["codec_name"] == "aac" and abs(float(final["duration"]) - 3.75) < 0.05
    [master] = _audio(finished3.out_dir / "master.mov")
    assert master["codec_name"] == "pcm_s24le" and abs(float(master["duration"]) - 3.75) < 0.001
