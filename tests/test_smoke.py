"""M0 placeholder: the package imports and the CLI answers --help."""

import pytest

import earwitness
from earwitness.cli import main


def test_version_is_set():
    assert earwitness.__version__ not in ("", None)


def test_help_exits_zero(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    assert "simulate" in capsys.readouterr().out


def test_no_command_prints_help(capsys):
    assert main([]) == 0
    assert "usage: earwitness" in capsys.readouterr().out
