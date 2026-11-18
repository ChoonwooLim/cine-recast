from cine_recast import cli
from cine_recast.cli import main


def test_version(capsys):
    assert main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == "cine-recast 0.1.0"


def test_error_exit_code(capsys):
    assert main(["analyze", "Z:/does/not/exist"]) == 2
    assert capsys.readouterr().err.startswith("error: ")


def _break_cuts_review(monkeypatch):
    def boom(args):
        raise KeyError("boom")

    monkeypatch.setattr(cli, "_cmd_cuts_review", boom)


def test_an_unexpected_failure_prints_one_line_and_exits_with_1(monkeypatch, capsys):
    monkeypatch.delenv("CINE_RECAST_DEBUG", raising=False)
    _break_cuts_review(monkeypatch)
    assert main(["cuts", "review", "anywhere"]) == 1
    out = capsys.readouterr()
    assert out.err == "error: unexpected failure (KeyError: 'boom'); this is a defect in cine-recast\n"
    assert out.out == ""


def test_debug_variable_adds_the_traceback(monkeypatch, capsys):
    monkeypatch.setenv("CINE_RECAST_DEBUG", "1")
    _break_cuts_review(monkeypatch)
    assert main(["cuts", "review", "anywhere"]) == 1
    err = capsys.readouterr().err
    assert err.startswith("error: unexpected failure (KeyError: 'boom')") and "Traceback" in err
