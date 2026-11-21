import os

import pytest

from humbledownloader.cli import main


def test_help_exits_cleanly(capsys):
    with pytest.raises(SystemExit) as ex:
        main(["--help"])
    assert ex.value.code == 0
    assert "humbledl" in capsys.readouterr().out


def test_version_matches_the_package_metadata(capsys):
    """__version__ and pyproject.toml are bumped by hand; catch one lagging."""
    from importlib.metadata import version

    with pytest.raises(SystemExit) as ex:
        main(["--version"])
    assert ex.value.code == 0
    assert capsys.readouterr().out.strip() == f"humbledl {version('humbledownloader')}"


def test_cookie_on_another_drive_and_exit_codes(tmp_path, monkeypatch):
    from humbledownloader import cli
    from humbledownloader.run import Summary

    lib = tmp_path / "lib"
    lib.mkdir()
    seen = {}

    def fake_run(library, site, exclude, dry_run, only):
        seen.update(library=library, exclude=exclude, dry_run=dry_run, only=only)
        s = Summary()
        s.counts["skipped"] = 1
        return s

    monkeypatch.setattr(cli, "run", fake_run)
    monkeypatch.setattr(cli, "load_session", lambda path: object())
    other_drive = r"Z:\elsewhere\cookies.txt" if os.name == "nt" else "/elsewhere/c.txt"
    cli.main(["list", "-l", str(lib), "-c", other_drive])
    assert seen == {
        "library": str(lib),
        "exclude": {"mac", "linux"},
        "dry_run": True,
        "only": None,
    }


def test_session_lost_exits_1(tmp_path, monkeypatch):
    from humbledownloader import cli
    from humbledownloader.site import SessionLost

    def lost(path):
        raise SessionLost("gone")

    monkeypatch.setattr(cli, "load_session", lost)
    with pytest.raises(SystemExit) as ex:
        cli.main(["download", "-l", str(tmp_path), "-c", "c.txt"])
    assert ex.value.code == 1


def test_cloudflare_block_exits_1_without_the_cookie_hint(
    tmp_path, monkeypatch, capsys
):
    from humbledownloader import cli
    from humbledownloader.site import Blocked

    def blocked(path):
        raise Blocked("POST /home/library: HTTP 403")

    monkeypatch.setattr(cli, "load_session", blocked)
    with pytest.raises(SystemExit) as ex:
        cli.main(["list", "-l", str(tmp_path), "-c", "c.txt"])
    assert ex.value.code == 1
    err = capsys.readouterr().err
    assert "Cloudflare blocked the request" in err
    assert "Export" not in err


def test_cookie_file_error_exits_1_with_one_hint(tmp_path, capsys):
    from humbledownloader import cli

    lib = tmp_path / "lib"
    lib.mkdir()
    with pytest.raises(SystemExit) as ex:
        cli.main(["list", "-l", str(lib), "-c", str(tmp_path / "typo.txt")])
    assert ex.value.code == 1
    err = capsys.readouterr().err
    assert "cannot be read" in err and "Session lost" not in err
    assert err.count("Export the humblebundle.com cookies again") == 1


def test_summary_shows_newer_and_current_counts(caplog):
    import logging

    from humbledownloader.cli import print_summary
    from humbledownloader.run import Summary

    s = Summary()
    s.counts.update({"downloaded": 1, "newer than catalog": 2, "already current": 1})
    with caplog.at_level(logging.INFO):
        print_summary(s, dry_run=False)
    assert "newer than catalog   2" in caplog.text
    assert "already current      1" in caplog.text
