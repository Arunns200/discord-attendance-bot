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


@dataclass(frozen=True, slots=True)
class ShiftExchange:
    id: int
    from_user_id: int
    from_username: str
    to_user_id: int
    to_username: str
    shift: str
    date: str
    notes: str
    created_at: datetime
    status: str


@dataclass(frozen=True, slots=True)
class WorkBoard:
    id: int
    started_by_user_id: int
    started_by_username: str
    label: str
    started_at: datetime
    ended_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class WorkAssignment:
    id: int
    board_id: int
    seat: str
    user_id: int | None
    username: str | None
    notes: str
    assigned_at: datetime | None


class AttendanceDatabase:
    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
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
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS shift_exchanges (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    from_user_id INTEGER NOT NULL,
                    from_username TEXT NOT NULL,
                    to_user_id INTEGER NOT NULL,
                    to_username TEXT NOT NULL,
                    shift_time TEXT NOT NULL,
                    details TEXT NOT NULL,
                    shift TEXT,
                    exchange_date TEXT,
                    notes TEXT,
                    created_at TEXT NOT NULL,
                    status TEXT NOT NULL
                )
                """
            )
            columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(shift_exchanges)").fetchall()
            }
            if "shift" not in columns:
                conn.execute("ALTER TABLE shift_exchanges ADD COLUMN shift TEXT")
            if "exchange_date" not in columns:
                conn.execute("ALTER TABLE shift_exchanges ADD COLUMN exchange_date TEXT")
            if "notes" not in columns:
                conn.execute("ALTER TABLE shift_exchanges ADD COLUMN notes TEXT")
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_shift_exchanges_status_created
                ON shift_exchanges (status, created_at)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_shift_exchanges_from_to
                ON shift_exchanges (from_user_id, to_user_id)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS work_boards (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_by_user_id INTEGER NOT NULL,
                    started_by_username TEXT NOT NULL,
                    label TEXT NOT NULL DEFAULT '',
                    started_at TEXT NOT NULL,
                    ended_at TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_work_boards_active
                ON work_boards (ended_at, started_at)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS work_assignments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    board_id INTEGER NOT NULL,
                    seat TEXT NOT NULL,
                    user_id INTEGER,
                    username TEXT,
                    notes TEXT NOT NULL DEFAULT '',
                    assigned_at TEXT,
                    UNIQUE (board_id, seat),
                    FOREIGN KEY (board_id) REFERENCES work_boards (id)
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_work_assignments_board
                ON work_assignments (board_id)
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

    def _row_to_shift_exchange(self, row: sqlite3.Row) -> ShiftExchange:
        shift = row["shift"] if "shift" in row.keys() and row["shift"] else row["shift_time"]
        exchange_date = (
            row["exchange_date"]
            if "exchange_date" in row.keys() and row["exchange_date"]
            else row["details"]
        )
        notes = row["notes"] if "notes" in row.keys() and row["notes"] else ""

        return ShiftExchange(
            id=row["id"],
            from_user_id=row["from_user_id"],
            from_username=row["from_username"],
            to_user_id=row["to_user_id"],
            to_username=row["to_username"],
            shift=shift,
            date=exchange_date,
            notes=notes,
            created_at=self._parse_timestamp(row["created_at"]),
            status=row["status"],
        )

    def _row_to_work_board(self, row: sqlite3.Row) -> WorkBoard:
        return WorkBoard(
            id=row["id"],
            started_by_user_id=row["started_by_user_id"],
            started_by_username=row["started_by_username"],
            label=row["label"] or "",
            started_at=self._parse_timestamp(row["started_at"]),
            ended_at=(
                self._parse_timestamp(row["ended_at"])
                if row["ended_at"] is not None
                else None
            ),
        )

    def _row_to_work_assignment(self, row: sqlite3.Row) -> WorkAssignment:
        return WorkAssignment(
            id=row["id"],
            board_id=row["board_id"],
            seat=row["seat"],
            user_id=row["user_id"],
            username=row["username"],
            notes=row["notes"] or "",
            assigned_at=(
                self._parse_timestamp(row["assigned_at"])
                if row["assigned_at"] is not None
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

    def create_shift_exchange(
        self,
        *,
        from_user_id: int,
        from_username: str,
        to_user_id: int,
        to_username: str,
        shift: str,
        date: str,
        notes: str,
        created_at: datetime,
        status: str = "exchanged",
    ) -> ShiftExchange:
        if not shift.strip():
            raise ValueError("Shift cannot be empty.")
        if not date.strip():
            raise ValueError("Date cannot be empty.")
        if status.strip() == "":
            raise ValueError("Status cannot be empty.")

        with self._connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO shift_exchanges (
                    from_user_id,
                    from_username,
                    to_user_id,
                    to_username,
                    shift_time,
                    details,
                    shift,
                    exchange_date,
                    notes,
                    created_at,
                    status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    from_user_id,
                    from_username,
                    to_user_id,
                    to_username,
                    shift.strip(),
                    date.strip(),
                    shift.strip(),
                    date.strip(),
                    notes.strip(),
                    self._format_timestamp(created_at),
                    status.strip(),
                ),
            )
            exchange_id = cursor.lastrowid
            row = conn.execute(
                """
                SELECT
                    id,
                    from_user_id,
                    from_username,
                    to_user_id,
                    to_username,
                    shift_time,
                    details,
                    shift,
                    exchange_date,
                    notes,
                    created_at,
                    status
                FROM shift_exchanges
                WHERE id = ?
                """,
                (exchange_id,),
            ).fetchone()

        if row is None:
            raise RuntimeError("Failed to create shift exchange.")

        exchange = self._row_to_shift_exchange(row)
        logger.info(
            "Created shift exchange %s from %s -> %s",
            exchange.id,
            exchange.from_user_id,
            exchange.to_user_id,
        )
        return exchange

    def list_shift_exchanges(self, limit: int = 10) -> list[ShiftExchange]:
        limit = max(1, min(int(limit), 50))
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT
                    id,
                    from_user_id,
                    from_username,
                    to_user_id,
                    to_username,
                    shift_time,
                    details,
                    shift,
                    exchange_date,
                    notes,
                    created_at,
                    status
                FROM shift_exchanges
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [self._row_to_shift_exchange(row) for row in rows]

    def get_active_work_board(self) -> WorkBoard | None:
        with self._connection() as conn:
            row = conn.execute(
                """
                SELECT id, started_by_user_id, started_by_username, label, started_at, ended_at
                FROM work_boards
                WHERE ended_at IS NULL
                ORDER BY started_at DESC
                LIMIT 1
                """
            ).fetchone()
        return self._row_to_work_board(row) if row else None

    def start_work_board(
        self,
        *,
        started_by_user_id: int,
        started_by_username: str,
        seats: list[str],
        started_at: datetime,
        label: str = "",
    ) -> WorkBoard:
        if self.get_active_work_board() is not None:
            raise ValueError("A work board is already active. End it with `/board end` first.")

        cleaned_seats: list[str] = []
        seen: set[str] = set()
        for seat in seats:
            name = seat.strip()
            if not name:
                continue
            key = name.casefold()
            if key in seen:
                continue
            seen.add(key)
            cleaned_seats.append(name)

        if not cleaned_seats:
            raise ValueError("No seats configured. Set BOARD_SEATS in the environment.")

        with self._connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO work_boards (
                    started_by_user_id,
                    started_by_username,
                    label,
                    started_at
                )
                VALUES (?, ?, ?, ?)
                """,
                (
                    started_by_user_id,
                    started_by_username,
                    label.strip(),
                    self._format_timestamp(started_at),
                ),
            )
            board_id = cursor.lastrowid
            conn.executemany(
                """
                INSERT INTO work_assignments (board_id, seat, user_id, username, notes, assigned_at)
                VALUES (?, ?, NULL, NULL, '', NULL)
                """,
                [(board_id, seat) for seat in cleaned_seats],
            )
            row = conn.execute(
                """
                SELECT id, started_by_user_id, started_by_username, label, started_at, ended_at
                FROM work_boards
                WHERE id = ?
                """,
                (board_id,),
            ).fetchone()

        if row is None:
            raise RuntimeError("Failed to create work board.")

        board = self._row_to_work_board(row)
        logger.info("Started work board %s with seats %s", board.id, cleaned_seats)
        return board

    def end_work_board(self, ended_at: datetime) -> WorkBoard:
        active = self.get_active_work_board()
        if active is None:
            raise ValueError("No active work board to end.")

        with self._connection() as conn:
            conn.execute(
                """
                UPDATE work_boards
                SET ended_at = ?
                WHERE id = ?
                """,
                (self._format_timestamp(ended_at), active.id),
            )
            row = conn.execute(
                """
                SELECT id, started_by_user_id, started_by_username, label, started_at, ended_at
                FROM work_boards
                WHERE id = ?
                """,
                (active.id,),
            ).fetchone()

        if row is None:
            raise RuntimeError("Failed to end work board.")

        board = self._row_to_work_board(row)
        logger.info("Ended work board %s", board.id)
        return board

    def list_work_assignments(self, board_id: int) -> list[WorkAssignment]:
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT id, board_id, seat, user_id, username, notes, assigned_at
                FROM work_assignments
                WHERE board_id = ?
                ORDER BY id ASC
                """,
                (board_id,),
            ).fetchall()
        return [self._row_to_work_assignment(row) for row in rows]

    def get_work_assignment(self, board_id: int, seat: str) -> WorkAssignment | None:
        with self._connection() as conn:
            rows = conn.execute(
                """
                SELECT id, board_id, seat, user_id, username, notes, assigned_at
                FROM work_assignments
                WHERE board_id = ?
                """,
                (board_id,),
            ).fetchall()

        target = seat.strip().casefold()
        for row in rows:
            if row["seat"].casefold() == target:
                return self._row_to_work_assignment(row)
        return None

    def assign_work_seat(
        self,
        *,
        board_id: int,
        seat: str,
        user_id: int,
        username: str,
        assigned_at: datetime,
        notes: str = "",
        overwrite: bool = False,
    ) -> WorkAssignment:
        assignment = self.get_work_assignment(board_id, seat)
        if assignment is None:
            raise ValueError(f"Unknown seat `{seat}`.")

        if assignment.user_id is not None and assignment.user_id != user_id and not overwrite:
            raise ValueError(
                f"`{assignment.seat}` is already claimed by {assignment.username}. "
                "Use `/reassign` to move it, or `/release` first."
            )

        with self._connection() as conn:
            conn.execute(
                """
                UPDATE work_assignments
                SET user_id = ?, username = ?, notes = ?, assigned_at = ?
                WHERE id = ?
                """,
                (
                    user_id,
                    username,
                    notes.strip(),
                    self._format_timestamp(assigned_at),
                    assignment.id,
                ),
            )
            row = conn.execute(
                """
                SELECT id, board_id, seat, user_id, username, notes, assigned_at
                FROM work_assignments
                WHERE id = ?
                """,
                (assignment.id,),
            ).fetchone()

        if row is None:
            raise RuntimeError("Failed to update work assignment.")

        updated = self._row_to_work_assignment(row)
        logger.info(
            "Assigned seat %s on board %s to user %s",
            updated.seat,
            board_id,
            user_id,
        )
        return updated

    def release_work_seat(self, *, board_id: int, seat: str) -> WorkAssignment:
        assignment = self.get_work_assignment(board_id, seat)
        if assignment is None:
            raise ValueError(f"Unknown seat `{seat}`.")
        if assignment.user_id is None:
            raise ValueError(f"`{assignment.seat}` is already unclaimed.")

        with self._connection() as conn:
            conn.execute(
                """
                UPDATE work_assignments
                SET user_id = NULL, username = NULL, notes = '', assigned_at = NULL
                WHERE id = ?
                """,
                (assignment.id,),
            )
            row = conn.execute(
                """
                SELECT id, board_id, seat, user_id, username, notes, assigned_at
                FROM work_assignments
                WHERE id = ?
                """,
                (assignment.id,),
            ).fetchone()

        if row is None:
            raise RuntimeError("Failed to release work assignment.")

        updated = self._row_to_work_assignment(row)
        logger.info("Released seat %s on board %s", updated.seat, board_id)
        return updated
