"""Security-focused live coverage for tools/file_operations.py.

Targets previously uncovered, security-relevant branches:

1. The grep fallback search (``_search_with_grep``): its shell command
   construction escapes pattern / path / file_glob through
   _escape_shell_arg. A regression that dropped any of those escapes
   would turn a search into shell injection. These tests search for
   content whose names and globs contain shell metacharacters and
   assert no side-effect file is ever created.

2. ``read_file`` binary-safety branches: image files must never be
   inlined (redirect hint to vision_analyze), binary content must be
   refused as text, unreadable files must fail gracefully.

These are live tests (real LocalEnvironment, hermetic tmpdirs) in the
same style as test_file_tools_live.py.
"""

import pytest

from tools.environments.local import LocalEnvironment
from tools.file_operations import ShellFileOperations


@pytest.fixture
def env(tmp_path):
    return LocalEnvironment(cwd=str(tmp_path), timeout=15)


@pytest.fixture
def ops(env, tmp_path):
    return ShellFileOperations(env, cwd=str(tmp_path))


@pytest.fixture
def grep_ops(ops):
    """Force the grep fallback deterministically, even where ripgrep
    is installed (CI installs rg), by poisoning the command cache."""
    ops._command_cache["rg"] = False
    return ops


def _write(tmp_path, name, content):
    target = tmp_path / name
    target.write_text(content, encoding="utf-8")
    return target


# --------------------------------------------------------------------------
# grep fallback: escaping of every user-controlled command component
# --------------------------------------------------------------------------

class TestGrepFallbackEscaping:
    def test_metachar_filenames_are_found_not_executed(self, grep_ops, tmp_path):
        """A file named with command-substitution syntax must be searchable
        and the payload must NOT run."""
        _write(tmp_path, "evil$(touch pwned).py", "needle here\n")
        result = grep_ops.search("needle", path=str(tmp_path))
        assert result.error is None
        assert result.total_count == 1
        assert "needle" in result.matches[0].content
        # The $(...) payload must never have executed.
        assert not (tmp_path / "pwned").exists()

    def test_hostile_file_glob_never_executes(self, grep_ops, tmp_path):
        """A file_glob containing shell-injection syntax must arrive at grep
        as a literal --include value, not be interpreted by the shell."""
        _write(tmp_path, "safe.py", "needle\n")
        marker = tmp_path / "pwned_by_glob"
        hostile_glob = f"*.py || touch {marker}"
        result = grep_ops.search("needle", path=str(tmp_path), file_glob=hostile_glob)
        # Escaped properly: no matches (the glob matches nothing) and,
        # critically, no side effect.
        assert result.total_count == 0
        assert not marker.exists()

    def test_hostile_path_never_executes(self, grep_ops, tmp_path):
        """A search path containing injection syntax must not execute."""
        _write(tmp_path, "real.py", "needle\n")
        marker = tmp_path / "pwned_by_path"
        result = grep_ops.search(
            "needle", path=f"{tmp_path} || touch {marker}"
        )
        assert not marker.exists()

    def test_files_only_and_count_output_modes(self, grep_ops, tmp_path):
        _write(tmp_path, "a.py", "needle\nneedle\n")
        _write(tmp_path, "b.py", "needle\n")
        _write(tmp_path, "c.txt", "needle\n")

        files = grep_ops.search(
            "needle", path=str(tmp_path), file_glob="*.py", output_mode="files_only"
        )
        assert files.error is None
        assert files.total_count == 2
        found = set(files.files)
        assert any(f.endswith("a.py") for f in found)
        assert all(not f.endswith(".txt") for f in found)

        counts = grep_ops.search(
            "needle", path=str(tmp_path), file_glob="*.py", output_mode="count"
        )
        assert counts.error is None
        per_file = dict(counts.counts)
        assert sum(per_file.values()) == 3  # a.py has 2, b.py has 1
        assert any(k.endswith("a.py") and v == 2 for k, v in per_file.items())

    def test_grep_fallback_honors_context(self, grep_ops, tmp_path):
        _write(tmp_path, "ctx.py", "before\nneedle\nafter\n")
        result = grep_ops.search("needle", path=str(tmp_path), context=1)
        assert result.error is None
        assert result.total_count >= 1


# --------------------------------------------------------------------------
# read_file: binary / image safety branches
# --------------------------------------------------------------------------

class TestReadFileBinarySafety:
    def test_image_is_never_inlined(self, ops, tmp_path):
        png = tmp_path / "photo.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
        result = ops.read_file(str(png))
        assert result.is_image is True
        assert result.is_binary is True
        assert "vision_analyze" in (result.hint or "")

    def test_binary_content_by_analysis_is_refused(self, ops, tmp_path):
        """No known binary extension: classification must come from the
        1000-byte head sample, and the file must be refused as text."""
        blob = tmp_path / "payload.xyz"
        blob.write_bytes(b"\x00" * 2048 + b"text" * 8)
        result = ops.read_file(str(blob))
        assert result.is_binary is True
        assert result.error and "Binary file" in result.error

    def test_missing_file_returns_graceful_error(self, ops, tmp_path):
        result = ops.read_file(str(tmp_path / "no_such_file.txt"))
        assert result.error is not None
        assert "no_such_file" in result.error or "similar" in result.error.lower()

    def test_read_command_failure_is_reported_not_raised(self, ops, tmp_path):
        """When the paginating read command fails, the caller gets an error
        result — never an exception."""
        from types import SimpleNamespace

        target = _write(tmp_path, "ok.txt", "hello\n")

        real_exec = ops._exec
        calls = {"n": 0}

        def flaky_exec(command, cwd=None, timeout=None, stdin_data=None):
            calls["n"] += 1
            # First call: the wc -c stat (must succeed).
            if command.startswith("wc -c"):
                return real_exec(command, cwd=cwd)
            # Head sample: benign text.
            if command.startswith("head -c"):
                return real_exec(command, cwd=cwd)
            # Everything after (sed pagination, wc -l) fails like a dying fs.
            return SimpleNamespace(stdout="read error: io failure", exit_code=1)

        ops._exec = flaky_exec
        result = ops.read_file(str(target))
        assert result.error is not None
        assert "Failed to read file" in result.error
