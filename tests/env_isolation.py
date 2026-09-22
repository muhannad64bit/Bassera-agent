"""Isolated-home helper for tests that clear the environment.

``patch.dict(os.environ, ..., clear=True)`` wipes HERMES_HOME, so code
under test that resolves its home at runtime (gateway adapters, tirith,
credential code) falls back to the developer's real ``~/.wafi`` and
persists state there. Observed in practice: feishu_seen_message_ids.json,
gateway_state.json, and tirith install markers leaking into the real home
during test runs.

Usage::

    from tests.env_isolation import cleared_env_with_isolated_home

    @patch.dict(os.environ, cleared_env_with_isolated_home(), clear=True)
    def test_something(self):
        adapter = FeishuAdapter(PlatformConfig())   # now hermetic

Tests that deliberately exercise the *default-home fallback* (no
HERMES_HOME → ``~/.wafi``) should keep using a plain cleared env — this
helper is only for tests that construct state-writing components under a
cleared environment.
"""

import atexit
import shutil
import tempfile

# One isolated home per test process. Adapters that persist dedup/state
# share it within a file, exactly as they previously shared the real home
# (so no new cross-test coupling is introduced — the blast radius shrinks
# from "the developer's real install" to a throwaway directory).
_ISOLATED_HOME = tempfile.mkdtemp(prefix="wafi-cleared-env-home-")
atexit.register(shutil.rmtree, _ISOLATED_HOME, ignore_errors=True)


def cleared_env_with_isolated_home() -> dict:
    """Env dict for ``patch.dict(..., clear=True)``: everything cleared
    except an isolated HERMES_HOME."""
    return {"HERMES_HOME": _ISOLATED_HOME}
