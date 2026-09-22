"""_tui_need_npm_install: auto npm when lockfile ahead of node_modules."""

import os
from pathlib import Path

import pytest


@pytest.fixture
def main_mod():
    import wafi_cli.main as m

    return m


def _touch_ink(root: Path) -> None:
    # The TUI's ink dependency ships as the @hermes scope
    # (ui-tui/package.json: "@hermes/ink": "file:./packages/hermes-ink"),
    # matching _tui_need_npm_install's node_modules/@hermes/ink probe.
    ink = root / "node_modules" / "@hermes" / "ink" / "package.json"
    ink.parent.mkdir(parents=True, exist_ok=True)
    ink.write_text("{}")


def test_need_install_when_ink_missing(tmp_path: Path, main_mod) -> None:
    (tmp_path / "package-lock.json").write_text("{}")
    assert main_mod._tui_need_npm_install(tmp_path) is True


def test_need_install_when_lock_content_differs(tmp_path: Path, main_mod) -> None:
    """A lockfile package that is missing or changed vs the installed marker
    triggers an install. The comparison is content-driven, not mtime-driven —
    mtimes only matter when the lockfile JSON cannot be parsed (see
    test_mtime_fallback_when_lock_json_corrupt)."""
    import json

    _touch_ink(tmp_path)
    (tmp_path / "package-lock.json").write_text(
        json.dumps({"packages": {"node_modules/left-pad": {"version": "1.0.0"}}})
    )
    (tmp_path / "node_modules" / ".package-lock.json").write_text(
        json.dumps({"packages": {}})
    )
    assert main_mod._tui_need_npm_install(tmp_path) is True


def test_mtime_fallback_when_lock_json_corrupt(tmp_path: Path, main_mod) -> None:
    """Unparseable lockfile JSON falls back to the mtime comparison."""
    _touch_ink(tmp_path)
    (tmp_path / "package-lock.json").write_text("{not json")
    (tmp_path / "node_modules" / ".package-lock.json").write_text("{not json")
    os.utime(tmp_path / "package-lock.json", (200, 200))
    os.utime(tmp_path / "node_modules" / ".package-lock.json", (100, 100))
    assert main_mod._tui_need_npm_install(tmp_path) is True


def test_no_install_when_lock_older_than_marker(tmp_path: Path, main_mod) -> None:
    _touch_ink(tmp_path)
    (tmp_path / "package-lock.json").write_text("{}")
    (tmp_path / "node_modules" / ".package-lock.json").write_text("{}")
    os.utime(tmp_path / "package-lock.json", (100, 100))
    os.utime(tmp_path / "node_modules" / ".package-lock.json", (200, 200))
    assert main_mod._tui_need_npm_install(tmp_path) is False


def test_need_install_when_marker_missing(tmp_path: Path, main_mod) -> None:
    _touch_ink(tmp_path)
    (tmp_path / "package-lock.json").write_text("{}")
    assert main_mod._tui_need_npm_install(tmp_path) is True


def test_no_install_without_lockfile_when_ink_present(tmp_path: Path, main_mod) -> None:
    _touch_ink(tmp_path)
    assert main_mod._tui_need_npm_install(tmp_path) is False
