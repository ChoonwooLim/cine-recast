import os
from pathlib import Path

from cine_recast.textrules import han_chars

SKIPPED_FOLDERS = {".git", ".venv", "__pycache__", ".pytest_cache", ".superpowers"}
BINARY_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp4", ".mov", ".ico", ".pdf"}


def _skipped(name: str) -> bool:
    return name in SKIPPED_FOLDERS or name.endswith(".egg-info")


def han_in_tree(root: Path) -> dict[str, list[str]]:
    """Han characters per file under root: in the relative path (folder and file names) and in the
    content of every file that decodes as UTF-8 and is not of a binary type. Skipped folders are
    not entered."""
    found: dict[str, list[str]] = {}
    for folder, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if not _skipped(d)]
        for name in files:
            path = Path(folder) / name
            rel = path.relative_to(root).as_posix()
            chars = han_chars(rel)
            if path.suffix.lower() not in BINARY_SUFFIXES:
                try:
                    chars += han_chars(path.read_bytes().decode("utf-8"))
                except UnicodeDecodeError:
                    pass
            if chars:
                found[rel] = chars
    return found


def test_repository_folder_has_no_han_characters():
    assert han_in_tree(Path(__file__).resolve().parents[1]) == {}


def test_the_scan_covers_names_files_without_a_suffix_and_svg(tmp_path):
    han = "\u4e2d"
    (tmp_path / "pic.svg").write_text(f"<svg><text>{han}</text></svg>", encoding="utf-8")
    (tmp_path / ".gitattributes").write_text(f"# {han}\n", encoding="utf-8")
    (tmp_path / "NOTICE").write_text(han, encoding="utf-8")
    (tmp_path / f"name{han}.txt").write_text("plain", encoding="utf-8")
    (tmp_path / "pic.png").write_bytes(han.encode("utf-8"))              # a binary type: content not read
    (tmp_path / "a.egg-info").mkdir()
    (tmp_path / "a.egg-info" / "PKG-INFO").write_text(han, encoding="utf-8")
    (tmp_path / ".superpowers").mkdir()
    (tmp_path / ".superpowers" / "x.md").write_text(han, encoding="utf-8")
    assert sorted(han_in_tree(tmp_path)) == sorted(["pic.svg", ".gitattributes", "NOTICE", f"name{han}.txt"])
