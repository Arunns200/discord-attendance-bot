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
    seats: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class WorkAssignment:
    id: int
    board_id: int
    seat: str
    user_id: int
    username: str
    notes: str
    assigned_at: datetime | None
    shift_from: str | None = None
    shift_to: str | None = None


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
                    seats_csv TEXT NOT NULL DEFAULT '',
                    started_at TEXT NOT NULL,
                    ended_at TEXT
                )
                """
            )
            board_columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(work_boards)").fetchall()
            }
            if "seats_csv" not in board_columns:
                conn.execute(
                    "ALTER TABLE work_boards ADD COLUMN seats_csv TEXT NOT NULL DEFAULT ''"
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
                    user_id INTEGER NOT NULL,
                    username TEXT NOT NULL,
                    notes TEXT NOT NULL DEFAULT '',
                    assigned_at TEXT,
                    shift_from TEXT,
                    shift_to TEXT,
                    UNIQUE (board_id, seat, user_id),
                    FOREIGN KEY (board_id) REFERENCES work_boards (id)
                )
                """
            )
            self._migrate_work_assignments_multi_user(conn)
            assignment_columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(work_assignments)").fetchall()
            }
            if "shift_from" not in assignment_columns:
                conn.execute("ALTER TABLE work_assignments ADD COLUMN shift_from TEXT")
            if "shift_to" not in assignment_columns:
                conn.execute("ALTER TABLE work_assignments ADD COLUMN shift_to TEXT")
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_work_assignments_board
                ON work_assignments (board_id)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_work_assignments_board_seat
                ON work_assignments (board_id, seat)
                """
            )
        logger.info("Database ready at %s", self.db_path)

    def _migrate_work_assignments_multi_user(self, conn: sqlite3.Connection) -> None:
        """Allow many people on the same seat (drop old UNIQUE board_id+seat)."""
        table = conn.execute(
            """
            SELECT sql FROM sqlite_master
            WHERE type = 'table' AND name = 'work_assignments'
            """
        ).fetchone()
        if table is None or table["sql"] is None:
            return

        create_sql = table["sql"]
        has_multi_user_unique = (
            "UNIQUE (board_id, seat, user_id)" in create_sql
            or "UNIQUE(board_id, seat, user_id)" in create_sql
        )
        has_legacy_seat_unique = (
            not has_multi_user_unique
            and (
                "UNIQUE (board_id, seat)" in create_sql
                or "UNIQUE(board_id, seat)" in create_sql
            )
        )
        has_nullable_user = "user_id INTEGER," in create_sql  # legacy placeholders
        if has_multi_user_unique and not has_nullable_user:
            return
        if not has_legacy_seat_unique and not has_nullable_user:
            return

        logger.info("Migrating work_assignments to allow multiple people per seat")
        conn.execute("ALTER TABLE work_assignments RENAME TO work_assignments_legacy")
        conn.execute(
            """
            CREATE TABLE work_assignments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                board_id INTEGER NOT NULL,
                seat TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                username TEXT NOT NULL,
                notes TEXT NOT NULL DEFAULT '',
                assigned_at TEXT,
                shift_from TEXT,
                shift_to TEXT,
                UNIQUE (board_id, seat, user_id),
                FOREIGN KEY (board_id) REFERENCES work_boards (id)
            )
            """
        )
        conn.execute(
            """
            INSERT INTO work_assignments (
                board_id, seat, user_id, username, notes, assigned_at, shift_from, shift_to
            )
            SELECT board_id, seat, user_id, username, COALESCE(notes, ''), assigned_at, NULL, NULL
            FROM work_assignments_legacy
            WHERE user_id IS NOT NULL
            """
        )
        # Backfill seats_csv for boards that only had placeholder rows.
        boards = conn.execute("SELECT id, seats_csv FROM work_boards").fetchall()
        for board in boards:
            if board["seats_csv"]:
                continue
            seat_rows = conn.execute(
                """
                SELECT DISTINCT seat FROM work_assignments_legacy
                WHERE board_id = ?
                ORDER BY id ASC
                """,
                (board["id"],),
            ).fetchall()
            if not seat_rows:
                seat_rows = conn.execute(
                    """
                    SELECT DISTINCT seat FROM work_assignments
                    WHERE board_id = ?
                    ORDER BY id ASC
                    """,
                    (board["id"],),
                ).fetchall()
            seats_csv = ",".join(row["seat"] for row in seat_rows)
            conn.execute(
                "UPDATE work_boards SET seats_csv = ? WHERE id = ?",
                (seats_csv, board["id"]),
            )
        conn.execute("DROP TABLE work_assignments_legacy")

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
        seats_csv = ""
        if "seats_csv" in row.keys() and row["seats_csv"]:
            seats_csv = row["seats_csv"]
        seats = tuple(
            part.strip() for part in seats_csv.split(",") if part.strip()
        )
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
            seats=seats,
        )

    def _row_to_work_assignment(self, row: sqlite3.Row) -> WorkAssignment:
        shift_from = row["shift_from"] if "shift_from" in row.keys() else None
        shift_to = row["shift_to"] if "shift_to" in row.keys() else None
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
            shift_from=shift_from or None,
            shift_to=shift_to or None,
        )

    _WORK_ASSIGNMENT_COLUMNS = (
        "id, board_id, seat, user_id, username, notes, assigned_at, shift_from, shift_to"
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
                SELECT id, started_by_user_id, started_by_username, label, seats_csv, started_at, ended_at
                FROM work_boards
                WHERE ended_at IS NULL
                ORDER BY started_at DESC
                LIMIT 1
                """
            ).fetchone()
        return self._row_to_work_board(row) if row else None

    def resolve_board_seat(self, board: WorkBoard, seat: str) -> str | None:
        target = seat.strip().casefold()
        seats = board.seats
        if not seats:
            # Legacy boards: accept any seat already used, or fall through.
            assignments = self.list_work_assignments(board.id)
            seats = tuple(dict.fromkeys(a.seat for a in assignments))
        for configured in seats:
            if configured.casefold() == target:
                return configured
        return None

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

        seats_csv = ",".join(cleaned_seats)
        with self._connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO work_boards (
                    started_by_user_id,
                    started_by_username,
                    label,
                    seats_csv,
                    started_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    started_by_user_id,
                    started_by_username,
                    label.strip(),
                    seats_csv,
                    self._format_timestamp(started_at),
                ),
            )
            board_id = cursor.lastrowid
            row = conn.execute(
                """
                SELECT id, started_by_user_id, started_by_username, label, seats_csv, started_at, ended_at
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

    def get_or_create_active_work_board(
        self,
        *,
        started_by_user_id: int,
        started_by_username: str,
        seats: list[str],
        started_at: datetime,
        label: str = "",
    ) -> WorkBoard:
        existing = self.get_active_work_board()
        if existing is not None:
            return existing
        return self.start_work_board(
            started_by_user_id=started_by_user_id,
            started_by_username=started_by_username,
            seats=seats,
            started_at=started_at,
            label=label,
        )

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
                SELECT id, started_by_user_id, started_by_username, label, seats_csv, started_at, ended_at
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
                f"""
                SELECT {self._WORK_ASSIGNMENT_COLUMNS}
                FROM work_assignments
                WHERE board_id = ?
                ORDER BY shift_from ASC, id ASC
                """,
                (board_id,),
            ).fetchall()
        return [self._row_to_work_assignment(row) for row in rows]

    def get_work_assignment(
        self,
        board_id: int,
        seat: str,
        user_id: int,
    ) -> WorkAssignment | None:
        with self._connection() as conn:
            rows = conn.execute(
                f"""
                SELECT {self._WORK_ASSIGNMENT_COLUMNS}
                FROM work_assignments
                WHERE board_id = ? AND user_id = ?
                """,
                (board_id, user_id),
            ).fetchall()

        target = seat.strip().casefold()
        for row in rows:
            if row["seat"].casefold() == target:
                return self._row_to_work_assignment(row)
        return None

    def list_seat_assignments(self, board_id: int, seat: str) -> list[WorkAssignment]:
        target = seat.strip().casefold()
        return [
            assignment
            for assignment in self.list_work_assignments(board_id)
            if assignment.seat.casefold() == target
        ]

    def assign_work_seat(
        self,
        *,
        board_id: int,
        seat: str,
        user_id: int,
        username: str,
        assigned_at: datetime,
        notes: str = "",
        shift_from: str | None = None,
        shift_to: str | None = None,
    ) -> WorkAssignment:
        board = None
        with self._connection() as conn:
            board_row = conn.execute(
                """
                SELECT id, started_by_user_id, started_by_username, label, seats_csv, started_at, ended_at
                FROM work_boards
                WHERE id = ?
                """,
                (board_id,),
            ).fetchone()
        if board_row is None:
            raise ValueError("Work board not found.")
        board = self._row_to_work_board(board_row)

        resolved = self.resolve_board_seat(board, seat)
        if resolved is None:
            available = ", ".join(board.seats) if board.seats else "(none)"
            raise ValueError(f"Unknown seat `{seat}`. Seats on this board: {available}")

        existing = self.get_work_assignment(board_id, resolved, user_id)

        with self._connection() as conn:
            if existing is not None:
                conn.execute(
                    """
                    UPDATE work_assignments
                    SET username = ?, notes = ?, assigned_at = ?, shift_from = ?, shift_to = ?
                    WHERE id = ?
                    """,
                    (
                        username,
                        notes.strip(),
                        self._format_timestamp(assigned_at),
                        shift_from,
                        shift_to,
                        existing.id,
                    ),
                )
                assignment_id = existing.id
            else:
                cursor = conn.execute(
                    """
                    INSERT INTO work_assignments (
                        board_id, seat, user_id, username, notes, assigned_at, shift_from, shift_to
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        board_id,
                        resolved,
                        user_id,
                        username,
                        notes.strip(),
                        self._format_timestamp(assigned_at),
                        shift_from,
                        shift_to,
                    ),
                )
                assignment_id = cursor.lastrowid

            row = conn.execute(
                f"""
                SELECT {self._WORK_ASSIGNMENT_COLUMNS}
                FROM work_assignments
                WHERE id = ?
                """,
                (assignment_id,),
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

    def release_work_seat(
        self,
        *,
        board_id: int,
        seat: str,
        user_id: int,
    ) -> WorkAssignment:
        assignment = self.get_work_assignment(board_id, seat, user_id)
        if assignment is None:
            raise ValueError(f"That person is not assigned to `{seat}`.")

        with self._connection() as conn:
            conn.execute("DELETE FROM work_assignments WHERE id = ?", (assignment.id,))

        logger.info(
            "Released seat %s on board %s for user %s",
            assignment.seat,
            board_id,
            user_id,
        )
        return assignment
