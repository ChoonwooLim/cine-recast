import shutil
from pathlib import Path

import pytest
from conftest import generate_and_use, good_casting, png, swap_image

from cine_recast.casting import check, swaps_for_shots, write_template
from cine_recast.cli import main
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
    sky = swap_image(assets3.root, "sky.png", "sky_battle")             # the user's pictures, in the swap table
    generate_and_use(assets3.root, "sky_battle", tmp_path, refs=[Path(sky)])  # refs stay fine for a background
    assert check(assets3.root) == []
    set_right(assets3.root, "likeness_consent", "yes")                  # the asset itself needs consent
    photo = swap_image(assets3.root, "person.png", "hero")
    generate_and_use(assets3.root, "hero", tmp_path, refs=[Path(photo)])
    set_right(assets3.root, "likeness_consent", "not_applicable")       # answer changed afterwards
    assert any("likeness_consent" in p and "reference images" in p for p in check(assets3.root))
    set_right(assets3.root, "likeness_consent", "yes")
    assert check(assets3.root) == []


# --- final-review wave: damaged and hand-written casting.json ---

def _cast_check(root, capsys):
    capsys.readouterr()
    code = main(["cast", "check", str(root)])
    return code, capsys.readouterr().err


@pytest.mark.parametrize("damage, fragments", [
    (lambda good: (good.rstrip().rstrip("}") + ",}").encode("utf-8"), ["casting.json", "line", "column"]),
    (lambda good: b"", ["casting.json", "line 1, column 1"]),
    (lambda good: good.encode("utf-16"), ["casting.json", "UTF-8"]),
])
def test_cast_check_names_an_unreadable_file(analyzed3, tmp_path, capsys, damage, fragments):
    good_casting(analyzed3.root, tmp_path)
    analyzed3.casting_file.write_bytes(damage(analyzed3.casting_file.read_text(encoding="utf-8")))
    code, err = _cast_check(analyzed3.root, capsys)
    assert code == 2 and err.startswith("error: ") and len(err.strip().splitlines()) == 1
    assert all(fragment in err for fragment in fragments)


def test_cast_check_accepts_a_byte_order_mark(analyzed3, tmp_path, capsys):
    good_casting(analyzed3.root, tmp_path)
    analyzed3.casting_file.write_bytes(b"\xef\xbb\xbf" + analyzed3.casting_file.read_bytes())
    assert _cast_check(analyzed3.root, capsys) == (0, "")


@pytest.mark.parametrize("mutate, fragment", [
    (lambda c: [], "top level"),
    (lambda c: {**c, "elements": "hero"}, "elements"),
    (lambda c: c.pop("swaps") and c, "swaps"),
    (lambda c: {**c, "elements": ["hero", *c["elements"]]}, "elements[0]"),
    (lambda c: {**c, "swaps": [*c["swaps"], "hero"]}, "swaps[2]"),
    (lambda c: c["elements"][0].update(id=["hero"]) or c, "id"),
    (lambda c: c["elements"][0].update(id="hero\n") or c, "id"),
    (lambda c: c["elements"][0].update(kind=["person"]) or c, "kind"),
    (lambda c: c["elements"][0].update(name=7) or c, "name"),
    (lambda c: c["elements"][0].update(look=["boy"]) or c, "look"),
    (lambda c: c["elements"][0].update(shots="1, 3") or c, "shots"),
    (lambda c: c["elements"][0].update(shots=[[1]]) or c, "shots"),
    (lambda c: c["elements"][0].update(shots=[True]) or c, "shots"),
    (lambda c: c["swaps"][0].update(element=["hero"]) or c, "element"),
    (lambda c: c["swaps"][0].update(images="swap/a.png") or c, "images"),
    (lambda c: c["swaps"][0].update(images=[7]) or c, "images"),
])
def test_cast_check_reports_shape_problems_as_lines(analyzed3, tmp_path, capsys, mutate, fragment):
    good_casting(analyzed3.root, tmp_path)
    write_json(analyzed3.casting_file, mutate(read_json(analyzed3.casting_file)))
    code, err = _cast_check(analyzed3.root, capsys)
    assert code == 2 and not err.startswith("error: ") and any(fragment in line for line in err.splitlines())


def test_an_unreadable_ledger_is_reported_not_raised(assets3, tmp_path):
    generate_and_use(assets3.root, "hero", tmp_path)
    with assets3.ledger_file.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write("{broken\n")
    assert any("likeness_consent" in p and "ledger" in p for p in check(assets3.root))
