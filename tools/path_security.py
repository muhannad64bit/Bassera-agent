"""Shared path validation helpers for tool implementations.

Extracts the ``resolve() + relative_to()`` and ``..`` traversal check
patterns previously duplicated across skill_manager_tool, skills_tool,
skills_hub, cronjob_tools, and credential_files.
"""

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def validate_within_dir(path: Path, root: Path) -> Optional[str]:
    """Ensure *path* resolves to a location within *root*.

    Returns an error message string if validation fails, or ``None`` if the
    path is safe.  Uses ``Path.resolve()`` to follow symlinks and normalize
    ``..`` components.

    Usage::

        error = validate_within_dir(user_path, allowed_root)
        if error:
            return json.dumps({"error": error})
    """
    try:
        resolved = path.resolve()
        root_resolved = root.resolve()
        resolved.relative_to(root_resolved)
    except (ValueError, OSError) as exc:
        return f"Path escapes allowed directory: {exc}"
    return None


def has_traversal_component(path_str: str) -> bool:
    """Return True if *path_str* contains ``..`` traversal components.

    Quick check for obvious traversal attempts before doing full resolution.
    """
    parts = Path(path_str).parts
    return ".." in parts


class PathEscapeError(ValueError):
    """A download destination resolves outside its allowed root."""


def secure_download_destination(
    destination: Path,
    allowed_root: Path,
    *,
    label: str = "download",
) -> Path:
    """Central choke point for every surface that writes REMOTE bytes to disk.

    Every download path MUST pass its destination through this before
    opening a file for writing — a filename derived from a URL, a
    Content-Disposition header, or a remote directory listing must never
    be able to escape the intended directory (via ``..``, absolute paths,
    or symlinked parents). Raises :class:`PathEscapeError` on escape and
    returns the resolved destination otherwise.

    The threat model previously flagged "no central choke point for path
    validation of downloads" as a partial protection; this closes that
    gap for new surfaces at the cost of one call.
    """
    dest_resolved = destination.resolve()
    root_resolved = allowed_root.resolve()
    try:
        dest_resolved.relative_to(root_resolved)
    except (ValueError, OSError) as exc:
        raise PathEscapeError(
            f"{label} destination escapes allowed directory "
            f"{root_resolved}: {destination} ({exc})"
        ) from exc
    return dest_resolved
