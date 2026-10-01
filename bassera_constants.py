"""Shared constants for Bassera Agent.

Import-safe module with no dependencies — can be imported from anywhere
without risk of circular imports.
"""

import os
import re
import sys
from pathlib import Path


_LEGACY_ENV_RE = re.compile(r"^(?:HERMES|WAFI)_(.+)$")


def apply_legacy_env_aliases() -> None:
    """Map legacy ``BASSERA_*`` / ``WAFI_*`` env vars onto ``BASSERA_*``.

    The 2026-09 rebrand renamed every internal env var to the
    ``BASSERA_*`` spelling. Existing user infrastructure (shell profiles,
    cron units, docker-compose files, scripts) that sets ``BASSERA_*`` or
    ``WAFI_*`` must keep working with zero downtime: for every legacy
    variable present, the equivalent ``BASSERA_*`` variable is set —
    unless it is already set explicitly (the new spelling always wins).

    Runs once at import of this module (which every entry point imports
    before reading any env var) and is idempotent.
    """
    for name in list(os.environ):
        match = _LEGACY_ENV_RE.match(name)
        if not match:
            continue
        canonical = "BASSERA_" + match.group(1)
        if canonical not in os.environ:
            os.environ[canonical] = os.environ[name]


apply_legacy_env_aliases()


def is_bassera_invocation() -> bool:
    """True when the process was launched via a bassera* entry point.

    Kept for callers that branch on entry-point identity; the legacy
    ``wafi``/``wafi-agent``/``wafi-acp`` aliases remain functional but
    report the Bassera identity.
    """
    return os.path.basename(sys.argv[0] if len(sys.argv) > 0 else "").startswith("bassera")


def agent_display_name() -> str:
    """The agent's display name: Bassera, regardless of entry point."""
    return "Bassera"


def default_home_root() -> Path:
    """Return the default home root with legacy-install compatibility.

    - ``~/.bassera`` wins when it exists (rebranded or fresh install).
    - Otherwise, if a legacy ``~/.wafi`` exists, it is used — existing
      installs keep their state, credentials, sessions and skills with
      zero downtime (no migration step).
    - Otherwise (fresh install) ``~/.bassera``.
    """
    def _safe_exists(path: Path) -> bool:
        try:
            return path.exists()
        except PermissionError:
            return False

    bassera_home = Path.home() / ".bassera"
    if _safe_exists(bassera_home):
        return bassera_home
    legacy_home = Path.home() / ".wafi"
    if _safe_exists(legacy_home):
        return legacy_home
    return bassera_home


def get_bassera_home() -> Path:
    """Return the Bassera home directory.

    Resolution order for the home root:
    1. ``BASSERA_HOME`` env var (canonical — internally for profiles and
       the hermetic test suite; also where legacy ``BASSERA_HOME`` /
       ``WAFI_HOME`` values are mapped by ``apply_legacy_env_aliases``)
    2. ``~/.bassera`` when it exists, else legacy ``~/.wafi`` when it
       exists, else ``~/.bassera`` for fresh installs

    The legacy fallback keeps existing user installs fully functional.
    """
    val = os.environ.get("BASSERA_HOME", "").strip()
    if val:
        return Path(val)
    return default_home_root()


def get_default_bassera_root() -> Path:
    """Return the root Bassera directory for profile-level operations.

    In standard deployments this is the default home root (``~/.bassera``,
    or the legacy ``~/.wafi`` when that is the existing install).

    In Docker or custom deployments where ``BASSERA_HOME`` points outside
    the default root (e.g. ``/opt/data``), returns ``BASSERA_HOME`` directly
    — that IS the root.

    In profile mode where ``BASSERA_HOME`` is ``<root>/profiles/<name>``,
    returns ``<root>`` so that ``profile list`` can see all profiles.
    Works both for standard (``~/.bassera/profiles/coder``) and Docker
    (``/opt/data/profiles/coder``) layouts.

    Import-safe — no dependencies beyond stdlib.
    """
    native_home = default_home_root()
    env_home = os.environ.get("BASSERA_HOME", "")
    if not env_home:
        return native_home
    env_path = Path(env_home)
    try:
        env_path.resolve().relative_to(native_home.resolve())
        # BASSERA_HOME is under ~/.bassera (normal or profile mode)
        return native_home
    except ValueError:
        pass

    # Docker / custom deployment.
    # Check if this is a profile path: <root>/profiles/<name>
    # If the immediate parent dir is named "profiles", the root is
    # the grandparent — this covers Docker profiles correctly.
    if env_path.parent.name == "profiles":
        return env_path.parent.parent

    # Not a profile path — BASSERA_HOME itself is the root
    return env_path


def get_optional_skills_dir(default: Path | None = None) -> Path:
    """Return the optional-skills directory, honoring package-manager wrappers.

    Packaged installs may ship ``optional-skills`` outside the Python package
    tree and expose it via ``BASSERA_OPTIONAL_SKILLS``.
    """
    override = os.getenv("BASSERA_OPTIONAL_SKILLS", "").strip()
    if override:
        return Path(override)
    if default is not None:
        return default
    return get_bassera_home() / "optional-skills"


def get_bassera_dir(new_subpath: str, old_name: str) -> Path:
    """Resolve a Bassera subdirectory with backward compatibility.

    New installs get the consolidated layout (e.g. ``cache/images``).
    Existing installs that already have the old path (e.g. ``image_cache``)
    keep using it — no migration required.

    Args:
        new_subpath: Preferred path relative to BASSERA_HOME (e.g. ``"cache/images"``).
        old_name: Legacy path relative to BASSERA_HOME (e.g. ``"image_cache"``).

    Returns:
        Absolute ``Path`` — old location if it exists on disk, otherwise the new one.
    """
    home = get_bassera_home()
    old_path = home / old_name
    if old_path.exists():
        return old_path
    return home / new_subpath


def display_bassera_home() -> str:
    """Return a user-friendly display string for the current BASSERA_HOME.

    Uses ``~/`` shorthand for readability::

        default:  ``~/.bassera``
        profile:  ``~/.bassera/profiles/coder``
        custom:   ``/opt/bassera-custom``

    Use this in **user-facing** print/log messages instead of hardcoding
    ``~/.bassera``.  For code that needs a real ``Path``, use
    :func:`get_bassera_home` instead.
    """
    home = get_bassera_home()
    try:
        return "~/" + str(home.relative_to(Path.home()))
    except ValueError:
        return str(home)


def get_subprocess_home() -> str | None:
    """Return a per-profile HOME directory for subprocesses, or None.

    When ``{BASSERA_HOME}/home/`` exists on disk, subprocesses should use it
    as ``HOME`` so system tools (git, ssh, gh, npm …) write their configs
    inside the Bassera data directory instead of the OS-level ``/root`` or
    ``~/``.  This provides:

    * **Docker persistence** — tool configs land inside the persistent volume.
    * **Profile isolation** — each profile gets its own git identity, SSH
      keys, gh tokens, etc.

    The Python process's own ``os.environ["HOME"]`` and ``Path.home()`` are
    **never** modified — only subprocess environments should inject this value.
    Activation is directory-based: if the ``home/`` subdirectory doesn't
    exist, returns ``None`` and behavior is unchanged.
    """
    bassera_home = os.getenv("BASSERA_HOME")
    if not bassera_home:
        return None
    profile_home = os.path.join(bassera_home, "home")
    if os.path.isdir(profile_home):
        return profile_home
    return None


VALID_REASONING_EFFORTS = ("minimal", "low", "medium", "high", "xhigh")


def parse_reasoning_effort(effort: str) -> dict | None:
    """Parse a reasoning effort level into a config dict.

    Valid levels: "none", "minimal", "low", "medium", "high", "xhigh".
    Returns None when the input is empty or unrecognized (caller uses default).
    Returns {"enabled": False} for "none".
    Returns {"enabled": True, "effort": <level>} for valid effort levels.
    """
    if not effort or not effort.strip():
        return None
    effort = effort.strip().lower()
    if effort == "none":
        return {"enabled": False}
    if effort in VALID_REASONING_EFFORTS:
        return {"enabled": True, "effort": effort}
    return None


def is_termux() -> bool:
    """Return True when running inside a Termux (Android) environment.

    Checks ``TERMUX_VERSION`` (set by Termux) or the Termux-specific
    ``PREFIX`` path.  Import-safe — no heavy deps.
    """
    prefix = os.getenv("PREFIX", "")
    return bool(os.getenv("TERMUX_VERSION") or "com.termux/files/usr" in prefix)


_wsl_detected: bool | None = None


def is_wsl() -> bool:
    """Return True when running inside WSL (Windows Subsystem for Linux).

    Checks ``/proc/version`` for the ``microsoft`` marker that both WSL1
    and WSL2 inject.  Result is cached for the process lifetime.
    Import-safe — no heavy deps.
    """
    global _wsl_detected
    if _wsl_detected is not None:
        return _wsl_detected
    try:
        with open("/proc/version", "r") as f:
            _wsl_detected = "microsoft" in f.read().lower()
    except Exception:
        _wsl_detected = False
    return _wsl_detected


_container_detected: bool | None = None


def is_container() -> bool:
    """Return True when running inside a Docker/Podman container.

    Checks ``/.dockerenv`` (Docker), ``/run/.containerenv`` (Podman),
    and ``/proc/1/cgroup`` for container runtime markers.  Result is
    cached for the process lifetime.  Import-safe — no heavy deps.
    """
    global _container_detected
    if _container_detected is not None:
        return _container_detected
    if os.path.exists("/.dockerenv"):
        _container_detected = True
        return True
    if os.path.exists("/run/.containerenv"):
        _container_detected = True
        return True
    try:
        with open("/proc/1/cgroup", "r") as f:
            cgroup = f.read()
            if "docker" in cgroup or "podman" in cgroup or "/lxc/" in cgroup:
                _container_detected = True
                return True
    except OSError:
        pass
    _container_detected = False
    return False


# ─── Well-Known Paths ─────────────────────────────────────────────────────────


def get_config_path() -> Path:
    """Return the path to ``config.yaml`` under BASSERA_HOME.

    Replaces the ``get_bassera_home() / "config.yaml"`` pattern repeated
    in 7+ files (skill_utils.py, bassera_logging.py, bassera_time.py, etc.).
    """
    return get_bassera_home() / "config.yaml"


def get_skills_dir() -> Path:
    """Return the path to the skills directory under BASSERA_HOME."""
    return get_bassera_home() / "skills"



def get_env_path() -> Path:
    """Return the path to the ``.env`` file under BASSERA_HOME."""
    return get_bassera_home() / ".env"


# ─── Network Preferences ─────────────────────────────────────────────────────


def apply_ipv4_preference(force: bool = False) -> None:
    """Monkey-patch ``socket.getaddrinfo`` to prefer IPv4 connections.

    On servers with broken or unreachable IPv6, Python tries AAAA records
    first and hangs for the full TCP timeout before falling back to IPv4.
    This affects httpx, requests, urllib, the OpenAI SDK — everything that
    uses ``socket.getaddrinfo``.

    When *force* is True, patches ``getaddrinfo`` so that calls with
    ``family=AF_UNSPEC`` (the default) resolve as ``AF_INET`` instead,
    skipping IPv6 entirely.  If no A record exists, falls back to the
    original unfiltered resolution so pure-IPv6 hosts still work.

    Safe to call multiple times — only patches once.
    Set ``network.force_ipv4: true`` in ``config.yaml`` to enable.
    """
    if not force:
        return

    import socket

    # Guard against double-patching
    if getattr(socket.getaddrinfo, "_bassera_ipv4_patched", False):
        return

    _original_getaddrinfo = socket.getaddrinfo

    def _ipv4_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        if family == 0:  # AF_UNSPEC — caller didn't request a specific family
            try:
                return _original_getaddrinfo(
                    host, port, socket.AF_INET, type, proto, flags
                )
            except socket.gaierror:
                # No A record — fall back to full resolution (pure-IPv6 hosts)
                return _original_getaddrinfo(host, port, family, type, proto, flags)
        return _original_getaddrinfo(host, port, family, type, proto, flags)

    _ipv4_getaddrinfo._bassera_ipv4_patched = True  # type: ignore[attr-defined]
    socket.getaddrinfo = _ipv4_getaddrinfo  # type: ignore[assignment]


OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_MODELS_URL = f"{OPENROUTER_BASE_URL}/models"

AI_GATEWAY_BASE_URL = "https://ai-gateway.vercel.sh/v1"
