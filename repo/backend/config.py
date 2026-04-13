"""Centralized paths and runtime configuration."""
from __future__ import annotations
import os
from pathlib import Path

APP_NAME = "CRHGC"
APP_DISPLAY_NAME = "Collegiate Resource & Housing Governance Console"

REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = REPO_ROOT / "database" / "migrations"
SEED_DIR = REPO_ROOT / "database" / "seed"


def data_dir() -> Path:
    """Per-user data directory. On Windows -> %LOCALAPPDATA%\\CRHGC."""
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share")))
    p = base / APP_NAME
    p.mkdir(parents=True, exist_ok=True)
    return p


def db_path() -> Path:
    override = os.environ.get("CRHGC_DB")
    if override:
        return Path(override)
    return data_dir() / "crhgc.db"


def key_path() -> Path:
    return data_dir() / "key.bin"


def log_path() -> Path:
    return data_dir() / "crhgc.log"


def evidence_dir() -> Path:
    p = data_dir() / "evidence"
    p.mkdir(parents=True, exist_ok=True)
    return p


def snapshot_dir() -> Path:
    p = data_dir() / "snapshots"
    p.mkdir(parents=True, exist_ok=True)
    return p


def update_signing_key_path() -> Path:
    """Public key (PEM) used to verify signed update packages.

    Distributed with the installer at `repo/installer/update_pubkey.pem`,
    copied into the data directory at first run if missing.
    """
    return data_dir() / "update_pubkey.pem"


# Tunables
PBKDF2_ITERATIONS = 200_000
MASK_UNLOCK_SECONDS = 5 * 60
NOTIF_RETRY_LIMIT = 3
NOTIF_RETRY_INTERVAL_SECONDS = 5 * 60
FUZZY_THRESHOLD = 78

# Crash-recovery / autosave
CHECKPOINT_INTERVAL_SECONDS = 60
WORKSPACE_STATE_KEY = "workspace_state_v1"

# Compliance retention
EVIDENCE_RETENTION_YEARS = 7

# Performance
TARGET_STARTUP_SECONDS = 5.0

# Fixed template variable registry
TEMPLATE_VARIABLES = {
    "StudentName", "StudentID", "Dorm", "Room", "Bed",
    "EffectiveDate", "EmployerName", "ResourceTitle",
    "Operator", "Today",
}
