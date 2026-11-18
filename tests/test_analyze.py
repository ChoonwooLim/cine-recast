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
