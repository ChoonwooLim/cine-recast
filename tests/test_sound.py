"""Sound items (sounds.json and the sound commands): the life cycle through the ledger, placement,
and the refusals of add and done. Every sound is made here with ffmpeg sine and noise sources."""

import json
import shutil

import pytest
from conftest import (AUDIO_MODEL, EXAMPLE_MODEL, IMAGE_MODEL, done_sound, make_clip, make_noise, make_tone, png,
                      run_cli)

from cine_recast import sound
from cine_recast.cli import main
from cine_recast.errors import CineRecastError
from cine_recast.jsonio import read_json, write_json
from cine_recast.ledger import approve, audit, job_states

PROMPT = "Heavy wooden door slams shut in a stone hallway, short echo."
HAN = "\u4e2d"


def _add(root, **overrides):
    args = dict(kind="sfx", label="door", prompt=PROMPT, model="audio_model_x", seconds=3, batch="snd-1")
    return sound.add(root, **{**args, **overrides})


def test_add_plans_a_sound_with_its_placement(sound3):
    item = _add(sound3.root, label="\ubb38 \ub2eb\ud798")                            # a Korean label is fine
    assert (item["id"], item["state"], item["kind"], item["seconds"], item["at_frame"]) == ("s0001", "planned", "sfx", 3, 0)
    assert (item["gain_db"], item["fade_in"], item["fade_out"], item["muted"], item["file"]) == (0.0, 0.0, 0.2, False, None)
    second = _add(sound3.root, kind="music", seconds=2.5, shot=2, offset_frames=3, gain_db=-6, fade_in=1, fade_out=0.5)
    assert (second["id"], second["at_frame"], second["seconds"], second["gain_db"]) == ("s0002", 33, 2.5, -6.0)
    assert _add(sound3.root, at_frame=89)["at_frame"] == 89
    assert [i["id"] for i in read_json(sound3.sounds_file)["sounds"]] == ["s0001", "s0002", "s0003"]


@pytest.mark.parametrize("overrides, fragment", [
    (dict(kind="dialogue"), "kind"),
    (dict(prompt="\ubb38\uc774 \ub2eb\ud78c\ub2e4"), "English"),
    (dict(prompt=f"A door {HAN} slams."), "English"),
    (dict(label=f"door {HAN}"), "Han"),
    (dict(label=" "), "label"),
    (dict(model="missing_model"), "not in models.json"),
    (dict(model="seedance_2_5"), "audio model"),
    (dict(seconds=0.5), "1 to 30"),
    (dict(seconds=31), "1 to 30"),
    (dict(seconds=float("nan")), "seconds"),
    (dict(at_frame=90), "90 frames"),
    (dict(at_frame=-1), "at least 0"),
    (dict(shot=4), "unknown shot 4"),
    (dict(shot=1, offset_frames=-1), "at least 0"),
    (dict(shot=3, offset_frames=36), "90 frames"),
    (dict(at_frame=5, shot=2), "--shot"),
    (dict(offset_frames=3), "--offset-frames"),
    (dict(gain_db=13), "gain_db"),
    (dict(gain_db=-61), "gain_db"),
    (dict(fade_in=-0.1), "fade_in"),
    (dict(fade_out=float("inf")), "fade_out"),
    (dict(batch=""), "batch"),
])
def test_add_refusals(sound3, overrides, fragment):
    with pytest.raises(CineRecastError, match=fragment):
        _add(sound3.root, **overrides)
    assert not sound3.sounds_file.exists()


def test_list_shows_what_is_priced_and_sent(sound3):
    _add(sound3.root, shot=3, gain_db=-3)
    [listed] = sound.list_sounds(sound3.root)
    assert listed == {"id": "s0001", "kind": "sfx", "label": "door", "batch": "snd-1", "state": "planned",
                      "est_credits": None, "params": {"model": "audio_model_x", "prompt": PROMPT, "duration": 3},
                      "at_frame": 54, "gain_db": -3.0, "fade_in": 0.0, "fade_out": 0.2, "muted": False, "file": None}


def test_issue_goes_through_the_ledger(sound3):
    root = sound3.root
    _add(root)
    assert sound.issue_next(root, "snd-1") == []                                    # nothing quoted yet
    sound.set_cost(root, "s0001", 4.0)
    with pytest.raises(CineRecastError, match="approval"):
        sound.issue_next(root, "snd-1")
    approve(root, "snd-1", 3.0, "owner")
    with pytest.raises(sound.IssueRefused, match="limit"):                          # 4 credits against a limit of 3
        sound.issue_next(root, "snd-1")
    approve(root, "snd-1", 1.0, "owner")
    assert sound.issue_next(root, "snd-1") == [{"job_id": "s0001", "kind": "sfx", "tool": "generate_audio",
                                                "params": {"model": "audio_model_x", "prompt": PROMPT, "duration": 3},
                                                "medias": []}]
    assert sound.issue_next(root, "snd-1") == [] and job_states(root) == {"s0001": "issued"}


def test_a_changed_sound_must_be_quoted_again(sound3):
    root = sound3.root
    _add(root)
    sound.set_cost(root, "s0001", 4.0); approve(root, "snd-1", 10.0, "owner")
    data = read_json(sound3.sounds_file); data["sounds"][0]["seconds"] = 6; write_json(sound3.sounds_file, data)
    with pytest.raises(CineRecastError, match="sound set-cost"):
        sound.issue_next(root, "snd-1")
    data["sounds"][0]["seconds"] = 3; data["sounds"][0]["prompt"] = "A different door."
    write_json(sound3.sounds_file, data)                                            # the prompt is not priced
    assert [j["job_id"] for j in sound.issue_next(root, "snd-1")] == ["s0001"]


def test_set_cost_refusals(sound3):
    _add(sound3.root)
    for credits in (0, -1, float("nan")):
        with pytest.raises(CineRecastError, match="credits"):
            sound.set_cost(sound3.root, "s0001", credits)
    with pytest.raises(CineRecastError, match="unknown sound"):
        sound.set_cost(sound3.root, "s0009", 1.0)


def _issued(root, **overrides):
    item = _add(root, **overrides)
    sound.set_cost(root, item["id"], 2.0)
    approve(root, "snd-1", 2.0, "owner")
    sound.issue_next(root, "snd-1")
    return item["id"]


def test_done_stores_the_file(sound3, tmp_path):
    root = sound3.root
    sid = _issued(root)
    tone = make_tone(tmp_path / "dl" / "door.WAV", 3)
    assert sound.record_done(root, sid, "prov-1", 1.5, tone) == {"sound": "s0001", "file": "sounds/s0001.wav"}
    assert (sound3.sounds_dir / "s0001.wav").read_bytes() == tone.read_bytes()
    [listed] = sound.list_sounds(root)
    assert (listed["state"], listed["file"]) == ("done", "sounds/s0001.wav") and audit(root)["ok"] is True
    other = make_tone(tmp_path / "dl" / "other.wav", 2, frequency=500)
    with pytest.raises(CineRecastError, match="done"):
        sound.record_done(root, sid, "prov-2", 1.0, other)                          # never replaced
    assert (sound3.sounds_dir / "s0001.wav").read_bytes() == tone.read_bytes()


@pytest.mark.parametrize("suffix", [".mp3", ".m4a", ".ogg", ".flac"])
def test_done_takes_the_usual_audio_formats(sound3, tmp_path, suffix):
    sid = _issued(sound3.root)
    assert sound.record_done(sound3.root, sid, "prov-1", 1.0, make_tone(tmp_path / f"t{suffix}", 2))["file"] == \
        f"sounds/s0001{suffix}"


def _silent_video_as_m4a(pp, clip, tmp):
    target = tmp / "silent.m4a"                         # a video file without an audio stream
    shutil.copyfile(make_clip(tmp / "v.mp4", [("testsrc2", 12)]), target)
    return target


def _bytes_as_wav(pp, clip, tmp):
    target = tmp / "broken.wav"
    target.write_bytes(b"this is not audio")
    return target


def _reference_copy(pp, clip, tmp):
    target = tmp / "ref.m4a"
    shutil.copyfile(clip, target)
    return target


@pytest.mark.parametrize("make, fragment", [
    (lambda pp, clip, tmp: tmp / "missing.wav", "not found"),
    (lambda pp, clip, tmp: make_tone(tmp / "t.aac", 2), ".wav"),
    (_silent_video_as_m4a, "no audio stream"),
    (_bytes_as_wav, "no audio stream"),
    (_reference_copy, "reference video"),
    (lambda pp, clip, tmp: make_tone(pp.root / "reference" / "t.wav", 2), "reference folder"),
    (lambda pp, clip, tmp: make_tone(pp.analysis_dir / "t.wav", 2), "analysis folder"),
    (lambda pp, clip, tmp: make_tone(pp.root / "takes" / "u01" / "t.wav", 2), "takes folder"),
    (lambda pp, clip, tmp: make_tone(pp.out_dir / "soundtrack.wav", 2), "out folder"),
    (lambda pp, clip, tmp: make_tone(pp.root / "assets" / "t.wav", 2), "assets folder"),
    (lambda pp, clip, tmp: make_tone(pp.root / "Sounds" / "s0002.wav", 2), "sounds folder"),
])
def test_done_refusals_leave_nothing(sound3, clip3, tmp_path, make, fragment):
    root = sound3.root
    sid = _issued(root)
    source = make(sound3, clip3, tmp_path)
    before = sound3.ledger_file.read_bytes()
    with pytest.raises(CineRecastError, match=fragment):
        sound.record_done(root, sid, "prov-1", 1.0, source)
    assert sound3.ledger_file.read_bytes() == before and job_states(root)[sid] == "issued"
    assert not (sound3.sounds_dir / "s0001.wav").exists() and sound.list_sounds(root)[0]["file"] is None


def test_done_needs_an_issued_sound(sound3, tmp_path):
    _add(sound3.root)
    with pytest.raises(CineRecastError, match="not issued"):
        sound.record_done(sound3.root, "s0001", "prov-1", 1.0, make_tone(tmp_path / "t.wav", 2))
    assert not sound3.sounds_dir.exists() or not any(sound3.sounds_dir.iterdir())


def test_a_ledger_refusal_removes_the_stored_file(sound3, tmp_path):
    _add(sound3.root)
    data = read_json(sound3.sounds_file); data["sounds"][0]["state"] = "issued"; write_json(sound3.sounds_file, data)
    with pytest.raises(CineRecastError, match="never issued"):
        sound.record_done(sound3.root, "s0001", "prov-1", 1.0, make_tone(tmp_path / "t.wav", 2))
    assert not any(sound3.sounds_dir.iterdir()) and read_json(sound3.sounds_file)["sounds"][0]["file"] is None


def test_leftovers_of_an_interrupted_done_are_replaced(sound3, tmp_path):
    root = sound3.root
    sid = _issued(root)
    sound3.sounds_dir.mkdir()
    (sound3.sounds_dir / "s0001.mp3").write_bytes(b"left over")                   # a stop before ledger.done
    tone = make_tone(tmp_path / "t.wav", 2)
    assert sound.record_done(root, sid, "prov-1", 1.0, tone)["file"] == "sounds/s0001.wav"
    assert sorted(p.name for p in sound3.sounds_dir.iterdir()) == ["s0001.wav"]


def test_fail_and_ids_never_reused(sound3):
    root = sound3.root
    sid = _issued(root)
    assert sound.record_fail(root, sid, "provider error", 0.5)["state"] == "failed"
    with pytest.raises(CineRecastError, match="not issued"):
        sound.record_fail(root, sid, "again")
    sound3.sounds_file.unlink()                                                     # the ledger still knows s0001
    assert _add(root)["id"] == "s0002"
    assert audit(root)["batches"]["snd-1"]["committed"] == 0.5


def test_a_video_job_an_asset_and_a_sound_share_one_batch_limit(planned3, capsys):
    from cine_recast import assets
    from cine_recast.plan import set_cost as set_job_cost

    root = planned3.root
    write_json(planned3.models_file, {"models": [EXAMPLE_MODEL, IMAGE_MODEL, AUDIO_MODEL]})
    assets.add(root, element="sky_battle", prompt="Sunset sky over red dunes.", model="image_model_x", batch="b")
    assets.set_cost(root, "a0001", 6.0)
    _add(root, batch="b"); sound.set_cost(root, "s0001", 5.0)
    set_job_cost(root, "j0001", 40.0)
    approve(root, "b", 47.0, "owner")
    assert [a["job_id"] for a in assets.issue_next(root, "b")] == ["a0001"]
    assert [s["job_id"] for s in sound.issue_next(root, "b")] == ["s0001"]
    capsys.readouterr()
    assert main(["jobs", "next", str(root), "--batch", "b"]) == 2                    # 6 + 5 + 40 > 47
    out = capsys.readouterr()
    assert json.loads(out.out) == [] and "j0001" in out.err and "limit" in out.err
    assert audit(root)["batches"]["b"] == {"limit": 47.0, "committed": 11.0, "remaining": 36.0}


def test_set_changes_the_placement_at_any_time(sound3, tmp_path):
    root = sound3.root
    done_sound(root, make_tone(tmp_path / "t.wav", 2), at_frame=10)
    item = sound.set_placement(root, "s0001", shot=2, offset_frames=2, gain_db=-12, fade_in=0.5, mute=True)
    assert (item["at_frame"], item["gain_db"], item["fade_in"], item["fade_out"], item["muted"]) == (32, -12.0, 0.5, 0.2, True)
    item = sound.set_placement(root, "s0001", at_frame=0, unmute=True)
    assert (item["at_frame"], item["muted"], item["gain_db"]) == (0, False, -12.0)
    assert sound.placement_line(root, item) == "s0001: frame 0 (0.000 s), gain -12.0 dB, fade in 0.5 s, fade out 0.2 s"
    for kwargs, fragment in ((dict(at_frame=90), "90 frames"), (dict(gain_db=20), "gain_db"),
                             (dict(mute=True, unmute=True), "--mute"), (dict(shot=7), "unknown shot")):
        with pytest.raises(CineRecastError, match=fragment):
            sound.set_placement(root, "s0001", **kwargs)
    assert sound.list_sounds(root)[0]["at_frame"] == 0


def test_cli_flow(sound3, tmp_path, capsys):
    root = sound3.root
    capsys.readouterr()
    assert main(["sound", "add", str(root), "--kind", "ambience", "--label", "wind", "--prompt", "Steady wind over "
                 "an open plain.", "--model", "audio_model_x", "--seconds", "4", "--batch", "snd-1", "--shot", "2",
                 "--offset-frames", "1", "--gain-db", "-9", "--fade-in", "0.5", "--fade-out", "1"]) == 0
    assert capsys.readouterr().out.strip() == "s0001"
    run_cli([["sound", "list", root],
             ["sound", "set-cost", root, "--sound", "s0001", "--credits", "3"],
             ["approve", root, "--batch", "snd-1", "--limit", "3", "--by", "owner"]])
    capsys.readouterr()
    assert main(["sound", "next", str(root), "--batch", "snd-1"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["params"] == {"model": "audio_model_x", "prompt": "Steady wind over "
                                                                "an open plain.", "duration": 4}
    noise = make_noise(tmp_path / "wind.flac", 4)
    assert main(["sound", "done", str(root), "--sound", "s0001", "--provider-id", "prov-1", "--credits", "3",
                 "--file", str(noise)]) == 0
    assert capsys.readouterr().out.strip() == "s0001: done, file sounds/s0001.flac"
    assert main(["sound", "set", str(root), "--sound", "s0001", "--at-frame", "12", "--mute"]) == 0
    assert capsys.readouterr().out.strip() == "s0001: frame 12 (0.500 s), gain -9.0 dB, fade in 0.5 s, fade out 1.0 s, muted"
    assert main(["sound", "add", str(root), "--kind", "sfx", "--label", "x", "--prompt", PROMPT, "--model",
                 "audio_model_x", "--seconds", "2", "--batch", "snd-1", "--offset-frames", "2"]) == 2
    assert "--offset-frames" in capsys.readouterr().err


def test_cli_prints_sounds_issued_before_a_refusal(sound3, capsys):
    root = sound3.root
    for _ in range(2):
        sound.set_cost(root, _add(root)["id"], 2.0)
    approve(root, "snd-1", 3.0, "owner")
    capsys.readouterr()
    assert main(["sound", "next", str(root), "--batch", "snd-1"]) == 2
    out = capsys.readouterr()
    assert [j["job_id"] for j in json.loads(out.out)] == ["s0001"] and "s0002" in out.err and "limit" in out.err
    assert main(["sound", "fail", str(root), "--sound", "s0001", "--reason", "not submitted"]) == 0


def _crash_after(monkeypatch, name):
    import cine_recast.ledger as ledger_module
    import cine_recast.sound as sound_module

    real = getattr(ledger_module, name)

    def then_crash(*args, **kwargs):
        record = real(*args, **kwargs)
        monkeypatch.setattr(sound_module, "_save", lambda *a, **k: (_ for _ in ()).throw(OSError("crash")))
        return record

    monkeypatch.setattr(ledger_module, name, then_crash)


def test_sent_params_survive_a_save_that_fails_after_the_ledger_issued(sound3, monkeypatch):
    root = sound3.root
    sound.set_cost(root, _add(root)["id"], 2.0); approve(root, "snd-1", 2.0, "owner")
    [before] = sound.list_sounds(root)
    _crash_after(monkeypatch, "issue")
    with pytest.raises(OSError):
        sound.issue_next(root, "snd-1")
    monkeypatch.undo()
    [after] = sound.list_sounds(root)
    assert after["state"] == "issued" and after["params"] == before["params"]


def test_the_file_survives_a_save_that_fails_after_the_ledger_recorded_done(sound3, tmp_path, monkeypatch):
    root = sound3.root
    sid = _issued(root)
    _crash_after(monkeypatch, "done")
    with pytest.raises(OSError):
        sound.record_done(root, sid, "prov-1", 1.0, make_tone(tmp_path / "t.wav", 2))
    monkeypatch.undo()
    [listed] = sound.list_sounds(root)
    assert (listed["state"], listed["file"]) == ("done", "sounds/s0001.wav")


@pytest.mark.parametrize("text, fragment", [("{", "line 1, column 2"), ('{"sounds": {}}', "'sounds'")])
def test_a_damaged_sounds_file_is_named(sound3, text, fragment):
    sound3.sounds_file.write_text(text, encoding="utf-8")
    with pytest.raises(CineRecastError, match=fragment) as refused:
        sound.list_sounds(sound3.root)
    assert refused.value.message.startswith("sounds.json")


def test_this_file_keeps_han_as_an_escape():
    from pathlib import Path

    assert "\\u4e2d" in Path(__file__).read_text(encoding="utf-8")


def test_png_helper_is_not_audio(sound3, tmp_path):
    sid = _issued(sound3.root)
    image = png(tmp_path / "x.png")
    with pytest.raises(CineRecastError, match=".wav"):
        sound.record_done(sound3.root, sid, "prov-1", 1.0, image)
