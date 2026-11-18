import shutil

import pytest
from conftest import good_casting

from cine_recast.casting import check, swaps_for_shots, write_template
from cine_recast.jsonio import read_json, write_json
from cine_recast.project import set_right


def test_valid_casting_has_no_problems(analyzed3, tmp_path):
    good_casting(analyzed3.root, tmp_path)
    assert check(analyzed3.root) == []


@pytest.mark.parametrize("mutate, fragment", [
    (lambda c: c["elements"][0].update(id="Hero"), "id"),
    (lambda c: c["elements"][0].update(kind="alien"), "kind"),
    (lambda c: c["elements"][0].update(shots=[9]), "shot"),
    (lambda c: c["elements"][0].update(look="\uc18c\ub144"), "look"),
    (lambda c: c["swaps"][0].update(element="ghost"), "element"),
    (lambda c: c["swaps"][0].update(images=[]), "image"),
    (lambda c: c["swaps"][0].update(images=["Z:/missing.jpg"]), "image"),
    (lambda c: c["swaps"].append(dict(c["swaps"][0])), "duplicate"),
    (lambda c: c["elements"][0].update(name="\u4e2d"), "Han"),
])
def test_problems_are_reported(analyzed3, tmp_path, mutate, fragment):
    good_casting(analyzed3.root, tmp_path)
    c = read_json(analyzed3.casting_file)
    mutate(c)
    write_json(analyzed3.casting_file, c)
    assert any(fragment in p for p in check(analyzed3.root))


def test_person_swap_needs_likeness_consent(analyzed3, tmp_path):
    good_casting(analyzed3.root, tmp_path)
    set_right(analyzed3.root, "likeness_consent", "unknown")
    assert any("likeness_consent" in p for p in check(analyzed3.root))


def test_swaps_for_shots(analyzed3, tmp_path):
    good_casting(analyzed3.root, tmp_path)  # hero in shots [1, 3], sky_battle in [1, 3]
    assert [s["element"] for s in swaps_for_shots(analyzed3.root, [1])] == ["hero", "sky_battle"]
    assert swaps_for_shots(analyzed3.root, [2]) == []


def test_template_lists_shots(analyzed3):
    t = read_json(write_template(analyzed3.root))
    assert [s["shot"] for s in t["shots"]] == [1, 2, 3] and t["elements"] == [] and t["swaps"] == []


def test_relative_swap_images_resolve_against_the_project(analyzed3, tmp_path, monkeypatch):
    good_casting(analyzed3.root, tmp_path)
    local = analyzed3.root / "refs" / "person.png"
    local.parent.mkdir()
    shutil.copyfile(tmp_path / "refs" / "person_front.png", local)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "cwd_only.png").write_bytes(local.read_bytes())
    monkeypatch.chdir(elsewhere)
    c = read_json(analyzed3.casting_file)
    c["swaps"][0]["images"] = ["refs/person.png"]
    write_json(analyzed3.casting_file, c)
    assert check(analyzed3.root) == []
    c["swaps"][0]["images"] = ["cwd_only.png"]                          # only found from the working folder
    write_json(analyzed3.casting_file, c)
    assert any("not found" in p for p in check(analyzed3.root))
