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
    with pytest.raises(CineRecastError, match="frozen"):
        analyze(project3.root)


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
