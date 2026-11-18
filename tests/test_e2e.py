"""The whole command-line flow, from a reference clip to the finished film. Every step goes
through cli.main; the generation service is stood in for by local files."""

import json

from conftest import CAST_ELEMENTS, EXAMPLE_MODEL, GOOD_PROMPT, IMAGE_MODEL, fake_raw, png, run_cli, set_prompt

from cine_recast.cli import main
from cine_recast.ffmpeg import probe
from cine_recast.jsonio import read_json, write_json
from cine_recast.paths import ProjectPaths

PERSON_PROMPT = "Full-body studio portrait of a fictional young woman, short black hair, olive field jacket."


def test_full_flow_through_the_cli(tmp_path, clip3):
    root = tmp_path / "\uc7ac\uc81c\uc791 \ud504\ub85c\uc81d\ud2b8"
    pp = ProjectPaths(root)
    run_cli([["init", root, "--reference", clip3, "--name", "e2e"],
             ["rights", "set", root, "--item", "reference_rights", "--answer", "yes"],
             ["rights", "set", root, "--item", "likeness_consent", "--answer", "not_applicable"],   # a virtual person
             ["rights", "set", root, "--item", "background_rights", "--answer", "yes"],
             ["analyze", root], ["cuts", "review", root], ["cast", "template", root]])

    # The agent writes the element list and the model list; the background is an ordinary file.
    background = png(tmp_path / "refs" / "background.png")
    write_json(pp.casting_file, {"elements": CAST_ELEMENTS, "swaps": [
        {"element": "sky_battle", "images": [background.as_posix()], "note": ""}]})
    write_json(pp.models_file, {"models": [EXAMPLE_MODEL, IMAGE_MODEL]})

    # The person is generated: quote, approval, issue, the returned image (a 1x1 PNG), the swap table.
    returned = png(tmp_path / "returned" / "person.png")
    run_cli([["models", "check", root],
             ["assets", "add", root, "--element", "hero", "--prompt", PERSON_PROMPT, "--model", "image_model_x",
              "--batch", "assets-1"],
             ["assets", "list", root],
             ["assets", "set-cost", root, "--asset", "a0001", "--credits", "6"],
             ["approve", root, "--batch", "assets-1", "--limit", "10", "--by", "owner"],
             ["assets", "next", root, "--batch", "assets-1"],
             ["assets", "done", root, "--asset", "a0001", "--provider-id", "prov-img-1", "--credits", "5.5",
              "--file", returned],
             ["assets", "use", root, "--element", "hero", "--image", "assets/hero/a0001_1.png"],
             ["cast", "check", root],
             ["plan", "build", root, "--model", "seedance_2_5", "--method", "edit", "--batch", "draft-1"]])
    assert [i["path"] for i in read_json(pp.plan_file)["units"][0]["inputs"]] == [
        "analysis/units/u01/ref.mp4", "assets/hero/a0001_1.png", background.as_posix()]

    # The video job: prompt, quote, approval, issue, the returned clip, then the film.
    set_prompt(root, "u01", GOOD_PROMPT)
    run_cli([["plan", "check", root], ["plan", "show", root], ["jobs", "list", root],
             ["jobs", "set-cost", root, "--job", "j0001", "--credits", "40"],
             ["approve", root, "--batch", "draft-1", "--limit", "50", "--by", "owner"],
             ["jobs", "next", root, "--batch", "draft-1"]])
    raw = fake_raw(root, "u01", tmp_path, fps="30")                    # a longer 30 fps copy of the unit clip
    run_cli([["jobs", "done", root, "--job", "j0001", "--provider-id", "prov-1", "--credits", "38", "--file", raw],
             ["conform", root, "--unit", "u01", "--take", "t001"], ["qc", root, "--unit", "u01", "--take", "t001"],
             ["takes", "select", root, "--unit", "u01", "--take", "t001"],
             ["assemble", root], ["compare", root], ["report", root], ["audit", root]])
    assert probe(root / "out" / "final.mp4").frames == 90
    assert (root / "out" / "compare.mp4").is_file() and (root / "out" / "report.md").is_file()


def test_unapproved_job_is_refused_by_the_cli(planned3, capsys):
    root = planned3.root                                               # planned, with prompts; batch "b"
    run_cli([["jobs", "set-cost", root, "--job", "j0001", "--credits", "40"]])
    capsys.readouterr()
    assert main(["jobs", "next", str(root), "--batch", "b"]) == 2
    out = capsys.readouterr()
    assert json.loads(out.out) == [] and "approval" in out.err


def test_unapproved_asset_is_refused_by_the_cli(assets3, capsys):
    root = assets3.root
    run_cli([["assets", "add", root, "--element", "hero", "--prompt", PERSON_PROMPT, "--model", "image_model_x",
              "--batch", "assets-1"],
             ["assets", "set-cost", root, "--asset", "a0001", "--credits", "6"]])
    capsys.readouterr()
    assert main(["assets", "next", str(root), "--batch", "assets-1"]) == 2
    out = capsys.readouterr()
    assert json.loads(out.out) == [] and "approval" in out.err
