"""Discord Attendance Bot — slash-command attendance tracking."""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime

import discord
from discord.ext import commands
from dotenv import load_dotenv

from config import default_database_path
from database import AttendanceDatabase
from time_utils import format_ist, to_ist, utc_now

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
DATABASE_PATH = str(default_database_path())

EMBED_COLOR_LOGIN = discord.Color.green()
EMBED_COLOR_LOGOUT = discord.Color.red()
EMBED_COLOR_STATUS = discord.Color.blurple()
EMBED_COLOR_ERROR = discord.Color.orange()
EMBED_COLOR_EXCHANGE = discord.Color.gold()


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


class AttendanceBot(commands.Bot):
    def __init__(self, database: AttendanceDatabase, guild_id: int | None) -> None:
        intents = discord.Intents.default()
        super().__init__(command_prefix=commands.when_mentioned, intents=intents)
        self.database = database
        self.guild_id = guild_id

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


def create_bot(database: AttendanceDatabase, guild_id: int | None) -> AttendanceBot:
    bot = AttendanceBot(database=database, guild_id=guild_id)

    @bot.tree.command(name="login", description="Record your login time and start an attendance session.")
    async def login(interaction: discord.Interaction) -> None:
        assert interaction.user is not None

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
                # Keep this compact to avoid embed limits.
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

    return bot


def validate_config() -> tuple[str, int | None]:
    if not DISCORD_TOKEN:
        raise RuntimeError("DISCORD_TOKEN is not set. Add it to your .env file or environment variables.")

    guild_id: int | None = None
    if DISCORD_GUILD_ID:
        try:
            guild_id = int(DISCORD_GUILD_ID)
        except ValueError as exc:
            raise RuntimeError("DISCORD_GUILD_ID must be a valid integer.") from exc

    return DISCORD_TOKEN, guild_id


def main() -> None:
    try:
        token, guild_id = validate_config()
    except RuntimeError as exc:
        logger.error("%s", exc)
        sys.exit(1)

    database = AttendanceDatabase(DATABASE_PATH)
    logger.info("Using database at %s", DATABASE_PATH)

    bot = create_bot(database=database, guild_id=guild_id)

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
