"""Extra shots (extras.json and the extra commands): shots that are not in the reference, made as
extensions of a take or from frames of a take, through the same approval ledger."""

import json
import shutil

import numpy as np
import pytest
from conftest import EXAMPLE_MODEL, IMAGE_MODEL, make_clip, png, run_cli
from PIL import Image

from cine_recast import extras
from cine_recast.cli import main
from cine_recast.errors import CineRecastError
from cine_recast.ffmpeg import iter_frames, probe
from cine_recast.jsonio import read_json, write_json
from cine_recast.ledger import approve, audit, job_states, set_budget
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
    assert item["inputs"] == [{"role": "start_image", "path": "extras/x0001/start.png"},
                              {"role": "end_image", "path": "extras/x0001/end.png"}]
    assert item["source"] == {"start": "u01/t001:last", "end": "u01/t001:20"}
    for name, index in (("start.png", frames - 1), ("end.png", 20)):
        with Image.open(folder / name) as image:
            assert image.size == (640, 360)
            pixels = np.asarray(image.convert("RGB")).astype(np.int16)
        assert np.abs(pixels - _frame(raw, index)).max() <= 2, name
    assert np.abs(_frame(raw, 20) - _frame(raw, 60)).mean() > 5            # the test can tell frames apart


def test_add_with_reference_images(taken):
    swap = png(taken.root / "swap" / "monster.png")
    asset = png(taken.root / "assets" / "hero" / "a0001_1.png")
    item = _add(taken.root, refs=[asset, "swap/monster.png"], start="u01/t001:first")
    assert item["inputs"] == [{"role": "start_image", "path": "extras/x0001/start.png"},
                              {"role": "image", "path": "assets/hero/a0001_1.png"},
                              {"role": "image", "path": "swap/monster.png"}]
    assert _add(taken.root)["inputs"] == [] and swap.is_file()               # a prompt-only extra is legal


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
    _add(root, extend="u01/t001", direction="forward")
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
                   "resolution": "480p", "direction": "forward"},
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
    with pytest.raises(CineRecastError, match="extra set-cost"):
        extras.issue_next(root, "x-1")
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
    _add(root, extend="u01/t001", direction="forward", batch="x-2")
    extras.set_cost(root, "x0002", 5.0); approve(root, "x-2", 5.0, "owner"); extras.issue_next(root, "x-2")
    assert main(["extra", "fail", str(root), "--extra", "x0002", "--reason", "not submitted"]) == 0


# --- start and end frames given as images under assets/ or swap/ ---

def test_start_and_end_accept_images_from_assets_and_swap(taken):
    root = taken.root
    key = png(root / "assets" / "hero" / "a0001_1.png")
    Image.new("RGB", (64, 36), (10, 200, 30)).save(key)
    photo = root / "swap" / "last.jpg"
    photo.parent.mkdir(parents=True)
    Image.new("RGB", (64, 36), (200, 20, 40)).save(photo, quality=95)
    item = extras.add(root, label="open", prompt=PROMPT, model="seedance_2_5", seconds=4, batch="x-1",
                      start="assets/hero/a0001_1.png", end=str(photo))
    folder = root / "extras" / "x0001"
    assert item["inputs"] == [{"role": "start_image", "path": "extras/x0001/start.png"},
                              {"role": "end_image", "path": "extras/x0001/end.png"}]
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


def test_the_quote_covers_the_content_of_a_start_image(taken):
    root = taken.root
    png(root / "assets" / "hero" / "a0001_1.png")
    extras.add(root, label="open", prompt=PROMPT, model="seedance_2_5", seconds=4, batch="x-1",
               start="assets/hero/a0001_1.png")
    extras.set_cost(root, "x0001", 10.0); approve(root, "x-1", 10.0, "owner")
    Image.new("RGB", (8, 8), (0, 0, 255)).save(root / "extras" / "x0001" / "start.png")
    with pytest.raises(CineRecastError, match="extra set-cost"):
        extras.issue_next(root, "x-1")


def test_this_file_keeps_han_as_an_escape():
    from pathlib import Path

    assert "\\u4e2d" in Path(__file__).read_text(encoding="utf-8")
