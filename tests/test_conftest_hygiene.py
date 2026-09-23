"""Conftest hygiene for shared xdist workers.

The canonical full-suite run regressed to 50 failures when ``tests/e2e``
joined it: the e2e conftest installed a BARE MagicMock over the real
(installed) discord library whenever it ran before anything imported the
real one, and cached the production platform modules bound to that bare
mock. Per-file patching could not undo the bindings, so every gateway
Discord test in those workers failed. A reload-based fix then split
class identities across two module generations (two ``MessageType``
enums compare unequal) and broke the voice/session tests instead.

The pinned contracts:

1. The ROOT conftest installs the shared comprehensive platform mocks
   (``tests/platform_mocks.py``) before any directory conftest or test
   module can import ``gateway.platforms.*`` — so the binding is decided
   exactly once per worker, identically for every directory.

2. No conftest ever REBINDS cached production modules afterwards (no
   reload, no purge): class identities must stay single-generation for
   the whole worker lifetime.
"""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _exec_file(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_ROOT_CONFTEST = """
import sys
from pathlib import Path

repo = Path(%r)
sys.path.insert(0, str(repo))

root_conftest = None
import importlib.util
spec = importlib.util.spec_from_file_location(
    "root_conftest_under_test", repo / "tests/conftest.py")
root_conftest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(root_conftest)
"""

# Contract 1: after the root + e2e conftests load (the e2e-first worker
# order that used to install the bare poison mock), the discord mock in
# sys.modules must be the SHARED comprehensive one — sentinel-marked,
# with the fake channel types and the kwargs-storing AllowedMentions the
# gateway tests assert on. Counterfactual: the pre-fix conftests fail
# this with a bare MagicMock (no types, MagicMock AllowedMentions) or
# with the real library (no sentinel).
_CONTRACT_SHARED_MOCK = _ROOT_CONFTEST + """
import importlib.util

spec = importlib.util.spec_from_file_location(
    "e2e_conftest_under_test", repo / "tests/e2e/conftest.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

failures = []
discord = sys.modules.get("discord")
telegram = sys.modules.get("telegram")

for name, entry in (("discord", discord), ("telegram", telegram)):
    if getattr(entry, "BASSERA_PLATFORM_MOCK", None) != "1":
        failures.append(
            name + ": the shared comprehensive mock is not installed "
            "(bare mock or real library left in sys.modules)")

am = getattr(discord, "AllowedMentions", None)
if not isinstance(am, type):
    failures.append("discord: AllowedMentions is not the kwargs-storing class")
else:
    probe = am(everyone=False, roles=False, users=True, replied_user=True)
    if not (probe.everyone is False and probe.roles is False
            and probe.users is True and probe.replied_user is True):
        failures.append("discord: AllowedMentions does not store its kwargs")

for attr in ("DMChannel", "Thread", "ForumChannel"):
    if not isinstance(getattr(discord, attr, None), type):
        failures.append("discord: mock lacks the fake " + attr + " type")

for attr in ("GROUP", "SUPERGROUP", "PRIVATE", "CHANNEL"):
    if not isinstance(getattr(telegram.constants.ChatType, attr, None), str):
        failures.append("telegram: mock lacks the ChatType." + attr + " constant")

print("FAIL: " + "; ".join(failures) if failures else "OK")
sys.exit(1 if failures else 0)
"""

# Contract 2: in a worker where the e2e conftest (and anything it
# imports, e.g. gateway.run via test modules) ran BEFORE the gateway
# conftest, loading the gateway conftest must NOT change any module or
# class identity — no reload, no purge, ever.
_CONTRACT_NO_REBIND = _ROOT_CONFTEST + """
import importlib.util

spec = importlib.util.spec_from_file_location(
    "e2e_conftest_under_test", repo / "tests/e2e/conftest.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

import gateway.run  # cached, bound against the root-conftest mocks
import gateway.platforms.base as base_before

spec = importlib.util.spec_from_file_location(
    "gateway_conftest_under_test", repo / "tests/gateway/conftest.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

failures = []
if sys.modules["gateway.platforms.base"] is not base_before:
    failures.append("gateway.platforms.base module object was replaced")
if sys.modules["gateway.run"].MessageType is not base_before.MessageType:
    failures.append(
        "gateway.run's MessageType is no longer the class in "
        "gateway.platforms.base — a mid-worker rebind split class "
        "identities")
if getattr(sys.modules["gateway.platforms.discord"], "discord", None) \
        is not sys.modules["discord"]:
    failures.append(
        "gateway.platforms.discord is bound to a different discord module "
        "than sys.modules")

print("FAIL: " + "; ".join(failures) if failures else "OK")
sys.exit(1 if failures else 0)
"""


@pytest.mark.parametrize("probe,source", [
    ("shared mock", _CONTRACT_SHARED_MOCK),
    ("no rebind", _CONTRACT_NO_REBIND),
], ids=["shared-mock", "no-rebind"])
def test_platform_mock_contracts(probe, source):
    result = subprocess.run(
        [sys.executable, "-c", source % str(REPO_ROOT)],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, (
        f"platform mock contract '{probe}' violated:\n"
        f"{result.stdout}\n{result.stderr}"
    )


def test_suite_isolates_machine_local_gateway_locks():
    """Gateway locks are machine-local BY DESIGN in production (two
    agent homes on one machine must not drive the same bot token), so
    home isolation alone does not cover them. Without the explicit
    BASSERA_GATEWAY_LOCK_DIR isolation, xdist workers collide on
    identical test identities — the whatsapp connect tests failed
    "session already in use (PID <other worker>)" under -n 4 — and the
    suite leaves lock files in the developer's real
    ~/.local/state/bassera/gateway-locks (observed in practice).
    """
    import os

    from gateway import status

    default_lock_dir = (
        Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state"))
        / "bassera"
        / "gateway-locks"
    )
    assert status._get_lock_dir() != default_lock_dir, (
        "the suite is using the developer's real machine-local gateway "
        "lock directory"
    )
    assert status._get_lock_dir().is_relative_to(os.environ["BASSERA_GATEWAY_LOCK_DIR"])
