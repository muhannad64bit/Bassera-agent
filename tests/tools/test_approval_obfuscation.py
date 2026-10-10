"""Adversarial obfuscation tests for the dangerous-command detector.

Proven bypasses (2026-10-02, security audit): nine spellings executed as
``rm -rf <target>`` in bash while detect_dangerous_command() saw nothing:

- intra-word quoting: ``r''m -rf /x``, ``r""m -rf /x``
- backslash char-escaping: ``r\\m -rf /x``
- $IFS word-separator substitution: ``rm${IFS}-rf${IFS}/x``, ``rm$IFS-rf$IFS/x``
- whole-word quoting: ``'rm' -rf /x``
- variable indirection: ``X=rm; $X -rf /x``, ``X=dd; $X if=/dev/zero ...``
- command substitution: ``$(echo rm) -rf /x``, backtick form
- pipe-into-xargs: ``echo rm | xargs ... {} -rf /x``

Root-cause fixes (tools/approval.py):
1. _normalize_command_for_detection now also neutralizes $IFS (to a
   space), backslash escapes, and quotes — the three shell mechanisms
   that hide tokens from literal matching without preventing execution.
2. Three targeted patterns added: rm-piped-into-xargs, dangerous binary
   assigned to a variable, and command substitution combined with -rf.

The benign corpus below MUST stay unflagged — this gate exists to catch
destructive commands, not to harass users with approval prompts for
quoted strings in ordinary commands.
"""

import pytest

from tools.approval import detect_dangerous_command


# Every entry here executes as a destructive command in bash.
DESTRUCTIVE_OBFUSCATIONS = [
    # previously CAUGHT (regression pins for existing defenses)
    "rm -rf /important",
    "rm -r\\f /important",
    "r\x1b[31mm -rf /important",       # ANSI escape inside the word
    "ｒｍ -ｒｆ /important",               # fullwidth (NFKC)
    "r\x00m -rf /important",           # null byte inside the word
    "eval 'rm -rf /important'",
    "env rm -rf /important",
    "nohup rm -rf /important",
    # previously BYPASSES (the vulnerabilities this suite pins)
    "r''m -rf /important",
    'r""m -rf /important',
    "r\\m -rf /important",
    "rm${IFS}-rf${IFS}/important",
    "rm$IFS-rf$IFS/important",
    "'rm' -rf /important",
    "X=rm; $X -rf /important",
    "X=dd; $X if=/dev/zero of=/dev/sda",
    "$(echo rm) -rf /important",
    "`echo rm` -rf /important",
    "echo rm | xargs -I{} {} -rf /important",
]


@pytest.mark.parametrize("command", DESTRUCTIVE_OBFUSCATIONS)
def test_destructive_obfuscation_is_detected(command):
    is_dangerous, _pattern_key, _description = detect_dangerous_command(command)
    assert is_dangerous, f"destructive obfuscation not detected: {command!r}"


# Ordinary commands with quotes/backslashes/assignments — MUST stay clean.
BENIGN_COMMANDS = [
    "echo 'hello world'",
    "grep 'pattern' file.txt",
    'printf "%s\\n" a b c',
    "COUNT=5; echo $COUNT",
    "NAME=bassera python -m pytest",
    "ls -la /tmp",
    "git commit -m 'fix: quoted message'",
    'echo "rm this file later" | tee notes.txt',
    "cp 'my file.txt' backup/",
    "FOO=rm_not_a_command echo hi",
    "rm single-file.txt",  # plain rm of one file is allowed by design
]


@pytest.mark.parametrize("command", BENIGN_COMMANDS)
def test_benign_commands_are_not_flagged(command):
    is_dangerous, _pattern_key, _description = detect_dangerous_command(command)
    assert not is_dangerous, f"false positive on benign command: {command!r}"


def test_normalization_is_detection_only():
    """The normalizer must never be handed the executed command — its
    output strips quotes/escapes, which would change semantics if used
    for execution. Pin that detect_dangerous_command is its only caller
    in production code (tests excluded)."""
    import inspect
    import tools.approval as approval_module

    src = inspect.getsource(approval_module)
    callers = [
        line.strip()
        for line in src.splitlines()
        if "_normalize_command_for_detection(" in line
        and "def _normalize_command_for_detection" not in line
    ]
    assert callers == ["command_lower = _normalize_command_for_detection(command).lower()"], (
        f"_normalize_command_for_detection gained a new caller — verify it is "
        f"detection-only before approving: {callers}"
    )
