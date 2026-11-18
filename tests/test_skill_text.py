import re
import shlex
from pathlib import Path

import pytest

from cine_recast import cli
from cine_recast.textrules import han_chars

SKILL = Path(__file__).resolve().parents[1] / "skill"


def test_front_matter():
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    head = text.split("---")[1]
    assert "name: cine-recast" in head and "description:" in head


def test_required_rules_are_present():
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    for needle in ("cine-recast.ps1", "jobs next", "approve", "audit", "cuts review", "cast check",
                   "plan build", "conform", "assemble", "report"):
        assert needle in text
    assert han_chars(text) == []


def test_referenced_files_exist():
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    for name in re.findall(r"references/[a-z\-]+\.md", text):
        assert (SKILL / name).is_file()


def test_generation_reference_mentions_the_gate():
    text = (SKILL / "references" / "generation-jobs.md").read_text(encoding="utf-8")
    for needle in ("get_cost", "jobs set-cost", "approve", "jobs next", "jobs done", "jobs fail", "audit"):
        assert needle in text


# --- extra guards beyond the brief's list ---

def test_both_references_are_linked_from_the_skill():
    text = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    assert set(re.findall(r"references/[a-z\-]+\.md", text)) == {"references/workflow.md",
                                                                 "references/generation-jobs.md"}


def test_generated_swap_assets_are_covered():
    skill = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    jobs = (SKILL / "references" / "generation-jobs.md").read_text(encoding="utf-8")
    for needle in ("assets add", "assets next", "assets use", "not_applicable", "likeness_consent"):
        assert needle in skill
    for needle in ("assets set-cost", "assets next", "assets done", "assets fail"):
        assert needle in jobs


def _documented_commands() -> list[tuple[str, list[str]]]:
    """Every `& $cr ...` command line of the skill documents, as (file name, arguments)."""
    found = []
    for path in sorted(SKILL.rglob("*.md")):
        for line in re.findall(r"& \$cr ([^\n`]+)", path.read_text(encoding="utf-8")):
            found.append((path.name, shlex.split(line)))
    return found


def test_every_documented_command_is_accepted_by_the_cli():
    parser = cli._build_parser()
    shown: set[str] = set()
    for name, args in _documented_commands():
        try:
            func = getattr(parser.parse_args(args), "func", None)
        except SystemExit as exc:
            if exc.code == 0:                           # --version or --help
                continue
            func = None
        if func is None:
            pytest.fail(f"{name} documents a command the tool refuses: cine-recast {' '.join(args)}")
        shown.add(func.__name__)
    assert shown == {name for name in vars(cli) if name.startswith("_cmd_")}    # and none is left out
