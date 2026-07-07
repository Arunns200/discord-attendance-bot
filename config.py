"""Environment and path configuration."""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent
DB_FILENAME = "attendance.db"


def default_database_path() -> Path:
    configured = os.getenv("DATABASE_PATH")
    if configured:
        return Path(configured)

    # Railway sets this automatically when a volume is attached.
    volume_mount = os.getenv("RAILWAY_VOLUME_MOUNT_PATH")
    if volume_mount:
        return Path(volume_mount) / DB_FILENAME

    if os.getenv("RAILWAY_ENVIRONMENT"):
        return Path("/app/data") / DB_FILENAME

    return PROJECT_ROOT / "data" / DB_FILENAME


def log_database_setup(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)

    on_railway = bool(os.getenv("RAILWAY_ENVIRONMENT"))
    has_volume = bool(os.getenv("RAILWAY_VOLUME_MOUNT_PATH"))

    if on_railway and not has_volume and not os.getenv("DATABASE_PATH"):
        logger.warning(
            "Running on Railway without an attached volume. "
            "Attendance data will be LOST on every redeploy. "
            "Add a Railway volume mounted at /app/data, then redeploy."
        )
    elif on_railway and has_volume:
        logger.info(
            "Using Railway volume at %s for persistent storage.",
            os.getenv("RAILWAY_VOLUME_MOUNT_PATH"),
        )

    logger.info("Database path: %s", db_path)
