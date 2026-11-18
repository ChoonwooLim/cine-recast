import shutil

import pytest
from conftest import generate_and_use, good_casting, png

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


def test_not_applicable_consent_does_not_cover_an_outside_photo(analyzed3, tmp_path):
    good_casting(analyzed3.root, tmp_path)  # the hero image is a file from outside the project
    set_right(analyzed3.root, "likeness_consent", "not_applicable")
    assert any("likeness_consent" in p for p in check(analyzed3.root))


def test_not_applicable_consent_needs_every_person_image_generated(assets3, tmp_path):
    generate_and_use(assets3.root, "hero", tmp_path)
    c = read_json(assets3.casting_file)
    c["swaps"][0]["images"].append(png(tmp_path / "photo.png").as_posix())
    write_json(assets3.casting_file, c)
    assert any("likeness_consent" in p for p in check(assets3.root))
    set_right(assets3.root, "likeness_consent", "yes")
    assert check(assets3.root) == []


def test_a_person_generated_from_reference_images_needs_likeness_consent(assets3, tmp_path):
    ref = png(tmp_path / "ref.png")
    generate_and_use(assets3.root, "sky_battle", tmp_path, refs=[ref])  # refs stay fine for a background
    assert check(assets3.root) == []
    generate_and_use(assets3.root, "hero", tmp_path, refs=[ref])
    assert any("likeness_consent" in p and "reference images" in p for p in check(assets3.root))
    set_right(assets3.root, "likeness_consent", "yes")
    assert check(assets3.root) == []
