"""Property-based testing for the dangerous-command detector.

The original vulnerability in this detector was an incomplete regex:
writes to ``$HERMES_HOME/.env`` bypassed the approval system because the
sensitive-write pattern only knew the ``$wafi_home`` spelling. Siblings of
that hole are exactly what this suite hunts: every shell spelling of a
credential-file write, every common write tool, case tricks, and crash or
ReDoS resistance on arbitrary input.

Run: scripts/run_tests.sh tests/tools/test_approval_fuzz.py
"""

import time

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from tools.approval import detect_dangerous_command

# Any text input the model/subprocess could plausibly produce. Surrogates
# excluded: they cannot appear in real command strings (invalid UTF-8
# encoding would have failed earlier), and .lower() on them is a no-op
# anyway — we just avoid hypothesis' surrogate-pair generation noise.
command_text = st.text(
    alphabet=st.characters(codec="utf-8", exclude_categories=("Cs",)),
    max_size=500,
)


# ---------------------------------------------------------------------------
# Invariants on arbitrary input
# ---------------------------------------------------------------------------

@given(command_text)
@settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])
def test_never_raises_and_returns_triple(cmd):
    """The detector is the gate before approval: it must never raise."""
    result = detect_dangerous_command(cmd)
    assert isinstance(result, tuple) and len(result) == 3


@given(command_text)
@settings(max_examples=200, deadline=None)
def test_case_insensitive(cmd):
    """The detector lowercases input, so case must never change the verdict.

    This pins the property that made uppercase spellings like
    ``echo x | tee "$HERMES_HOME/.env"`` safe even though the patterns are
    written in lowercase.
    """
    assert detect_dangerous_command(cmd) == detect_dangerous_command(cmd.lower())


@given(command_text)
@settings(max_examples=100, deadline=None)
def test_idempotent(cmd):
    assert detect_dangerous_command(cmd) == detect_dangerous_command(cmd)


# ---------------------------------------------------------------------------
# Credential-file write property: every spelling must be flagged
# ---------------------------------------------------------------------------

HOME_ENV_NAMES = ["HERMES_HOME", "WAFI_HOME", "BASSERA_HOME"]
REDIRECT_OPENERS = [
    "echo x > ",
    "echo x >> ",
    "echo x 1> ",
    "echo x 2> ",
    "echo x | tee ",
    "echo x | tee -a ",
]
QUOTES = ['"', "'", "`", ""]

# A prefix of unrelated earlier commands. Always terminated so the opener
# below stays a separate token.
junk_prefix = st.one_of(
    st.just(""),
    st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789 ", max_size=30).map(
        lambda s: s + "; "
    ),
)


@given(
    st.sampled_from(HOME_ENV_NAMES),
    st.sampled_from(REDIRECT_OPENERS),
    st.booleans(),
    st.sampled_from(QUOTES),
    junk_prefix,
)
@settings(max_examples=300, deadline=None)
def test_credential_file_writes_always_flagged(name, opener, braces, quote, prefix):
    """Redirect/tee writes into the agent's credential .env file — in every
    env-var spelling (bare, braced, quoted, any casing) and with any
    preceding junk — must require approval."""
    var = f"${{{name}}}" if braces else f"${name}"
    cmd = f"{prefix}{opener}{quote}{var}/.env{quote}"
    dangerous, key, desc = detect_dangerous_command(cmd)
    assert dangerous is True, f"credential-file write not flagged: {cmd!r}"


# ---------------------------------------------------------------------------
# Suspected siblings of the original hole: other write tools into the
# credential file and other sensitive targets
# ---------------------------------------------------------------------------

COPY_TOOLS = ["cp", "mv", "install -m 600", "rsync -a"]
COPY_TARGETS = [
    "$HERMES_HOME/.env",
    "${HERMES_HOME}/.env",
    "~/.ssh/authorized_keys",
    "$HOME/.ssh/authorized_keys",
    "/etc/passwd",
    "/dev/sda",
]


@pytest.mark.parametrize("tool", COPY_TOOLS)
@pytest.mark.parametrize("target", COPY_TARGETS)
def test_copy_tools_into_sensitive_targets_flagged(tool, target):
    """cp/mv/install/rsync writing INTO a sensitive target must be flagged.

    Sibling of the original vulnerability: the redirect/tee patterns were
    completed for $HERMES_HOME/.env, but a copy into the same file
    (`cp payload $HERMES_HOME/.env`) overwrites the credential store just
    as effectively and was not covered.
    """
    cmd = f"{tool} /tmp/payload {target}"
    dangerous, key, desc = detect_dangerous_command(cmd)
    assert dangerous is True, f"copy into sensitive target not flagged: {cmd!r}"


@pytest.mark.parametrize(
    "cmd",
    [
        "sed -i 's/x/y/' $HERMES_HOME/.env",
        "sed --in-place s/x/y/ ${HERMES_HOME}/.env",
        "sed -i s/x/y/ ~/.ssh/config",
        "sed -i s/x/y/ /etc/passwd",
    ],
)
def test_in_place_edits_of_sensitive_files_flagged(cmd):
    """In-place sed edits of sensitive files must be flagged."""
    dangerous, key, desc = detect_dangerous_command(cmd)
    assert dangerous is True, f"in-place sensitive edit not flagged: {cmd!r}"


# ---------------------------------------------------------------------------
# Over-flagging guard: benign commands must stay silent
# ---------------------------------------------------------------------------

BENIGN = [
    "cp a /tmp/b",
    "mv a /tmp/b",
    "echo x > /tmp/out.txt",
    "echo x | tee /tmp/out.txt",
    "sed -i s/x/y/ /tmp/notes.md",
    "install -m 644 a /tmp/b",
    "rsync -a /tmp/a/ /tmp/b/",
    "cat ~/.bashrc",
    "ls $HERMES_HOME",
    "echo $HERMES_HOME",
]


@pytest.mark.parametrize("cmd", BENIGN)
def test_benign_commands_not_flagged(cmd):
    dangerous, key, desc = detect_dangerous_command(cmd)
    assert dangerous is False, f"benign command flagged: {cmd!r} ({desc})"


# ---------------------------------------------------------------------------
# ReDoS / pathological input resistance
# ---------------------------------------------------------------------------

ADVERSARIAL = [
    ("tee " * 4000) + "/tmp/out",
    "x" * 60000,
    ("echo x > " * 3000) + "$HERMES_HOME",
    ("find . -exec " * 2000) + "rm {} ;",
    ("; " * 20000) + "echo done",
    ("$HERMES_HOME/" * 5000) + ".env",
]


@pytest.mark.parametrize("cmd", ADVERSARIAL)
def test_pathological_input_completes_quickly(cmd):
    """The detector runs on every terminal command pre-approval; pathological
    input must not blow up latency (the patterns use .* with DOTALL — this
    guards against catastrophic backtracking regressions)."""
    start = time.monotonic()
    detect_dangerous_command(cmd)
    elapsed = time.monotonic() - start
    assert elapsed < 2.0, f"detector took {elapsed:.2f}s on pathological input (len={len(cmd)})"
