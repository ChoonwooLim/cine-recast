from cine_recast.cli import main


def test_version(capsys):
    assert main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == "cine-recast 0.1.0"


def test_error_exit_code(capsys):
    assert main(["analyze", "Z:/does/not/exist"]) == 2
    assert capsys.readouterr().err.startswith("error: ")
