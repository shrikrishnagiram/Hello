"""Upload the collected numbers to the private Git repository (and pull Claude's verdicts)."""

from __future__ import annotations

import subprocess

from . import config


class SyncError(RuntimeError):
    pass


def _git(*args, check=True, timeout=180) -> str:
    r = subprocess.run(["git", "-C", str(config.DATA_DIR), *args], capture_output=True, text=True, timeout=timeout)
    if check and r.returncode != 0:
        raise SyncError(f"git {' '.join(args[:2])}: {(r.stderr or r.stdout).strip()[:300]}")
    return r.stdout


def ready() -> bool:
    return (config.DATA_DIR / ".git").exists()


def push(message: str) -> str:
    """Commit everything new and push. Rebases on top of Claude's commits first."""
    if not ready():
        raise SyncError(f"No data repository at {config.DATA_DIR}. Run: market-setup")
    _git("add", "-A")
    if not _git("status", "--porcelain").strip():
        return "nothing new"
    _git("-c", "user.name=phone-collector", "-c", "user.email=collector@localhost", "commit", "-q", "-m", message)
    br = _git("rev-parse", "--abbrev-ref", "HEAD").strip()
    for _ in range(3):
        try:
            _git("pull", "-q", "--rebase", "origin", br)
            _git("push", "-q", "origin", br)
            return "pushed"
        except SyncError as e:
            last = e
    raise last


def pull() -> str:
    if not ready():
        raise SyncError("No data repository. Run: market-setup")
    br = _git("rev-parse", "--abbrev-ref", "HEAD").strip()
    _git("pull", "-q", "--rebase", "origin", br)
    return "pulled"
