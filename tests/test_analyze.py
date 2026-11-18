import pytest

from cine_recast.analyze import analyze, review_strips
from cine_recast.cli import main
from cine_recast.errors import CineRecastError
from cine_recast.ffmpeg import probe
from cine_recast.jsonio import read_json, write_json


def test_analyze_writes_shots(project3):
    result = analyze(project3.root)
    assert result["cuts"] == [30, 54] and result["shots"] == 3
    assert read_json(project3.cuts_file) == {"cuts": [30, 54], "source": "detected"}
    assert [read_json(project3.sheet_file(i))["frames"] for i in (1, 2, 3)] == [30, 24, 36]
    assert probe(project3.shot_dir(2) / "ref.mp4").frames == 24
    assert (project3.shot_dir(3) / "stills" / "last.png").is_file()
    assert set(read_json(project3.sheet_file(1))) >= {"picture_box", "elements", "pose", "depth", "mattes", "audio_hits", "dialogue"}


def test_manual_cuts_override(project3):
    analyze(project3.root, cuts=[45])
    assert read_json(project3.cuts_file) == {"cuts": [45], "source": "manual"}
    assert not project3.shot_dir(3).exists()


def test_analysis_is_frozen_once_a_plan_exists(project3):
    analyze(project3.root)
    write_json(project3.plan_file, {"units": [], "jobs": []})
    with pytest.raises(CineRecastError, match="frozen") as refused:
        analyze(project3.root)
    assert "picture area" in refused.value.message and "new project folder" in refused.value.message


# --- final-review wave: a picture area the agent gives ---

def test_given_picture_area_replaces_detection(project3):
    result = analyze(project3.root, picture=(0, 28, 384, 160))
    data = read_json(project3.video_file)
    assert data["picture_box"] == result["picture_box"] == {"x": 0, "y": 28, "w": 384, "h": 160}
    assert data["picture_source"] == "given" and result["cuts"] == [30, 54]
    info = probe(project3.shot_dir(1) / "ref.mp4")
    assert (info.width, info.height) == (384, 160)


def test_detected_picture_area_is_marked(project3):
    analyze(project3.root)
    assert read_json(project3.video_file)["picture_source"] == "detected"


@pytest.mark.parametrize("picture, fragment", [
    ((1, 28, 380, 160), "even"),
    ((0, 28, 384, 161), "even"),
    ((0, 28, 386, 160), "inside"),
    ((-2, 0, 100, 100), "inside"),
    ((0, 0, 0, 216), "greater than 0"),
])
def test_given_picture_area_is_validated(project3, picture, fragment):
    with pytest.raises(CineRecastError, match=fragment):
        analyze(project3.root, picture=picture)
    assert not project3.video_file.exists()


def test_cli_picture_and_cuts_together(project3, capsys):
    capsys.readouterr()
    assert main(["analyze", str(project3.root), "--picture", "0,28,384,160", "--cuts", "30,54"]) == 0
    assert capsys.readouterr().out.strip() == "shots: 3  cuts: [30, 54]  picture: {'x': 0, 'y': 28, 'w': 384, 'h': 160}"
    assert read_json(project3.cuts_file)["source"] == "manual" and read_json(project3.video_file)["picture_source"] == "given"


@pytest.mark.parametrize("text", ["0,28,384", "0,28,384,160,2", "a,b,c,d", "0.5,28,384,160"])
def test_cli_refuses_a_malformed_picture(project3, capsys, text):
    capsys.readouterr()
    assert main(["analyze", str(project3.root), "--picture", text]) == 2
    assert "--picture" in capsys.readouterr().err and not project3.video_file.exists()


def test_review_strips(project3):
    analyze(project3.root)
    names = sorted(p.name for p in review_strips(project3.root))
    assert names == ["cut_0030.png", "cut_0054.png", "shot_0001.png", "shot_0002.png", "shot_0003.png"]


@pytest.mark.parametrize("file, text, fragment", [
    ("video", "{}", "analysis/video.json is missing the key"),
    ("video", '{"x": ', "analysis/video.json is not valid JSON"),
    ("cuts", '{"cuts": "30,54"}', "analysis/cuts.json"),
    ("cuts", '{"cuts": [30, "54"]}', "analysis/cuts.json"),
])
def test_a_damaged_analysis_file_is_named(analyzed3, capsys, file, text, fragment):
    path = analyzed3.video_file if file == "video" else analyzed3.cuts_file
    path.write_text(text, encoding="utf-8")
    for command in (["cuts", "review", str(analyzed3.root)], ["cast", "template", str(analyzed3.root)]):
        capsys.readouterr()
        assert main(command) == 2
        assert fragment in capsys.readouterr().err


def test_a_damaged_picture_box_is_named(analyzed3):
    data = read_json(analyzed3.video_file)
    del data["picture_box"]["w"]
    write_json(analyzed3.video_file, data)
    with pytest.raises(CineRecastError, match="picture_box"):
        review_strips(analyzed3.root)


def test_cli_flow(tmp_path, clip3):
    root = tmp_path / "p"
    assert main(["init", str(root), "--reference", str(clip3), "--name", "t"]) == 0
    assert main(["analyze", str(root), "--cuts", "30,54"]) == 0
    assert main(["cuts", "review", str(root)]) == 0


# --- task 20 (E): the timing of the reference's sound ---

def _clicks_clip(path):
    """Two shots (testsrc2 30 frames, smptebars 42 frames, 24 fps) with a sound that is silent except
    for 5 ms bursts of a 2 kHz tone starting exactly at frames 10, 30 and 55, the loudest at 55."""
    from conftest import _encode_args, _run

    bursts = "+".join(rf"{level}*between(t\,{frame}/24\,{frame}/24+0.005)" for frame, level in
                      ((10, 0.5), (30, 0.2), (55, 0.9)))
    _run(["-f", "lavfi", "-i", "testsrc2=size=384x216:rate=24", "-f", "lavfi", "-i", "smptebars=size=384x216:rate=24",
          "-f", "lavfi", "-i", f"aevalsrc=({bursts})*sin(2*PI*2000*t):s=48000:d=3",
          "-filter_complex", "[0:v]trim=end_frame=30,setpts=PTS-STARTPTS,format=yuv420p[a];"
                             "[1:v]trim=end_frame=42,setpts=PTS-STARTPTS,format=yuv420p[b];[a][b]concat=n=2:v=1:a=0[v]",
          "-map", "[v]", "-map", "2:a", "-c:a", "aac", "-b:a", "192k", "-t", "3", *_encode_args(path)])
    return path


def test_analyze_reports_the_hits_of_the_reference_sound(tmp_path):
    from cine_recast.project import init_project

    pp = init_project(tmp_path / "p", _clicks_clip(tmp_path / "clicks.mp4"), "t")
    analyze(pp.root)
    data = read_json(pp.audio_file)
    assert (data["has_audio"], data["frames"], len(data["rms_db"])) == (True, 72, 72)
    assert data["hits"] == [55, 10, 30]                                 # strongest first
    assert min(data["rms_db"][f] for f in (10, 30, 55)) > -40 and data["rms_db"][20] <= -80
    assert min(data["rms_db"]) >= -90
    assert [(s["shot"], s["start"], s["end"], s["hits"]) for s in data["shots"]] == [(1, 0, 30, [10]),
                                                                                  (2, 30, 72, [30, 55])]
    assert all(-90 <= s["mean_db"] < -20 for s in data["shots"])
    assert [read_json(pp.sheet_file(n))["audio_hits"] for n in (1, 2)] == [[10], [30, 55]]


def test_a_silent_reference_has_no_audio_map(project3):
    analyze(project3.root)                                              # clip3 has no audio stream
    assert read_json(project3.audio_file) == {"has_audio": False, "frames": 90, "rms_db": [], "hits": [], "shots": []}
    assert all(read_json(project3.sheet_file(n))["audio_hits"] == [] for n in (1, 2, 3))


def test_onsets_are_spaced_and_limited():
    from cine_recast.audiomap import onsets

    levels = [-90.0] * 60
    levels[10], levels[13] = -30.0, -5.0           # jumps of 60 and 75 dB, 3 frames apart: the stronger wins
    assert onsets(levels) == [13]
    levels = [-90.0] * 500
    for n in range(45):                             # 45 jumps 10 frames apart, each 1 dB stronger than the last
        levels[5 + 10 * n] = -60.0 + n
    assert onsets(levels) == [5 + 10 * n for n in range(44, 4, -1)]     # the 40 strongest, strongest first
    assert onsets([-90.0, -85.0, -82.0]) == []      # jumps below 9 dB


def test_audio_hits_follow_the_audio_start_offset(tmp_path):
    """The same bursts as _clicks_clip, in a MOV whose PCM sound starts 0.25 s (6 frames) after the
    picture: every hit moves 6 frames later."""
    from conftest import _run

    from cine_recast.project import init_project

    bursts = "+".join(rf"{level}*between(t\,{frame}/24\,{frame}/24+0.005)" for frame, level in
                      ((10, 0.5), (30, 0.2), (55, 0.9)))
    clip = tmp_path / "late.mov"
    _run(["-f", "lavfi", "-i", "testsrc2=size=384x216:rate=24", "-itsoffset", "0.25", "-f", "lavfi", "-i",
          f"aevalsrc=({bursts})*sin(2*PI*2000*t):s=48000:d=2.5", "-frames:v", "72", "-map", "0:v", "-map", "1:a",
          "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le", str(clip)])
    pp = init_project(tmp_path / "p", clip, "t")
    analyze(pp.root)
    data = read_json(pp.audio_file)
    assert data["hits"] == [61, 16, 36] and len(data["rms_db"]) == 72
    assert max(data["rms_db"][:6]) == -90.0                                 # before the sound starts
