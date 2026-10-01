"""Tests for edge cases in tools/file_operations.py.

Covers:
- ``_is_likely_binary()`` content-analysis branch (dead-code removal regression guard)
- ``_check_lint()`` robustness against file paths containing curly braces
"""

import pytest
from unittest.mock import MagicMock, patch

from tools.file_operations import ShellFileOperations


# =========================================================================
# _is_likely_binary edge cases
# =========================================================================


class TestIsLikelyBinary:
    """Verify content-analysis logic after dead-code removal."""

    @pytest.fixture()
    def ops(self):
        return ShellFileOperations.__new__(ShellFileOperations)

    def test_binary_extension_returns_true(self, ops):
        """Known binary extensions should short-circuit without content analysis."""
        assert ops._is_likely_binary("image.png") is True
        assert ops._is_likely_binary("archive.tar.gz", content_sample="hello") is True

    def test_text_content_returns_false(self, ops):
        """Normal printable text should not be classified as binary."""
        sample = "Hello, world!\nThis is a normal text file.\n"
        assert ops._is_likely_binary("unknown.xyz", content_sample=sample) is False

    def test_binary_content_returns_true(self, ops):
        """Content with >30% non-printable characters should be classified as binary."""
        # 500 NUL bytes + 500 printable = 50% non-printable → binary
        # Use .xyz extension (not in BINARY_EXTENSIONS) to ensure content analysis runs
        sample = "\x00" * 500 + "a" * 500
        assert ops._is_likely_binary("data.xyz", content_sample=sample) is True

    def test_no_content_sample_returns_false(self, ops):
        """When no content sample is provided and extension is unknown → not binary."""
        assert ops._is_likely_binary("mystery_file") is False

    def test_none_content_sample_returns_false(self, ops):
        """Explicit ``None`` content_sample should behave the same as missing."""
        assert ops._is_likely_binary("mystery_file", content_sample=None) is False

    def test_empty_string_content_sample_returns_false(self, ops):
        """Empty string is falsy, so content analysis should be skipped → not binary."""
        assert ops._is_likely_binary("mystery_file", content_sample="") is False

    def test_threshold_boundary(self, ops):
        """Exactly 30% non-printable should NOT trigger binary classification (> 0.30, not >=)."""
        # 300 NUL bytes + 700 printable = 30.0% → should be False (uses strict >)
        sample = "\x00" * 300 + "a" * 700
        assert ops._is_likely_binary("data.xyz", content_sample=sample) is False

    def test_just_above_threshold(self, ops):
        """301/1000 = 30.1% non-printable → should be binary."""
        sample = "\x00" * 301 + "a" * 699
        assert ops._is_likely_binary("data.xyz", content_sample=sample) is True

    def test_tabs_and_newlines_excluded(self, ops):
        """Tabs, carriage returns, and newlines should not count as non-printable."""
        sample = "\t" * 400 + "\n" * 300 + "\r" * 200 + "a" * 100
        assert ops._is_likely_binary("file.txt", content_sample=sample) is False

    def test_content_sample_longer_than_1000(self, ops):
        """Only the first 1000 characters should be analysed."""
        # First 1000 chars: 200 NUL + 800 printable = 20% → not binary
        # Remaining 1000 chars: all NUL → ignored by [:1000] slice
        sample = "\x00" * 200 + "a" * 800 + "\x00" * 1000
        assert ops._is_likely_binary("file.xyz", content_sample=sample) is False


# =========================================================================
# _check_lint edge cases
# =========================================================================


class TestCheckLintBracePaths:
    """Verify _check_lint handles file paths with curly braces safely."""

    @pytest.fixture()
    def ops(self):
        obj = ShellFileOperations.__new__(ShellFileOperations)
        obj._command_cache = {}
        return obj

    def test_normal_path(self, ops):
        """Normal path without braces should work as before."""
        with patch.object(ops, "_has_command", return_value=True), \
             patch.object(ops, "_exec") as mock_exec:
            mock_exec.return_value = MagicMock(exit_code=0, stdout="")
            result = ops._check_lint("/tmp/test_file.py")

        assert result.success is True
        # Verify the command was built correctly
        cmd_arg = mock_exec.call_args[0][0]
        assert "'/tmp/test_file.py'" in cmd_arg

    def test_path_with_curly_braces(self, ops):
        """Path containing ``{`` and ``}`` must not raise KeyError/ValueError."""
        with patch.object(ops, "_has_command", return_value=True), \
             patch.object(ops, "_exec") as mock_exec:
            mock_exec.return_value = MagicMock(exit_code=0, stdout="")
            # This would raise KeyError with .format() but works with .replace()
            result = ops._check_lint("/tmp/{test}_file.py")

        assert result.success is True
        cmd_arg = mock_exec.call_args[0][0]
        assert "{test}" in cmd_arg

    def test_path_with_nested_braces(self, ops):
        """Path with complex brace patterns like ``{{var}}`` should be safe."""
        with patch.object(ops, "_has_command", return_value=True), \
             patch.object(ops, "_exec") as mock_exec:
            mock_exec.return_value = MagicMock(exit_code=0, stdout="")
            result = ops._check_lint("/tmp/{{var}}.py")

        assert result.success is True

    def test_unsupported_extension_skipped(self, ops):
        """Extensions without a linter should return a skipped result."""
        result = ops._check_lint("/tmp/file.unknown_ext")
        assert result.skipped is True

    def test_missing_linter_skipped(self, ops):
        """When the linter binary is not installed, skip gracefully."""
        with patch.object(ops, "_has_command", return_value=False):
            result = ops._check_lint("/tmp/test.py")
        assert result.skipped is True

    def test_lint_failure_returns_output(self, ops):
        """When the linter exits non-zero, result should capture output."""
        with patch.object(ops, "_has_command", return_value=True), \
             patch.object(ops, "_exec") as mock_exec:
            mock_exec.return_value = MagicMock(
                exit_code=1,
                stdout="SyntaxError: invalid syntax",
            )
            result = ops._check_lint("/tmp/bad.py")

        assert result.success is False
        assert "SyntaxError" in result.output


class TestExpandPathSecurity:
    """_expand_path: the home/`~user` expansion is an injection surface.

    Paths reach it from the LLM; `~evil; rm -rf /` must never be handed to
    a shell, and only validated usernames may be expanded.
    """

    def _ops(self):
        from tools.environments.local import LocalEnvironment
        from tools.file_operations import ShellFileOperations

        env = LocalEnvironment(cwd="/tmp", timeout=10)
        return ShellFileOperations(terminal_env=env, cwd="/tmp")

    def test_plain_tilde_uses_home(self, monkeypatch):
        import os

        monkeypatch.setenv("HOME", "/home/testuser")
        ops = self._ops()
        assert ops._expand_path("~") == "/home/testuser"
        assert ops._expand_path("~/docs/x.txt") == "/home/testuser/docs/x.txt"

    def test_malicious_username_not_expanded(self):
        """`~; rm -rf /` and friends must fall through UNEXPANDED (the shell
        never sees them through the username channel)."""
        ops = self._ops()
        for evil in ("~; rm -rf /", "~$(reboot)", "~`id`"):
            expanded = ops._expand_path(evil)
            assert "~" in expanded, f"{evil!r} must not be shell-expanded"

    def test_invalid_username_chars_rejected(self):
        ops = self._ops()
        # Contains a space -> not a valid username -> left as-is.
        assert "~not a user/x" == ops._expand_path("~not a user/x")

    def test_relative_and_absolute_untouched(self):
        ops = self._ops()
        assert ops._expand_path("/etc/passwd") == "/etc/passwd"
        assert ops._expand_path("relative/file.txt") == "relative/file.txt"


class TestReadFileRaw:
    """read_file_raw: no pagination, full content via cat."""

    def _ops(self):
        from tools.environments.local import LocalEnvironment
        from tools.file_operations import ShellFileOperations

        env = LocalEnvironment(cwd="/tmp", timeout=10)
        return ShellFileOperations(terminal_env=env, cwd="/tmp")

    def test_reads_full_text(self, tmp_path):
        target = tmp_path / "raw.txt"
        target.write_text("line1\nline2\nline3\n", encoding="utf-8")
        ops = self._ops()
        result = ops.read_file_raw(str(target))
        assert result.error is None
        assert result.content == "line1\nline2\nline3\n"
        assert result.file_size == len("line1\nline2\nline3\n")

    def test_binary_detected(self, tmp_path):
        target = tmp_path / "blob.bin"
        target.write_bytes(b"\x00\x01\x02\x03binary")
        ops = self._ops()
        result = ops.read_file_raw(str(target))
        assert result.is_binary is True
        assert result.content in ("", None)
        assert result.error

    def test_missing_file_suggests(self, tmp_path):
        ops = self._ops()
        result = ops.read_file_raw(str(tmp_path / "nope-missing.txt"))
        assert result.content in ("", None)
        # missing file must produce guidance, never a crash
        assert result.error or result.similar_files


class TestSearchFallback:
    """Content + filename search, including the no-ripgrep fallback."""

    def _ops(self):
        from tools.environments.local import LocalEnvironment
        from tools.file_operations import ShellFileOperations

        env = LocalEnvironment(cwd="/tmp", timeout=10)
        return ShellFileOperations(terminal_env=env, cwd="/tmp")

    def test_content_search_finds_matches(self, tmp_path):
        (tmp_path / "a.txt").write_text("needle found here\n", encoding="utf-8")
        (tmp_path / "b.txt").write_text("nothing relevant\n", encoding="utf-8")
        ops = self._ops()
        result = ops.search("needle", path=str(tmp_path), file_glob="*.txt")
        assert result.error is None
        found = [m.path for m in result.matches]
        assert any(p.endswith("a.txt") for p in found)
        assert not any(p.endswith("b.txt") for p in found)

    def test_filename_search_find_fallback_without_rg(self, tmp_path, monkeypatch):
        """target='files' with no ripgrep must still find files by name."""
        (tmp_path / "target_notes.txt").write_text("x", encoding="utf-8")
        from tools.file_operations import ShellFileOperations

        real_has_command = ShellFileOperations._has_command
        monkeypatch.setattr(
            ShellFileOperations,
            "_has_command",
            lambda self, name: False if name == "rg" else real_has_command(self, name),
        )
        ops = self._ops()
        result = ops.search("target_notes.txt", path=str(tmp_path), target="files")
        found = list(result.files) + [m.path for m in result.matches]
        assert any(p.endswith("target_notes.txt") for p in found), (
            "the find fallback must still locate files by name"
        )

    def test_filename_search_with_rg(self, tmp_path):
        (tmp_path / "target_notes.txt").write_text("x", encoding="utf-8")
        ops = self._ops()
        result = ops.search("target_notes.txt", path=str(tmp_path), target="files")
        found = list(result.files) + [m.path for m in result.matches]
        assert any(p.endswith("target_notes.txt") for p in found)

    def test_search_missing_path_gives_hint(self, tmp_path):
        ops = self._ops()
        result = ops.search("x", path=str(tmp_path / "does-not-exist"))
        # Missing path must produce a helpful error, never a crash.
        assert result.error is not None
        assert "not found" in result.error.lower() or "Path not found" in result.error
