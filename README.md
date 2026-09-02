# Discord Attendance Bot

A production-ready Discord bot for tracking user login and logout times with slash commands, SQLite persistence, and embed-based responses.

## Features

- `/login` — start an attendance session and post a login embed
- `/logout` — end your session, calculate duration, and post a logout embed
- `/status` — list all currently logged-in users
- `/exchange` — record a shift handoff with a teammate
- `/board start|show|end` — shared shift work board (who owns which queue)
- `/assign` — assign up to 3 seats to 3 people in one command
- `/split` — set who works Mantis / Zendesk / etc. by seat name
- `/claim` / `/release` — pick up or free an open seat
- `/reassign` — move one seat to someone else
- SQLite storage with automatic schema creation
- Timezone-aware timestamps (stored in UTC, displayed in IST)
- Structured logging
- Guild-scoped slash command sync for fast development

## Project Structure

```
discord-attendance-bot/
├── bot.py
├── config.py
├── database.py
├── time_utils.py
├── requirements.txt
├── runtime.txt
├── nixpacks.toml
├── Procfile
├── railway.toml
├── .env.example
├── .gitignore
├── README.md
└── data/
    └── attendance.db   # created automatically at runtime
```

## Prerequisites

- Python 3.10 or newer
- A [Discord application](https://discord.com/developers/applications) with a bot token
- The bot invited to your server with the `applications.commands` scope

### Discord Developer Portal Setup

1. Create an application in the [Discord Developer Portal](https://discord.com/developers/applications).
2. Open **Bot** and create a bot user. Copy the token for `DISCORD_TOKEN`.
3. Open **OAuth2 → URL Generator**:
   - Scopes: `bot`, `applications.commands`
   - Bot permissions: `Send Messages`, `Embed Links`, `Read Message History`
4. Use the generated URL to invite the bot to your server.
5. Copy your server (guild) ID for `DISCORD_GUILD_ID`:
   - Enable Developer Mode in Discord (**User Settings → Advanced**)
   - Right-click your server → **Copy Server ID**

## Local Setup

### 1. Clone or download the project

```bash
cd discord-attendance-bot
```

### 2. Create a virtual environment

**Windows (PowerShell):**

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

**macOS / Linux:**

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment variables

```bash
cp .env.example .env
```

Edit `.env`:

```env
DISCORD_TOKEN=your_discord_bot_token_here
DISCORD_GUILD_ID=your_guild_id_here
```

`DISCORD_GUILD_ID` syncs slash commands to one server immediately during development. Remove it or leave it empty to sync commands globally (can take up to an hour to propagate).

### 5. Run the bot

```bash
python bot.py
```

You should see log output confirming the bot is online and slash commands are synced.

### 6. Test commands in Discord

In your server, type `/` and run:

- `/login`
- `/status`
- `/logout`
- `/board start`
- `/claim seat:Mantis`
- `/board show`
- `/board end`

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `DISCORD_TOKEN` | Yes | Discord bot token |
| `DISCORD_GUILD_ID` | Recommended | Guild ID for instant slash command sync |
| `DISCORD_SHIFT_LOG_CHANNEL_ID` | Recommended | Lock attendance + board commands to one channel |
| `BOARD_SEATS` | No | Comma-separated seats (default: `Mantis,Zendesk,SalesIQ,Escalations`) |
| `DATABASE_PATH` | No | SQLite file path (default: `data/attendance.db` locally, `/app/data/attendance.db` on Railway) |
| `LOG_LEVEL` | No | Logging level (default: `INFO`) |

### Shift work board

No `/board start` needed — just assign directly:

1. `/assign seat1:Mantis user1:@A from1:09:00 to1:14:00 seat2:Mantis user2:@B from2:14:00 to2:18:00`
2. `/claim seat:Mantis from_time:14:00 to_time:18:00` to join with your time slot
3. `/release seat:Mantis` to leave · `/release seat:Mantis user:@other` to remove someone
4. `/board show` to see current assignments · `/board end` to reset for a new shift

Times are **IST** (24-hour, e.g. `09:00`, `14:30`). `from`/`to` are optional.

All user-facing timestamps are shown in **IST** (Asia/Kolkata). Data is stored in UTC internally for consistency.

## Deploy on Railway

This project is ready to deploy on [Railway](https://railway.app).

### 1. Push to GitHub

```bash
git init
git add .
git commit -m "Initial Discord attendance bot"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/discord-attendance-bot.git
git push -u origin main
```

### 2. Create a Railway project

1. Go to [railway.app](https://railway.app) and create a new project.
2. Choose **Deploy from GitHub repo** and select this repository.
3. Railway detects Python automatically via Nixpacks.

### 3. Set environment variables

In Railway → your service → **Variables**, add:

| Variable | Value |
|----------|-------|
| `DISCORD_TOKEN` | Your Discord bot token |
| `DISCORD_GUILD_ID` | Your Discord server ID |

Optional:

| Variable | Value |
|----------|-------|
| `LOG_LEVEL` | `INFO` |
| `DATABASE_PATH` | `/app/data/attendance.db` |

### 4. Disable HTTP health check (important)

This bot is a long-running worker, not a web server. In Railway → your service → **Settings → Health Check**, disable the health check or leave the port unset so Railway does not expect HTTP responses.

The start command is already configured in `railway.toml`, `nixpacks.toml`, and `Procfile`:

```toml
startCommand = "python bot.py"
```

### 5. Persist SQLite data (recommended)

Railway containers use ephemeral filesystems by default. Attendance data is lost on redeploy unless you attach persistent storage.

1. In Railway, open your service.
2. Go to **Volumes**.
3. Add a volume mounted at `/app/data`.
4. Optionally set `DATABASE_PATH=/app/data/attendance.db` (this is the default when `RAILWAY_ENVIRONMENT` is detected).

### 6. Deploy

Railway builds and starts the bot automatically. Check **Deployments → Logs** for:

```
Synced 3 slash command(s) to guild ...
Logged in as YourBot#1234
```

## Error Handling

The bot handles common cases gracefully:

- Logging in while already logged in
- Logging out without an active session
- Database failures with user-friendly embed messages
- Invalid or missing environment variables at startup

## License

MIT
