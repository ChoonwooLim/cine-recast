from pathlib import Path

from cine_recast.textrules import han_chars


def test_repository_folder_has_no_han_characters():
    root = Path(__file__).resolve().parents[1]
    exts = {".py", ".md", ".json", ".toml", ".ps1", ".txt", ".yaml", ".yml", ".cfg"}
    skip = {".git", ".venv", "__pycache__", ".pytest_cache", ".superpowers"}
    bad = {str(p.relative_to(root)): han_chars(p.read_text(encoding="utf-8"))
           for p in root.rglob("*") if p.is_file() and p.suffix in exts and not (set(p.parts) & skip)}
    assert {k: v for k, v in bad.items() if v} == {}
