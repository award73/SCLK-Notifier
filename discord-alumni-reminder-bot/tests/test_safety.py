import os
import sys
import unittest
import uuid
from datetime import datetime, time, timedelta, timezone
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

os.environ.setdefault("DISCORD_TOKEN", "test-token")
os.environ.setdefault("GUILD_ID", "123")
os.environ.setdefault("ANNOUNCEMENT_CHANNEL_ID", "456")
os.environ.setdefault("ALUMNI_ROLE_ID", "789")
os.environ.setdefault("TIMEZONE", "America/New_York")
os.environ.setdefault("EVENT_NAME_FILTER", "Alumni")

import bot  # noqa: E402


class SafetyTestCase(unittest.TestCase):
    def setUp(self) -> None:
        scratch_dir = PROJECT_DIR / ".test-tmp"
        scratch_dir.mkdir(exist_ok=True)
        bot.DB_PATH = scratch_dir / f"{self._testMethodName}-{uuid.uuid4().hex}.db"
        bot.init_db()
        self.event_id = "event-1"
        now = bot.utc_now()
        with bot.get_db() as conn:
            conn.execute(
                """
                INSERT INTO tracked_events (
                    discord_event_id, name, start_time_utc, status,
                    first_seen_at_utc, last_synced_at_utc
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    self.event_id,
                    "Alumni Meeting",
                    bot.datetime_to_db(now + timedelta(days=2)),
                    "Scheduled",
                    bot.datetime_to_db(now),
                    bot.datetime_to_db(now),
                ),
            )

    def tearDown(self) -> None:
        if bot.DB_PATH.exists():
            bot.DB_PATH.unlink()

    def test_sanitize_display_text_neutralizes_mentions_and_markdown(self) -> None:
        raw = "@everyone @here <@123> <@!456> <@&789> <#222> **bold** `code`"

        sanitized = bot.sanitize_display_text(raw)

        self.assertNotIn("@everyone", sanitized)
        self.assertNotIn("@here", sanitized)
        self.assertNotIn("<@123>", sanitized)
        self.assertNotIn("<@!456>", sanitized)
        self.assertNotIn("<@&789>", sanitized)
        self.assertNotIn("<#222>", sanitized)
        self.assertIn("\\*\\*bold\\*\\*", sanitized)
        self.assertIn("\\`code\\`", sanitized)

    def test_agenda_cooldown_and_quotas(self) -> None:
        bot.add_agenda_item(self.event_id, "First item", 42, "Member")

        self.assertIn("wait", bot.agenda_write_rejection_reason(bot.get_tracked_event(self.event_id), 42).lower())

        old_time = bot.datetime_to_db(bot.utc_now() - timedelta(hours=2))
        with bot.get_db() as conn:
            conn.execute("UPDATE agenda_items SET created_at_utc = ?", (old_time,))
        bot.add_agenda_item(self.event_id, "Second item", 42, "Member")
        with bot.get_db() as conn:
            conn.execute("UPDATE agenda_items SET created_at_utc = ?", (old_time,))
        bot.add_agenda_item(self.event_id, "Third item", 42, "Member")
        with bot.get_db() as conn:
            conn.execute("UPDATE agenda_items SET created_at_utc = ?", (old_time,))

        self.assertIn("already have", bot.agenda_write_rejection_reason(bot.get_tracked_event(self.event_id), 42))

    def test_rejected_write_cooldown(self) -> None:
        for _ in range(bot.REJECTED_WRITE_LIMIT):
            bot.record_rejected_write(99, self.event_id, "bad input")

        self.assertTrue(bot.user_has_rejection_cooldown(99))
        self.assertIn("Too many", bot.agenda_write_rejection_reason(bot.get_tracked_event(self.event_id), 99))

    def test_public_message_truncation_preserves_footer(self) -> None:
        lines = ["Meeting time: <t:123:F>", "Discord event:", "https://discord.com/events/1/2", "x" * 3000]

        message = bot.truncate_public_message(lines)

        self.assertLessEqual(len(message), bot.PUBLIC_MESSAGE_MAX_CHARS + len("..."))
        self.assertIn("Meeting time", message)
        self.assertIn("Discord event", message)
        self.assertIn("Agenda truncated for safety", message)

    def test_tracked_events_schema_has_new_reminder_flags(self) -> None:
        with bot.get_db() as conn:
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(tracked_events)").fetchall()}

        self.assertIn("seven_day_sent", columns)
        self.assertIn("one_day_sent", columns)
        self.assertIn("day_of_sent", columns)

    def test_day_of_reminder_due_at_four_pm_local_before_event(self) -> None:
        local_tz = bot.config.timezone
        event_start = datetime(2026, 7, 1, 19, 30, tzinfo=local_tz).astimezone(timezone.utc)
        before_four = datetime(2026, 7, 1, 15, 59, tzinfo=local_tz).astimezone(timezone.utc)
        at_four = datetime.combine(
            datetime(2026, 7, 1).date(),
            time(hour=16),
            tzinfo=local_tz,
        ).astimezone(timezone.utc)
        after_start = datetime(2026, 7, 1, 20, 0, tzinfo=local_tz).astimezone(timezone.utc)

        self.assertFalse(bot.day_of_reminder_due(before_four, event_start, local_tz))
        self.assertTrue(bot.day_of_reminder_due(at_four, event_start, local_tz))
        self.assertFalse(bot.day_of_reminder_due(after_start, event_start, local_tz))


if __name__ == "__main__":
    unittest.main()
