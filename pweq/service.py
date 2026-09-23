# SPDX-License-Identifier: GPL-3.0-or-later
"""systemd user service control."""

from __future__ import annotations

import subprocess

SERVICE = "pweq.service"


def _systemctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True)


def reload_daemon() -> bool:
    """Ask a running daemon to reload. Returns False if it isn't running."""
    return _systemctl("reload", SERVICE).returncode == 0


def is_active() -> bool:
    return _systemctl("is-active", "--quiet", SERVICE).returncode == 0


def enable_now() -> str | None:
    """Enable and start the service; returns an error message on failure."""
    r = _systemctl("enable", "--now", SERVICE)
    return (r.stderr.strip() or "failed to start pweq.service") if r.returncode else None
