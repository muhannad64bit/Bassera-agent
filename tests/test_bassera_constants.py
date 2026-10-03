"""Tests for bassera_constants module."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest

import bassera_constants
from bassera_constants import default_home_root, get_default_bassera_root, is_container


class TestGetDefaultBasseraRoot:
    """Tests for get_default_bassera_root() — Docker/custom deployment awareness."""

    def test_no_bassera_home_returns_native(self, tmp_path, monkeypatch):
        """When BASSERA_HOME is not set, returns ~/.bassera."""
        monkeypatch.delenv("BASSERA_HOME", raising=False)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        assert get_default_bassera_root() == tmp_path / ".bassera"

    def test_bassera_home_is_native(self, tmp_path, monkeypatch):
        """When BASSERA_HOME = ~/.bassera, returns ~/.bassera."""
        native = tmp_path / ".bassera"
        native.mkdir()
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setenv("BASSERA_HOME", str(native))
        assert get_default_bassera_root() == native

    def test_bassera_home_is_profile(self, tmp_path, monkeypatch):
        """When BASSERA_HOME is a profile under ~/.bassera, returns ~/.bassera."""
        native = tmp_path / ".bassera"
        profile = native / "profiles" / "coder"
        profile.mkdir(parents=True)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setenv("BASSERA_HOME", str(profile))
        assert get_default_bassera_root() == native

    def test_bassera_home_is_docker(self, tmp_path, monkeypatch):
        """When BASSERA_HOME points outside ~/.bassera (Docker), returns BASSERA_HOME."""
        docker_home = tmp_path / "opt" / "data"
        docker_home.mkdir(parents=True)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setenv("BASSERA_HOME", str(docker_home))
        assert get_default_bassera_root() == docker_home

    def test_bassera_home_is_custom_path(self, tmp_path, monkeypatch):
        """Any BASSERA_HOME outside ~/.bassera is treated as the root."""
        custom = tmp_path / "my-bassera-data"
        custom.mkdir()
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setenv("BASSERA_HOME", str(custom))
        assert get_default_bassera_root() == custom

    def test_docker_profile_active(self, tmp_path, monkeypatch):
        """When a Docker profile is active (BASSERA_HOME=<root>/profiles/<name>),
        returns the Docker root, not the profile dir."""
        docker_root = tmp_path / "opt" / "data"
        profile = docker_root / "profiles" / "coder"
        profile.mkdir(parents=True)
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setenv("BASSERA_HOME", str(profile))
        assert get_default_bassera_root() == docker_root


class TestDefaultHomeRoot:
    def test_permission_error_on_bassera_home_falls_through(self, monkeypatch):
        """Permission errors on ~/.bassera should not crash home resolution."""
        monkeypatch.setattr(Path, "home", lambda: Path("/root"))

        def fake_exists(path):
            if path == Path("/root/.bassera"):
                raise PermissionError("permission denied")
            return False

        monkeypatch.setattr(Path, "exists", fake_exists)
        assert default_home_root() == Path("/root/.bassera")

    def test_permission_error_on_bassera_home_allows_legacy_fallback(self, monkeypatch):
        """If ~/.bassera is inaccessible, ~/.wafi can still be selected."""
        monkeypatch.setattr(Path, "home", lambda: Path("/root"))

        def fake_exists(path):
            if path == Path("/root/.bassera"):
                raise PermissionError("permission denied")
            return path == Path("/root/.wafi")

        monkeypatch.setattr(Path, "exists", fake_exists)
        assert default_home_root() == Path("/root/.wafi")


class TestIsContainer:
    """Tests for is_container() — Docker/Podman detection."""

    def _reset_cache(self, monkeypatch):
        """Reset the cached detection result before each test."""
        monkeypatch.setattr(bassera_constants, "_container_detected", None)

    def test_detects_dockerenv(self, monkeypatch, tmp_path):
        """/.dockerenv triggers container detection."""
        self._reset_cache(monkeypatch)
        monkeypatch.setattr(os.path, "exists", lambda p: p == "/.dockerenv")
        assert is_container() is True

    def test_detects_containerenv(self, monkeypatch, tmp_path):
        """/run/.containerenv triggers container detection (Podman)."""
        self._reset_cache(monkeypatch)
        monkeypatch.setattr(os.path, "exists", lambda p: p == "/run/.containerenv")
        assert is_container() is True

    def test_detects_cgroup_docker(self, monkeypatch, tmp_path):
        """/proc/1/cgroup containing 'docker' triggers detection."""
        import builtins
        self._reset_cache(monkeypatch)
        monkeypatch.setattr(os.path, "exists", lambda p: False)
        cgroup_file = tmp_path / "cgroup"
        cgroup_file.write_text("12:memory:/docker/abc123\n")
        _real_open = builtins.open
        monkeypatch.setattr("builtins.open", lambda p, *a, **kw: _real_open(str(cgroup_file), *a, **kw) if p == "/proc/1/cgroup" else _real_open(p, *a, **kw))
        assert is_container() is True

    def test_negative_case(self, monkeypatch, tmp_path):
        """Returns False on a regular Linux host."""
        import builtins
        self._reset_cache(monkeypatch)
        monkeypatch.setattr(os.path, "exists", lambda p: False)
        cgroup_file = tmp_path / "cgroup"
        cgroup_file.write_text("12:memory:/\n")
        _real_open = builtins.open
        monkeypatch.setattr("builtins.open", lambda p, *a, **kw: _real_open(str(cgroup_file), *a, **kw) if p == "/proc/1/cgroup" else _real_open(p, *a, **kw))
        assert is_container() is False

    def test_caches_result(self, monkeypatch):
        """Second call uses cached value without re-probing."""
        monkeypatch.setattr(bassera_constants, "_container_detected", True)
        assert is_container() is True
        # Even if we make os.path.exists return False, cached value wins
        monkeypatch.setattr(os.path, "exists", lambda p: False)
        assert is_container() is True
