"""Discord Attendance Bot — slash-command attendance tracking."""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

from config import default_database_path, log_database_setup
from database import AttendanceDatabase, WorkAssignment, WorkBoard
from time_utils import format_ist, format_shift_window, parse_shift_window, to_ist, utc_now

load_dotenv()

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("attendance-bot")

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
DISCORD_GUILD_ID = os.getenv("DISCORD_GUILD_ID")
DISCORD_SHIFT_LOG_CHANNEL_ID = os.getenv("DISCORD_SHIFT_LOG_CHANNEL_ID")
DATABASE_PATH = str(default_database_path())
DEFAULT_BOARD_SEATS = ("Mantis", "Zendesk", "SalesIQ", "Escalations")

EMBED_COLOR_LOGIN = discord.Color.green()
EMBED_COLOR_LOGOUT = discord.Color.red()
EMBED_COLOR_STATUS = discord.Color.blurple()
EMBED_COLOR_ERROR = discord.Color.orange()
EMBED_COLOR_EXCHANGE = discord.Color.gold()
EMBED_COLOR_BOARD = discord.Color.teal()


def parse_board_seats(raw: str | None) -> list[str]:
    if not raw or not raw.strip():
        return list(DEFAULT_BOARD_SEATS)

    seats: list[str] = []
    seen: set[str] = set()
    for part in raw.split(","):
        name = part.strip()
        if not name:
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        seats.append(name)
    return seats or list(DEFAULT_BOARD_SEATS)


BOARD_SEATS = parse_board_seats(os.getenv("BOARD_SEATS"))


def format_duration(start: datetime, end: datetime) -> str:
    total_seconds = max(int((end - start).total_seconds()), 0)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    parts: list[str] = []
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    parts.append(f"{seconds}s")
    return " ".join(parts)


def error_embed(title: str, description: str) -> discord.Embed:
    return discord.Embed(title=title, description=description, color=EMBED_COLOR_ERROR)


def parse_optional_int(value: str | None, name: str) -> int | None:
    if not value:
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a valid integer.") from exc


def resolve_seat_name(seat: str, seats: list[str]) -> str | None:
    target = seat.strip().casefold()
    for configured in seats:
        if configured.casefold() == target:
            return configured
    return None


def format_assignment_line(assignment: WorkAssignment) -> str:
    line = f"<@{assignment.user_id}>"
    line += format_shift_window(assignment.shift_from, assignment.shift_to)
    if assignment.notes:
        line += f" — {assignment.notes}"
    return line


def format_assignment_mention(assignment: WorkAssignment, member: discord.Member) -> str:
    line = f"**{assignment.seat}** → {member.mention}"
    line += format_shift_window(assignment.shift_from, assignment.shift_to)
    return line


def build_board_embed(
    board: WorkBoard,
    assignments: list[WorkAssignment],
    *,
    title: str = "🗂️ Shift Work Board",
) -> discord.Embed:
    label = board.label.strip() or "Active shift"
    embed = discord.Embed(
        title=title,
        description=f"**{label}**\nStarted {format_ist(board.started_at)}",
        color=EMBED_COLOR_BOARD,
        timestamp=to_ist(utc_now()),
    )

    by_seat: dict[str, list[WorkAssignment]] = {}
    for assignment in assignments:
        by_seat.setdefault(assignment.seat, []).append(assignment)

    seat_order = list(board.seats) if board.seats else list(by_seat.keys())
    for seat in by_seat:
        if seat not in seat_order:
            seat_order.append(seat)

    lines: list[str] = []
    unclaimed = 0
    for seat in seat_order:
        people = by_seat.get(seat, [])
        if not people:
            lines.append(f"**{seat}** → _unclaimed_")
            unclaimed += 1
            continue
        owners: list[str] = []
        for person in people:
            owners.append(format_assignment_line(person))
        lines.append(f"**{seat}** → {', '.join(owners)}")

    embed.add_field(
        name="Assignments",
        value="\n".join(lines) if lines else "_No seats configured._",
        inline=False,
    )

    claimed_people = len(assignments)
    embed.add_field(name="Unclaimed seats", value=str(unclaimed), inline=True)
    embed.add_field(name="People assigned", value=str(claimed_people), inline=True)
    embed.set_footer(text=f"Board ID: {board.id} • /assign • /claim • /release • /board show")
    return embed


class AttendanceBot(commands.Bot):
    def __init__(
        self,
        database: AttendanceDatabase,
        guild_id: int | None,
        shift_log_channel_id: int | None,
        board_seats: list[str],
    ) -> None:
        intents = discord.Intents.default()
        super().__init__(command_prefix=commands.when_mentioned, intents=intents)
        self.database = database
        self.guild_id = guild_id
        self.shift_log_channel_id = shift_log_channel_id
        self.board_seats = board_seats

    async def setup_hook(self) -> None:
        guild = discord.Object(id=self.guild_id) if self.guild_id else None

        if guild is not None:
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            logger.info("Synced %s slash command(s) to guild %s", len(synced), self.guild_id)
        else:
            synced = await self.tree.sync()
            logger.info("Synced %s global slash command(s)", len(synced))

    async def on_ready(self) -> None:
        logger.info("Logged in as %s (%s)", self.user, self.user.id if self.user else "unknown")
        logger.info("Board seats: %s", ", ".join(self.board_seats))
        if self.shift_log_channel_id:
            logger.info("Shift log channel locked to %s", self.shift_log_channel_id)
        else:
            logger.warning(
                "DISCORD_SHIFT_LOG_CHANNEL_ID is not set. "
                "Attendance commands will post in any channel."
            )


def create_bot(
    database: AttendanceDatabase,
    guild_id: int | None,
    shift_log_channel_id: int | None,
    board_seats: list[str] | None = None,
) -> AttendanceBot:
    seats = board_seats or list(BOARD_SEATS)
    bot = AttendanceBot(
        database=database,
        guild_id=guild_id,
        shift_log_channel_id=shift_log_channel_id,
        board_seats=seats,
    )

    async def ensure_shift_log_channel(interaction: discord.Interaction) -> bool:
        """Return True if this channel is allowed to post attendance logs."""
        if bot.shift_log_channel_id is None:
            return True

        if interaction.channel_id == bot.shift_log_channel_id:
            return True

        await interaction.response.send_message(
            embed=error_embed(
                "Wrong Channel",
                (
                    "Attendance logs are only allowed in the shift log channel.\n"
                    f"Please use <#{bot.shift_log_channel_id}> for "
                    "`/login`, `/logout`, `/status`, `/exchange`, `/board`, "
                    "`/split`, `/assign`, `/claim`, `/release`, and `/reassign`."
                ),
            ),
            ephemeral=True,
        )
        return False

    async def seat_autocomplete(
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        try:
            query = current.strip().casefold()
            choices: list[app_commands.Choice[str]] = []
            for seat in bot.board_seats:
                if query and query not in seat.casefold():
                    continue
                choices.append(app_commands.Choice(name=seat, value=seat))
                if len(choices) >= 25:
                    break
            return choices
        except Exception:
            logger.exception("Seat autocomplete failed")
            return [
                app_commands.Choice(name=seat, value=seat)
                for seat in bot.board_seats[:25]
            ]

    def ensure_work_board(interaction: discord.Interaction) -> WorkBoard:
        assert interaction.user is not None
        return bot.database.get_or_create_active_work_board(
            started_by_user_id=interaction.user.id,
            started_by_username=str(interaction.user),
            seats=bot.board_seats,
            started_at=utc_now(),
        )

    def assign_member_to_seat(
        *,
        board_id: int,
        seat: str,
        member: discord.Member,
        notes: str = "",
        shift_from: str | None = None,
        shift_to: str | None = None,
    ) -> WorkAssignment:
        if member.bot:
            raise ValueError("You cannot assign a seat to a bot.")
        return bot.database.assign_work_seat(
            board_id=board_id,
            seat=seat,
            user_id=member.id,
            username=str(member),
            assigned_at=utc_now(),
            notes=notes,
            shift_from=shift_from,
            shift_to=shift_to,
        )

    @bot.tree.command(name="login", description="Record your login time and start an attendance session.")
    async def login(interaction: discord.Interaction) -> None:
        assert interaction.user is not None

        if not await ensure_shift_log_channel(interaction):
            return

        try:
            session = bot.database.start_session(
                user_id=interaction.user.id,
                username=str(interaction.user),
                login_at=utc_now(),
            )
        except ValueError as exc:
            await interaction.response.send_message(
                embed=error_embed("Already Logged In", str(exc)),
                ephemeral=True,
            )
            return
        except Exception:
            logger.exception("Failed to start session for user %s", interaction.user.id)
            await interaction.response.send_message(
                embed=error_embed("Login Failed", "Something went wrong while recording your login."),
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="✅ User Logged In",
            color=EMBED_COLOR_LOGIN,
            timestamp=to_ist(session.login_at),
        )
        embed.add_field(name="Username", value=session.username, inline=False)
        embed.add_field(name="Timestamp", value=format_ist(session.login_at), inline=False)

        await interaction.response.send_message(embed=embed)

    @bot.tree.command(name="logout", description="Record your logout time and close your attendance session.")
    async def logout(interaction: discord.Interaction) -> None:
        assert interaction.user is not None

        if not await ensure_shift_log_channel(interaction):
            return

        logout_at = utc_now()

        try:
            session = bot.database.end_session(user_id=interaction.user.id, logout_at=logout_at)
        except ValueError as exc:
            await interaction.response.send_message(
                embed=error_embed("Not Logged In", str(exc)),
                ephemeral=True,
            )
            return
        except Exception:
            logger.exception("Failed to end session for user %s", interaction.user.id)
            await interaction.response.send_message(
                embed=error_embed("Logout Failed", "Something went wrong while recording your logout."),
                ephemeral=True,
            )
            return

        duration = format_duration(session.login_at, logout_at)

        embed = discord.Embed(
            title="🔴 User Logged Out",
            color=EMBED_COLOR_LOGOUT,
            timestamp=to_ist(logout_at),
        )
        embed.add_field(name="Username", value=session.username, inline=False)
        embed.add_field(name="Login Time", value=format_ist(session.login_at), inline=False)
        embed.add_field(name="Logout Time", value=format_ist(logout_at), inline=False)
        embed.add_field(name="Total Duration", value=duration, inline=False)
        embed.add_field(
            name="⚠️ Reminder",
            value="Don't forget to set your **Zoho SalesIQ** status to **Busy**.",
            inline=False,
        )

        await interaction.response.send_message(embed=embed)

    @bot.tree.command(name="status", description="Show all users currently logged in.")
    async def status(interaction: discord.Interaction) -> None:
        if not await ensure_shift_log_channel(interaction):
            return

        try:
            sessions = bot.database.list_active_sessions()
            exchanges = bot.database.list_shift_exchanges(limit=5)
        except Exception:
            logger.exception("Failed to fetch active sessions")
            await interaction.response.send_message(
                embed=error_embed("Status Unavailable", "Something went wrong while loading attendance status."),
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="📋 Attendance Status",
            description="Currently logged-in users and recent shift exchanges",
            color=EMBED_COLOR_STATUS,
            timestamp=to_ist(utc_now()),
        )

        if not sessions:
            embed.add_field(
                name="No Active Sessions",
                value="Nobody is currently logged in.",
                inline=False,
            )
        else:
            for index, session in enumerate(sessions, start=1):
                embed.add_field(
                    name=f"{index}. {session.username}",
                    value=f"Login Time: {format_ist(session.login_at)}",
                    inline=False,
                )

        if exchanges:
            lines: list[str] = []
            for ex in exchanges:
                lines.append(
                    f"- **{ex.date}** • **{ex.shift}** • {ex.from_username} → {ex.to_username} (ID: {ex.id})"
                )
            embed.add_field(
                name="Recent Shift Exchanges",
                value="\n".join(lines),
                inline=False,
            )
        else:
            embed.add_field(
                name="Recent Shift Exchanges",
                value="No shift exchanges recorded yet.",
                inline=False,
            )

        await interaction.response.send_message(embed=embed)

    @bot.tree.command(
        name="exchange",
        description="Record a shift exchange with a teammate (records it + posts an embed).",
    )
    async def exchange(
        interaction: discord.Interaction,
        teammate: discord.Member,
        shift: str,
        date: str,
        notes: str = "",
    ) -> None:
        assert interaction.user is not None

        if not await ensure_shift_log_channel(interaction):
            return

        if teammate.bot:
            await interaction.response.send_message(
                embed=error_embed("Invalid Teammate", "You cannot exchange shifts with a bot."),
                ephemeral=True,
            )
            return

        if teammate.id == interaction.user.id:
            await interaction.response.send_message(
                embed=error_embed("Invalid Teammate", "You cannot request a shift exchange with yourself."),
                ephemeral=True,
            )
            return

        created_at = utc_now()

        try:
            exchange_row = bot.database.create_shift_exchange(
                from_user_id=interaction.user.id,
                from_username=str(interaction.user),
                to_user_id=teammate.id,
                to_username=str(teammate),
                shift=shift,
                date=date,
                notes=notes,
                created_at=created_at,
            )
        except ValueError as exc:
            await interaction.response.send_message(
                embed=error_embed("Exchange Not Saved", str(exc)),
                ephemeral=True,
            )
            return
        except Exception:
            logger.exception("Failed to create shift exchange for user %s", interaction.user.id)
            await interaction.response.send_message(
                embed=error_embed(
                    "Exchange Failed",
                    "Something went wrong while recording your shift exchange.",
                ),
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="🔁 Shift Exchanged",
            color=EMBED_COLOR_EXCHANGE,
            timestamp=to_ist(exchange_row.created_at),
        )
        embed.add_field(name="From", value=f"{interaction.user.mention} (`{exchange_row.from_username}`)", inline=False)
        embed.add_field(name="To", value=f"{teammate.mention} (`{exchange_row.to_username}`)", inline=False)
        embed.add_field(name="Shift", value=shift.strip(), inline=False)
        embed.add_field(name="Date", value=date.strip(), inline=False)
        if notes.strip():
            embed.add_field(name="Notes", value=notes.strip(), inline=False)
        embed.set_footer(text=f"Exchange ID: {exchange_row.id} • Status: {exchange_row.status}")

        await interaction.response.send_message(embed=embed)

    board_group = app_commands.Group(
        name="board",
        description="Shift work board — who owns Mantis, Zendesk, and other seats.",
    )

    @board_group.command(name="start", description="Optional: start a fresh board (not required — /assign and /claim work directly).")
    @app_commands.describe(label="Optional label, e.g. Night shift or 27 Aug evening")
    async def board_start(interaction: discord.Interaction, label: str = "") -> None:
        assert interaction.user is not None

        if not await ensure_shift_log_channel(interaction):
            return

        try:
            existing = bot.database.get_active_work_board()
            if existing is not None:
                await interaction.response.send_message(
                    embed=error_embed(
                        "Board Already Active",
                        "A board is already running. Use `/board end` to reset, or just `/assign` / `/claim`.",
                    ),
                    ephemeral=True,
                )
                return
            board = bot.database.start_work_board(
                started_by_user_id=interaction.user.id,
                started_by_username=str(interaction.user),
                seats=bot.board_seats,
                started_at=utc_now(),
                label=label,
            )
            assignments = bot.database.list_work_assignments(board.id)
        except ValueError as exc:
            await interaction.response.send_message(
                embed=error_embed("Board Start Failed", str(exc)),
                ephemeral=True,
            )
            return
        except Exception:
            logger.exception("Failed to start work board for user %s", interaction.user.id)
            await interaction.response.send_message(
                embed=error_embed("Board Start Failed", "Something went wrong while starting the board."),
                ephemeral=True,
            )
            return

        embed = build_board_embed(board, assignments, title="🗂️ Shift Work Board Started")
        embed.add_field(
            name="How to use",
            value=(
                "`/assign seat1:Mantis user1:@A seat2:Mantis user2:@B seat3:Zendesk user3:@C`\n"
                "`/claim seat:Mantis` · `/release seat:Mantis` · `/board show` · `/board end`"
            ),
            inline=False,
        )
        await interaction.response.send_message(embed=embed)

    @board_group.command(name="show", description="Show who is on each seat right now.")
    async def board_show(interaction: discord.Interaction) -> None:
        if not await ensure_shift_log_channel(interaction):
            return

        try:
            board = ensure_work_board(interaction)
            assignments = bot.database.list_work_assignments(board.id)
        except Exception:
            logger.exception("Failed to load work board")
            await interaction.response.send_message(
                embed=error_embed("Board Unavailable", "Something went wrong while loading the board."),
                ephemeral=True,
            )
            return

        await interaction.response.send_message(embed=build_board_embed(board, assignments))

    @board_group.command(name="end", description="End the current shift work board.")
    async def board_end(interaction: discord.Interaction) -> None:
        if not await ensure_shift_log_channel(interaction):
            return

        try:
            active = bot.database.get_active_work_board()
            if active is None:
                await interaction.response.send_message(
                    embed=error_embed("No Active Board", "There is no active work board to end."),
                    ephemeral=True,
                )
                return
            assignments = bot.database.list_work_assignments(active.id)
            board = bot.database.end_work_board(ended_at=utc_now())
        except ValueError as exc:
            await interaction.response.send_message(
                embed=error_embed("Board End Failed", str(exc)),
                ephemeral=True,
            )
            return
        except Exception:
            logger.exception("Failed to end work board")
            await interaction.response.send_message(
                embed=error_embed("Board End Failed", "Something went wrong while ending the board."),
                ephemeral=True,
            )
            return

        embed = build_board_embed(board, assignments, title="✅ Shift Work Board Ended")
        embed.color = EMBED_COLOR_LOGOUT
        embed.set_footer(text=f"Board ID: {board.id} • Ended {format_ist(board.ended_at or utc_now())}")
        await interaction.response.send_message(embed=embed)

    bot.tree.add_command(board_group)

    @bot.tree.command(
        name="split",
        description="Enter who should work each seat. Leave blank seats for others to /claim.",
    )
    @app_commands.describe(
        mantis="Who should work Mantis",
        zendesk="Who should work Zendesk",
        salesiq="Who should work SalesIQ",
        escalations="Who should work Escalations",
    )
    async def split(
        interaction: discord.Interaction,
        mantis: discord.Member | None = None,
        zendesk: discord.Member | None = None,
        salesiq: discord.Member | None = None,
        escalations: discord.Member | None = None,
    ) -> None:
        assert interaction.user is not None

        if not await ensure_shift_log_channel(interaction):
            return

        planned: list[tuple[str, discord.Member]] = []
        for seat_name, member in (
            ("Mantis", mantis),
            ("Zendesk", zendesk),
            ("SalesIQ", salesiq),
            ("Escalations", escalations),
        ):
            if member is None:
                continue
            resolved = resolve_seat_name(seat_name, bot.board_seats)
            if resolved is None:
                continue
            planned.append((resolved, member))

        if not planned:
            await interaction.response.send_message(
                embed=error_embed(
                    "Nothing To Set",
                    "Pick at least one person, e.g. `/split mantis:@you zendesk:@teammate`.\n"
                    "Leave seats blank so others can `/claim` them.",
                ),
                ephemeral=True,
            )
            return

        try:
            board = ensure_work_board(interaction)
            mentions: list[str] = []
            for seat_name, member in planned:
                assign_member_to_seat(
                    board_id=board.id,
                    seat=seat_name,
                    member=member,
                )
                mentions.append(f"**{seat_name}** → {member.mention}")
            assignments = bot.database.list_work_assignments(board.id)
        except ValueError as exc:
            await interaction.response.send_message(
                embed=error_embed("Split Failed", str(exc)),
                ephemeral=True,
            )
            return
        except Exception:
            logger.exception("Failed to split work board for user %s", interaction.user.id)
            await interaction.response.send_message(
                embed=error_embed("Split Failed", "Something went wrong while setting the board."),
                ephemeral=True,
            )
            return

        embed = build_board_embed(board, assignments, title="🗂️ Shift Split Updated")
        await interaction.response.send_message(
            content="Work split:\n" + "\n".join(mentions),
            embed=embed,
        )

    @bot.tree.command(
        name="assign",
        description="Assign seats to people with optional time slots (IST).",
    )
    @app_commands.describe(
        seat1="First seat — Mantis, Zendesk, SalesIQ, or Escalations",
        user1="Person for seat 1",
        seat2="Second seat (optional, can match seat1)",
        user2="Person for seat 2 (optional)",
        seat3="Third seat (optional, can match seat1/seat2)",
        user3="Person for seat 3 (optional)",
        from1="Start time for seat 1, e.g. 09:00 (optional)",
        to1="End time for seat 1, e.g. 14:00 (optional)",
        from2="Start time for seat 2 (optional)",
        to2="End time for seat 2 (optional)",
        from3="Start time for seat 3 (optional)",
        to3="End time for seat 3 (optional)",
        notes="Optional note applied to all assignments in this command",
    )
    async def assign(
        interaction: discord.Interaction,
        seat1: str,
        user1: discord.Member,
        seat2: str = "",
        user2: discord.Member | None = None,
        seat3: str = "",
        user3: discord.Member | None = None,
        from1: str = "",
        to1: str = "",
        from2: str = "",
        to2: str = "",
        from3: str = "",
        to3: str = "",
        notes: str = "",
    ) -> None:
        await _assign_seat_command(
            interaction,
            entries=[
                (seat1, user1, from1, to1),
                (seat2, user2, from2, to2),
                (seat3, user3, from3, to3),
            ],
            notes=notes,
        )

    async def _assign_seat_command(
        interaction: discord.Interaction,
        *,
        entries: list[tuple[str | None, discord.Member | None, str, str]],
        notes: str = "",
    ) -> None:
        assert interaction.user is not None

        if not await ensure_shift_log_channel(interaction):
            return

        planned: list[tuple[str, discord.Member, str | None, str | None]] = []
        seen_pairs: set[tuple[str, int]] = set()

        for index, (seat_raw, member, from_raw, to_raw) in enumerate(entries, start=1):
            seat_text = (seat_raw or "").strip()
            if not seat_text and member is None:
                continue
            if seat_text and member is None:
                await interaction.response.send_message(
                    embed=error_embed(
                        "Missing Person",
                        f"You set `seat{index}` but not `user{index}`. Pick who should work that seat.",
                    ),
                    ephemeral=True,
                )
                return
            if member is not None and not seat_text:
                await interaction.response.send_message(
                    embed=error_embed(
                        "Missing Seat",
                        f"You set `user{index}` but not `seat{index}`. Pick which seat they should work.",
                    ),
                    ephemeral=True,
                )
                return

            assert member is not None
            resolved = resolve_seat_name(seat_text, bot.board_seats)
            if resolved is None:
                await interaction.response.send_message(
                    embed=error_embed(
                        "Unknown Seat",
                        f"`{seat_text}` is not a configured seat.\nSeats: {', '.join(bot.board_seats)}",
                    ),
                    ephemeral=True,
                )
                return

            try:
                shift_from, shift_to = parse_shift_window(from_raw, to_raw)
            except ValueError as exc:
                await interaction.response.send_message(
                    embed=error_embed("Invalid Time", str(exc)),
                    ephemeral=True,
                )
                return

            pair_key = (resolved.casefold(), member.id)
            if pair_key in seen_pairs:
                await interaction.response.send_message(
                    embed=error_embed(
                        "Duplicate Assignment",
                        f"{member.mention} is listed twice for **{resolved}** in this command.",
                    ),
                    ephemeral=True,
                )
                return
            seen_pairs.add(pair_key)
            planned.append((resolved, member, shift_from, shift_to))

        if not planned:
            await interaction.response.send_message(
                embed=error_embed(
                    "Nothing To Assign",
                    "Example: `/assign seat1:Mantis user1:@A seat2:Mantis user2:@B seat3:Zendesk user3:@C`",
                    "Seats: type `Mantis`, `Zendesk`, `SalesIQ`, or `Escalations`. Times go in from1/to1, from2/to2, from3/to3.",
                ),
                ephemeral=True,
            )
            return

        try:
            board = ensure_work_board(interaction)
            mentions: list[str] = []
            for seat_name, member, shift_from, shift_to in planned:
                assignment = assign_member_to_seat(
                    board_id=board.id,
                    seat=seat_name,
                    member=member,
                    notes=notes,
                    shift_from=shift_from,
                    shift_to=shift_to,
                )
                mentions.append(format_assignment_mention(assignment, member))
            assignments = bot.database.list_work_assignments(board.id)
        except ValueError as exc:
            await interaction.response.send_message(
                embed=error_embed("Assign Failed", str(exc)),
                ephemeral=True,
            )
            return
        except Exception:
            logger.exception("Failed to assign seats for user %s", interaction.user.id)
            await interaction.response.send_message(
                embed=error_embed("Assign Failed", "Something went wrong while assigning seats."),
                ephemeral=True,
            )
            return

        embed = build_board_embed(board, assignments, title="🗂️ Assignments Updated")
        await interaction.response.send_message(
            content="Assigned:\n" + "\n".join(mentions),
            embed=embed,
        )

    @bot.tree.command(name="claim", description="Join a seat with an optional time slot (IST).")
    @app_commands.describe(
        seat="Seat to claim, e.g. Mantis or Zendesk",
        from_time="Start time, e.g. 09:00 (optional)",
        to_time="End time, e.g. 14:00 (optional)",
        notes="Optional note",
    )
    @app_commands.autocomplete(seat=seat_autocomplete)
    async def claim(
        interaction: discord.Interaction,
        seat: str,
        from_time: str = "",
        to_time: str = "",
        notes: str = "",
    ) -> None:
        assert interaction.user is not None

        if not await ensure_shift_log_channel(interaction):
            return

        resolved = resolve_seat_name(seat, bot.board_seats)
        if resolved is None:
            await interaction.response.send_message(
                embed=error_embed(
                    "Unknown Seat",
                    f"`{seat}` is not a configured seat.\nSeats: {', '.join(bot.board_seats)}",
                ),
                ephemeral=True,
            )
            return

        try:
            shift_from, shift_to = parse_shift_window(from_time, to_time)
        except ValueError as exc:
            await interaction.response.send_message(
                embed=error_embed("Invalid Time", str(exc)),
                ephemeral=True,
            )
            return

        try:
            board = ensure_work_board(interaction)
            assignment = bot.database.assign_work_seat(
                board_id=board.id,
                seat=resolved,
                user_id=interaction.user.id,
                username=str(interaction.user),
                assigned_at=utc_now(),
                notes=notes,
                shift_from=shift_from,
                shift_to=shift_to,
            )
            assignments = bot.database.list_work_assignments(board.id)
        except ValueError as exc:
            await interaction.response.send_message(
                embed=error_embed("Claim Failed", str(exc)),
                ephemeral=True,
            )
            return
        except Exception:
            logger.exception("Failed to claim seat for user %s", interaction.user.id)
            await interaction.response.send_message(
                embed=error_embed("Claim Failed", "Something went wrong while claiming the seat."),
                ephemeral=True,
            )
            return

        embed = build_board_embed(board, assignments)
        status = f"{interaction.user.mention} joined **{resolved}**"
        status += format_shift_window(assignment.shift_from, assignment.shift_to)
        await interaction.response.send_message(content=status + ".", embed=embed)

    @bot.tree.command(name="release", description="Remove yourself (or someone) from a seat.")
    @app_commands.describe(
        seat="Seat to leave, e.g. Mantis",
        user="Optional: remove this person instead of yourself",
    )
    @app_commands.autocomplete(seat=seat_autocomplete)
    async def release(
        interaction: discord.Interaction,
        seat: str,
        user: discord.Member | None = None,
    ) -> None:
        assert interaction.user is not None

        if not await ensure_shift_log_channel(interaction):
            return

        resolved = resolve_seat_name(seat, bot.board_seats)
        if resolved is None:
            await interaction.response.send_message(
                embed=error_embed(
                    "Unknown Seat",
                    f"`{seat}` is not a configured seat.\nSeats: {', '.join(bot.board_seats)}",
                ),
                ephemeral=True,
            )
            return

        target = user or interaction.user
        if target.bot:
            await interaction.response.send_message(
                embed=error_embed("Invalid User", "Bots cannot be on the work board."),
                ephemeral=True,
            )
            return

        try:
            board = ensure_work_board(interaction)
            bot.database.release_work_seat(
                board_id=board.id,
                seat=resolved,
                user_id=target.id,
            )
            assignments = bot.database.list_work_assignments(board.id)
        except ValueError as exc:
            await interaction.response.send_message(
                embed=error_embed("Release Failed", str(exc)),
                ephemeral=True,
            )
            return
        except Exception:
            logger.exception("Failed to release seat for user %s", interaction.user.id)
            await interaction.response.send_message(
                embed=error_embed("Release Failed", "Something went wrong while releasing the seat."),
                ephemeral=True,
            )
            return

        embed = build_board_embed(board, assignments)
        remaining = bot.database.list_seat_assignments(board.id, resolved)
        if remaining:
            status = f"{target.mention} left **{resolved}**."
        else:
            status = f"{target.mention} left **{resolved}** — seat is now unclaimed."
        await interaction.response.send_message(content=status, embed=embed)

    @bot.tree.command(
        name="reassign",
        description="Add someone to a seat (same as one /assign pair).",
    )
    @app_commands.describe(
        seat="Seat to assign, e.g. Zendesk",
        user="Teammate to add on this seat",
        notes="Optional note",
    )
    @app_commands.autocomplete(seat=seat_autocomplete)
    async def reassign(
        interaction: discord.Interaction,
        seat: str,
        user: discord.Member,
        notes: str = "",
    ) -> None:
        await _assign_seat_command(
            interaction,
            entries=[(seat, user, "", "")],
            notes=notes,
        )

    return bot


def validate_config() -> tuple[str, int | None, int | None]:
    if not DISCORD_TOKEN:
        raise RuntimeError("DISCORD_TOKEN is not set. Add it to your .env file or environment variables.")

    guild_id = parse_optional_int(DISCORD_GUILD_ID, "DISCORD_GUILD_ID")
    shift_log_channel_id = parse_optional_int(
        DISCORD_SHIFT_LOG_CHANNEL_ID,
        "DISCORD_SHIFT_LOG_CHANNEL_ID",
    )

    return DISCORD_TOKEN, guild_id, shift_log_channel_id


def main() -> None:
    try:
        token, guild_id, shift_log_channel_id = validate_config()
    except RuntimeError as exc:
        logger.error("%s", exc)
        sys.exit(1)

    database = AttendanceDatabase(DATABASE_PATH)
    log_database_setup(Path(DATABASE_PATH))

    active_count = len(database.list_active_sessions())
    logger.info("Active sessions in database: %s", active_count)
    logger.info("Configured board seats: %s", ", ".join(BOARD_SEATS))

    bot = create_bot(
        database=database,
        guild_id=guild_id,
        shift_log_channel_id=shift_log_channel_id,
        board_seats=BOARD_SEATS,
    )

    try:
        bot.run(token, log_handler=None)
    except discord.LoginFailure:
        logger.error("Invalid Discord token. Check DISCORD_TOKEN and try again.")
        sys.exit(1)
    except KeyboardInterrupt:
        logger.info("Bot stopped by user.")
    except Exception:
        logger.exception("Bot crashed unexpectedly")
        sys.exit(1)


if __name__ == "__main__":
    main()
