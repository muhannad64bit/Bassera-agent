"""Suite hermeticity contracts, pinned as tests."""

from pathlib import Path


def test_suite_isolates_machine_local_gateway_locks():
    """Gateway locks are machine-local BY DESIGN in production (two
    agent homes on one machine must not drive the same bot token), so
    home isolation alone does not cover them. Without the explicit
    HERMES_GATEWAY_LOCK_DIR isolation, xdist workers collide on
    identical test identities — the whatsapp connect tests failed
    "session already in use (PID <other worker>)" under -n 4 — and the
    suite leaves lock files in the developer's real
    ~/.local/state/wafi/gateway-locks (observed in practice).
    """
    import os
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

    from gateway import status

    default_lock_dir = (
        Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
        / "wafi"
        / "gateway-locks"
    )
    assert status._get_lock_dir() != default_lock_dir, (
        "the suite is using the developer's real machine-local gateway "
        "lock directory"
    )
    assert status._get_lock_dir().is_relative_to(os.environ["HERMES_GATEWAY_LOCK_DIR"])
