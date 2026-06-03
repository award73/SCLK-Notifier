import logging
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv


DB_PATH = Path(__file__).with_name("alumni_bot.db")
DATETIME_INPUT_FORMAT = "%Y-%m-%d %H:%M"
AGENDA_ITEM_LIMIT = 500

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("alumni_reminder_bot")


@dataclass(frozen=True)
class Config:
    discord_token: str
    guild_id: int
    announcement_channel_id: int
    alumni_role_id: int
    timezone_name: str
    timezone: ZoneInfo


def load_config() -> Config:
    load_dotenv()

    required_vars = [
        "DISCORD_TOKEN",
        "GUILD_ID",
        "ANNOUNCEMENT_CHANNEL_ID",
        "ALUMNI_ROLE_ID",
        "TIMEZONE",
    ]
    missing = [name for name in required_vars if not os.getenv(name)]
    if missing:
        raise ValueError(f"Missing required .env variables: {', '.join(missing)}")

    try:
        timezone_name = os.environ["TIMEZONE"]
        bot_timezone = ZoneInfo(timezone_name)
        return Config(
            discord_token=os.environ["DISCORD_TOKEN"],
            guild_id=int(os.environ["GUILD_ID"]),
            announcement_channel_id=int(os.environ["ANNOUNCEMENT_CHANNEL_ID"]),
            alumni_role_id=int(os.environ["ALUMNI_ROLE_ID"]),
            timezone_name=timezone_name,
            timezone=bot_timezone,
        )
    except ValueError as exc:
        raise ValueError("GUILD_ID, ANNOUNCEMENT_CHANNEL_ID, and ALUMNI_ROLE_ID must be numeric IDs.") from exc
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Invalid TIMEZONE value: {os.environ['TIMEZONE']}") from exc


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with get_db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS meetings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                start_time_utc TEXT NOT NULL,
                channel_id TEXT NOT NULL,
                role_id TEXT NOT NULL,
                seven_day_sent INTEGER NOT NULL DEFAULT 0,
                one_hour_sent INTEGER NOT NULL DEFAULT 0,
                active INTEGER NOT NULL DEFAULT 1,
                created_by TEXT,
                created_at_utc TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS agenda_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                meeting_id INTEGER NOT NULL,
                item_text TEXT NOT NULL,
                submitted_by_user_id TEXT NOT NULL,
                submitted_by_display_name TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                created_at_utc TEXT NOT NULL,
                FOREIGN KEY (meeting_id) REFERENCES meetings(id)
            )
            """
        )


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def datetime_to_db(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def datetime_from_db(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_eastern_datetime(value: str, bot_timezone: ZoneInfo) -> datetime:
    parsed = datetime.strptime(value, DATETIME_INPUT_FORMAT)
    return parsed.replace(tzinfo=bot_timezone).astimezone(timezone.utc)


def to_discord_timestamp(value: datetime, style: str = "F") -> str:
    unix_timestamp = int(value.timestamp())
    return f"<t:{unix_timestamp}:{style}>"


def add_meeting(
    title: str,
    start_time_utc: datetime,
    channel_id: int,
    role_id: int,
    created_by: int,
) -> int:
    with get_db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO meetings (
                title, start_time_utc, channel_id, role_id, created_by, created_at_utc
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                title.strip(),
                datetime_to_db(start_time_utc),
                str(channel_id),
                str(role_id),
                str(created_by),
                datetime_to_db(utc_now()),
            ),
        )
        return int(cursor.lastrowid)


def get_active_meeting(meeting_id: int) -> Optional[sqlite3.Row]:
    with get_db() as conn:
        return conn.execute(
            "SELECT * FROM meetings WHERE id = ? AND active = 1",
            (meeting_id,),
        ).fetchone()


def list_meetings() -> list[sqlite3.Row]:
    with get_db() as conn:
        return conn.execute(
            """
            SELECT * FROM meetings
            WHERE active = 1 AND start_time_utc > ?
            ORDER BY start_time_utc ASC
            """,
            (datetime_to_db(utc_now()),),
        ).fetchall()


def remove_meeting(meeting_id: int) -> bool:
    with get_db() as conn:
        cursor = conn.execute(
            "UPDATE meetings SET active = 0 WHERE id = ? AND active = 1",
            (meeting_id,),
        )
        return cursor.rowcount > 0


def add_agenda_item(
    meeting_id: int,
    item_text: str,
    submitted_by_user_id: int,
    submitted_by_display_name: str,
) -> int:
    with get_db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO agenda_items (
                meeting_id, item_text, submitted_by_user_id, submitted_by_display_name, created_at_utc
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                meeting_id,
                item_text.strip(),
                str(submitted_by_user_id),
                submitted_by_display_name,
                datetime_to_db(utc_now()),
            ),
        )
        return int(cursor.lastrowid)


def list_agenda_items(meeting_id: int) -> list[sqlite3.Row]:
    with get_db() as conn:
        return conn.execute(
            """
            SELECT * FROM agenda_items
            WHERE meeting_id = ? AND active = 1
            ORDER BY created_at_utc ASC
            """,
            (meeting_id,),
        ).fetchall()


def remove_agenda_item(agenda_item_id: int) -> bool:
    with get_db() as conn:
        cursor = conn.execute(
            "UPDATE agenda_items SET active = 0 WHERE id = ? AND active = 1",
            (agenda_item_id,),
        )
        return cursor.rowcount > 0


def mark_reminder_sent(meeting_id: int, reminder_column: str) -> None:
    if reminder_column not in {"seven_day_sent", "one_hour_sent"}:
        raise ValueError("Invalid reminder column.")

    with get_db() as conn:
        conn.execute(
            f"UPDATE meetings SET {reminder_column} = 1 WHERE id = ?",
            (meeting_id,),
        )


def format_agenda_for_reminder(meeting_id: int) -> str:
    items = list_agenda_items(meeting_id)
    if not items:
        return ""

    lines = [f"{index}. {row['item_text']}" for index, row in enumerate(items, start=1)]
    return "\n".join(lines)


def user_can_manage_guild(interaction: discord.Interaction) -> bool:
    permissions = getattr(interaction.user, "guild_permissions", None)
    return bool(permissions and permissions.manage_guild)


async def require_manage_guild(interaction: discord.Interaction) -> bool:
    if user_can_manage_guild(interaction):
        return True

    await interaction.response.send_message(
        "You need the Manage Server permission to use this command.",
        ephemeral=True,
    )
    return False


class AlumniReminderBot(commands.Bot):
    def __init__(self, config: Config):
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents)
        self.config = config
        self.guild_object = discord.Object(id=config.guild_id)

    async def setup_hook(self) -> None:
        self.tree.copy_global_to(guild=self.guild_object)
        synced = await self.tree.sync(guild=self.guild_object)
        logger.info("Synced %s slash commands to guild %s.", len(synced), self.config.guild_id)
        self.reminder_loop.start()

    async def on_ready(self) -> None:
        logger.info("Logged in as %s.", self.user)

    async def get_announcement_channel(self) -> Optional[discord.abc.Messageable]:
        channel = self.get_channel(self.config.announcement_channel_id)
        if channel is None:
            try:
                channel = await self.fetch_channel(self.config.announcement_channel_id)
            except discord.DiscordException:
                logger.exception("Could not fetch announcement channel %s.", self.config.announcement_channel_id)
                return None

        if not hasattr(channel, "send"):
            logger.error("Configured announcement channel %s cannot receive messages.", self.config.announcement_channel_id)
            return None

        return channel

    async def send_reminder(self, meeting: sqlite3.Row, reminder_type: str) -> bool:
        start_time = datetime_from_db(meeting["start_time_utc"])
        role_mention = f"<@&{meeting['role_id']}>"
        timestamp = to_discord_timestamp(start_time)
        agenda = format_agenda_for_reminder(int(meeting["id"]))

        if reminder_type == "seven_day":
            lines = [
                f"{role_mention} Reminder: {meeting['title']} is in 7 days.",
                "",
                f"Meeting time: {timestamp}",
            ]
            if agenda:
                lines.extend(
                    [
                        "",
                        "Current agenda:",
                        agenda,
                        "",
                        "Add an agenda item with:",
                        f'/agenda_add meeting_id:{meeting["id"]} item:"Your topic here"',
                    ]
                )
            lines.extend(["", "Please RSVP on the Discord event if one has been created."])
        else:
            lines = [
                f"{role_mention} Reminder: {meeting['title']} starts in 1 hour.",
                "",
                f"Meeting time: {timestamp}",
            ]
            if agenda:
                lines.extend(["", "Agenda:", agenda])
            else:
                lines.extend(["", "No agenda items have been added yet."])

        channel = await self.get_announcement_channel()
        if channel is None:
            return False

        try:
            await channel.send(
                "\n".join(lines),
                allowed_mentions=discord.AllowedMentions(roles=True, everyone=False, users=False),
            )
            return True
        except discord.DiscordException:
            logger.exception("Failed to send %s reminder for meeting %s.", reminder_type, meeting["id"])
            return False

    @tasks.loop(minutes=1)
    async def reminder_loop(self) -> None:
        await check_reminders(self)

    @reminder_loop.before_loop
    async def before_reminder_loop(self) -> None:
        await self.wait_until_ready()


async def check_reminders(bot: AlumniReminderBot) -> None:
    now = utc_now()
    with get_db() as conn:
        meetings = conn.execute(
            """
            SELECT * FROM meetings
            WHERE active = 1
              AND start_time_utc > ?
              AND (seven_day_sent = 0 OR one_hour_sent = 0)
            ORDER BY start_time_utc ASC
            """,
            (datetime_to_db(now),),
        ).fetchall()

    for meeting in meetings:
        meeting_id = int(meeting["id"])
        start_time = datetime_from_db(meeting["start_time_utc"])

        if not meeting["seven_day_sent"] and now >= start_time - timedelta(days=7):
            if await bot.send_reminder(meeting, "seven_day"):
                mark_reminder_sent(meeting_id, "seven_day_sent")
                logger.info("Sent 7-day reminder for meeting %s.", meeting_id)

        if not meeting["one_hour_sent"] and now >= start_time - timedelta(hours=1):
            if await bot.send_reminder(meeting, "one_hour"):
                mark_reminder_sent(meeting_id, "one_hour_sent")
                logger.info("Sent 1-hour reminder for meeting %s.", meeting_id)


config = load_config()
bot = AlumniReminderBot(config)


@bot.tree.command(name="meeting_add", description="Add an alumni meeting reminder.")
@app_commands.describe(
    title="Meeting title",
    start_time="Meeting start time in YYYY-MM-DD HH:MM format using the configured timezone",
)
async def meeting_add(interaction: discord.Interaction, title: str, start_time: str) -> None:
    if not await require_manage_guild(interaction):
        return

    clean_title = title.strip()
    if not clean_title:
        await interaction.response.send_message("Meeting title cannot be empty.", ephemeral=True)
        return

    try:
        start_time_utc = parse_eastern_datetime(start_time, config.timezone)
    except ValueError:
        await interaction.response.send_message(
            f"Invalid date format. Use `YYYY-MM-DD HH:MM`, for example `2026-07-01 19:00`.",
            ephemeral=True,
        )
        return

    if start_time_utc <= utc_now():
        await interaction.response.send_message("Meeting date/time must be in the future.", ephemeral=True)
        return

    meeting_id = add_meeting(
        clean_title,
        start_time_utc,
        config.announcement_channel_id,
        config.alumni_role_id,
        interaction.user.id,
    )

    await interaction.response.send_message(
        "\n".join(
            [
                f"Meeting added: {clean_title}",
                f"Meeting ID: {meeting_id}",
                f"Starts: {to_discord_timestamp(start_time_utc)}",
                f"I will notify <@&{config.alumni_role_id}> 7 days before and 1 hour before.",
            ]
        ),
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none(),
    )


@bot.tree.command(name="meeting_list", description="List active upcoming alumni meetings.")
async def meeting_list(interaction: discord.Interaction) -> None:
    if not await require_manage_guild(interaction):
        return

    meetings = list_meetings()
    if not meetings:
        await interaction.response.send_message("No active upcoming meetings found.", ephemeral=True)
        return

    lines = ["Active upcoming meetings:"]
    for row in meetings:
        start_time = datetime_from_db(row["start_time_utc"])
        lines.extend(
            [
                "",
                f"ID: {row['id']}",
                f"Title: {row['title']}",
                f"Starts: {to_discord_timestamp(start_time)}",
                f"7-day reminder sent: {'yes' if row['seven_day_sent'] else 'no'}",
                f"1-hour reminder sent: {'yes' if row['one_hour_sent'] else 'no'}",
            ]
        )

    await interaction.response.send_message("\n".join(lines), ephemeral=True)


@bot.tree.command(name="meeting_remove", description="Deactivate an alumni meeting.")
@app_commands.describe(meeting_id="Meeting ID to remove")
async def meeting_remove(interaction: discord.Interaction, meeting_id: int) -> None:
    if not await require_manage_guild(interaction):
        return

    if remove_meeting(meeting_id):
        await interaction.response.send_message(f"Removed meeting {meeting_id}.", ephemeral=True)
    else:
        await interaction.response.send_message("Invalid meeting ID or meeting is already inactive.", ephemeral=True)


@bot.tree.command(name="agenda_add", description="Add an agenda item to a scheduled alumni meeting.")
@app_commands.describe(
    meeting_id="Meeting ID",
    item="Agenda item text",
)
async def agenda_add(interaction: discord.Interaction, meeting_id: int, item: str) -> None:
    clean_item = item.strip()
    if not clean_item:
        await interaction.response.send_message("Agenda item cannot be empty.", ephemeral=True)
        return

    if len(clean_item) > AGENDA_ITEM_LIMIT:
        await interaction.response.send_message(
            f"Agenda items must be {AGENDA_ITEM_LIMIT} characters or fewer.",
            ephemeral=True,
        )
        return

    meeting = get_active_meeting(meeting_id)
    if meeting is None:
        await interaction.response.send_message("Invalid meeting ID or meeting is inactive.", ephemeral=True)
        return

    start_time = datetime_from_db(meeting["start_time_utc"])
    if start_time <= utc_now():
        await interaction.response.send_message("Agenda items cannot be added after the meeting has started.", ephemeral=True)
        return

    agenda_item_id = add_agenda_item(
        meeting_id,
        clean_item,
        interaction.user.id,
        interaction.user.display_name,
    )

    await interaction.response.send_message(
        f'Agenda item {agenda_item_id} added to {meeting["title"]}:\n\n"{clean_item}"',
        ephemeral=True,
    )


@bot.tree.command(name="agenda_list", description="List agenda items for a meeting.")
@app_commands.describe(meeting_id="Meeting ID")
async def agenda_list(interaction: discord.Interaction, meeting_id: int) -> None:
    meeting = get_active_meeting(meeting_id)
    if meeting is None:
        await interaction.response.send_message("Invalid meeting ID or meeting is inactive.", ephemeral=True)
        return

    items = list_agenda_items(meeting_id)
    if not items:
        await interaction.response.send_message("No agenda items have been added yet.", ephemeral=True)
        return

    lines = [f"Agenda for {meeting['title']}:"]
    for index, row in enumerate(items, start=1):
        submitter = row["submitted_by_display_name"] or f"User {row['submitted_by_user_id']}"
        lines.append(f"{index}. [{row['id']}] {row['item_text']} - submitted by {submitter}")

    await interaction.response.send_message("\n".join(lines), ephemeral=True)


@bot.tree.command(name="agenda_remove", description="Deactivate an agenda item.")
@app_commands.describe(agenda_item_id="Agenda item ID to remove")
async def agenda_remove(interaction: discord.Interaction, agenda_item_id: int) -> None:
    if not await require_manage_guild(interaction):
        return

    if remove_agenda_item(agenda_item_id):
        await interaction.response.send_message(f"Removed agenda item {agenda_item_id}.", ephemeral=True)
    else:
        await interaction.response.send_message("Invalid agenda item ID or item is already inactive.", ephemeral=True)


def main() -> None:
    init_db()
    bot.run(config.discord_token)


if __name__ == "__main__":
    main()
