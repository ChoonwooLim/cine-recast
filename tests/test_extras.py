"""Extra shots (extras.json and the extra commands): shots that are not in the reference, made as
extensions of a take or from frames of a take, through the same approval ledger."""

import json
import shutil
from pathlib import Path

import numpy as np
import pytest
from conftest import EXAMPLE_MODEL, IMAGE_MODEL, generate_and_use, make_clip, png, run_cli, swap_image
from PIL import Image

from cine_recast import assets, casting, extras
from cine_recast.cli import main
from cine_recast.errors import CineRecastError
from cine_recast.ffmpeg import extract_still, iter_frames, probe
from cine_recast.jsonio import read_json, write_json
from cine_recast.ledger import approve, audit, job_states, set_budget
from cine_recast.paths import sha256_of
from cine_recast.project import set_right

PROMPT = "The giant roars at the camera before it attacks, the same light and place as the take."
HAN = "\u4e2d"
NO_REF = {**EXAMPLE_MODEL, "id": "no_video_ref", "video_ref": False}
ONE_IMAGE = {**EXAMPLE_MODEL, "id": "one_image", "image_refs": 1}


@pytest.fixture
def taken(conformed3):
    """conformed3 (u01 with take t001, casting ready) with a video, an image, a no-video_ref and a
    one-image model."""
    write_json(conformed3.models_file, {"models": [EXAMPLE_MODEL, IMAGE_MODEL, NO_REF, ONE_IMAGE]})
    return conformed3


def _add(root, **overrides):
    args = dict(label="roar", prompt=PROMPT, model="seedance_2_5", seconds=4, batch="x-1")
    return extras.add(root, **{**args, **overrides})


def test_add_an_extension(taken):
    item = _add(taken.root, extend="u01/t001", direction="backward")
    assert (item["id"], item["kind"], item["state"], item["est_credits"], item["result"]) == (
        "x0001", "extend", "planned", None, None)
    assert item["params"] == {"model": "seedance_2_5", "prompt": PROMPT, "duration": 4, "aspect_ratio": "16:9",
                              "resolution": "480p", "direction": "backward"}
    assert item["inputs"] == [{"role": "video", "path": "takes/u01/t001/raw.mp4"}]
    assert item["source"] == {"extend": "u01/t001", "direction": "backward"}
    assert read_json(taken.root / "extras.json")["extras"][0]["id"] == "x0001"


def _frame(path, index):
    return next(iter_frames(path, start=index, count=1)).astype(np.int16)


def test_add_start_and_end_frames_writes_the_exact_frames(taken):
    raw = taken.take_dir("u01", "t001") / "raw.mp4"
    frames = probe(raw).frames
    item = _add(taken.root, start="u01/t001:last", end="u01/t001:20", quality="final", label="\ubcf4\ub108\uc2a4")
    folder = taken.root / "extras" / "x0001"
    assert item["kind"] == "shot" and item["params"]["resolution"] == "4k" and "direction" not in item["params"]
    assert _roles(item) == [("start_image", "extras/x0001/start.png"), ("end_image", "extras/x0001/end.png")]
    assert [e["sha256"] for e in item["inputs"]] == [sha256_of(folder / "start.png"), sha256_of(folder / "end.png")]
    assert item["source"] == {"start": "u01/t001:last", "end": "u01/t001:20"}
    for name, index in (("start.png", frames - 1), ("end.png", 20)):
        with Image.open(folder / name) as image:
            assert image.size == (640, 360)
            pixels = np.asarray(image.convert("RGB")).astype(np.int16)
        assert np.abs(pixels - _frame(raw, index)).max() <= 2, name
    assert np.abs(_frame(raw, 20) - _frame(raw, 60)).mean() > 5            # the test can tell frames apart


def _roles(item):
    return [(entry["role"], entry["path"]) for entry in item["inputs"]]


def test_add_with_reference_images(taken, tmp_path):
    asset = generate_and_use(taken.root, "hero", tmp_path)                  # a stored file of a done asset
    swap = swap_image(taken.root, "monster.png", "sky_battle")              # a picture the swap table names
    item = _add(taken.root, refs=[taken.root / asset, swap], start="u01/t001:first")
    assert _roles(item) == [("start_image", "extras/x0001/start.png"), ("image", "assets/hero/a0001_1.png"),
                            ("image", "swap/monster.png")]
    assert _add(taken.root)["inputs"] == []                                 # a prompt-only extra is legal


@pytest.mark.parametrize("overrides, fragment", [
    (dict(extend="u01/t009", direction="backward"), "t009"),
    (dict(extend="u07/t001", direction="backward"), "u07"),
    (dict(start="u01/t001:500"), "500"),
    (dict(extend="u01/t001", direction="backward", start="u01/t001:first"), "--start"),
    (dict(extend="u01/t001"), "--direction"),
    (dict(direction="forward"), "--extend"),
    (dict(extend="u01/t001", direction="sideways"), "direction"),
    (dict(refs=["outside.png"]), "assets/"),
    (dict(start="u01/t001:first", end="u01/t001:last", model="one_image"), "image_refs"),
    (dict(extend="u01/t001", direction="forward", model="no_video_ref"), "video_ref"),
    (dict(model="image_model_x"), "video model"),
    (dict(model="missing"), "not in models.json"),
    (dict(seconds=3), "4 to 15"),
    (dict(seconds=16), "4 to 15"),
    (dict(seconds=4.5), "whole number"),
    (dict(prompt=f"The giant {HAN} roars."), "English"),
    (dict(prompt="\uad34\ubb3c\uc774 \uc6b4\ub2e4"), "English"),
    (dict(label=f"roar {HAN}"), "Han"),
    (dict(aspect_ratio="9:99"), "aspect_ratio"),
    (dict(batch=" "), "batch"),
])
def test_add_refusals(taken, tmp_path, overrides, fragment):
    if "refs" in overrides:
        overrides = {**overrides, "refs": [png(tmp_path / "outside.png")]}
    with pytest.raises(CineRecastError, match=fragment):
        _add(taken.root, **overrides)
    assert not (taken.root / "extras.json").exists() and not (taken.root / "extras").exists()


def test_a_ref_inside_the_project_but_outside_assets_and_swap_is_refused(taken):
    png(taken.root / "review" / "still.png")
    with pytest.raises(CineRecastError, match="assets/.*swap/"):
        _add(taken.root, refs=["review/still.png"])


def test_add_set_cost_and_next_refuse_while_the_casting_is_not_ready(taken):
    root = taken.root
    _add(root, extend="u01/t001", direction="backward")
    extras.set_cost(root, "x0001", 10.0)
    approve(root, "x-1", 10.0, "owner")
    set_right(root, "likeness_consent", "no")
    for call in (lambda: _add(root, start="u01/t001:first"), lambda: extras.set_cost(root, "x0001", 10.0),
                 lambda: extras.issue_next(root, "x-1")):
        with pytest.raises(CineRecastError, match="likeness_consent"):
            call()
    assert job_states(root) == {}


def test_issue_goes_through_the_ledger(taken):
    root = taken.root
    _add(root, extend="u01/t001", direction="backward")
    assert extras.issue_next(root, "x-1") == []                            # nothing quoted yet
    extras.set_cost(root, "x0001", 30.0)
    with pytest.raises(CineRecastError, match="approval"):
        extras.issue_next(root, "x-1")
    approve(root, "x-1", 20.0, "owner")
    with pytest.raises(extras.IssueRefused, match="limit"):
        extras.issue_next(root, "x-1")
    approve(root, "x-1", 10.0, "owner")
    raw = (taken.take_dir("u01", "t001") / "raw.mp4").resolve()
    assert extras.issue_next(root, "x-1") == [{
        "job_id": "x0001", "kind": "extend", "tool": "generate_video",
        "params": {"model": "seedance_2_5", "prompt": PROMPT, "duration": 4, "aspect_ratio": "16:9",
                   "resolution": "480p", "direction": "backward"},
        "medias": [{"role": "video", "path": str(raw)}]}]
    assert extras.issue_next(root, "x-1") == [] and job_states(root) == {"x0001": "issued"}


def test_the_budget_bounds_the_approval_of_an_extra(taken):
    root = taken.root
    set_budget(root, 25.0, "owner")
    _add(root, extend="u01/t001", direction="backward")
    extras.set_cost(root, "x0001", 30.0)
    with pytest.raises(CineRecastError, match="budget"):
        approve(root, "x-1", 30.0, "owner")
    with pytest.raises(CineRecastError, match="approval"):
        extras.issue_next(root, "x-1")


def test_a_changed_input_file_or_param_must_be_quoted_again(taken):
    root = taken.root
    _add(root, start="u01/t001:first")
    extras.set_cost(root, "x0001", 10.0); approve(root, "x-1", 20.0, "owner")
    start = taken.root / "extras" / "x0001" / "start.png"
    original = start.read_bytes()
    Image.new("RGB", (640, 360), (255, 0, 0)).save(start)                  # same path, other content
    with pytest.raises(CineRecastError, match="add the extra again") as refused:   # final review 2 (I2)
        extras.issue_next(root, "x-1")
    assert "extra set-cost" not in refused.value.message
    start.write_bytes(original)
    data = read_json(taken.root / "extras.json"); data["extras"][0]["params"]["duration"] = 5
    write_json(taken.root / "extras.json", data)
    with pytest.raises(CineRecastError, match="extra set-cost"):
        extras.issue_next(root, "x-1")
    data["extras"][0]["params"]["duration"] = 4; data["extras"][0]["params"]["prompt"] = "Another wording."
    write_json(taken.root / "extras.json", data)                           # the prompt is not priced
    assert [j["job_id"] for j in extras.issue_next(root, "x-1")] == ["x0001"]


def _issued(root, **overrides):
    item = _add(root, extend="u01/t001", direction="backward", **overrides)
    extras.set_cost(root, item["id"], 10.0)
    approve(root, item["batch"], 10.0, "owner")
    extras.issue_next(root, item["batch"])
    return item["id"]


def test_done_stores_the_take_and_never_replaces_it(taken, result_clip, tmp_path):
    root = taken.root
    xid = _issued(root)
    assert extras.record_done(root, xid, "prov-x1", 9.0, result_clip) == {"extra": "x0001",
                                                                           "result": "extras/x0001/take.mp4"}
    assert (root / "extras" / "x0001" / "take.mp4").read_bytes() == result_clip.read_bytes()
    [listed] = extras.list_extras(root)
    assert (listed["state"], listed["result"]) == ("done", "extras/x0001/take.mp4")
    report = audit(root)
    assert report["ok"] and report["provider_ids"] == ["prov-x1"] and report["batches"]["x-1"]["committed"] == 9.0
    other = make_clip(tmp_path / "other.mov", [("smptebars", 12)])
    with pytest.raises(CineRecastError, match="done"):
        extras.record_done(root, xid, "prov-x2", 1.0, other)


def _reference_copy(pp, clip, tmp):
    target = tmp / "copy.mp4"
    shutil.copyfile(clip, target)
    return target


def _text_file(pp, clip, tmp):
    target = tmp / "clip.txt"
    target.write_text("x", encoding="utf-8")
    return target


def _not_a_video(pp, clip, tmp):
    target = tmp / "broken.mp4"
    target.write_bytes(b"not a video")
    return target


@pytest.mark.parametrize("make, fragment", [
    (lambda pp, clip, tmp: tmp / "missing.mp4", "not found"),
    (_not_a_video, "readable video"),
    (_reference_copy, "reference video"),
    (lambda pp, clip, tmp: pp.take_dir("u01", "t001") / "raw.mp4", "takes folder"),
    (lambda pp, clip, tmp: make_clip(pp.root / "extras" / "x0099" / "take.mp4", [("testsrc2", 12)]), "extras folder"),
    (lambda pp, clip, tmp: make_clip(pp.root / "Sounds" / "v.mp4", [("testsrc2", 12)]), "sounds folder"),
    (lambda pp, clip, tmp: make_clip(pp.root / "swap" / "v.mp4", [("testsrc2", 12)]), "swap folder"),
    (_text_file, ".mp4"),
])
def test_done_refusals_leave_nothing(taken, clip3, tmp_path, make, fragment):
    root = taken.root
    xid = _issued(root)
    source = make(taken, clip3, tmp_path)
    before = taken.ledger_file.read_bytes()
    with pytest.raises(CineRecastError, match=fragment):
        extras.record_done(root, xid, "prov-x1", 1.0, source)
    assert taken.ledger_file.read_bytes() == before and job_states(root)[xid] == "issued"
    assert not list((root / "extras" / "x0001").glob("take.*"))


def test_fail_and_ids_never_reused(taken):
    root = taken.root
    xid = _issued(root)
    assert extras.record_fail(root, xid, "provider refused", 0.5)["state"] == "failed"
    with pytest.raises(CineRecastError, match="not issued"):
        extras.record_fail(root, xid, "again")
    (root / "extras.json").unlink()                                        # the ledger still knows x0001
    assert _add(root)["id"] == "x0002"


def test_report_lists_the_extra_shots(finished3, result_clip):
    from cine_recast.report import write_report

    root = finished3.root
    write_json(finished3.models_file, {"models": [EXAMPLE_MODEL]})
    xid = _issued(root)
    _add(root, label="bridge", start="u01/t001:last", batch="x-2")
    extras.record_done(root, xid, "prov-x1", 9.0, result_clip)
    text = write_report(root).read_text(encoding="utf-8")
    part = text.split("## \uc6d0\ubcf8\uc5d0 \uc5c6\ub294 \uc20f")[1].split("\n## ")[0]
    assert "| x0001 | roar | extend | u01/t001 backward | seedance_2_5 | 480p | 9.00 | done |" in part
    assert "| x0002 | bridge | shot | start u01/t001:last | seedance_2_5 | 480p | - | planned |" in part
    _issued(root, label="open", batch="x-3")
    open_items = write_report(root).read_text(encoding="utf-8").split("## \uc5f4\ub9b0 \ud56d\ubaa9")[1]
    assert "x0003" in [line for line in open_items.splitlines() if "\uc6d0\ubcf8\uc5d0 \uc5c6\ub294 \uc20f" in line][0]


def test_cli_flow(taken, result_clip, capsys):
    root = taken.root
    capsys.readouterr()
    assert main(["extra", "add", str(root), "--label", "roar", "--prompt", PROMPT, "--model", "seedance_2_5",
                 "--seconds", "4", "--batch", "x-1", "--extend", "u01/t001", "--direction", "backward"]) == 0
    assert capsys.readouterr().out.strip() == "x0001"
    run_cli([["extra", "list", root], ["extra", "set-cost", root, "--extra", "x0001", "--credits", "12"],
             ["approve", root, "--batch", "x-1", "--limit", "12", "--by", "owner"]])
    capsys.readouterr()
    assert main(["extra", "next", str(root), "--batch", "x-1"]) == 0
    assert json.loads(capsys.readouterr().out)[0]["kind"] == "extend"
    assert main(["extra", "done", str(root), "--extra", "x0001", "--provider-id", "prov-1", "--credits", "11",
                 "--file", str(result_clip)]) == 0
    assert capsys.readouterr().out.strip() == "x0001: done, result extras/x0001/take.mp4"
    assert main(["extra", "add", str(root), "--label", "x", "--prompt", PROMPT, "--model", "seedance_2_5",
                 "--seconds", "4", "--batch", "x-1", "--direction", "forward"]) == 2
    assert "--extend" in capsys.readouterr().err
    _add(root, extend="u01/t001", direction="backward", batch="x-2")
    extras.set_cost(root, "x0002", 5.0); approve(root, "x-2", 5.0, "owner"); extras.issue_next(root, "x-2")
    assert main(["extra", "fail", str(root), "--extra", "x0002", "--reason", "not submitted"]) == 0


# --- start and end frames given as images under assets/ or swap/ ---

def test_start_and_end_accept_images_from_assets_and_swap(taken, tmp_path):
    root = taken.root
    key = root / generate_and_use(root, "hero", tmp_path)                 # assets/hero/a0001_1.png, a done asset
    Image.new("RGB", (64, 36), (10, 200, 30)).save(key)
    swap_image(root, "last.jpg", "sky_battle", color=(200, 20, 40))       # named in the swap table
    photo = root / "swap" / "last.jpg"
    item = extras.add(root, label="open", prompt=PROMPT, model="seedance_2_5", seconds=4, batch="x-1",
                      start="assets/hero/a0001_1.png", end=str(photo))
    folder = root / "extras" / "x0001"
    assert _roles(item) == [("start_image", "extras/x0001/start.png"), ("end_image", "extras/x0001/end.png")]
    assert item["source"] == {"start": "assets/hero/a0001_1.png", "end": str(photo)}
    assert (folder / "start.png").read_bytes() == key.read_bytes()            # a PNG is copied as it is
    with Image.open(folder / "end.png") as end:                               # another type becomes PNG
        assert end.format == "PNG" and end.size == (64, 36)
        assert np.abs(np.asarray(end.convert("RGB")).astype(int) - (200, 20, 40)).max() <= 8
    mixed = extras.add(root, label="mixed", prompt=PROMPT, model="seedance_2_5", seconds=4, batch="x-1",
                       start="u01/t001:last", end="swap/last.jpg")
    assert mixed["source"] == {"start": "u01/t001:last", "end": "swap/last.jpg"}


@pytest.mark.parametrize("flag", ["start", "end"])
def test_start_and_end_images_outside_assets_and_swap_are_refused(taken, tmp_path, flag):
    root = taken.root
    for value in (str(png(tmp_path / "outside.png")), str(png(root / "review" / "still.png")), "u01/t001:middle"):
        with pytest.raises(CineRecastError, match="assets/.*swap/") as refused:
            extras.add(root, label="x", prompt=PROMPT, model="seedance_2_5", seconds=4, batch="x-1",
                       **{flag: value})
        assert "U/T:FRAME" in refused.value.message
    assert not (root / "extras.json").exists() and not (root / "extras").exists()


def test_a_changed_start_image_is_added_again_not_quoted_again(taken, tmp_path):
    root = taken.root
    stored = generate_and_use(root, "hero", tmp_path)
    extras.add(root, label="open", prompt=PROMPT, model="seedance_2_5", seconds=4, batch="x-1", start=stored)
    extras.set_cost(root, "x0001", 10.0); approve(root, "x-1", 10.0, "owner")
    Image.new("RGB", (8, 8), (0, 0, 255)).save(root / "extras" / "x0001" / "start.png")
    for call in (lambda: extras.set_cost(root, "x0001", 10.0), lambda: extras.issue_next(root, "x-1")):
        with pytest.raises(CineRecastError, match="add the extra again") as refused:
            call()
        assert "extra set-cost" not in refused.value.message
    assert "x0001" not in job_states(root)


def test_this_file_keeps_han_as_an_escape():
    from cine_recast.textrules import han_chars

    assert han_chars(Path(__file__).read_text(encoding="utf-8")) == []      # Han only as escapes such as HAN


# --- final review 2 (C1, I1, I2, m5): every image input passes one rule at add, set-cost and next ---

def _without_swap(pp, element):
    data = read_json(pp.casting_file)
    data["swaps"] = [s for s in data["swaps"] if s["element"] != element]
    write_json(pp.casting_file, data)


def _photo_sheet_then_withdrawn(taken, tmp_path):
    """Scenario C1, steps 1 to 3: the user's photo swap/person_front.jpg is the hero swap while
    likeness_consent is "yes", a hero sheet is generated from it, then the person swap leaves the
    table and the consent becomes "no"; cast check passes. Returns the sheet's path."""
    root = taken.root
    swap_image(root, "person_front.jpg", "hero", only=True)
    sheet = generate_and_use(root, "hero", tmp_path, refs=[Path("swap/person_front.jpg")])
    _without_swap(taken, "hero")
    set_right(root, "likeness_consent", "no")
    assert casting.check(root) == []
    return sheet


def test_c1_a_person_photo_or_a_picture_made_from_it_is_refused_at_add(taken, tmp_path):
    root = taken.root
    sheet = _photo_sheet_then_withdrawn(taken, tmp_path)
    png(root / "swap" / "actor.jpg")                                       # a photo no swap entry names
    for given, fragment in ((dict(refs=[sheet]), "likeness_consent must be 'yes'"),
                            (dict(start="swap/person_front.jpg"), "not an image of the swap table"),
                            (dict(end="swap/person_front.jpg"), "not an image of the swap table"),
                            (dict(refs=["swap/actor.jpg"]), "not an image of the swap table")):
        with pytest.raises(CineRecastError, match=fragment) as refused:
            _add(root, **given)
        assert "cast check" in refused.value.message or "rights set" in refused.value.message   # what to do next
    assert not (root / "extras.json").exists() and not (root / "extras").exists()
    with pytest.raises(CineRecastError, match="likeness_consent"):        # the asset path refuses the same file
        assets.add(root, element="sky_battle", prompt="Rider and sky.", model="image_model_x", batch="n", refs=[sheet])


def test_c1_a_picture_made_from_a_person_carries_the_person_whatever_its_element(taken, tmp_path):
    root = taken.root
    swap_image(root, "person_front.jpg", "hero", only=True)
    sheet = generate_and_use(root, "hero", tmp_path, refs=[Path("swap/person_front.jpg")])   # consent "yes"
    pair = generate_and_use(root, "sky_battle", tmp_path, refs=[root / sheet])               # made from the person
    _without_swap(taken, "hero")
    set_right(root, "likeness_consent", "no")
    with pytest.raises(CineRecastError, match="likeness_consent must be 'yes'"):
        _add(root, refs=[pair])


def test_c1_swap_table_images_and_a_virtual_person_are_still_accepted(taken, tmp_path):
    root = taken.root
    sky = swap_image(root, "sky.png", "sky_battle")
    assert _add(root, refs=[sky], start=sky)["id"] == "x0001"
    _without_swap(taken, "hero")
    set_right(root, "likeness_consent", "not_applicable")
    hero = generate_and_use(root, "hero", tmp_path)                        # made without reference images
    pair = generate_and_use(root, "sky_battle", tmp_path, refs=[root / hero])  # the guided pair sheet (m3)
    assert [_add(root, refs=[hero])["id"], _add(root, end=pair)["id"]] == ["x0002", "x0003"]
    assert casting.not_applicable_holds(root) is True


def test_c1_the_rule_holds_again_at_set_cost_and_next(taken):
    root = taken.root
    photo = swap_image(root, "person_front.jpg", "hero", only=True)       # consent "yes"
    _add(root, refs=[photo])
    extras.set_cost(root, "x0001", 10.0); approve(root, "x-1", 10.0, "owner")
    _without_swap(taken, "hero")
    set_right(root, "likeness_consent", "no")
    assert casting.check(root) == []
    for call in (lambda: extras.set_cost(root, "x0001", 10.0), lambda: extras.issue_next(root, "x-1")):
        with pytest.raises(CineRecastError, match="not an image of the swap table"):
            call()
    swap_image(root, "person_front.jpg", "hero", only=True)                # named again: cast check wants "yes"
    with pytest.raises(CineRecastError, match="likeness_consent"):
        extras.issue_next(root, "x-1")
    assert job_states(root) == {}


def test_c1_an_extra_sent_with_a_person_photo_withholds_the_virtual_person_check(taken, tmp_path, result_clip):
    root = taken.root
    photo = swap_image(root, "person_front.jpg", "hero", only=True)
    _add(root, refs=[photo])
    extras.set_cost(root, "x0001", 10.0); approve(root, "x-1", 10.0, "owner")
    [run] = extras.issue_next(root, "x-1")
    assert run["medias"] == [{"role": "image", "path": str((root / photo).resolve())}]
    extras.record_done(root, "x0001", "prov-x1", 9.0, result_clip)
    generate_and_use(root, "hero", tmp_path)                               # the swap becomes a virtual person
    set_right(root, "likeness_consent", "not_applicable")
    assert casting.check(root) == [] and casting.not_applicable_holds(root) is False


def _still_copy(taken, name, *, jpeg=False, full_frame=False):
    """A frame of the reference copied into swap/ and named in the swap table (scenario I1)."""
    target = taken.root / "swap" / name
    target.parent.mkdir(exist_ok=True)
    if full_frame:
        extract_still(taken.root / "reference" / "source.mp4", 40, target)
    else:
        with Image.open(taken.shot_dir(1) / "stills" / "first.png") as still:
            still.convert("RGB").save(target, **({"quality": 90} if jpeg else {}))
    data = read_json(taken.casting_file)
    data["swaps"][1]["images"].append(f"swap/{name}")
    write_json(taken.casting_file, data)
    assert casting.check(taken.root) == []
    return f"swap/{name}"


@pytest.mark.parametrize("copy", [dict(), dict(jpeg=True), dict(full_frame=True)])
@pytest.mark.parametrize("flag", ["start", "end"])
def test_i1_a_frame_of_the_reference_is_never_a_start_or_end_image(taken, copy, flag):
    root = taken.root
    image = _still_copy(taken, "first.jpg" if copy.get("jpeg") else "first.png", **copy)
    with pytest.raises(CineRecastError, match="frame of the reference video is never a start or end image") as refused:
        _add(root, **{flag: image})
    assert "assets add" in refused.value.message and "shot-first" in refused.value.message
    assert not (root / "extras.json").exists() and not (root / "extras").exists()


def test_i1_a_picture_placed_by_hand_under_assets_is_not_an_input(taken):
    root = taken.root
    png(root / "assets" / "hero" / "handmade.png")
    for given in (dict(start="assets/hero/handmade.png"), dict(refs=["assets/hero/handmade.png"])):
        with pytest.raises(CineRecastError, match="not a file a done asset stored") as refused:
            _add(root, **given)
        assert "assets add" in refused.value.message
    assert not (root / "extras.json").exists()


def test_i1_an_unreadable_reference_refuses_an_image_start(taken):
    root = taken.root
    sky = swap_image(root, "sky.png", "sky_battle")
    (root / "reference" / "source.mp4").write_bytes(b"not a video any more")
    with pytest.raises(CineRecastError, match="reference video cannot be read"):
        _add(root, start=sky)
    assert not (root / "extras").exists()


@pytest.mark.parametrize("flag", ["--start", "--end", "--ref"])
def test_m5_a_file_that_is_not_a_readable_image_is_refused_with_a_sentence(taken, capsys, flag):
    root = taken.root
    broken = swap_image(root, "broken.png", "sky_battle")
    (root / broken).write_bytes(b"this is not a picture")
    capsys.readouterr()
    assert main(["extra", "add", str(root), "--label", "x", "--prompt", PROMPT, "--model", "seedance_2_5",
                 "--seconds", "4", "--batch", "x-1", flag, broken]) == 2
    err = capsys.readouterr().err
    assert "cannot be read as an image" in err and "defect" not in err
    assert not (root / "extras.json").exists() and not (root / "extras").exists()


def test_i2_inputs_changed_by_hand_are_refused_at_set_cost_and_next(taken):
    """Probe D: the video input changed to the unit's reference clip, and a photo added by hand."""
    root = taken.root
    _add(root, extend="u01/t001", direction="backward")
    extras.set_cost(root, "x0001", 10.0); approve(root, "x-1", 10.0, "owner")
    data = read_json(taken.extras_file)
    original = data["extras"][0]["inputs"]
    png(root / "swap" / "actor.jpg")
    for changed, fragment in (
            ([{"role": "video", "path": "analysis/units/u01/ref.mp4"}], "raw file of a take"),
            ([*original, {"role": "image", "path": "swap/actor.jpg"}], "not an image of the swap table"),
            ([*original, {"role": "start_image", "path": "swap/actor.jpg"}], "add the extra again")):
        data["extras"][0]["inputs"] = changed
        write_json(taken.extras_file, data)
        for call in (lambda: extras.set_cost(root, "x0001", 10.0), lambda: extras.issue_next(root, "x-1")):
            with pytest.raises(CineRecastError, match=fragment):
                call()
    assert job_states(root) == {}


# --- final review 2 (I3): join an extra to the frames the user confirmed ---

def _spy_frames(monkeypatch):
    """Record (take, raw frame) of every take frame extra add writes."""
    calls = []
    real = extras.extract_still

    def spy(path, index, out, **kwargs):
        calls.append((f"{Path(path).parent.parent.name}/{Path(path).parent.name}", index))
        return real(path, index, out, **kwargs)

    monkeypatch.setattr(extras, "extract_still", spy)
    return calls


@pytest.fixture
def per_shot(three_units):
    """three_units (u01-u03 per shot, padded, each take conformed with --take-cuts "0,<shot frames>")
    with the example model."""
    write_json(three_units.models_file, {"models": [EXAMPLE_MODEL, IMAGE_MODEL]})
    return three_units


def test_i3_shot_first_and_shot_last_are_the_frames_conform_kept(per_shot, monkeypatch):
    root = per_shot.root
    calls = _spy_frames(monkeypatch)
    raw = per_shot.take_dir("u01", "t001") / "raw.mp4"
    assert probe(raw).frames == 108
    assert read_json(per_shot.take_dir("u01", "t001") / "conform.json")["take_cuts"] == [0, 30]
    item = _add(root, start="u01/t001:shot-last", end="u02/t001:shot-first")
    assert item["source"] == {"start": "u01/t001:shot-last", "end": "u02/t001:shot-first"}
    _add(root, start="u01/t001:last", end="u03/t001:shot-last")
    assert calls == [("u01/t001", 29), ("u02/t001", 0), ("u01/t001", 107), ("u03/t001", 35)]


def test_i3_a_unit_of_several_shots_and_a_take_conformed_without_cuts(taken, monkeypatch):
    calls = _spy_frames(monkeypatch)
    _add(taken.root, start="u01/t001:shot-first", end="u01/t001:shot-last")    # start_frame 0, 90 unit frames
    assert calls == [("u01/t001", 0), ("u01/t001", 89)]


def test_i3_a_take_that_was_not_conformed_is_refused(per_shot, tmp_path):
    from conftest import fake_take

    take = fake_take(per_shot.root, "u01", tmp_path)
    with pytest.raises(CineRecastError, match=f"take u01/{take} has not been conformed") as refused:
        _add(per_shot.root, start=f"u01/{take}:shot-last")
    assert "conform" in refused.value.message and f"u01/{take}:<n>" in refused.value.message


def test_i3_another_frame_rate_is_converted_to_a_frame_of_the_raw_file(per_shot, tmp_path, monkeypatch):
    from conftest import fake_take

    from cine_recast.conform import conform

    root = per_shot.root
    take = fake_take(root, "u01", tmp_path, fps="30")                     # a 30 fps take of a 24 fps film
    conform(root, "u01", take, take_cuts=[0, 30])                          # fitted frames 0 to 29 kept
    calls = _spy_frames(monkeypatch)
    _add(root, start=f"u01/{take}:shot-last", end=f"u01/{take}:shot-first")
    assert calls == [(f"u01/{take}", 36), (f"u01/{take}", 0)]              # fitted frame 29 is raw frame 36


@pytest.mark.parametrize("raw_fps, film_fps", [("30", "24"), ("24", "30"), ("25", "24000/1001"), ("24", "24")])
def test_i3_the_raw_frame_is_the_one_conform_shows(tmp_path, raw_fps, film_fps):
    """extras.raw_frame against ffmpeg's fps filter, as conform runs it, frame by frame."""
    from fractions import Fraction

    from cine_recast.conform import _lossless
    from cine_recast.cuts import _signatures
    from cine_recast.ffmpeg import run
    from cine_recast.picture import PictureBox

    raw = make_clip(tmp_path / "raw.mp4", [("testsrc2", 45)], fps=raw_fps)
    fitted = tmp_path / "fitted.mov"
    run(["-i", str(raw), "-map", "0:v:0", "-vf", f"fps={film_fps}", *_lossless(fitted)])
    box = PictureBox(0, 0, 384, 216)
    raw_sigs, fitted_sigs = list(_signatures(raw, box)), list(_signatures(fitted, box))
    for j, sig in enumerate(fitted_sigs[:-1]):                             # the last frame may be the end padding
        distances = [float(np.abs(sig - other).mean()) for other in raw_sigs]
        nearest = int(np.argmin(distances))
        assert sorted(distances)[1] > 5 * max(distances[nearest], 0.2)     # one raw frame clearly matches
        assert extras.raw_frame(j, Fraction(raw_fps), Fraction(film_fps)) == nearest, j


def test_i3_a_padded_take_is_not_extended_forward(taken):
    root = taken.root
    assert read_json(taken.plan_file)["units"][0]["pad_frames"] == 6
    with pytest.raises(CineRecastError, match="padded") as refused:
        _add(root, extend="u01/t001", direction="forward")
    assert "extra add --start u01/t001:shot-last" in refused.value.message
    assert _add(root, extend="u01/t001", direction="backward")["id"] == "x0001"
    extras.set_cost(root, "x0001", 10.0); approve(root, "x-1", 10.0, "owner")
    data = read_json(taken.extras_file)
    data["extras"][0]["params"]["direction"] = "forward"; data["extras"][0]["source"]["direction"] = "forward"
    write_json(taken.extras_file, data)
    for call in (lambda: extras.set_cost(root, "x0001", 10.0), lambda: extras.issue_next(root, "x-1")):
        with pytest.raises(CineRecastError, match="padded"):
            call()
    assert job_states(root) == {}


def test_i3_a_take_whose_shot_reaches_the_end_of_its_raw_file_is_extended_forward(taken, tmp_path):
    from conftest import fake_take

    from cine_recast.conform import conform

    root = taken.root
    take = fake_take(root, "u01", tmp_path, extra_frames=-6)              # 90 frames: exactly the unit
    conform(root, "u01", take)
    assert read_json(taken.take_dir("u01", take) / "conform.json")["source_frames"] == 90
    assert _add(root, extend=f"u01/{take}", direction="forward")["params"]["direction"] == "forward"
    data = read_json(taken.plan_file); data["units"][0]["pad_frames"] = 0; write_json(taken.plan_file, data)
    unconformed = fake_take(root, "u01", tmp_path)                        # never conformed, unit not padded
    assert _add(root, extend=f"u01/{unconformed}", direction="forward")["id"] == "x0002"


# --- final review 2, round 2 (R5): the take's own conform record decides a forward extension ---

def test_r5_a_forward_extension_is_refused_after_plan_unit_removed_the_padding(taken):
    """Probe PAD: the take was made from a padded clip; plan unit moves the unit to a model whose
    minimum is below the shot, which sets pad_frames to 0."""
    from cine_recast import plan

    root = taken.root
    write_json(taken.models_file, {"models": [EXAMPLE_MODEL, IMAGE_MODEL,
                                              {**EXAMPLE_MODEL, "id": "short_min", "min_seconds": 3}]})
    plan.refresh_unit(root, "u01", model="short_min")
    assert read_json(taken.plan_file)["units"][0]["pad_frames"] == 0
    with pytest.raises(CineRecastError, match="conform kept its frames 0 to 89 of 108") as refused:
        _add(root, extend="u01/t001", direction="forward")
    assert "extra add --start u01/t001:shot-last" in refused.value.message
    assert not (root / "extras.json").exists()


def test_i2_an_extra_without_the_record_of_its_start_image_is_added_again(taken):
    root = taken.root
    _add(root, start="u01/t001:shot-first")
    data = read_json(taken.extras_file); del data["extras"][0]["inputs"][0]["sha256"]; write_json(taken.extras_file, data)
    with pytest.raises(CineRecastError, match="does not record what extra add wrote.*add the extra again"):
        extras.set_cost(root, "x0001", 10.0)


# --- final review 2, round 2 (R1): a picture that two swap entries name, through extra add ---

@pytest.mark.parametrize("order", [("horse", "hero"), ("hero", "horse")])
def test_r1_extra_add_refuses_a_picture_named_for_a_creature_and_a_person_in_either_order(taken, order):
    from cine_recast.inputs import image_problems

    root = taken.root
    c = read_json(taken.casting_file)
    c["elements"].append({"id": "horse", "kind": "creature", "name": "Horse", "shots": [1], "look": "brown horse"})
    c["swaps"] = [s for s in c["swaps"] if s["element"] != "hero"]
    write_json(taken.casting_file, c)
    for element in order:
        swap_image(root, "both.jpg", element)
    set_right(root, "likeness_consent", "no")
    with pytest.raises(CineRecastError, match="likeness_consent"):
        _add(root, refs=["swap/both.jpg"])
    assert any("may carry a real person's likeness" in p
               for p in image_problems(root, ["swap/both.jpg"], label="extra"))   # the rule extra add runs
    assert not (root / "extras.json").exists()


# --- final review 2, round 2 (R2): copies at another size or compression, flat frames, scaled bars ---

def _copy_of_still(taken, name, change):
    """The first still of shot 1, changed by `change` (a PIL image -> PIL image), saved as swap/<name>
    and named in the swap table."""
    with Image.open(taken.shot_dir(1) / "stills" / "first.png") as still:
        picture = change(still.convert("RGB"))
    target = taken.root / "swap" / name
    target.parent.mkdir(exist_ok=True)
    picture.save(target, **({"quality": 50} if name.endswith(".jpg") else {}))
    data = read_json(taken.casting_file); data["swaps"][1]["images"].append(f"swap/{name}")
    write_json(taken.casting_file, data)
    return f"swap/{name}"


@pytest.mark.parametrize("name, change", [
    ("half.png", lambda im: im.resize((im.width // 2, im.height // 2), Image.BICUBIC)),
    ("half_nearest.png", lambda im: im.resize((im.width // 2, im.height // 2), Image.NEAREST)),
    ("three_quarter.png", lambda im: im.resize((im.width * 3 // 4, im.height * 3 // 4), Image.BICUBIC)),
    ("three_quarter_nearest.png", lambda im: im.resize((im.width * 3 // 4, im.height * 3 // 4), Image.NEAREST)),
    ("q50.jpg", lambda im: im),
])
def test_r2_a_resized_or_recompressed_frame_of_the_reference_is_refused(taken, name, change):
    image = _copy_of_still(taken, name, change)
    with pytest.raises(CineRecastError, match="another size or compression") as refused:
        _add(taken.root, start=image)
    assert "frame of the reference video is never a start or end image" in refused.value.message
    assert not (taken.root / "extras").exists()


def test_r2_an_unrelated_picture_and_a_generated_key_picture_are_accepted(taken, tmp_path):
    from PIL import ImageDraw

    root = taken.root
    noise = root / swap_image(root, "noise.png", "sky_battle")
    Image.fromarray(np.random.default_rng(3).integers(0, 256, (216, 384, 3), dtype=np.uint8)).save(noise)
    key = root / generate_and_use(root, "hero", tmp_path)
    picture = Image.new("RGB", (384, 216), (40, 90, 160))
    draw = ImageDraw.Draw(picture)
    draw.ellipse((100, 40, 280, 190), fill=(220, 180, 60)); draw.rectangle((0, 170, 384, 216), fill=(30, 120, 40))
    picture.save(key)
    assert _add(root, start="swap/noise.png", end=key)["id"] == "x0001"


@pytest.fixture
def dark(tmp_path):
    """A letterboxed reference (bars of 24 px) that opens on 12 black frames, then 36 frames of
    testsrc2, analysed, with a background swap so pictures can be named, every right "yes"."""
    from cine_recast.analyze import analyze
    from cine_recast.project import RIGHTS_ITEMS, init_project

    clip = make_clip(tmp_path / "dark.mp4", [("color=c=black", 12), ("testsrc2", 36)], bars=24)
    pp = init_project(tmp_path / "d", clip, "dark")
    analyze(pp.root, picture=(0, 24, 384, 168), cuts=[12])
    write_json(pp.casting_file, {"elements": [{"id": "sky", "kind": "background", "name": "Sky", "shots": [1, 2],
                                               "look": "a coloured test pattern"}], "swaps": []})
    for item in RIGHTS_ITEMS:
        set_right(pp.root, item, "yes")
    write_json(pp.models_file, {"models": [EXAMPLE_MODEL]})
    return pp


def test_r2_a_near_black_picture_is_not_a_copy_of_a_black_frame(dark, monkeypatch):
    root = dark.root
    picture = root / swap_image(root, "night.png", "sky")
    Image.new("RGB", (384, 216), (1, 1, 1)).save(picture)                 # 1.0 from black: inside 1.5
    assert _add(root, start="swap/night.png")["id"] == "x0001"            # flat reference frames keep 0.25
    monkeypatch.setattr(extras, "FLAT_SPREAD", 0.0)                       # without that rule it would be refused
    with pytest.raises(CineRecastError, match="frame 0 of the reference video"):
        _add(root, start="swap/night.png")


def test_r2_a_scaled_full_frame_with_its_bars_is_refused(dark):
    root = dark.root
    full = root / swap_image(root, "full.png", "sky")
    extract_still(root / "reference" / "source.mp4", 30, full)            # the whole frame, bars included
    with Image.open(full) as frame:
        frame.convert("RGB").resize((192, 108), Image.BICUBIC).save(full)
    with pytest.raises(CineRecastError, match="another size or compression"):
        _add(root, start="swap/full.png")
