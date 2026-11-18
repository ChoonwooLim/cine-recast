"""sound mix: placement to the frame, the plain sum, loudness and peak, soundtrack.json, and the
reference check. All sound is synthetic (ffmpeg sine, silence and noise sources)."""

import json
import subprocess

import numpy as np
import pytest
from conftest import AUDIO_MODEL, EXAMPLE_MODEL, _encode_args, _run, done_sound, make_noise, make_tone

from cine_recast import sound, soundtrack
from cine_recast.cli import main
from cine_recast.errors import CineRecastError
from cine_recast.ffmpeg import decode_audio
from cine_recast.jsonio import read_json, write_json
from cine_recast.paths import sha256_of

RATE = 48000
FRAME = RATE // 24                       # samples per film frame of the 24 fps test film


def _stream(path):
    proc = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries",
                           "stream=codec_name,sample_rate,channels,duration_ts", "-of", "json", str(path)],
                          capture_output=True, text=True, check=True)
    return json.loads(proc.stdout)["streams"][0]


def _levels(path):
    """RMS level in dBFS of each film frame of the left channel."""
    x = decode_audio(path, rate=RATE, channels=2)[:, 0].astype(np.float64)
    frames = len(x) // FRAME
    rms = np.sqrt((x[:frames * FRAME].reshape(frames, FRAME) ** 2).mean(axis=1))
    return 20 * np.log10(np.maximum(rms, 1e-9))


def _amplitude(x, frequency, start, length=4800):
    """Amplitude of the sine component at `frequency` in x[start:start + length] (whole periods)."""
    t = np.arange(length) / RATE
    part = x[start:start + length].astype(np.float64)
    s, c = part @ np.sin(2 * np.pi * frequency * t), part @ np.cos(2 * np.pi * frequency * t)
    return 2 * np.hypot(s, c) / length


def test_a_tone_starts_at_its_frame_and_the_mix_lasts_the_film(sound3, tmp_path):
    root = sound3.root
    done_sound(root, make_tone(tmp_path / "tone.wav", 1, frequency=1000), seconds=1, at_frame=30, fade_out=0)
    result = soundtrack.mix(root)
    assert result["path"] == sound3.soundtrack_file
    info = _stream(sound3.soundtrack_file)
    assert (info["codec_name"], info["sample_rate"], info["channels"]) == ("pcm_s24le", "48000", 2)
    assert int(info["duration_ts"]) == 90 * FRAME                             # 3.75 s, to the sample
    levels = _levels(sound3.soundtrack_file)
    first = int(np.argmax(levels > -40))
    assert abs(first - 30) <= 1 and levels[:29].max() < -80 and levels[32:53].min() > -30
    assert levels[56:].max() < -80                                            # one second: frames 30 to 53
    assert abs(result["loudness_after"] + 16) <= 1 and result["peak_dbfs"] <= -1 + 0.3


def test_two_overlapping_sounds_are_both_heard_at_their_own_level(sound3, tmp_path):
    root = sound3.root
    done_sound(root, make_tone(tmp_path / "low.wav", 3, frequency=1000), seconds=3, at_frame=0, fade_out=0)
    done_sound(root, make_tone(tmp_path / "high.wav", 2, frequency=3000), seconds=2, at_frame=36, fade_out=0)
    soundtrack.mix(root)
    x = decode_audio(sound3.soundtrack_file, rate=RATE, channels=2)[:, 0]
    alone, both = 6 * FRAME, 50 * FRAME                                       # before and inside the overlap
    low_alone, low_both, high_both = (_amplitude(x, 1000, alone), _amplitude(x, 1000, both),
                                      _amplitude(x, 3000, both))
    assert _amplitude(x, 3000, alone) < 0.01 * low_alone                      # the high tone has not started
    assert abs(20 * np.log10(low_both / low_alone)) < 0.5                     # a plain sum: no level change
    assert abs(20 * np.log10(high_both / low_both)) < 0.5                     # equal tones, equal gain


def test_gain_and_fades_shape_each_sound(sound3, tmp_path):
    root = sound3.root
    done_sound(root, make_tone(tmp_path / "a.wav", 3, frequency=1000), seconds=3, at_frame=0, fade_out=0)
    done_sound(root, make_tone(tmp_path / "b.wav", 3, frequency=3000), seconds=1, at_frame=24, gain_db=-12,
               fade_in=0.5, fade_out=0)
    soundtrack.mix(root)
    x = decode_audio(sound3.soundtrack_file, rate=RATE, channels=2)[:, 0]
    a, b = _amplitude(x, 1000, 30 * FRAME), _amplitude(x, 3000, 40 * FRAME)  # b at full level after its fade-in
    assert abs(20 * np.log10(b / a) + 12) < 0.5
    assert _amplitude(x, 3000, 24 * FRAME, 480) < 0.15 * b                    # the first 10 ms of the fade-in
    assert _amplitude(x, 3000, 49 * FRAME) < 0.01 * a                         # cut to its 1 second (to frame 48)


def test_a_sound_past_the_end_is_cut_with_a_short_fade(sound3, tmp_path):
    root = sound3.root
    done_sound(root, make_tone(tmp_path / "long.wav", 3, frequency=1000), seconds=3, at_frame=60, fade_out=0)
    soundtrack.mix(root)
    x = decode_audio(sound3.soundtrack_file, rate=RATE, channels=2)[:, 0]
    assert len(x) == 90 * FRAME
    full = _amplitude(x, 1000, 70 * FRAME)
    assert _amplitude(x, 1000, len(x) - int(0.1 * RATE), 2400) > 0.9 * full  # before the last 50 ms
    assert np.abs(x[-48:]).max() < 0.05 * full                                # faded out at the very end


def test_soundtrack_record(sound3, tmp_path):
    root = sound3.root
    done_sound(root, make_tone(tmp_path / "a.wav", 1, frequency=1000), kind="sfx", seconds=1, at_frame=29,
               gain_db=-3, fade_out=0.1)
    done_sound(root, make_noise(tmp_path / "b.wav", 2, seed=3), kind="ambience", seconds=2, at_frame=60)
    done_sound(root, make_tone(tmp_path / "c.wav", 1, frequency=500), seconds=1, at_frame=54)
    sound.set_placement(root, "s0003", mute=True)                             # muted: not in the mix
    soundtrack.mix(root)
    record = read_json(sound3.soundtrack_record)
    assert set(record) == {"sounds", "loudness_before", "gain_applied_db", "loudness_after", "peak_dbfs",
                           "duration_seconds", "sha256", "reference_correlation", "cuts"}
    assert record["sounds"] == [
        {"id": "s0001", "kind": "sfx", "label": "test sound", "start_seconds": round(29 / 24, 6), "at_frame": 29,
         "seconds": 1, "gain_db": -3.0, "fade_in": 0.0, "fade_out": 0.1},
        {"id": "s0002", "kind": "ambience", "label": "test sound", "start_seconds": 2.5, "at_frame": 60,
         "seconds": 2, "gain_db": 0.0, "fade_in": 0.0, "fade_out": 0.2}]
    assert record["cuts"] == [{"frame": 30, "sounds": ["s0001"]}, {"frame": 54, "sounds": []}]
    assert record["sha256"] == sha256_of(sound3.soundtrack_file) and record["duration_seconds"] == 3.75
    assert record["reference_correlation"] is None                           # clip3 has no sound
    assert abs(record["loudness_before"] + record["gain_applied_db"] + 16) < 0.01
    assert abs(record["loudness_after"] + 16) <= 1 and record["peak_dbfs"] <= -0.7


def _silence(path, seconds):
    _run(["-f", "lavfi", "-i", "anullsrc=sample_rate=48000:channel_layout=stereo", "-t", str(seconds), str(path)])
    return path


def test_mix_refusals(sound3, tmp_path):
    root = sound3.root
    with pytest.raises(CineRecastError, match="no sound is done"):
        soundtrack.mix(root)
    done_sound(root, make_tone(tmp_path / "a.wav", 1), seconds=1)
    sound.set_placement(root, "s0001", mute=True)
    with pytest.raises(CineRecastError, match="no sound is done"):
        soundtrack.mix(root)
    sound.set_placement(root, "s0001", unmute=True, gain_db=-60)
    done_sound(root, _silence(tmp_path / "quiet.wav", 2), seconds=2)
    sound.set_placement(root, "s0001", mute=True)
    with pytest.raises(CineRecastError, match="silence"):
        soundtrack.mix(root)
    data = read_json(sound3.sounds_file); data["sounds"][0].update(muted=False, at_frame=95)
    write_json(sound3.sounds_file, data)                                      # edited by hand outside the film
    with pytest.raises(CineRecastError, match="at_frame"):
        soundtrack.mix(root)
    assert not sound3.soundtrack_file.exists() and not sound3.soundtrack_record.exists()


def test_a_done_sound_with_a_missing_file_is_refused(sound3, tmp_path):
    done_sound(sound3.root, make_tone(tmp_path / "a.wav", 1), seconds=1)
    (sound3.sounds_dir / "s0001.wav").unlink()
    with pytest.raises(CineRecastError, match="s0001.wav"):
        soundtrack.mix(sound3.root)


# --- the reference check -----------------------------------------------------------------

@pytest.fixture(scope="session")
def _noisy_template(tmp_path_factory):
    """A one-shot project whose reference carries pink noise (seed 7) as its sound, analysed, with the
    audio model; its sound extracted to a WAV file outside the project."""
    from cine_recast.analyze import analyze
    from cine_recast.project import init_project

    base = tmp_path_factory.mktemp("noisy")
    clip = base / "noisy.mp4"
    _run(["-f", "lavfi", "-i", "testsrc2=size=384x216:rate=24", "-f", "lavfi", "-i",
          "anoisesrc=color=pink:seed=7:amplitude=0.5:sample_rate=48000", "-frames:v", "96", "-t", "4",
          "-c:a", "aac", "-b:a", "192k", *_encode_args(clip)])
    root = base / "p"
    init_project(root, clip, "t")
    analyze(root)
    write_json(root / "models.json", {"models": [EXAMPLE_MODEL, AUDIO_MODEL]})
    _run(["-i", str(clip), "-vn", "-c:a", "pcm_s16le", str(base / "reference_sound.wav")])
    return base


@pytest.fixture
def noisy(_noisy_template, tmp_path):
    import shutil

    from cine_recast.paths import ProjectPaths

    shutil.copytree(_noisy_template, tmp_path / "n")
    pp = ProjectPaths(tmp_path / "n" / "p")
    pp.reference_sound = tmp_path / "n" / "reference_sound.wav"
    return pp


@pytest.mark.parametrize("at_frame, extra", [(0, False), (6, False), (0, True)])
def test_the_reference_sound_is_refused_and_nothing_is_left(noisy, tmp_path, at_frame, extra):
    root = noisy.root
    done_sound(root, noisy.reference_sound, kind="ambience", seconds=4, at_frame=at_frame)
    if extra:                                                                 # hidden under a quieter music bed
        done_sound(root, make_tone(tmp_path / "bed.wav", 4, frequency=700, volume=0.2), kind="music", seconds=4,
                   gain_db=-6)
    with pytest.raises(CineRecastError, match="reference") as refused:
        soundtrack.mix(root)
    assert "correlation" in refused.value.message
    assert not noisy.out_dir.exists() or not any(noisy.out_dir.iterdir())    # no soundtrack, no scratch file


def test_an_unrelated_sound_passes_the_reference_check(noisy, tmp_path, capsys):
    root = noisy.root
    done_sound(root, make_noise(tmp_path / "other.wav", 4, seed=99), kind="ambience", seconds=4)
    capsys.readouterr()
    assert main(["sound", "mix", str(root)]) == 0
    lines = capsys.readouterr().out.splitlines()
    record = read_json(noisy.soundtrack_record)
    assert 0 <= record["reference_correlation"] < 0.2
    assert lines[0] == str(noisy.soundtrack_file)
    assert lines[1] == (f"loudness {record['loudness_after']} LUFS  peak {record['peak_dbfs']} dBFS  reference "
                        f"correlation {record['reference_correlation']}")


def test_max_correlation():
    rng = np.random.default_rng(5)
    x = rng.standard_normal(16000)
    other = rng.standard_normal(16000)
    shifted = np.concatenate([np.zeros(2400), x])[:16000]                     # 0.3 s later at 8 kHz
    assert soundtrack.max_correlation(x, x, 4000) == pytest.approx(1.0, abs=1e-9)
    assert soundtrack.max_correlation(shifted, x, 4000) > 0.99
    assert soundtrack.max_correlation(x, shifted, 4000) > 0.99
    assert soundtrack.max_correlation(-0.1 * x, x, 4000) == pytest.approx(1.0, abs=1e-9)
    assert soundtrack.max_correlation(np.concatenate([np.zeros(4800), x])[:16000], x, 4000) < 0.1   # beyond 0.5 s
    assert soundtrack.max_correlation(other, x, 4000) < 0.1
    assert soundtrack.max_correlation(np.zeros(16000), x, 4000) == 0.0
