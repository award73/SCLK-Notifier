import logging
import os
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Iterator, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord
from discord import app_commands
from discord.ext import commands, tasks
from dotenv import load_dotenv


DB_PATH = Path(__file__).with_name("alumni_bot.db")
BOT_GUIDE_URL = "https://github.com/award73/SCLK-Notifier/blob/main/docs/BOT_GUIDE.md"
AGENDA_ITEM_LIMIT = 500
AGENDA_EVENT_LIMIT = 25
SQLITE_TIMEOUT_SECONDS = 30
REJECTED_WRITE_LIMIT = 5
REJECTED_WRITE_WINDOW_SECONDS = 600
REJECTED_WRITE_COOLDOWN_SECONDS = 1800
PUBLIC_MESSAGE_MAX_CHARS = 1800
PUBLIC_AGENDA_LINE_LIMIT = 10
DIAGNOSTIC_EVENT_LIMIT = 5
DAY_OF_REMINDER_LOCAL_TIME = time(hour=16, minute=0)

MENTION_PATTERN = re.compile(r"<(@!?\d+|@&\d+|#\d+)>")
MARKDOWN_CHARS = "\\*_~`>|"

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
    alumni_board_role_id: int
    timezone_name: str
    timezone: ZoneInfo
    reminders_enabled: bool


@dataclass(frozen=True)
class EventDiagnostic:
    name: str
    status: str
    start_time: Optional[datetime]
    reason: str


@dataclass(frozen=True)
class SyncResult:
    fetched_count: int
    matched_events: list[discord.ScheduledEvent]
    diagnostics: list[EventDiagnostic]
    error: Optional[str] = None
    cached_count: int = 0

    @property
    def matched_count(self) -> int:
        return len(self.matched_events)


def load_config() -> Config:
    load_dotenv()

    required_vars = [
        "DISCORD_TOKEN",
        "GUILD_ID",
        "ANNOUNCEMENT_CHANNEL_ID",
        "ALUMNI_ROLE_ID",
        "ALUMNI_BOARD_ROLE_ID",
        "TIMEZONE",
    ]
    missing = [name for name in required_vars if not os.getenv(name)]
    if missing:
        raise ValueError(f"Missing required .env variables: {', '.join(missing)}")

    try:
        timezone_name = os.environ["TIMEZONE"]
        bot_timezone = ZoneInfo(timezone_name)
        reminders_enabled = os.getenv("REMINDERS_ENABLED", "true").strip().lower() not in {
            "0",
            "false",
            "no",
            "off",
        }
        return Config(
            discord_token=os.environ["DISCORD_TOKEN"],
            guild_id=int(os.environ["GUILD_ID"]),
            announcement_channel_id=int(os.environ["ANNOUNCEMENT_CHANNEL_ID"]),
            alumni_role_id=int(os.environ["ALUMNI_ROLE_ID"]),
            alumni_board_role_id=int(os.environ["ALUMNI_BOARD_ROLE_ID"]),
            timezone_name=timezone_name,
            timezone=bot_timezone,
            reminders_enabled=reminders_enabled,
        )
    except ValueError as exc:
        raise ValueError(
            "GUILD_ID, ANNOUNCEMENT_CHANNEL_ID, ALUMNI_ROLE_ID, and ALUMNI_BOARD_ROLE_ID must be numeric IDs."
        ) from exc
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Invalid TIMEZONE value: {os.environ['TIMEZONE']}") from exc


@contextmanager
def get_db() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(DB_PATH, timeout=SQLITE_TIMEOUT_SECONDS)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(f"PRAGMA busy_timeout = {SQLITE_TIMEOUT_SECONDS * 1000}")
        with conn:
            yield conn
    finally:
        conn.close()


def init_db() -> None:
    with get_db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tracked_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                discord_event_id TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                description TEXT,
                start_time_utc TEXT NOT NULL,
                end_time_utc TEXT,
                location TEXT,
                status TEXT,
                seven_day_sent INTEGER NOT NULL DEFAULT 0,
                one_hour_sent INTEGER NOT NULL DEFAULT 0,
                active INTEGER NOT NULL DEFAULT 1,
                first_seen_at_utc TEXT NOT NULL,
                last_synced_at_utc TEXT NOT NULL
            )
            """
        )

        tracked_columns = {row["name"] for row in conn.execute("PRAGMA table_info(tracked_events)").fetchall()}
        for column in ("one_day_sent", "day_of_sent"):
            if column not in tracked_columns:
                logger.info("Adding tracked_events.%s column.", column)
                conn.execute(f"ALTER TABLE tracked_events ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0")

        agenda_columns = conn.execute("PRAGMA table_info(agenda_items)").fetchall()
        if agenda_columns and "discord_event_id" not in {row["name"] for row in agenda_columns}:
            logger.info("Renaming legacy agenda_items table to agenda_items_legacy.")
            conn.execute("ALTER TABLE agenda_items RENAME TO agenda_items_legacy")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS agenda_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                discord_event_id TEXT NOT NULL,
                item_text TEXT NOT NULL,
                submitted_by_user_id TEXT NOT NULL,
                submitted_by_display_name TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                created_at_utc TEXT NOT NULL,
                FOREIGN KEY (discord_event_id) REFERENCES tracked_events(discord_event_id)
            )
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS abuse_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                discord_user_id TEXT,
                discord_event_id TEXT,
                details TEXT,
                created_at_utc TEXT NOT NULL
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


def optional_datetime_to_db(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return datetime_to_db(value)


def to_discord_timestamp(value: datetime, style: str = "F") -> str:
    return f"<t:{int(value.timestamp())}:{style}>"


def event_status_name(status: object) -> str:
    return getattr(status, "name", str(status)).replace("_", " ").title()


def event_is_eligible(event: discord.ScheduledEvent, config: Config) -> bool:
    return event_rejection_reason(event, config) is None


def event_rejection_reason(event: discord.ScheduledEvent, config: Config) -> Optional[str]:
    if not event.start_time:
        return "missing start time"

    status = event_status_name(event.status).lower()
    if status not in {"scheduled", "active"}:
        return f"status is {event_status_name(event.status)}"

    if event.start_time.astimezone(timezone.utc) <= utc_now():
        return "event already started"

    return None


def event_link(guild_id: int, discord_event_id: str) -> str:
    return f"https://discord.com/events/{guild_id}/{discord_event_id}"


def upsert_tracked_event(event: discord.ScheduledEvent) -> None:
    now = datetime_to_db(utc_now())
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO tracked_events (
                discord_event_id, name, description, start_time_utc, end_time_utc,
                location, status, first_seen_at_utc, last_synced_at_utc
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(discord_event_id) DO UPDATE SET
                name = excluded.name,
                description = excluded.description,
                start_time_utc = excluded.start_time_utc,
                end_time_utc = excluded.end_time_utc,
                location = excluded.location,
                status = excluded.status,
                active = 1,
                last_synced_at_utc = excluded.last_synced_at_utc
            """,
            (
                str(event.id),
                event.name,
                event.description,
                datetime_to_db(event.start_time),
                optional_datetime_to_db(event.end_time),
                event.location,
                event_status_name(event.status),
                now,
                now,
            ),
        )


def mark_unseen_events_inactive(seen_event_ids: set[str]) -> None:
    with get_db() as conn:
        if seen_event_ids:
            placeholders = ",".join("?" for _ in seen_event_ids)
            conn.execute(
                f"UPDATE tracked_events SET active = 0 WHERE active = 1 AND discord_event_id NOT IN ({placeholders})",
                tuple(seen_event_ids),
            )
        else:
            conn.execute("UPDATE tracked_events SET active = 0 WHERE active = 1")


def mark_started_events_inactive() -> None:
    with get_db() as conn:
        conn.execute(
            "UPDATE tracked_events SET active = 0 WHERE active = 1 AND start_time_utc <= ?",
            (datetime_to_db(utc_now()),),
        )


def get_tracked_event(discord_event_id: str) -> Optional[sqlite3.Row]:
    with get_db() as conn:
        return conn.execute(
            "SELECT * FROM tracked_events WHERE discord_event_id = ? AND active = 1",
            (discord_event_id,),
        ).fetchone()


def get_next_tracked_event() -> Optional[sqlite3.Row]:
    with get_db() as conn:
        return conn.execute(
            """
            SELECT * FROM tracked_events
            WHERE active = 1 AND start_time_utc > ?
            ORDER BY start_time_utc ASC
            LIMIT 1
            """,
            (datetime_to_db(utc_now()),),
        ).fetchone()


def list_tracked_events() -> list[sqlite3.Row]:
    with get_db() as conn:
        return conn.execute(
            """
            SELECT
                tracked_events.*,
                COUNT(agenda_items.id) AS agenda_count
            FROM tracked_events
            LEFT JOIN agenda_items
                ON agenda_items.discord_event_id = tracked_events.discord_event_id
                AND agenda_items.active = 1
            WHERE tracked_events.active = 1
              AND tracked_events.start_time_utc > ?
            GROUP BY tracked_events.id
            ORDER BY tracked_events.start_time_utc ASC
            """,
            (datetime_to_db(utc_now()),),
        ).fetchall()


def reset_reminder_flags(discord_event_id: str) -> bool:
    with get_db() as conn:
        cursor = conn.execute(
            """
            UPDATE tracked_events
            SET seven_day_sent = 0,
                one_day_sent = 0,
                day_of_sent = 0,
                one_hour_sent = 0
            WHERE discord_event_id = ? AND active = 1
            """,
            (discord_event_id,),
        )
        return cursor.rowcount > 0


def mark_reminder_sent(discord_event_id: str, reminder_column: str) -> None:
    if reminder_column not in {"seven_day_sent", "one_day_sent", "day_of_sent", "one_hour_sent"}:
        raise ValueError("Invalid reminder column.")

    with get_db() as conn:
        conn.execute(
            f"UPDATE tracked_events SET {reminder_column} = 1 WHERE discord_event_id = ?",
            (discord_event_id,),
        )


def log_abuse_event(
    event_type: str,
    discord_user_id: Optional[int | str] = None,
    discord_event_id: Optional[int | str] = None,
    details: Optional[str] = None,
) -> None:
    safe_details = None if details is None else sanitize_operational_details(details)[:500]
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO abuse_events (
                event_type, discord_user_id, discord_event_id, details, created_at_utc
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                event_type,
                None if discord_user_id is None else str(discord_user_id),
                None if discord_event_id is None else str(discord_event_id),
                safe_details,
                datetime_to_db(utc_now()),
            ),
        )


def sanitize_operational_details(value: str) -> str:
    redacted = re.sub(r"DISCORD_TOKEN\s*=\s*\S+", "DISCORD_TOKEN=[redacted]", value)
    return redacted.replace("\n", " ").strip()


def add_agenda_item(
    discord_event_id: str,
    item_text: str,
    submitted_by_user_id: int,
    submitted_by_display_name: str,
) -> int:
    with get_db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO agenda_items (
                discord_event_id, item_text, submitted_by_user_id,
                submitted_by_display_name, created_at_utc
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                discord_event_id,
                item_text.strip(),
                str(submitted_by_user_id),
                submitted_by_display_name,
                datetime_to_db(utc_now()),
            ),
        )
        return int(cursor.lastrowid)


def count_active_agenda_items(discord_event_id: str) -> int:
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS count FROM agenda_items
            WHERE discord_event_id = ? AND active = 1
            """,
            (discord_event_id,),
        ).fetchone()
        return int(row["count"])


def count_user_agenda_items(discord_event_id: str, submitted_by_user_id: int | str) -> int:
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS count FROM agenda_items
            WHERE discord_event_id = ?
              AND submitted_by_user_id = ?
              AND active = 1
            """,
            (discord_event_id, str(submitted_by_user_id)),
        ).fetchone()
        return int(row["count"])


def get_user_last_agenda_item_time(submitted_by_user_id: int | str) -> Optional[datetime]:
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT created_at_utc FROM agenda_items
            WHERE submitted_by_user_id = ?
            ORDER BY created_at_utc DESC
            LIMIT 1
            """,
            (str(submitted_by_user_id),),
        ).fetchone()
    if row is None:
        return None
    return datetime_from_db(row["created_at_utc"])


def user_has_rejection_cooldown(discord_user_id: int | str) -> bool:
    cutoff = datetime_to_db(utc_now() - timedelta(seconds=REJECTED_WRITE_COOLDOWN_SECONDS))
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT 1 FROM abuse_events
            WHERE event_type = 'cooldown_started'
              AND discord_user_id = ?
              AND created_at_utc >= ?
            ORDER BY created_at_utc DESC
            LIMIT 1
            """,
            (str(discord_user_id), cutoff),
        ).fetchone()
    return row is not None


def record_rejected_write(
    discord_user_id: int | str,
    discord_event_id: Optional[int | str],
    reason: str,
) -> None:
    log_abuse_event("rejected_write", discord_user_id, discord_event_id, reason)
    cutoff = datetime_to_db(utc_now() - timedelta(seconds=REJECTED_WRITE_WINDOW_SECONDS))
    with get_db() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS count FROM abuse_events
            WHERE event_type = 'rejected_write'
              AND discord_user_id = ?
              AND created_at_utc >= ?
            """,
            (str(discord_user_id), cutoff),
        ).fetchone()
    if int(row["count"]) >= REJECTED_WRITE_LIMIT and not user_has_rejection_cooldown(discord_user_id):
        log_abuse_event("cooldown_started", discord_user_id, discord_event_id, reason)


def list_agenda_items(discord_event_id: str) -> list[sqlite3.Row]:
    with get_db() as conn:
        return conn.execute(
            """
            SELECT * FROM agenda_items
            WHERE discord_event_id = ? AND active = 1
            ORDER BY created_at_utc ASC
            """,
            (discord_event_id,),
        ).fetchall()


def remove_agenda_item(agenda_item_id: int) -> bool:
    with get_db() as conn:
        cursor = conn.execute(
            "UPDATE agenda_items SET active = 0 WHERE id = ? AND active = 1",
            (agenda_item_id,),
        )
        return cursor.rowcount > 0


def sanitize_display_text(value: str) -> str:
    text = value.replace("\r", " ").replace("\n", " ").strip()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"@(everyone|here)\b", r"@ \1", text, flags=re.IGNORECASE)
    text = MENTION_PATTERN.sub(lambda match: f"< {match.group(1)}>", text)
    for char in MARKDOWN_CHARS:
        text = text.replace(char, f"\\{char}")
    return text


def format_agenda_lines(
    discord_event_id: str,
    include_submitters: bool = False,
    max_items: Optional[int] = None,
) -> list[str]:
    items = list_agenda_items(discord_event_id)
    if max_items is not None:
        items = items[:max_items]
    lines = []
    for index, row in enumerate(items, start=1):
        item_text = sanitize_display_text(row["item_text"])
        if include_submitters:
            submitter = sanitize_display_text(row["submitted_by_display_name"] or "Unknown")
            lines.append(f"{index}. {item_text} - submitted by {submitter}")
        else:
            lines.append(f"{index}. {item_text}")
    return lines


def append_agenda_summary(lines: list[str], discord_event_id: str, heading: str) -> None:
    all_items = list_agenda_items(discord_event_id)
    agenda_lines = format_agenda_lines(discord_event_id, max_items=PUBLIC_AGENDA_LINE_LIMIT)
    if agenda_lines:
        lines.extend([heading, *agenda_lines])
        remaining = len(all_items) - len(agenda_lines)
        if remaining > 0:
            lines.append(f"...and {remaining} more agenda item{'s' if remaining != 1 else ''}. Run /agenda for the full list.")
    else:
        lines.append("No agenda items have been added yet.")


def truncate_public_message(lines: list[str]) -> str:
    message = "\n".join(lines)
    if len(message) <= PUBLIC_MESSAGE_MAX_CHARS:
        return message

    footer = "\n\nAgenda truncated for safety. Run /agenda for the full list."
    return message[: max(0, PUBLIC_MESSAGE_MAX_CHARS - len(footer))].rstrip() + footer


def reminder_label(reminder_type: str) -> str:
    labels = {
        "seven_day": "7-day",
        "one_day": "1-day",
        "day_of": "day-of",
    }
    return labels.get(reminder_type, reminder_type)


def test_notification_type(event: sqlite3.Row) -> str:
    start_time = datetime_from_db(event["start_time_utc"])
    now = utc_now()
    if day_of_reminder_due(now, start_time, config.timezone):
        return "day_of"
    if now >= start_time - timedelta(days=1):
        return "one_day"
    return "seven_day"


def compose_reminder_lines(
    event: sqlite3.Row,
    config: Config,
    reminder_type: str,
    mention_text: str,
) -> list[str]:
    start_time = datetime_from_db(event["start_time_utc"])
    timestamp = to_discord_timestamp(start_time)
    relative_timestamp = to_discord_timestamp(start_time, "R")
    link = event_link(config.guild_id, event["discord_event_id"])
    event_name = sanitize_display_text(event["name"])

    if reminder_type == "seven_day":
        opening = f"{mention_text} Reminder: {event_name} is coming up in about 1 week."
    elif reminder_type == "one_day":
        opening = f"{mention_text} Reminder: {event_name} is tomorrow."
    elif reminder_type == "day_of":
        opening = f"{mention_text} Reminder: {event_name} starts {relative_timestamp}."
    else:
        opening = f"{mention_text} Reminder: {event_name} is coming up."

    lines = [
        opening,
        "",
        f"Meeting time: {timestamp}",
        "",
        "Discord event:",
        link,
        "",
    ]
    append_agenda_summary(lines, event["discord_event_id"], "Agenda:")
    if reminder_type in {"seven_day", "one_day"}:
        lines.extend(
            [
                "",
                "Add an agenda item with:",
                '/agenda_add item:"Your topic here"',
            ]
        )
    lines.extend(
        [
            "",
            "Learn more about the bot:",
            BOT_GUIDE_URL,
            "",
            "Please RSVP on the Discord event.",
        ]
    )
    return lines


def role_only_allowed_mentions(role_id: int) -> discord.AllowedMentions:
    return discord.AllowedMentions(
        users=False,
        roles=[discord.Object(id=role_id)],
        everyone=False,
    )


def day_of_reminder_due(now: datetime, start_time: datetime, bot_timezone: ZoneInfo) -> bool:
    now_local = now.astimezone(bot_timezone)
    start_local = start_time.astimezone(bot_timezone)
    reminder_time = datetime.combine(
        start_local.date(),
        DAY_OF_REMINDER_LOCAL_TIME,
        tzinfo=bot_timezone,
    )
    return now_local >= reminder_time and now < start_time


def user_has_role(interaction: discord.Interaction, role_id: int) -> bool:
    roles = getattr(interaction.user, "roles", []) or []
    return any(getattr(role, "id", None) == role_id for role in roles)


def user_can_use_elevated_commands(interaction: discord.Interaction) -> bool:
    return user_has_role(interaction, config.alumni_board_role_id)


async def require_alumni_board_role(interaction: discord.Interaction) -> bool:
    if user_can_use_elevated_commands(interaction):
        return True

    logger.info("Permission denied for user %s on elevated command.", interaction.user.id)
    log_abuse_event("permission_denied", interaction.user.id, details="alumni board command denied")
    await send_ephemeral(interaction, "Only Alumni Board members can use this command.")
    return False


async def defer_ephemeral(interaction: discord.Interaction) -> None:
    if not interaction.response.is_done():
        await interaction.response.defer(ephemeral=True)


async def send_ephemeral(interaction: discord.Interaction, content: str) -> None:
    try:
        if interaction.response.is_done():
            await interaction.followup.send(content, ephemeral=True)
        else:
            await interaction.response.send_message(content, ephemeral=True)
    except discord.NotFound:
        logger.exception("Interaction expired before response could be sent for user %s.", interaction.user.id)


def short_event_label(row: sqlite3.Row, config: Config) -> str:
    start_time = datetime_from_db(row["start_time_utc"]).astimezone(config.timezone)
    return f"{sanitize_display_text(row['name'])} - {start_time:%b %d, %Y at %I:%M %p}"


def describe_sync_result(result: SyncResult, config: Config) -> str:
    if result.error:
        return result.error

    lines = [
        "No upcoming Discord events found.",
        "",
        f"Discord returned {result.fetched_count} scheduled event{'s' if result.fetched_count != 1 else ''}.",
    ]
    if result.cached_count != result.fetched_count:
        lines.append(f"Cached scheduled events visible to the bot: {result.cached_count}.")
    if result.diagnostics:
        lines.extend(["", "Events I inspected but did not track:"])
        for diagnostic in result.diagnostics[:DIAGNOSTIC_EVENT_LIMIT]:
            start_label = "no start time"
            if diagnostic.start_time is not None:
                start_label = to_discord_timestamp(diagnostic.start_time)
            lines.append(
                f"- {sanitize_display_text(diagnostic.name)} - {diagnostic.status}, {start_label}; {diagnostic.reason}"
            )
    else:
        lines.extend(["", "No Discord scheduled events were returned for this server."])
    lines.extend(
        [
            "",
            "Check that the event is scheduled in the future and visible to the bot.",
        ]
    )
    return "\n".join(lines)


def agenda_write_rejection_reason(event: sqlite3.Row, user_id: int | str) -> Optional[str]:
    if user_has_rejection_cooldown(user_id):
        return "Too many rejected agenda attempts. Please wait before trying again."

    if count_active_agenda_items(event["discord_event_id"]) >= AGENDA_EVENT_LIMIT:
        return "This meeting agenda is full. Please ask an admin to review existing items."

    return None


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
        self.event_sync_loop.start()
        self.reminder_loop.start()

    async def on_ready(self) -> None:
        logger.info("Logged in as %s.", self.user)

    async def get_configured_guild(self) -> Optional[discord.Guild]:
        guild = self.get_guild(self.config.guild_id)
        if guild:
            return guild

        try:
            return await self.fetch_guild(self.config.guild_id)
        except discord.DiscordException:
            logger.exception("Could not fetch configured guild %s.", self.config.guild_id)
            return None

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

    async def sync_events(self) -> SyncResult:
        guild = await self.get_configured_guild()
        if guild is None:
            return SyncResult(0, [], [], "Could not fetch configured guild.")

        cached_events = list(getattr(guild, "scheduled_events", []) or [])
        try:
            scheduled_events = await guild.fetch_scheduled_events(with_counts=True)
        except discord.DiscordException:
            logger.exception("Could not fetch scheduled events for guild %s.", self.config.guild_id)
            if cached_events:
                logger.warning("Using %s cached scheduled events after fetch failure.", len(cached_events))
                scheduled_events = cached_events
            else:
                return SyncResult(
                    0,
                    [],
                    [],
                    "Could not fetch scheduled events. Check bot permissions and scheduled event visibility.",
                    cached_count=len(cached_events),
                )

        if not scheduled_events and cached_events:
            logger.warning("Fetch returned 0 scheduled events; using %s cached scheduled events.", len(cached_events))
            scheduled_events = cached_events

        matching_events = []
        diagnostics = []
        for event in scheduled_events:
            rejection_reason = event_rejection_reason(event, self.config)
            if rejection_reason is None:
                matching_events.append(event)
            else:
                diagnostics.append(
                    EventDiagnostic(
                        name=event.name,
                        status=event_status_name(event.status),
                        start_time=event.start_time,
                        reason=rejection_reason,
                    )
                )
        seen_event_ids = {str(event.id) for event in matching_events}

        for event in matching_events:
            upsert_tracked_event(event)
            logger.info("Tracked Discord event %s: %s.", event.id, event.name)

        mark_unseen_events_inactive(seen_event_ids)
        mark_started_events_inactive()
        logger.info(
            "Event sync complete. Fetched %s events and found %s upcoming events.",
            len(scheduled_events),
            len(matching_events),
        )
        return SyncResult(len(scheduled_events), matching_events, diagnostics, cached_count=len(cached_events))

    async def get_next_event_with_sync(self) -> Optional[sqlite3.Row]:
        await self.sync_events()
        return get_next_tracked_event()

    async def send_reminder(self, event: sqlite3.Row, reminder_type: str) -> bool:
        if not self.config.reminders_enabled:
            log_abuse_event("reminder_skipped", discord_event_id=event["discord_event_id"], details="reminders disabled")
            logger.info("Skipped %s reminder for event %s because reminders are disabled.", reminder_type, event["discord_event_id"])
            return False

        fresh_event = get_tracked_event(event["discord_event_id"])
        if fresh_event is None:
            log_abuse_event("reminder_skipped", discord_event_id=event["discord_event_id"], details="event inactive or missing")
            logger.warning("Skipped %s reminder for event %s because the event is inactive or missing.", reminder_type, event["discord_event_id"])
            return False

        start_time = datetime_from_db(fresh_event["start_time_utc"])
        if start_time <= utc_now():
            log_abuse_event("reminder_skipped", discord_event_id=event["discord_event_id"], details="event already started")
            logger.info("Skipped %s reminder for event %s because the event has started.", reminder_type, event["discord_event_id"])
            return False

        role_mention = f"<@&{self.config.alumni_role_id}>"
        lines = compose_reminder_lines(fresh_event, self.config, reminder_type, role_mention)

        channel = await self.get_announcement_channel()
        if channel is None:
            log_abuse_event("send_failure", discord_event_id=event["discord_event_id"], details="announcement channel unavailable")
            return False

        try:
            await channel.send(
                truncate_public_message(lines),
                allowed_mentions=role_only_allowed_mentions(self.config.alumni_role_id),
            )
            log_abuse_event("reminder_sent", discord_event_id=event["discord_event_id"], details=reminder_type)
            return True
        except discord.DiscordException:
            logger.exception("Failed to send %s reminder for event %s.", reminder_type, event["discord_event_id"])
            log_abuse_event("send_failure", discord_event_id=event["discord_event_id"], details=reminder_type)
            return False

    @tasks.loop(minutes=5)
    async def event_sync_loop(self) -> None:
        await self.sync_events()

    @event_sync_loop.before_loop
    async def before_event_sync_loop(self) -> None:
        await self.wait_until_ready()

    @tasks.loop(minutes=1)
    async def reminder_loop(self) -> None:
        await check_reminders(self)

    @reminder_loop.before_loop
    async def before_reminder_loop(self) -> None:
        await self.wait_until_ready()


async def check_reminders(bot: AlumniReminderBot) -> None:
    now = utc_now()
    with get_db() as conn:
        events = conn.execute(
            """
            SELECT * FROM tracked_events
            WHERE active = 1
              AND start_time_utc > ?
              AND (seven_day_sent = 0 OR one_day_sent = 0 OR day_of_sent = 0)
            ORDER BY start_time_utc ASC
            """,
            (datetime_to_db(now),),
        ).fetchall()

    for event in events:
        discord_event_id = event["discord_event_id"]
        start_time = datetime_from_db(event["start_time_utc"])

        if not event["seven_day_sent"] and now >= start_time - timedelta(days=7):
            if await bot.send_reminder(event, "seven_day"):
                mark_reminder_sent(discord_event_id, "seven_day_sent")
                logger.info("Sent 7-day reminder for Discord event %s.", discord_event_id)

        if not event["one_day_sent"] and now >= start_time - timedelta(days=1):
            if await bot.send_reminder(event, "one_day"):
                mark_reminder_sent(discord_event_id, "one_day_sent")
                logger.info("Sent 1-day reminder for Discord event %s.", discord_event_id)

        if not event["day_of_sent"] and day_of_reminder_due(now, start_time, bot.config.timezone):
            if await bot.send_reminder(event, "day_of"):
                mark_reminder_sent(discord_event_id, "day_of_sent")
                logger.info("Sent day-of reminder for Discord event %s.", discord_event_id)


config = load_config()
bot = AlumniReminderBot(config)


@bot.tree.command(name="agenda_add", description="Add an item to the next alumni meeting agenda.")
@app_commands.describe(item="Agenda item to add")
async def agenda_add(interaction: discord.Interaction, item: str) -> None:
    await defer_ephemeral(interaction)
    clean_item = item.strip()
    if not clean_item:
        record_rejected_write(interaction.user.id, None, "empty agenda item")
        await send_ephemeral(interaction, "Please enter an agenda item.")
        return

    if len(clean_item) > AGENDA_ITEM_LIMIT:
        record_rejected_write(interaction.user.id, None, "agenda item too long")
        await send_ephemeral(
            interaction,
            f"That agenda item is too long. Please keep agenda items under {AGENDA_ITEM_LIMIT} characters.",
        )
        return

    event = await bot.get_next_event_with_sync()
    if event is None:
        record_rejected_write(interaction.user.id, None, "no upcoming event")
        await send_ephemeral(
            interaction,
            "I could not find an upcoming Discord event to attach this agenda item to.\n\n"
            "An admin can create a Discord event, then run:\n/event_sync",
        )
        return

    start_time = datetime_from_db(event["start_time_utc"])
    if start_time <= utc_now():
        record_rejected_write(interaction.user.id, event["discord_event_id"], "meeting already started")
        await send_ephemeral(
            interaction,
            "This meeting has already started, so new agenda items are closed.",
        )
        return

    safety_rejection = agenda_write_rejection_reason(event, interaction.user.id)
    if safety_rejection:
        record_rejected_write(interaction.user.id, event["discord_event_id"], safety_rejection)
        log_abuse_event("rate_limit_hit", interaction.user.id, event["discord_event_id"], safety_rejection)
        await send_ephemeral(interaction, safety_rejection)
        return

    add_agenda_item(
        event["discord_event_id"],
        clean_item,
        interaction.user.id,
        interaction.user.display_name,
    )
    logger.info("User %s added agenda item to event %s.", interaction.user.id, event["discord_event_id"])

    await send_ephemeral(
        interaction,
        f"Added to the agenda for {sanitize_display_text(event['name'])}:\n"
        f"Meeting time: {to_discord_timestamp(start_time)}\n\n"
        f'"{sanitize_display_text(clean_item)}"',
    )


@bot.tree.command(name="agenda", description="View the next alumni meeting agenda.")
async def agenda(interaction: discord.Interaction) -> None:
    await defer_ephemeral(interaction)
    event = await bot.get_next_event_with_sync()
    if event is None:
        await send_ephemeral(
            interaction,
            "I could not find an upcoming Discord event.\n\n"
            "An admin can create a Discord event, then run:\n/event_sync",
        )
        return

    start_time = datetime_from_db(event["start_time_utc"])
    agenda_lines = format_agenda_lines(event["discord_event_id"], include_submitters=True)
    if agenda_lines:
        body = "\n".join(agenda_lines)
    else:
        body = (
            "No agenda items have been added yet for the next event.\n\n"
            'Add one with:\n/agenda_add item:"Your topic here"'
        )

    await send_ephemeral(
        interaction,
        f"Agenda for {sanitize_display_text(event['name'])}\n"
        f"Meeting time: {to_discord_timestamp(start_time)}\n\n"
        f"{body}",
    )


@bot.tree.command(name="next_meeting", description="Show the next alumni meeting.")
async def next_meeting(interaction: discord.Interaction) -> None:
    await defer_ephemeral(interaction)
    event = await bot.get_next_event_with_sync()
    if event is None:
        await send_ephemeral(
            interaction,
            "I could not find an upcoming Discord event.\n\n"
            "An admin can create a Discord event, then run:\n/event_sync",
        )
        return

    start_time = datetime_from_db(event["start_time_utc"])
    agenda_count = len(list_agenda_items(event["discord_event_id"]))
    lines = [
        f"Next meeting: {sanitize_display_text(event['name'])}",
        f"Time: {to_discord_timestamp(start_time)}",
        f"Discord event: {event_link(config.guild_id, event['discord_event_id'])}",
        "",
        f"Agenda items added so far: {agenda_count}",
        "",
        "Add an agenda item with:",
        '/agenda_add item:"Your topic here"',
    ]

    if user_can_use_elevated_commands(interaction):
        lines.extend(
            [
                "",
                "Admin reminder status:",
                f"7-day reminder: {'Sent' if event['seven_day_sent'] else 'Not sent'}",
                f"1-day reminder: {'Sent' if event['one_day_sent'] else 'Not sent'}",
                f"Day-of reminder: {'Sent' if event['day_of_sent'] else 'Not sent'}",
            ]
        )

    await send_ephemeral(interaction, "\n".join(lines))


@bot.tree.command(name="event_sync", description="Sync native Discord events for alumni reminders.")
async def event_sync(interaction: discord.Interaction) -> None:
    if not await require_alumni_board_role(interaction):
        return

    await defer_ephemeral(interaction)
    result = await bot.sync_events()
    if not result.matched_events:
        await send_ephemeral(interaction, describe_sync_result(result, config))
        return

    lines = [
        "Event sync complete.",
        "",
        f"Found {result.matched_count} upcoming event{'s' if result.matched_count != 1 else ''}:",
    ]
    for event in result.matched_events:
        lines.append(f"{sanitize_display_text(event.name)} - {to_discord_timestamp(event.start_time)}")
    lines.extend(["", "These events are now being tracked for 7-day, 1-day, and day-of reminders."])
    await send_ephemeral(interaction, "\n".join(lines))


@bot.tree.command(name="event_list", description="List tracked alumni events.")
async def event_list(interaction: discord.Interaction) -> None:
    if not await require_alumni_board_role(interaction):
        return

    await defer_ephemeral(interaction)
    result = await bot.sync_events()
    events = list_tracked_events()
    if not events:
        await send_ephemeral(interaction, describe_sync_result(result, config))
        return

    lines = ["Tracked Events"]
    for index, row in enumerate(events, start=1):
        start_time = datetime_from_db(row["start_time_utc"])
        lines.extend(
            [
                "",
                f"{index}. {sanitize_display_text(row['name'])}",
                f"Time: {to_discord_timestamp(start_time)}",
                f"Status: {row['status']}",
                f"7-day reminder: {'Sent' if row['seven_day_sent'] else 'Not sent'}",
                f"1-day reminder: {'Sent' if row['one_day_sent'] else 'Not sent'}",
                f"Day-of reminder: {'Sent' if row['day_of_sent'] else 'Not sent'}",
                f"Agenda items: {row['agenda_count']}",
                f"Event link: {event_link(config.guild_id, row['discord_event_id'])}",
                "",
                "Technical details:",
                f"Discord Event ID: {row['discord_event_id']}",
            ]
        )

    await send_ephemeral(interaction, "\n".join(lines))


async def event_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[str]]:
    rows = list_tracked_events()
    current_lower = current.lower()
    choices = []
    for row in rows:
        label = short_event_label(row, config)
        if current_lower and current_lower not in label.lower():
            continue
        choices.append(app_commands.Choice(name=label[:100], value=row["discord_event_id"]))
    return choices[:25]


@bot.tree.command(name="event_reset_reminders", description="Reset reminder flags for an alumni meeting.")
@app_commands.describe(meeting="Optional meeting; defaults to the next upcoming alumni meeting")
@app_commands.autocomplete(meeting=event_autocomplete)
async def event_reset_reminders(interaction: discord.Interaction, meeting: Optional[str] = None) -> None:
    if not await require_alumni_board_role(interaction):
        return

    await defer_ephemeral(interaction)
    await bot.sync_events()
    event = get_tracked_event(meeting) if meeting else get_next_tracked_event()
    if event is None:
        await send_ephemeral(
            interaction,
            "I could not find an upcoming Discord event to reset.",
        )
        return

    if reset_reminder_flags(event["discord_event_id"]):
        await send_ephemeral(
            interaction,
            f"Reminder flags reset for:\n\n"
            f"{sanitize_display_text(event['name'])}\n"
            f"Time: {to_discord_timestamp(datetime_from_db(event['start_time_utc']))}\n\n"
            "The bot may now send the 7-day, 1-day, and day-of reminders again if those reminder times are still valid.",
        )
    else:
        await send_ephemeral(interaction, "I could not reset reminders for that meeting.")


@bot.tree.command(name="test_notify", description="Post a test notification for an alumni meeting.")
@app_commands.describe(meeting="Optional meeting; defaults to the next upcoming alumni meeting")
@app_commands.autocomplete(meeting=event_autocomplete)
async def test_notify(interaction: discord.Interaction, meeting: Optional[str] = None) -> None:
    await send_manual_notification(
        interaction,
        meeting,
        config.alumni_board_role_id,
        "test_notification_sent",
        "Alumni Board",
    )


@bot.tree.command(name="notify", description="Post a notification for an alumni meeting.")
@app_commands.describe(meeting="Optional meeting; defaults to the next upcoming alumni meeting")
@app_commands.autocomplete(meeting=event_autocomplete)
async def notify(interaction: discord.Interaction, meeting: Optional[str] = None) -> None:
    await send_manual_notification(
        interaction,
        meeting,
        config.alumni_role_id,
        "manual_notification_sent",
        "Alumni",
    )


async def send_manual_notification(
    interaction: discord.Interaction,
    meeting: Optional[str],
    mention_role_id: int,
    log_event_type: str,
    mention_label: str,
) -> None:
    if not await require_alumni_board_role(interaction):
        return

    await defer_ephemeral(interaction)
    await bot.sync_events()
    event = get_tracked_event(meeting) if meeting else get_next_tracked_event()
    if event is None:
        await send_ephemeral(
            interaction,
            "I could not find an upcoming Discord event to notify about.",
        )
        return

    channel = await bot.get_announcement_channel()
    if channel is None:
        await send_ephemeral(interaction, "I could not access the configured announcement channel.")
        return

    reminder_type = test_notification_type(event)
    lines = compose_reminder_lines(event, config, reminder_type, f"<@&{mention_role_id}>")
    try:
        await channel.send(
            truncate_public_message(lines),
            allowed_mentions=role_only_allowed_mentions(mention_role_id),
        )
    except discord.DiscordException:
        logger.exception("Failed to send notification for event %s.", event["discord_event_id"])
        await send_ephemeral(interaction, "I could not send the notification to the announcement channel.")
        return

    log_abuse_event(
        log_event_type,
        interaction.user.id,
        event["discord_event_id"],
        reminder_type,
    )
    await send_ephemeral(
        interaction,
        f"Sent a {reminder_label(reminder_type)} notification to the announcement channel mentioning {mention_label}.",
    )


async def agenda_item_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> list[app_commands.Choice[int]]:
    event = get_next_tracked_event()
    if event is None:
        return []

    current_lower = current.lower()
    choices = []
    for row in list_agenda_items(event["discord_event_id"]):
        submitter = sanitize_display_text(row["submitted_by_display_name"] or "Unknown")
        label = f"[{row['id']}] {sanitize_display_text(row['item_text'])} - {submitter}"
        if current_lower and current_lower not in label.lower():
            continue
        choices.append(app_commands.Choice(name=label[:100], value=int(row["id"])))
    return choices[:25]


@bot.tree.command(name="agenda_remove", description="Remove an agenda item from the next meeting.")
@app_commands.describe(agenda_item="Agenda item to remove")
@app_commands.autocomplete(agenda_item=agenda_item_autocomplete)
async def agenda_remove(interaction: discord.Interaction, agenda_item: int) -> None:
    if not await require_alumni_board_role(interaction):
        return

    await defer_ephemeral(interaction)
    await bot.sync_events()
    if remove_agenda_item(agenda_item):
        logger.info("User %s removed agenda item %s.", interaction.user.id, agenda_item)
        log_abuse_event("agenda_removed", interaction.user.id, details=f"agenda_item_id={agenda_item}")
        await send_ephemeral(interaction, f"Removed agenda item {agenda_item}.")
    else:
        await send_ephemeral(interaction, "I could not find that active agenda item.")


def main() -> None:
    init_db()
    bot.run(config.discord_token)


if __name__ == "__main__":
    main()
