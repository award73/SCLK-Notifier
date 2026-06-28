# SCLK Notifier Bot Guide

SCLK Notifier helps the alumni group keep track of upcoming meetings, collect agenda items, and send meeting reminders in Discord.

## Add To The Agenda

Use this slash command in Discord:

```text
/agenda_add item:"Your topic here"
```

Agenda items attach to the next upcoming Discord Scheduled Event that the bot can see.

Good agenda items are short, specific, and focused on one topic. If you have several topics, add them one at a time.

## View The Agenda

Use:

```text
/agenda
```

The bot will show the agenda for the next upcoming meeting.

## See The Next Meeting

Use:

```text
/next_meeting
```

This shows the next tracked meeting, meeting time, Discord event link, and agenda summary.

## Reminder Posts

Reminder posts are based on Discord Scheduled Events. Please RSVP on the Discord event so organizers have a better sense of attendance.

## Board Commands

Alumni Board members can sync events, list tracked events, remove agenda items, reset reminder flags, and send notification tests.

The regular alumni-facing notification command is `/notify`, so use it carefully because it tags the Alumni role.
