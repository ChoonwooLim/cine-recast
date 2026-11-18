from fractions import Fraction

import pytest
from conftest import _run

from cine_recast.cli import main
from cine_recast.errors import CineRecastError
from cine_recast.project import RIGHTS_ITEMS, init_project, load_project, reference_problem, set_right


def _no_video(tmp, clip):
    _run(["-f", "lavfi", "-i", "sine=f=440:d=1", str(tmp / "tone.wav")])
    return tmp / "tone.wav"


def _not_media(tmp, clip):
    (tmp / "notes.mp4").write_text("not a video", encoding="utf-8")
    return tmp / "notes.mp4"


def _gif(tmp, clip):
    _run(["-i", str(clip), "-frames:v", "12", str(tmp / "clip.gif")])
    return tmp / "clip.gif"


def _rotated(tmp, clip):
    _run(["-display_rotation", "90", "-i", str(clip), "-c", "copy", str(tmp / "rotated.mp4")])
    return tmp / "rotated.mp4"


def _non_square(tmp, clip):
    _run(["-i", str(clip), "-vf", "setsar=4/3", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(tmp / "sar.mp4")])
    return tmp / "sar.mp4"


def _variable_rate(tmp, clip):
    _run(["-i", str(clip), "-vf", r"select='if(lt(n\,24)\,not(mod(n\,2))\,1)'", "-fps_mode", "vfr",
          "-c:v", "libx264", "-pix_fmt", "yuv420p", str(tmp / "vfr.mp4")])
    return tmp / "vfr.mp4"


@pytest.mark.parametrize("make, fragment", [
    (_no_video, "no video stream"),
    (_not_media, "ffprobe cannot read it"),
    (_gif, "container"),
    (_rotated, "rotation"),
    (_non_square, "square"),
    (_variable_rate, "variable"),
])
def test_init_refuses_an_unusable_reference_and_creates_nothing(tmp_path, clip3, capsys, make, fragment):
    reference = make(tmp_path, clip3)
    capsys.readouterr()
    assert main(["init", str(tmp_path / "p"), "--reference", str(reference), "--name", "t"]) == 2
    err = capsys.readouterr().err
    assert fragment in err and "re-encode" in err and len(err.strip().splitlines()) == 1
    assert not (tmp_path / "p").exists()


def _facts(**changes):
    return {"video": True, "formats": ["mov", "mp4"], "rotation": 0.0, "sar": None,
            "avg_rate": Fraction(24), "r_rate": Fraction(24), **changes}


@pytest.mark.parametrize("facts, fragment", [
    (_facts(), None),
    (_facts(avg_rate=Fraction(2352, 100)), None),                      # 2.0 % below: still constant
    (_facts(avg_rate=Fraction(2350, 100)), "variable"),                # more than 2 % below
    (_facts(avg_rate=Fraction(0)), None),                              # no average given: not judged
    (_facts(sar=Fraction(1)), None),
    (_facts(sar=Fraction(4, 3)), "square"),
    (_facts(rotation=-90.0), "rotation"),
    (_facts(formats=["matroska", "webm"]), None),
    (_facts(formats=["mpegts"]), None),
    (_facts(formats=["hls"]), "container"),
])
def test_reference_problem(facts, fragment):
    problem = reference_problem(facts)
    assert problem is None if fragment is None else fragment in problem


def test_init_creates_project(tmp_path, clip3):
    pp = init_project(tmp_path / "\ud504\ub85c\uc81d\ud2b8 01", clip3, "t")
    data = load_project(pp.root)
    assert data["reference"] == "reference/source.mp4" and (pp.root / data["reference"]).is_file()
    assert data["rights"] == {k: None for k in RIGHTS_ITEMS}
    assert data["thresholds"]["camera_min"] == 0.6


def test_init_refuses_non_empty_folder(tmp_path, clip3):
    (tmp_path / "p").mkdir()
    (tmp_path / "p" / "x.txt").write_text("x")
    with pytest.raises(CineRecastError):
        init_project(tmp_path / "p", clip3, "t")


@pytest.mark.parametrize("text, fragment", [
    ("{", "line 1, column 2"),
    ("[1]", "top level"),
    ('{"rights": {}}', "'reference'"),
    ('{"reference": "reference/source.mp4", "rights": []}', "'rights'"),
])
def test_a_damaged_project_file_is_named(project3, capsys, text, fragment):
    project3.project_file.write_text(text, encoding="utf-8")
    for command in (["rights", "set", str(project3.root), "--item", "reference_rights", "--answer", "yes"],
                    ["analyze", str(project3.root)], ["audit", str(project3.root)]):
        capsys.readouterr()
        assert main(command) == 2
        err = capsys.readouterr().err
        assert err.startswith("error: project.json") and fragment in err


def test_set_right_validates(tmp_path, clip3):
    pp = init_project(tmp_path / "p", clip3, "t")
    set_right(pp.root, "likeness_consent", "yes", "own face")
    assert load_project(pp.root)["rights"]["likeness_consent"]["answer"] == "yes"
    for item, answer in (("nope", "yes"), ("likeness_consent", "maybe")):
        with pytest.raises(CineRecastError):
            set_right(pp.root, item, answer)
