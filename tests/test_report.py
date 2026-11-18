from conftest import generate_and_use, png, write_models

from cine_recast.assemble import assemble
from cine_recast.jsonio import read_json, write_json
from cine_recast.plan import build
from cine_recast.report import write_report
from cine_recast.textrules import han_chars


def _open_items(text: str) -> list[str]:
    return text.split("## \uc5f4\ub9b0 \ud56d\ubaa9")[1].strip().splitlines()


def test_report_lists_shots_almost_identical_to_the_reference(finished3):
    scores_file = finished3.take_dir("u01", "t001") / "scores.json"
    scores = read_json(scores_file)
    for shot, overlap in (("1", 0.99), ("2", 0.98), ("3", 0.979)):
        scores["shots"][shot]["overlap_ratio"] = overlap
    write_json(scores_file, scores)
    [line] = [l for l in _open_items(write_report(finished3.root).read_text(encoding="utf-8")) if "0.98" in l]
    assert ": 1, 2 " in line and "3" not in line.split(":")[1]
    for shot in scores["shots"].values():
        shot["overlap_ratio"] = 0.5
    write_json(scores_file, scores)
    [line] = [l for l in _open_items(write_report(finished3.root).read_text(encoding="utf-8")) if "0.98" in l]
    assert line.endswith(": \uc5c6\uc74c")


def _section(text: str, heading: str) -> str:
    return text.split(heading)[1].split("\n## ")[0]


def test_report_totals_costs_and_lists_provider_ids(finished3):
    from cine_recast import ledger

    ledger.approve(finished3.root, "c", 20.0, "owner"); ledger.issue(finished3.root, "j0900", "c", 5.0)
    cost = _section(write_report(finished3.root).read_text(encoding="utf-8"), "## \ube44\uc6a9")
    assert "| \ud569\uacc4 | 70.00 | 43.00 | 27.00 |" in cost                     # b: 50 / 38, c: 20 / 5
    [ids] = [line for line in cost.splitlines() if "Higgsfield" in line]
    assert ids.endswith(": prov-1")


def _rights_line(root, item: str) -> str:
    text = write_report(root).read_text(encoding="utf-8")
    return next(line for line in _section(text, "## \uad8c\ub9ac \ud655\uc778").splitlines() if f"({item})" in line)


def test_verified_not_applicable_consent_needs_no_confirmation(assets3, tmp_path):
    root = assets3.root                                               # likeness_consent is "not_applicable"
    generate_and_use(root, "hero", tmp_path)                          # a person generated without references
    write_models(root)
    build(root, model="seedance_2_5", method="edit", batch="b")
    assert "\ud655\uc778 \ud544\uc694" not in _rights_line(root, "likeness_consent")
    c = read_json(assets3.casting_file); c["swaps"][0]["images"] = [png(tmp_path / "photo.png").as_posix()]
    write_json(assets3.casting_file, c)                               # an outside photo: no longer verified
    assert "\ud655\uc778 \ud544\uc694" in _rights_line(root, "likeness_consent")


def test_report_content(finished3):
    assemble(finished3.root)
    text = write_report(finished3.root).read_text(encoding="utf-8")
    assert text.startswith("# CineRecast \ubcf4\uace0\uc11c")
    for heading in ("## \uc694\uc57d", "## \uad8c\ub9ac \ud655\uc778", "## \uad50\uccb4\ud45c", "## \uc20f\ubcc4 \uacb0\uacfc", "## \ube44\uc6a9", "## \uc5f4\ub9b0 \ud56d\ubaa9"):
        assert heading in text
    assert "| 1 | 0-30 | u01 |" in text and "t001" in text and han_chars(text) == []
