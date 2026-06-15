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

        await interaction.response.send_message(embed=embed)

    @bot.tree.command(name="status", description="Show all users currently logged in.")
    async def status(interaction: discord.Interaction) -> None:
        try:
            sessions = bot.database.list_active_sessions()
        except Exception:
            logger.exception("Failed to fetch active sessions")
            await interaction.response.send_message(
                embed=error_embed("Status Unavailable", "Something went wrong while loading attendance status."),
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="📋 Attendance Status",
            description="Currently logged-in users",
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
