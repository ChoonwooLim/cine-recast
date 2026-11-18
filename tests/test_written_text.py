"""Final review 2 (m6): every free text a command writes into a project file is checked for Han
characters through one helper (textrules.refuse_han), and a refusal writes nothing."""

from pathlib import Path

import pytest
from conftest import EXAMPLE_MODEL, IMAGE_MODEL, png

from cine_recast import assets, extras, ledger, plan, review, sound
from cine_recast.cli import main
from cine_recast.errors import CineRecastError
from cine_recast.jsonio import read_json, write_json
from cine_recast.project import init_project, set_right
from cine_recast.textrules import han_problem, refuse_han

HAN = "\u4e2d"
PROMPT = "The giant roars at the camera, the same light and place as the take."


def test_the_helper_names_the_field_and_the_characters():
    assert han_problem("--note", "accepted \ud55c\uae00") is None                 # Hangul is allowed
    assert han_problem("--note", f"a {HAN} b {HAN}") == f"--note must not contain Han characters ({HAN})"
    with pytest.raises(CineRecastError, match="--by must not contain Han characters"):
        refuse_han("--by", f"owner{HAN}")
    refuse_han("--by", "owner")


@pytest.mark.parametrize("kwargs, field", [
    (dict(batch=f"draft{HAN}"), "batch"),
    (dict(by=f"owner{HAN}"), "by"),
    (dict(note=f"ok {HAN}"), "note"),
])
def test_approve_refuses_han(analyzed3, kwargs, field):
    args = {"batch": "b", "limit_credits": 10.0, "by": "owner", "note": "", **kwargs}
    with pytest.raises(CineRecastError, match=f"{field} must not contain Han"):
        ledger.approve(analyzed3.root, args.pop("batch"), args.pop("limit_credits"), args.pop("by"), args.pop("note"))
    assert not analyzed3.ledger_file.exists()


@pytest.mark.parametrize("by, note", [(f"owner{HAN}", ""), ("owner", f"chat {HAN}")])
def test_budget_set_refuses_han(analyzed3, by, note):
    with pytest.raises(CineRecastError, match="must not contain Han"):
        ledger.set_budget(analyzed3.root, 100.0, by, note)
    assert not analyzed3.ledger_file.exists()


def test_rights_set_and_init_refuse_han(analyzed3, clip3, tmp_path):
    before = analyzed3.project_file.read_bytes()
    with pytest.raises(CineRecastError, match="note must not contain Han"):
        set_right(analyzed3.root, "reference_rights", "yes", f"licensed {HAN}")
    assert analyzed3.project_file.read_bytes() == before
    with pytest.raises(CineRecastError, match="name must not contain Han"):
        init_project(tmp_path / "new", clip3, f"film {HAN}")
    assert not (tmp_path / "new").exists()


@pytest.mark.parametrize("by, note", [(f"owner{HAN}", ""), ("owner", f"fine {HAN}")])
def test_takes_confirm_refuses_han(finished3, by, note):
    data = read_json(finished3.plan_file); del data["units"][0]["confirmed"]; write_json(finished3.plan_file, data)
    before = finished3.plan_file.read_bytes()
    with pytest.raises(CineRecastError, match="must not contain Han"):
        review.confirm(finished3.root, "u01", "t001", by, note)
    assert finished3.plan_file.read_bytes() == before


def test_fail_reasons_and_provider_ids_refuse_han(planned3, result_clip, capsys):
    root = planned3.root
    plan.set_cost(root, "j0001", 10.0); ledger.approve(root, "b", 10.0, "owner"); plan.issue_next(root, "b")
    before = planned3.ledger_file.read_bytes()
    capsys.readouterr()
    assert main(["jobs", "fail", str(root), "--job", "j0001", "--reason", f"refused {HAN}"]) == 2
    assert "reason must not contain Han" in capsys.readouterr().err
    assert main(["jobs", "done", str(root), "--job", "j0001", "--provider-id", f"p{HAN}", "--credits", "9",
                 "--file", str(result_clip)]) == 2
    assert "provider_id must not contain Han" in capsys.readouterr().err
    assert planned3.ledger_file.read_bytes() == before and not (planned3.root / "takes" / "u01").exists()


def test_batch_names_refuse_han_at_every_add(planned3):
    root = planned3.root
    write_json(planned3.models_file, {"models": [EXAMPLE_MODEL, IMAGE_MODEL,
                                                 {"id": "audio_model_x", "kind": "audio", "min_seconds": 1,
                                                  "max_seconds": 30, "read_at": "2026-11-18"}]})
    before = planned3.plan_file.read_bytes()
    with pytest.raises(CineRecastError, match="batch must not contain Han"):
        plan.add_job(root, "u01", f"b{HAN}", "draft")
    assert planned3.plan_file.read_bytes() == before
    with pytest.raises(CineRecastError, match="batch must not contain Han"):
        assets.add(root, element="sky_battle", prompt="Sunset sky.", model="image_model_x", batch=f"a{HAN}")
    with pytest.raises(CineRecastError, match="batch must not contain Han"):
        sound.add(root, kind="sfx", label="door", prompt="A door slams.", model="audio_model_x", seconds=2,
                  batch=f"s{HAN}")
    with pytest.raises(CineRecastError, match="batch must not contain Han"):
        extras.add(root, label="x", prompt=PROMPT, model="seedance_2_5", seconds=4, batch=f"x{HAN}")
    for name in ("assets.json", "sounds.json", "extras.json"):
        assert not (root / name).exists()


def test_plan_build_refuses_a_han_batch(cast3):
    from conftest import write_models

    write_models(cast3.root)
    with pytest.raises(CineRecastError, match="batch must not contain Han"):
        plan.build(cast3.root, model="seedance_2_5", method="edit", batch=f"draft{HAN}")
    assert not cast3.plan_file.exists()


def test_file_names_of_input_images_refuse_han(conformed3, tmp_path):
    root = conformed3.root
    write_json(conformed3.models_file, {"models": [EXAMPLE_MODEL, IMAGE_MODEL]})
    photo = png(root / "swap" / f"hero{HAN}.png")
    with pytest.raises(CineRecastError, match="ref .* must not contain Han"):
        assets.add(root, element="sky_battle", prompt="Sunset sky.", model="image_model_x", batch="a1",
                   refs=[Path(f"swap/hero{HAN}.png")])
    for given in (dict(refs=[f"swap/hero{HAN}.png"]), dict(start=f"swap/hero{HAN}.png")):
        with pytest.raises(CineRecastError, match="must not contain Han"):
            extras.add(root, label="x", prompt=PROMPT, model="seedance_2_5", seconds=4, batch="x1", **given)
    assert photo.is_file() and not (root / "assets.json").exists()
    assert not (root / "extras.json").exists() and not (root / "extras").exists()
