"""Tests for the BASSERA_HOME alias added to get_wafi_home().

BASSERA_HOME is a Bassera-brand alias: it redirects the home directory
only when HERMES_HOME (the canonical var) is unset. This keeps the
existing test suite and internal profile logic (which set HERMES_HOME)
unaffected.
"""

import os
from pathlib import Path

from wafi_constants import get_wafi_home


class TestBasseraHomeAlias:
    def test_hermes_home_wins_over_bassera(self, tmp_path, monkeypatch):
        """HERMES_HOME is canonical and takes precedence over BASSERA_HOME."""
        hermes = tmp_path / "hermes-home"
        bassera = tmp_path / "bassera-home"
        hermes.mkdir()
        bassera.mkdir()
        monkeypatch.setenv("HERMES_HOME", str(hermes))
        monkeypatch.setenv("BASSERA_HOME", str(bassera))
        assert get_wafi_home() == hermes

    def test_bassera_home_used_when_hermes_unset(self, tmp_path, monkeypatch):
        """BASSERA_HOME redirects home when HERMES_HOME is absent."""
        bassera = tmp_path / "bassera-home"
        bassera.mkdir()
        monkeypatch.delenv("HERMES_HOME", raising=False)
        monkeypatch.setenv("BASSERA_HOME", str(bassera))
        assert get_wafi_home() == bassera

    def test_default_wafi_when_both_unset(self, tmp_path, monkeypatch):
        """With neither var set, falls back to ~/.wafi."""
        monkeypatch.delenv("HERMES_HOME", raising=False)
        monkeypatch.delenv("BASSERA_HOME", raising=False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert get_wafi_home() == tmp_path / ".wafi"

    def test_empty_bassera_home_ignored(self, tmp_path, monkeypatch):
        """An empty BASSERA_HOME value is ignored, falling through to default."""
        monkeypatch.delenv("HERMES_HOME", raising=False)
        monkeypatch.setenv("BASSERA_HOME", "   ")
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert get_wafi_home() == tmp_path / ".wafi"
