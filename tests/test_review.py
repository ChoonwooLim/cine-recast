"""Section J of task 20: takes review writes what the user looks at, takes confirm records the user's
answer, and every change of a unit's picture removes the confirmation."""

import pytest
from conftest import fake_take
from PIL import Image

from cine_recast.cli import main
from cine_recast.conform import conform
from cine_recast.errors import CineRecastError
from cine_recast.ffmpeg import probe
from cine_recast.jsonio import read_json, write_json
from cine_recast.plan import build, check
from cine_recast.review import confirm, review_take
from cine_recast.takes import select


def test_review_writes_the_side_by_side_clip_and_the_still_sheet(conformed3, capsys):
    root = conformed3.root
    capsys.readouterr()
    assert main(["takes", "review", str(root), "--unit", "u01", "--take", "t001"]) == 0
    folder = conformed3.take_dir("u01", "t001")
    assert capsys.readouterr().out.splitlines() == [str(folder / "review.mp4"), str(folder / "review.jpg")]
    info = probe(folder / "review.mp4")                                    # the unit's 90 frames, no sound
    assert (info.frames, info.width, info.height, info.fps_num, info.fps_den, info.has_audio) == (
        90, 768, 216, 24, 1, False)
    with Image.open(folder / "review.jpg") as sheet:                       # 3 shots: rows of 6 cells 320x180
        assert (sheet.format, sheet.size) == ("JPEG", (1920, 540))


def test_review_and_confirm_need_a_conformed_take(planned3, tmp_path):
    root = planned3.root
    take = fake_take(root, "u01", tmp_path)
    with pytest.raises(CineRecastError, match="conform"):
        review_take(root, "u01", take)
    select(root, "u01", take)
    with pytest.raises(CineRecastError, match="conform"):
        confirm(root, "u01", take, "owner")
    assert "confirmed" not in read_json(planned3.plan_file)["units"][0]


def test_confirm_records_the_user_on_the_unit(conformed3, capsys):
    root = conformed3.root
    select(root, "u01", "t001")
    capsys.readouterr()
    assert main(["takes", "confirm", str(root), "--unit", "u01", "--take", "t001", "--by", "owner",
                 "--note", "\ud45c\uc815\uc774 \uc88b\ub2e4"]) == 0
    assert capsys.readouterr().out.strip() == "u01: take t001 confirmed by owner"
    confirmed = read_json(conformed3.plan_file)["units"][0]["confirmed"]
    assert set(confirmed) == {"take", "by", "at", "note"}
    assert (confirmed["take"], confirmed["by"], confirmed["note"]) == ("t001", "owner", "\ud45c\uc815\uc774 \uc88b\ub2e4")
    assert confirmed["at"].endswith("Z") and check(root) == []


@pytest.mark.parametrize("args, fragment", [
    (("u01", "t002", "owner"), "selected take"),
    (("u07", "t001", "owner"), "unknown unit"),
    (("u01", "t001", " "), "by"),
])
def test_confirm_refusals(conformed3, tmp_path, args, fragment):
    root = conformed3.root
    fake_take(root, "u01", tmp_path)                                       # t002 exists, t001 stays selected
    select(root, "u01", "t001")
    with pytest.raises(CineRecastError, match=fragment):
        confirm(root, *args)
    assert "confirmed" not in read_json(conformed3.plan_file)["units"][0]


def test_an_unselected_take_cannot_be_confirmed(conformed3):
    with pytest.raises(CineRecastError, match="selected take"):          # conformed, but nothing selected
        confirm(conformed3.root, "u01", "t001", "owner")


def _confirmed(pp):
    return read_json(pp.plan_file)["units"][0].get("confirmed")


def test_selecting_another_take_removes_the_confirmation(finished3, tmp_path):
    root = finished3.root
    select(root, "u01", "t001")                                            # the same take: kept
    assert _confirmed(finished3)["take"] == "t001"
    take = fake_take(root, "u01", tmp_path)
    select(root, "u01", take)
    assert _confirmed(finished3) is None


def test_conforming_the_selected_take_again_removes_the_confirmation(finished3, tmp_path):
    root = finished3.root
    other = fake_take(root, "u01", tmp_path)
    conform(root, "u01", other)                                            # another take: kept
    assert _confirmed(finished3)["take"] == "t001"
    conform(root, "u01", "t001")
    assert _confirmed(finished3) is None


def test_conform_also_removes_the_review_files_it_makes_stale(finished3):
    root = finished3.root
    mp4, jpg = review_take(root, "u01", "t001")
    conform(root, "u01", "t001")
    assert not mp4.exists() and not jpg.exists()


def test_replacing_the_plan_leaves_no_confirmation(finished3):
    root = finished3.root
    data = build(root, model="seedance_2_5", method="edit", batch="b2", replace=True)
    assert [u["id"] for u in data["units"]] == ["u02"] and "confirmed" not in data["units"][0]
    assert all("confirmed" not in u for u in read_json(finished3.plan_file)["units"])


@pytest.mark.parametrize("value, fragment", [
    ("yes", "confirmed"),
    ({"take": "t001", "by": "owner", "at": "2026-11-18T00:00:00Z"}, "note"),
    ({"take": "x1", "by": "owner", "at": "2026-11-18T00:00:00Z", "note": ""}, "take"),
    ({"take": "t001", "by": "", "at": "2026-11-18T00:00:00Z", "note": ""}, "by"),
    ({"take": "t001", "by": "owner", "at": 5, "note": ""}, "at"),
])
def test_plan_check_validates_a_confirmation_when_present(finished3, capsys, value, fragment):
    assert check(finished3.root) == []                                     # present and valid
    data = read_json(finished3.plan_file)
    data["units"][0]["confirmed"] = value
    write_json(finished3.plan_file, data)
    problems = check(finished3.root)
    assert any("confirmed" in p and fragment in p for p in problems)
    del data["units"][0]["confirmed"]
    write_json(finished3.plan_file, data)
    assert check(finished3.root) == []                                     # a missing key is fine


def test_moving_a_unit_to_another_model_keeps_its_confirmation(finished3):
    from cine_recast.plan import refresh_unit

    model = read_json(finished3.models_file)["models"][0]
    write_json(finished3.models_file, {"models": [model, {**model, "id": "other_video", "min_seconds": 1}]})
    refresh_unit(finished3.root, "u01", model="other_video")          # the selected take's picture is unchanged
    assert _confirmed(finished3)["take"] == "t001"
