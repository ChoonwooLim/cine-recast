from cine_recast.assemble import assemble
from cine_recast.jsonio import read_json, write_json
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


def test_report_content(finished3):
    assemble(finished3.root)
    text = write_report(finished3.root).read_text(encoding="utf-8")
    assert text.startswith("# CineRecast \ubcf4\uace0\uc11c")
    for heading in ("## \uc694\uc57d", "## \uad8c\ub9ac \ud655\uc778", "## \uad50\uccb4\ud45c", "## \uc20f\ubcc4 \uacb0\uacfc", "## \ube44\uc6a9", "## \uc5f4\ub9b0 \ud56d\ubaa9"):
        assert heading in text
    assert "| 1 | 0-30 | u01 |" in text and "t001" in text and han_chars(text) == []
