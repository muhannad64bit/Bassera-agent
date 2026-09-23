#!/bin/bash
# Docker/Podman entrypoint: bootstrap config files into the mounted volume, then run bassera.
set -e

BASSERA_HOME="${BASSERA_HOME:-/opt/data}"
INSTALL_DIR="/opt/bassera"

# --- Privilege dropping via gosu ---
# When started as root (the default for Docker, or fakeroot in rootless Podman),
# optionally remap the bassera user/group to match host-side ownership, fix volume
# permissions, then re-exec as bassera.
if [ "$(id -u)" = "0" ]; then
    if [ -n "$BASSERA_UID" ] && [ "$BASSERA_UID" != "$(id -u bassera)" ]; then
        echo "Changing bassera UID to $BASSERA_UID"
        usermod -u "$BASSERA_UID" bassera
    fi

    if [ -n "$BASSERA_GID" ] && [ "$BASSERA_GID" != "$(id -g bassera)" ]; then
        echo "Changing bassera GID to $BASSERA_GID"
        # -o allows non-unique GID (e.g. macOS GID 20 "staff" may already exist
        # as "dialout" in the Debian-based container image)
        groupmod -o -g "$BASSERA_GID" bassera 2>/dev/null || true
    fi

    actual_bassera_uid=$(id -u bassera)
    if [ "$(stat -c %u "$BASSERA_HOME" 2>/dev/null)" != "$actual_bassera_uid" ]; then
        echo "$BASSERA_HOME is not owned by $actual_bassera_uid, fixing"
        # In rootless Podman the container's "root" is mapped to an unprivileged
        # host UID — chown will fail.  That's fine: the volume is already owned
        # by the mapped user on the host side.
        chown -R bassera:bassera "$BASSERA_HOME" 2>/dev/null || \
            echo "Warning: chown failed (rootless container?) — continuing anyway"
    fi

    echo "Dropping root privileges"
    exec gosu bassera "$0" "$@"
fi

# --- Running as bassera from here ---
source "${INSTALL_DIR}/.venv/bin/activate"

# Create essential directory structure.  Cache and platform directories
# (cache/images, cache/audio, platforms/whatsapp, etc.) are created on
# demand by the application — don't pre-create them here so new installs
# get the consolidated layout from get_bassera_dir().
# The "home/" subdirectory is a per-profile HOME for subprocesses (git,
# ssh, gh, npm …).  Without it those tools write to /root which is
# ephemeral and shared across profiles.  See issue #4426.
mkdir -p "$BASSERA_HOME"/{cron,sessions,logs,hooks,memories,skills,skins,plans,workspace,home}

# .env
if [ ! -f "$BASSERA_HOME/.env" ]; then
    cp "$INSTALL_DIR/.env.example" "$BASSERA_HOME/.env"
fi

# config.yaml
if [ ! -f "$BASSERA_HOME/config.yaml" ]; then
    cp "$INSTALL_DIR/cli-config.yaml.example" "$BASSERA_HOME/config.yaml"
fi

# SOUL.md
if [ ! -f "$BASSERA_HOME/SOUL.md" ]; then
    cp "$INSTALL_DIR/docker/SOUL.md" "$BASSERA_HOME/SOUL.md"
fi

# Sync bundled skills (manifest-based so user edits are preserved)
if [ -d "$INSTALL_DIR/skills" ]; then
    python3 "$INSTALL_DIR/tools/skills_sync.py"
fi

exec bassera "$@"
