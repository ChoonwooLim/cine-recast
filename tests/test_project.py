import pytest

from cine_recast.errors import CineRecastError
from cine_recast.project import RIGHTS_ITEMS, init_project, load_project, set_right


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


def test_set_right_validates(tmp_path, clip3):
    pp = init_project(tmp_path / "p", clip3, "t")
    set_right(pp.root, "likeness_consent", "yes", "own face")
    assert load_project(pp.root)["rights"]["likeness_consent"]["answer"] == "yes"
    for item, answer in (("nope", "yes"), ("likeness_consent", "maybe")):
        with pytest.raises(CineRecastError):
            set_right(pp.root, item, answer)
