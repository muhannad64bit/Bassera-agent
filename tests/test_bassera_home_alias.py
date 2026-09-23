"""Tests for the Bassera home resolution and legacy env compatibility.

Contract (2026-09 rebrand):
- ``BASSERA_HOME`` is the canonical home env var.
- Legacy ``BASSERA_HOME`` / ``WAFI_HOME`` values are mapped onto
  ``BASSERA_HOME`` at import of bassera_constants (zero-downtime compat
  for existing user infrastructure).
- The default home root is ``~/.bassera`` for fresh installs, but a
  legacy ``~/.wafi`` install keeps using its existing directory.
"""

import os
from pathlib import Path

import pytest

import bassera_constants
from bassera_constants import (
    apply_legacy_env_aliases,
    default_home_root,
    get_bassera_home,
)


class TestLegacyEnvAliases:
    def test_bassera_home_mapped(self, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", "/legacy/bassera/home")
        monkeypatch.delenv("BASSERA_HOME", raising=False)
        apply_legacy_env_aliases()
        assert os.environ["BASSERA_HOME"] == "/legacy/bassera/home"
        assert get_bassera_home() == Path("/legacy/bassera/home")

    def test_wafi_home_mapped(self, monkeypatch):
        monkeypatch.setenv("WAFI_HOME", "/legacy/wafi/home")
        monkeypatch.delenv("BASSERA_HOME", raising=False)
        apply_legacy_env_aliases()
        assert os.environ["BASSERA_HOME"] == "/legacy/wafi/home"

    def test_canonical_spelling_wins(self, monkeypatch):
        monkeypatch.setenv("HERMES_HOME", "/legacy/home")
        monkeypatch.setenv("BASSERA_HOME", "/canonical/home")
        apply_legacy_env_aliases()
        assert os.environ["BASSERA_HOME"] == "/canonical/home"

    def test_generic_legacy_var_mapped(self, monkeypatch):
        monkeypatch.setenv("HERMES_MAX_ITERATIONS", "17")
        monkeypatch.delenv("BASSERA_MAX_ITERATIONS", raising=False)
        apply_legacy_env_aliases()
        assert os.environ["BASSERA_MAX_ITERATIONS"] == "17"

    def test_idempotent(self, monkeypatch):
        monkeypatch.setenv("HERMES_QUIET", "1")
        apply_legacy_env_aliases()
        first = os.environ["BASSERA_QUIET"]
        apply_legacy_env_aliases()
        assert os.environ["BASSERA_QUIET"] == first


class TestDefaultHomeRoot:
    def test_fresh_install_gets_bassera(self, tmp_path, monkeypatch):
        monkeypatch.delenv("BASSERA_HOME", raising=False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        # neither ~/.bassera nor ~/.wafi exists
        assert default_home_root() == tmp_path / ".bassera"
        assert get_bassera_home() == tmp_path / ".bassera"

    def test_legacy_wafi_install_keeps_working(self, tmp_path, monkeypatch):
        monkeypatch.delenv("BASSERA_HOME", raising=False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        (tmp_path / ".wafi").mkdir()
        # legacy install exists and no bassera dir: keep the legacy dir
        assert get_bassera_home() == tmp_path / ".wafi"

    def test_bassera_dir_takes_over_when_both_exist(self, tmp_path, monkeypatch):
        monkeypatch.delenv("BASSERA_HOME", raising=False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        (tmp_path / ".wafi").mkdir()
        (tmp_path / ".bassera").mkdir()
        assert get_bassera_home() == tmp_path / ".bassera"

    def test_env_var_beats_disk_state(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BASSERA_HOME", str(tmp_path / "explicit"))
        assert get_bassera_home() == tmp_path / "explicit"

    def test_empty_env_value_ignored(self, tmp_path, monkeypatch):
        monkeypatch.delenv("BASSERA_HOME", raising=False)
        monkeypatch.setenv("BASSERA_HOME", "   ")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert get_bassera_home() == tmp_path / ".bassera"


def test_module_import_applies_aliases():
    """The alias mapping runs at import time, before any consumer reads."""
    assert hasattr(bassera_constants, "apply_legacy_env_aliases")
    # importing the module must not raise even with legacy vars present
    monkey_env = {"BASSERA_HOME": "/tmp/x", "PATH": os.environ.get("PATH", "")}
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-c",
         "import bassera_constants, os; "
         "assert os.environ.get('BASSERA_HOME') == '/tmp/x'"],
        capture_output=True,
        text=True,
        env=monkey_env,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("fn_name", ["get_bassera_home", "default_home_root"])
def test_public_api_names(fn_name):
    assert callable(getattr(bassera_constants, fn_name))
