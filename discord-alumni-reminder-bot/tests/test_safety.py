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
os.environ.setdefault("ALUMNI_BOARD_ROLE_ID", "987")
os.environ.setdefault("TIMEZONE", "America/New_York")

import bot  # noqa: E402


class FakeScheduledEvent:
    def __init__(self, name: str, start_time, status_name: str = "scheduled") -> None:
        self.name = name
        self.start_time = start_time
        self.status = type("Status", (), {"name": status_name})()


class FakeRole:
    def __init__(self, role_id: int) -> None:
        self.id = role_id


class FakeUser:
    def __init__(self, role_ids: list[int]) -> None:
        self.roles = [FakeRole(role_id) for role_id in role_ids]


class FakeInteraction:
    def __init__(self, role_ids: list[int]) -> None:
        self.user = FakeUser(role_ids)


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

    def test_agenda_rapidfire_has_no_per_user_cooldown_or_cap(self) -> None:
        bot.add_agenda_item(self.event_id, "First item", 42, "Member")
        bot.add_agenda_item(self.event_id, "Second item", 42, "Member")
        bot.add_agenda_item(self.event_id, "Third item", 42, "Member")

        self.assertIsNone(bot.agenda_write_rejection_reason(bot.get_tracked_event(self.event_id), 42))
        self.assertEqual(bot.count_user_agenda_items(self.event_id, 42), 3)

    def test_rejected_write_cooldown(self) -> None:
        for _ in range(bot.REJECTED_WRITE_LIMIT):
            bot.record_rejected_write(99, self.event_id, "bad input")

        self.assertTrue(bot.user_has_rejection_cooldown(99))
        self.assertIn("Too many", bot.agenda_write_rejection_reason(bot.get_tracked_event(self.event_id), 99))

    def test_elevated_commands_require_alumni_board_role(self) -> None:
        self.assertTrue(bot.user_can_use_elevated_commands(FakeInteraction([bot.config.alumni_board_role_id])))
        self.assertFalse(bot.user_can_use_elevated_commands(FakeInteraction([bot.config.alumni_role_id])))

    def test_notification_composer_differs_only_by_role_mention(self) -> None:
        event = bot.get_tracked_event(self.event_id)

        board_lines = bot.compose_reminder_lines(event, bot.config, "seven_day", f"<@&{bot.config.alumni_board_role_id}>")
        alumni_lines = bot.compose_reminder_lines(event, bot.config, "seven_day", f"<@&{bot.config.alumni_role_id}>")

        self.assertEqual(
            board_lines[0].replace(str(bot.config.alumni_board_role_id), str(bot.config.alumni_role_id)),
            alumni_lines[0],
        )
        self.assertEqual(board_lines[1:], alumni_lines[1:])

    def test_notification_composer_includes_agenda_help_and_guide_link(self) -> None:
        event = bot.get_tracked_event(self.event_id)

        lines = bot.compose_reminder_lines(event, bot.config, "seven_day", f"<@&{bot.config.alumni_role_id}>")

        self.assertIn("Add an agenda item with:", lines)
        self.assertIn('/agenda_add item:"Your topic here"', lines)
        self.assertIn("Learn more about the bot:", lines)
        self.assertIn(bot.BOT_GUIDE_URL, lines)

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

    def test_event_eligibility_does_not_require_name_filter(self) -> None:
        event = FakeScheduledEvent("Completely Different Event Name", bot.utc_now() + timedelta(days=3))

        self.assertIsNone(bot.event_rejection_reason(event, bot.config))


if __name__ == "__main__":
    unittest.main()
