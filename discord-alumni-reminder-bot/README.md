# Discord Alumni Reminder Bot

A simple Discord bot for alumni association meeting reminders.

The bot lets admins add scheduled meetings and automatically pings the configured alumni role 7 days before and 1 hour before each meeting. Server members can add agenda items, and reminder messages include the current agenda when one exists.

## Features

- Guild-specific Discord slash commands
- SQLite persistence in `alumni_bot.db`
- Meeting reminders checked every 60 seconds
- Restart-safe reminder tracking
- Role pings with restricted allowed mentions
- Agenda item collection
- Local `.env` configuration

## Commands

Admin-only commands require the Discord **Manage Server** permission.

| Command | Who can use it | Description |
| --- | --- | --- |
| `/meeting_add title start_time` | Admins | Adds a meeting. `start_time` uses `YYYY-MM-DD HH:MM` in the configured timezone. |
| `/meeting_list` | Admins | Lists active upcoming meetings. |
| `/meeting_remove meeting_id` | Admins | Soft-removes a meeting. |
| `/agenda_add meeting_id item` | Any server member | Adds an agenda item to an active future meeting. |
| `/agenda_list meeting_id` | Any server member | Lists agenda items for a meeting. |
| `/agenda_remove agenda_item_id` | Admins | Soft-removes an agenda item. |

Example:

```text
/meeting_add title:"Alumni Association Monthly Meeting" start_time:"2026-07-01 19:00"
/agenda_add meeting_id:1 item:"Discuss spring alumni event planning"
/agenda_list meeting_id:1
```

## Create the Discord Bot

1. Go to the [Discord Developer Portal](https://discord.com/developers/applications).
2. Select **New Application**.
3. Give the application a name.
4. Open **Bot** in the left sidebar.
5. Select **Add Bot**.
6. Copy the bot token and place it in your local `.env` file as `DISCORD_TOKEN`.

Keep the token private. Do not commit your real `.env` file.

## Required Bot Permissions

The bot only needs these permissions:

- Send Messages
- Use Slash Commands
- Read Message History
- Mention Roles

It does not need Administrator permission.

## Invite the Bot

1. In the Discord Developer Portal, open your application.
2. Go to **OAuth2**.
3. Open **URL Generator**.
4. Under **Scopes**, select:
   - `bot`
   - `applications.commands`
5. Under **Bot Permissions**, select:
   - Send Messages
   - Use Slash Commands
   - Read Message History
   - Mention Roles
6. Open the generated URL and invite the bot to your server.

## Copy Discord IDs

Enable Developer Mode:

1. Open Discord.
2. Go to **User Settings**.
3. Open **Advanced**.
4. Enable **Developer Mode**.

Copy IDs:

- Server ID: right-click the server name and choose **Copy Server ID**.
- Channel ID: right-click the announcement channel and choose **Copy Channel ID**.
- Role ID: go to server settings roles, right-click the alumni role, and choose **Copy Role ID**.

## Configure Environment

Copy `.env.example` to `.env`:

```text
DISCORD_TOKEN=
GUILD_ID=
ANNOUNCEMENT_CHANNEL_ID=
ALUMNI_ROLE_ID=
TIMEZONE=America/New_York
```

Field meanings:

- `DISCORD_TOKEN`: Discord bot token.
- `GUILD_ID`: Discord server ID.
- `ANNOUNCEMENT_CHANNEL_ID`: Channel where reminders should be posted.
- `ALUMNI_ROLE_ID`: Role ID for the alumni role.
- `TIMEZONE`: Timezone for meeting input, usually `America/New_York`.

## Install Locally

Create a virtual environment:

```bash
python -m venv .venv
```

Activate on Windows:

```powershell
.venv\Scripts\activate
```

Activate on Mac/Linux:

```bash
source .venv/bin/activate
```

Install requirements:

```bash
pip install -r requirements.txt
```

Run the bot:

```bash
python bot.py
```

The bot creates `alumni_bot.db` automatically the first time it starts.

## Time Format

Use this format for meeting start times:

```text
YYYY-MM-DD HH:MM
```

The bot interprets this time in the configured `TIMEZONE`, stores it internally as UTC, and displays it with Discord timestamp formatting so each user sees the meeting time in their own local timezone.

## Reminder Behavior

The bot checks once per minute.

- It sends a 7-day reminder when the meeting is 7 days away or less.
- It sends a 1-hour reminder when the meeting is 1 hour away or less.
- It marks each reminder as sent in SQLite.
- Restarting the bot does not resend reminders that were already marked sent.
- If the bot was offline at the exact reminder time, it sends the reminder after it comes back online as long as the meeting has not already started.
- It does not send reminders for meetings that have already started.

## Agenda Behavior

Any server member can add agenda items with `/agenda_add`.

Validation rules:

- The meeting must exist.
- The meeting must be active.
- The meeting must not have started.
- Agenda items must not be empty.
- Agenda items must be 500 characters or fewer.

Admins can remove agenda items with `/agenda_remove`. Removed items are marked inactive in SQLite rather than deleted.

## Hosting Notes

The bot only works while the Python process is running. If you run it on a laptop, reminders stop when the laptop sleeps, shuts down, loses internet, or the script closes.

For always-on use, host it later on a service such as Railway, a paid Render instance, Fly.io, or a cheap VPS.

You do not need Docker for the MVP.
