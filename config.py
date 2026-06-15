"""Environment and path configuration."""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent


def default_database_path() -> Path:
    configured = os.getenv("DATABASE_PATH")
    if configured:
        return Path(configured)

    if os.getenv("RAILWAY_ENVIRONMENT"):
        return Path("/app/data/attendance.db")

    return PROJECT_ROOT / "data" / "attendance.db"
