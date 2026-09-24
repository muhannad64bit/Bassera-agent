"""Tests for the central download-path choke point.

tools.path_security.secure_download_destination is the single enforcement
point every surface that writes REMOTE bytes to disk must pass through —
closing the threat-model gap "no central choke point for path validation
of downloads" (previously each surface had to remember to validate).
"""

import os
import tempfile
from pathlib import Path

import pytest

from tools.path_security import (
    PathEscapeError,
    has_traversal_component,
    secure_download_destination,
    validate_within_dir,
)


class TestSecureDownloadDestination:
    def test_valid_destination_passes(self, tmp_path):
        dest = tmp_path / "sub" / "image.jpg"
        result = secure_download_destination(dest, tmp_path)
        assert result == dest.resolve()

    def test_traversal_rejected(self, tmp_path):
        dest = tmp_path / "downloads" / ".." / ".." / "escape.png"
        with pytest.raises(PathEscapeError):
            secure_download_destination(dest, tmp_path / "downloads")

    def test_absolute_escape_rejected(self, tmp_path):
        with pytest.raises(PathEscapeError):
            secure_download_destination(Path("/etc/passwd"), tmp_path)

    def test_symlink_escape_rejected(self, tmp_path):
        """A symlinked parent must not smuggle writes outside the root."""
        outside = tmp_path / "outside"
        outside.mkdir()
        root = tmp_path / "downloads"
        root.mkdir()
        (root / "link").symlink_to(outside)
        try:
            with pytest.raises(PathEscapeError):
                secure_download_destination(root / "link" / "evil.bin", root)
        except OSError:
            pytest.skip("Symlinks not supported on this platform")

    def test_label_in_error_message(self, tmp_path):
        with pytest.raises(PathEscapeError, match="vision image"):
            secure_download_destination(
                Path("/etc/passwd"), tmp_path, label="vision image"
            )

    def test_dest_directly_in_root_passes(self, tmp_path):
        result = secure_download_destination(tmp_path / "file.bin", tmp_path)
        assert result == (tmp_path / "file.bin").resolve()


class TestLegacyHelpers:
    def test_validate_within_dir_safe(self, tmp_path):
        assert validate_within_dir(tmp_path / "a.txt", tmp_path) is None

    def test_validate_within_dir_escape(self, tmp_path):
        assert validate_within_dir(tmp_path / ".." / "x", tmp_path) is not None

    def test_has_traversal_component(self):
        assert has_traversal_component("a/../b")
        assert not has_traversal_component("a/b/c")


class TestAdoption:
    def test_vision_download_validates_destination(self, tmp_path, monkeypatch):
        """The vision tool's download must refuse destinations outside the
        OS temp dir — the 'never the process CWD' invariant, enforced."""
        import asyncio

        from tools.vision_tools import _download_image

        # A CWD-relative destination (the historical pollution bug) resolves
        # outside the OS temp dir and must be rejected BEFORE any network
        # activity.
        outside = Path("cwd_pollution.png").resolve()
        assert not str(outside).startswith(str(Path(tempfile.gettempdir()).resolve()))
        with pytest.raises(PathEscapeError):
            asyncio.run(_download_image("https://example.invalid/x.png", outside))

    def test_tirith_release_paths_validated(self, tmp_path, monkeypatch):
        """The tirith installer validates all release artifact paths
        against its install tmpdir before downloading."""
        source = Path("tools/tirith_security.py").read_text(encoding="utf-8")
        assert "secure_download_destination" in source, (
            "tirith_security.py stopped using the download choke point"
        )
        source_vision = Path("tools/vision_tools.py").read_text(encoding="utf-8")
        assert "secure_download_destination" in source_vision, (
            "vision_tools.py stopped using the download choke point"
        )
