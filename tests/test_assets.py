import json
from pathlib import Path

import pytest
from conftest import CAST_ELEMENTS, EXAMPLE_MODEL, IMAGE_MODEL, generate_and_use, png, run_cli

from cine_recast.assets import (add, generated_files, issue_next, list_assets, record_done, record_fail,
                                ref_free_files, set_cost, use)
from cine_recast.casting import check
from cine_recast.cli import main
from cine_recast.errors import CineRecastError
from cine_recast.jsonio import read_json, write_json
from cine_recast.ledger import approve, audit, job_states
from cine_recast.plan import check as check_plan
from cine_recast.plan import check_models
from cine_recast.plan import set_cost as set_job_cost
from cine_recast.project import set_right

PROMPT = "Full-body portrait of a young adult with short black hair, neutral studio light."


def test_add_creates_a_planned_asset(assets3):
    a = add(assets3.root, element="hero", prompt=PROMPT, model="image_model_x", batch="assets-1", count=2)
    assert (a["id"], a["state"], a["kind"], a["params"]) == ("a0001", "planned", "person", {"aspect_ratio": "1:1", "resolution": "1k", "count": 2})
    bg = add(assets3.root, element="sky_battle", prompt="Sunset sky over red dunes.", model="image_model_x", batch="assets-1")
    assert bg["id"] == "a0002" and bg["params"]["aspect_ratio"] == "16:9"       # nearest to the 384x216 picture


@pytest.mark.parametrize("kwargs, fragment", [
    (dict(element="ghost"), "element"),
    (dict(prompt="\uc18c\ub144 \uc804\uc2e0"), "prompt"),
    (dict(model="seedance_2_5"), "image"),
    (dict(count=5), "count"),
    (dict(aspect_ratio="9:99"), "aspect"),
    (dict(refs=[Path("Z:/missing.png")]), "ref"),
])
def test_add_refusals(assets3, kwargs, fragment):
    base = dict(element="hero", prompt=PROMPT, model="image_model_x", batch="assets-1")
    with pytest.raises(CineRecastError, match=fragment):
        add(assets3.root, **{**base, **kwargs})


def test_issue_goes_through_the_ledger(assets3):
    add(assets3.root, element="hero", prompt=PROMPT, model="image_model_x", batch="assets-1")
    assert issue_next(assets3.root, "assets-1") == []                           # nothing quoted yet
    set_cost(assets3.root, "a0001", 6.0)
    with pytest.raises(CineRecastError, match="approval"):
        issue_next(assets3.root, "assets-1")
    approve(assets3.root, "assets-1", 10.0, "owner")
    job = issue_next(assets3.root, "assets-1")[0]
    assert (job["job_id"], job["tool"], job["element"]) == ("a0001", "generate_image", "hero")
    assert job["params"] == {"model": "image_model_x", "prompt": PROMPT, "aspect_ratio": "1:1", "resolution": "1k", "count": 1}
    assert issue_next(assets3.root, "assets-1") == []                           # never issued twice


def test_a_changed_asset_must_be_quoted_again(assets3):
    add(assets3.root, element="hero", prompt=PROMPT, model="image_model_x", batch="assets-1")
    set_cost(assets3.root, "a0001", 6.0); approve(assets3.root, "assets-1", 10.0, "owner")
    data = read_json(assets3.assets_file); data["assets"][0]["params"]["count"] = 4; write_json(assets3.assets_file, data)
    with pytest.raises(CineRecastError, match="set-cost"):
        issue_next(assets3.root, "assets-1")


def test_done_stores_files_and_use_fills_the_swap_table(assets3, tmp_path):
    add(assets3.root, element="hero", prompt=PROMPT, model="image_model_x", batch="assets-1", count=2)
    set_cost(assets3.root, "a0001", 6.0); approve(assets3.root, "assets-1", 10.0, "owner"); issue_next(assets3.root, "assets-1")
    done = record_done(assets3.root, "a0001", "prov-1", 5.5, [png(tmp_path / "x.png"), png(tmp_path / "y.png")])
    assert done == {"asset": "a0001", "files": ["assets/hero/a0001_1.png", "assets/hero/a0001_2.png"]}
    assert generated_files(assets3.root, "hero") == done["files"] and audit(assets3.root)["ok"] is True
    with pytest.raises(CineRecastError):
        use(assets3.root, "hero", "assets/hero/a0009_1.png")                   # not a generated file
    use(assets3.root, "hero", "assets/hero/a0001_2.png")
    swap = read_json(assets3.casting_file)["swaps"][0]
    assert swap == {"element": "hero", "images": ["assets/hero/a0001_2.png"], "note": "generated: a0001"}


def test_generated_person_passes_without_a_likeness_consent(assets3, tmp_path):
    generate_and_use(assets3.root, "hero", tmp_path)                            # helper: add..use for one element
    assert check(assets3.root) == []                                            # likeness_consent is not_applicable
    c = read_json(assets3.casting_file); c["swaps"][0]["images"] = [str(png(tmp_path / "photo.png"))]
    write_json(assets3.casting_file, c)
    assert any("likeness_consent" in p for p in check(assets3.root))            # an outside photo needs "yes"


def test_refused_done_leaves_no_files(assets3, tmp_path):
    add(assets3.root, element="hero", prompt=PROMPT, model="image_model_x", batch="assets-1")
    with pytest.raises(CineRecastError):
        record_done(assets3.root, "a0001", "prov-1", 1.0, [png(tmp_path / "x.png")])   # never issued
    assert not assets3.asset_dir("hero").exists() or not any(assets3.asset_dir("hero").iterdir())


def test_asset_ids_continue_above_the_ledger(assets3):
    add(assets3.root, element="hero", prompt=PROMPT, model="image_model_x", batch="assets-1")
    set_cost(assets3.root, "a0001", 6.0); approve(assets3.root, "assets-1", 10.0, "owner"); issue_next(assets3.root, "assets-1")
    assets3.assets_file.unlink()
    assert add(assets3.root, element="hero", prompt=PROMPT, model="image_model_x", batch="assets-1")["id"] == "a0002"


def test_image_model_needs_no_duration_fields(assets3):
    assert check_models(assets3.root) == []


def test_cli_flow(assets3, tmp_path):
    root = assets3.root
    run_cli([["assets", "add", root, "--element", "sky_battle", "--prompt", "Sunset sky over red dunes.",
              "--model", "image_model_x", "--batch", "assets-1"],
             ["assets", "set-cost", root, "--asset", "a0001", "--credits", "4"],
             ["approve", root, "--batch", "assets-1", "--limit", "5", "--by", "owner"],
             ["assets", "next", root, "--batch", "assets-1"],
             ["assets", "done", root, "--asset", "a0001", "--provider-id", "prov-1", "--credits", "4",
              "--file", png(tmp_path / "bg.png")],
             ["assets", "use", root, "--element", "sky_battle", "--image", "assets/sky_battle/a0001_1.png"],
             ["assets", "list", root], ["cast", "check", root]])


# --- the rest of the life cycle: refusals at done, failures, listing, the CLI on a refusal ---

def _issued(root, *elements):
    """Add one asset per element in batch assets-1, quote each at 6 credits, approve 10 and issue."""
    ids = [add(root, element=e, prompt=PROMPT, model="image_model_x", batch="assets-1")["id"] for e in elements]
    for asset_id in ids:
        set_cost(root, asset_id, 6.0)
    approve(root, "assets-1", 10.0, "owner")
    return ids


def test_a_ledger_refusal_removes_the_stored_files(assets3, tmp_path):
    add(assets3.root, element="hero", prompt=PROMPT, model="image_model_x", batch="assets-1")
    data = read_json(assets3.assets_file); data["assets"][0]["state"] = "issued"; write_json(assets3.assets_file, data)
    with pytest.raises(CineRecastError, match="never issued"):
        record_done(assets3.root, "a0001", "prov-1", 1.0, [png(tmp_path / "x.png")])
    assert not any(assets3.asset_dir("hero").iterdir()) and "a0001" not in job_states(assets3.root)


def test_a_stored_file_is_never_overwritten(assets3, tmp_path):
    _issued(assets3.root, "hero")
    issue_next(assets3.root, "assets-1")
    existing = assets3.asset_dir("hero") / "a0001_1.png"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"kept")
    with pytest.raises(CineRecastError, match="overwritten"):
        record_done(assets3.root, "a0001", "prov-1", 1.0, [png(tmp_path / "x.png")])
    assert existing.read_bytes() == b"kept" and job_states(assets3.root)["a0001"] == "issued"


def test_done_takes_only_image_files(assets3, tmp_path):
    _issued(assets3.root, "hero")
    issue_next(assets3.root, "assets-1")
    clip = tmp_path / "x.mp4"
    clip.write_bytes(b"not an image")
    with pytest.raises(CineRecastError, match=".png"):
        record_done(assets3.root, "a0001", "prov-1", 1.0, [clip])


def test_fail_and_list(assets3):
    _issued(assets3.root, "hero")
    issue_next(assets3.root, "assets-1")
    assert record_fail(assets3.root, "a0001", "timeout", 0.5)["state"] == "failed"
    with pytest.raises(CineRecastError, match="not issued"):
        record_fail(assets3.root, "a0001", "again")
    add(assets3.root, element="sky_battle", prompt="Sunset sky over red dunes.", model="image_model_x", batch="assets-1")
    listed = list_assets(assets3.root)
    assert [(a["id"], a["state"], a["kind"]) for a in listed] == [("a0001", "failed", "person"), ("a0002", "planned", "background")]
    assert set(listed[0]) == {"id", "element", "kind", "batch", "state", "est_credits", "params", "files"}
    assert listed[0]["params"] == {"model": "image_model_x", "prompt": PROMPT, "aspect_ratio": "1:1", "resolution": "1k", "count": 1}
    assert audit(assets3.root)["batches"]["assets-1"]["committed"] == 0.5


def test_cli_prints_assets_issued_before_a_refusal(assets3, capsys):
    _issued(assets3.root, "hero", "sky_battle")                                 # 6 + 6 credits against a limit of 10
    capsys.readouterr()
    assert main(["assets", "next", str(assets3.root), "--batch", "assets-1"]) == 2
    out = capsys.readouterr()
    assert [j["job_id"] for j in json.loads(out.out)] == ["a0001"] and "a0002" in out.err and "limit" in out.err


def test_a_unit_on_an_image_model_is_refused(planned3):
    write_json(planned3.models_file, {"models": [EXAMPLE_MODEL, IMAGE_MODEL]})
    data = read_json(planned3.plan_file); data["units"][0]["model"] = "image_model_x"; write_json(planned3.plan_file, data)
    assert any("video model" in p for p in check_plan(planned3.root))
    with pytest.raises(CineRecastError, match="video model"):
        set_job_cost(planned3.root, "j0001", 1.0)


def test_clearing_refs_afterwards_does_not_make_a_virtual_person(assets3, tmp_path):
    set_right(assets3.root, "likeness_consent", "yes")                          # refs need consent to be generated
    image = generate_and_use(assets3.root, "hero", tmp_path, refs=[png(tmp_path / "ref.png")])
    set_right(assets3.root, "likeness_consent", "not_applicable")
    assert generated_files(assets3.root, "hero") == [image] and ref_free_files(assets3.root, "hero") == []
    data = read_json(assets3.assets_file); data["assets"][0]["refs"] = []; write_json(assets3.assets_file, data)
    assert ref_free_files(assets3.root, "hero") == []                           # the quote binds what was sent
    assert any("likeness_consent" in p for p in check(assets3.root))


# --- fix round 1: generated files come from the ledger, and done takes no file of assets/ ---

def test_a_done_written_by_hand_does_not_count_as_generated(assets3):
    root = assets3.root
    _issued(root, "hero")
    issue_next(root, "assets-1")                                                # a0001 issued, never finished
    png(assets3.asset_dir("hero") / "a0001_1.png")
    data = read_json(assets3.assets_file)
    data["assets"][0].update(state="done", files=["assets/hero/a0001_1.png"]); write_json(assets3.assets_file, data)
    assert generated_files(root, "hero") == [] and ref_free_files(root, "hero") == []
    c = read_json(assets3.casting_file)
    c["swaps"] = [{"element": "hero", "images": ["assets/hero/a0001_1.png"], "note": ""}]
    write_json(assets3.casting_file, c)
    assert any("likeness_consent" in p for p in check(root))


# --- final-review wave: an asset with reference images reads the rights answers ---

def test_person_asset_with_refs_needs_consent_at_add(assets3, tmp_path, capsys):
    root = assets3.root
    set_right(root, "likeness_consent", "no")
    photo = png(tmp_path / "real_person.png")
    capsys.readouterr()
    assert main(["assets", "add", str(root), "--element", "hero", "--prompt", PROMPT, "--model", "image_model_x",
                 "--batch", "a1", "--ref", str(photo)]) == 2
    err = capsys.readouterr().err
    assert "likeness_consent" in err and "rights set" in err
    assert not assets3.assets_file.exists()


def test_consent_withdrawn_after_add_stops_quote_and_issue(assets3, tmp_path):
    root = assets3.root
    set_right(root, "likeness_consent", "yes")
    add(root, element="hero", prompt=PROMPT, model="image_model_x", batch="a1", refs=[png(tmp_path / "ref.png")])
    set_cost(root, "a0001", 6.0); approve(root, "a1", 10.0, "owner")
    set_right(root, "likeness_consent", "no")
    before = assets3.ledger_file.read_text(encoding="utf-8")
    with pytest.raises(CineRecastError, match="likeness_consent"):
        set_cost(root, "a0001", 6.0)
    with pytest.raises(CineRecastError, match="likeness_consent"):
        issue_next(root, "a1")
    assert assets3.ledger_file.read_text(encoding="utf-8") == before
    assert list_assets(root)[0]["state"] == "quoted"


def test_person_asset_without_refs_is_unaffected(assets3):
    set_right(assets3.root, "likeness_consent", "no")
    add(assets3.root, element="hero", prompt=PROMPT, model="image_model_x", batch="a1")
    set_cost(assets3.root, "a0001", 6.0); approve(assets3.root, "a1", 10.0, "owner")
    assert [j["job_id"] for j in issue_next(assets3.root, "a1")] == ["a0001"]


def test_kind_is_read_from_casting_at_that_moment(assets3, tmp_path):
    root = assets3.root
    ref = png(tmp_path / "ref.png")
    add(root, element="sky_battle", prompt="Sunset sky over red dunes.", model="image_model_x", batch="a1",
        refs=[ref])                                              # a background with refs: background_rights "yes"
    c = read_json(assets3.casting_file); c["elements"][1]["kind"] = "person"; write_json(assets3.casting_file, c)
    with pytest.raises(CineRecastError, match="likeness_consent"):
        set_cost(root, "a0001", 6.0)                             # likeness_consent is "not_applicable"


def test_background_asset_with_refs_needs_background_rights(analyzed3, tmp_path):
    root = analyzed3.root
    write_json(analyzed3.casting_file, {"elements": CAST_ELEMENTS, "swaps": []})
    write_json(analyzed3.models_file, {"models": [EXAMPLE_MODEL, IMAGE_MODEL]})
    with pytest.raises(CineRecastError, match="background_rights"):
        add(root, element="sky_battle", prompt="Sunset sky over red dunes.", model="image_model_x", batch="a1",
            refs=[png(tmp_path / "ref.png")])
    set_right(root, "background_rights", "unknown")              # answered, as for a background swap
    assert add(root, element="sky_battle", prompt="Sunset sky over red dunes.", model="image_model_x", batch="a1",
               refs=[png(tmp_path / "ref.png")])["id"] == "a0001"


def test_done_refuses_a_file_from_the_project_assets_folder(assets3, tmp_path):
    root = assets3.root
    first = generate_and_use(root, "hero", tmp_path)                            # a0001 done
    add(root, element="hero", prompt=PROMPT, model="image_model_x", batch="assets-1")
    set_cost(root, "a0002", 1.0); approve(root, "assets-1", 1.0, "owner"); issue_next(root, "assets-1")
    with pytest.raises(CineRecastError, match="assets folder"):
        record_done(root, "a0002", "prov-2", 1.0, [root / first])
    assert [p.name for p in assets3.asset_dir("hero").iterdir()] == ["a0001_1.png"]
    assert job_states(root)["a0002"] == "issued"
