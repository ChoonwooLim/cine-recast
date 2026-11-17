from cine_recast.paths import ProjectPaths


def test_paths_layout(tmp_path):
    pp = ProjectPaths(tmp_path)
    assert pp.sheet_file(7) == tmp_path / "analysis" / "shots" / "0007" / "sheet.json"
    assert pp.take_dir("u02", "t003") == tmp_path / "takes" / "u02" / "t003"
    assert pp.ledger_file == tmp_path / "ledger.jsonl"
