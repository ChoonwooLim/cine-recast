from cine_recast.assemble import assemble
from cine_recast.report import write_report
from cine_recast.textrules import han_chars


def test_report_content(finished3):
    assemble(finished3.root)
    text = write_report(finished3.root).read_text(encoding="utf-8")
    assert text.startswith("# CineRecast \ubcf4\uace0\uc11c")
    for heading in ("## \uc694\uc57d", "## \uad8c\ub9ac \ud655\uc778", "## \uad50\uccb4\ud45c", "## \uc20f\ubcc4 \uacb0\uacfc", "## \ube44\uc6a9", "## \uc5f4\ub9b0 \ud56d\ubaa9"):
        assert heading in text
    assert "| 1 | 0-30 | u01 |" in text and "t001" in text and han_chars(text) == []
