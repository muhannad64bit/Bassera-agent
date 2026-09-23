"""Shared fixtures for gateway tests.

The platform-library mocks (discord / telegram) that the gateway tests
are written against are installed by the ROOT conftest
(``tests/conftest.py``) via ``tests/platform_mocks.py`` — exactly once
per xdist worker, BEFORE any test module or directory conftest can
import ``gateway.platforms.*``. That single early decision point is what
makes shared workers consistent: previously this conftest installed its
own mock gated on import state (``"x" in sys.modules``), so whichever
conftest ran first in a worker decided the binding, and a bare
MagicMock installed over the real library got baked into
``gateway.platforms.discord`` — failing every gateway Discord test in
those workers (the 50-failure full-suite regression).

This file only re-asserts the installation (idempotent, sentinel-
checked) as a defensive net. It must NEVER rebind cached production
modules (reload/purge): that splits class identities across two module
generations — two ``MessageType`` enums compare unequal even with
identical values, and enum-identity assertions in the voice/session
tests fail. See ``tests/test_conftest_hygiene.py`` for the pinned
contracts.
"""

import importlib.util
import os
import sys


def _load_platform_mocks():
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "platform_mocks.py",
    )
    spec = importlib.util.spec_from_file_location("bassera_platform_mocks", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["bassera_platform_mocks"] = mod
    spec.loader.exec_module(mod)
    return mod


# Idempotent no-op when the root conftest already installed the mocks.
_load_platform_mocks().ensure_telegram_mock()
_load_platform_mocks().ensure_discord_mock()
