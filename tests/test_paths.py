import pytest

from cine_recast.errors import CineRecastError
from cine_recast.paths import ProjectPaths, material_folder, refuse_project_material


def test_paths_layout(tmp_path):
    pp = ProjectPaths(tmp_path)
    assert pp.sheet_file(7) == tmp_path / "analysis" / "shots" / "0007" / "sheet.json"
    assert pp.take_dir("u02", "t003") == tmp_path / "takes" / "u02" / "t003"
    assert pp.ledger_file == tmp_path / "ledger.jsonl"


def test_asset_paths(tmp_path):
    pp = ProjectPaths(tmp_path)
    assert pp.assets_file == tmp_path / "assets.json"
    assert pp.asset_dir("hero") == tmp_path / "assets" / "hero"


# --- final review 2 (m2, m8): one guard for "project material, not a result" ---

@pytest.mark.parametrize("folder", ["reference", "analysis", "takes", "out", "assets", "sounds", "extras", "swap",
                                    "SWAP", "Extras", "Sounds"])
def test_every_project_folder_is_project_material_in_any_spelling(tmp_path, folder):
    path = tmp_path / folder / "x" / "file.png"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"x")
    for spelling in (path, tmp_path / "downloads" / ".." / folder / "x" / "file.png"):
        assert material_folder(tmp_path, spelling) is not None
        with pytest.raises(CineRecastError, match=f"job j0001: .* is project material \\(inside the project's "
                                                  f"{folder.lower()} folder\\)"):
            refuse_project_material(tmp_path, spelling, "job j0001")


def test_files_outside_the_project_folders_are_not_project_material(tmp_path):
    for path in (tmp_path / "downloads" / "a.mp4", tmp_path / "swapped" / "a.png", tmp_path / "review" / "a.png",
                 tmp_path.parent / "elsewhere.mp4"):
        assert material_folder(tmp_path, path) is None
        refuse_project_material(tmp_path, path, "job j0001")
