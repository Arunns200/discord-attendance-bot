"""SQLite persistence layer for attendance sessions."""

from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path(__file__).resolve().parent / "data" / "attendance.db"


@dataclass(frozen=True, slots=True)
class Session:
    id: int
    user_id: int
    username: str
    login_at: datetime
    logout_at: datetime | None = None


class AttendanceDatabase:
    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    username TEXT NOT NULL,
                    login_at TEXT NOT NULL,
                    logout_at TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_sessions_active_user
                ON sessions (user_id)
                WHERE logout_at IS NULL
                """
            )
        logger.info("Database ready at %s", self.db_path)

    @staticmethod
    def _parse_timestamp(value: str) -> datetime:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _format_timestamp(value: datetime) -> str:
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()

    def _row_to_session(self, row: sqlite3.Row) -> Session:
        return Session(
            id=row["id"],
            user_id=row["user_id"],
            username=row["username"],
            login_at=self._parse_timestamp(row["login_at"]),
            logout_at=(
                self._parse_timestamp(row["logout_at"])
                if row["logout_at"] is not None
                else None
            ),
        )

    def get_active_session(self, user_id: int) -> Session | None:
        with self._connection() as conn:
            row = conn.execute(
                """
                SELECT id, user_id, username, login_at, logout_at
                FROM sessions
                WHERE user_id = ? AND logout_at IS NULL
                ORDER BY login_at DESC
                LIMIT 1
                """,
                (user_id,),
            ).fetchone()
        return self._row_to_session(row) if row else None

    def start_session(self, user_id: int, username: str, login_at: datetime) -> Session:
        if self.get_active_session(user_id) is not None:
            raise ValueError("User already has an active session.")

        with self._connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO sessions (user_id, username, login_at)
                VALUES (?, ?, ?)
                """,
                (user_id, username, self._format_timestamp(login_at)),
            )
            session_id = cursor.lastrowid
            row = conn.execute(
                """
                SELECT id, user_id, username, login_at, logout_at
                FROM sessions
                WHERE id = ?
                """,
                (session_id,),
            ).fetchone()

        if row is None:
            raise RuntimeError("Failed to create attendance session.")

        session = self._row_to_session(row)
        logger.info("Started session %s for user %s (%s)", session.id, user_id, username)
        return session

    def end_session(self, user_id: int, logout_at: datetime) -> Session:
        active = self.get_active_session(user_id)
        if active is None:
            raise ValueError("No active session found for this user.")

        with self._connection() as conn:
            conn.execute(
                """
                UPDATE sessions
                SET logout_at = ?
                WHERE id = ?
                """,
                (self._format_timestamp(logout_at), active.id),
            )
            row = conn.execute(
                """
                SELECT id, user_id, username, login_at, logout_at
                FROM sessions
                WHERE id = ?
                """,
                (active.id,),
            ).fetchone()

        if row is None:
            raise RuntimeError("Failed to close attendance session.")

        session = self._row_to_session(row)
        logger.info("Ended session %s for user %s (%s)", session.id, user_id, session.username)
        return session

    def list_active_sessions(self) -> list[Session]:
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT id, user_id, username, login_at, logout_at
                FROM sessions
                WHERE logout_at IS NULL
                ORDER BY login_at ASC
                """
            ).fetchall()
        return [self._row_to_session(row) for row in rows]
