# Local Deploy And Redeploy Guide

This guide gets SCLK Notifier running from a Windows PC. It is the MVP deployment path until an always-on host is selected.

## First-Time Setup

1. Open PowerShell.

2. Clone the repo:

   ```powershell
   cd C:\Users\andre\Documents
   git clone https://github.com/award73/SCLK-Notifier.git
   cd SCLK-Notifier\discord-alumni-reminder-bot
   ```

   If the repo is already cloned:

   ```powershell
   cd C:\Users\andre\Documents\SCLK-Notifier\discord-alumni-reminder-bot
   ```

3. Create and activate a virtual environment:

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

   If PowerShell blocks activation because of execution policy, enable it only for the current terminal session:

   ```powershell
   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
   .\.venv\Scripts\Activate.ps1
   ```

4. Install dependencies:

   ```powershell
   pip install -r requirements.txt
   ```

5. Create `.env`:

   ```powershell
   Copy-Item .env.example .env
   ```

6. Fill in `.env`:

   ```text
   DISCORD_TOKEN=
   GUILD_ID=
   ANNOUNCEMENT_CHANNEL_ID=
   ALUMNI_ROLE_ID=
   TIMEZONE=America/New_York
   EVENT_NAME_FILTER=Alumni Association Monthly Meeting
   REMINDERS_ENABLED=true
   ```

   Do not commit `.env`.

7. Run local checks:

   ```powershell
   python -m py_compile bot.py
   python -m unittest discover -s tests
   ```

8. Start the bot:

   ```powershell
   python bot.py
   ```

9. Confirm the logs show the bot logged in and synced slash commands.

## Discord Smoke Test

In Discord:

1. Create a future native Discord Scheduled Event.
2. Make sure the event name contains `EVENT_NAME_FILTER`.
3. As a Manage Server admin, run `/event_sync`.
4. Run `/event_list`.
5. Run `/next_meeting`.
6. Run `/event_test_reminder reminder_type:"7-day reminder"`.
7. Run `/event_test_reminder reminder_type:"7-day reminder" send_to_channel:true` and confirm the test message mentions only you.
8. Run `/agenda_add item:"Test agenda item"`.
9. Run `/agenda`.
10. Add a test item containing `@everyone`, `@here`, a role mention, a user mention, a channel mention, a link, and markdown. Confirm no ping occurs when viewing `/agenda`.

## Redeploy Existing Local Bot

Use this when new code has been merged or you want to test a branch.

1. Stop the running bot process.

   In the PowerShell window running the bot, press `Ctrl+C`.

2. Update the repo:

   ```powershell
   cd C:\Users\andre\Documents\SCLK-Notifier
   git switch main
   git pull
   ```

   To test a review branch instead:

   ```powershell
   git switch codex/incremental-prs
   git pull
   ```

3. Activate the venv:

   ```powershell
   cd discord-alumni-reminder-bot
   .\.venv\Scripts\Activate.ps1
   ```

4. Update dependencies:

   ```powershell
   pip install -r requirements.txt
   ```

5. Run tests:

   ```powershell
   python -m py_compile bot.py
   python -m unittest discover -s tests
   ```

6. Confirm `.env` still has the correct Discord values and `REMINDERS_ENABLED=true`.

7. Restart the bot:

   ```powershell
   python bot.py
   ```

8. In Discord, run `/event_sync`, `/event_list`, `/next_meeting`, `/agenda_add`, and `/agenda`.

## Emergency Stop

To stop public reminder posts immediately:

1. Stop the bot process with `Ctrl+C`, or remove the bot's Send Messages permission in the announcement channel.
2. To keep the bot running but stop reminder posts, set this in `.env`:

   ```text
   REMINDERS_ENABLED=false
   ```

3. Restart the bot.

## Temporary Rollback

Use this if the latest code fails and you need to test a known-good commit.

1. Stop the bot process.

2. Show recent commits:

   ```powershell
   cd C:\Users\andre\Documents\SCLK-Notifier
   git log --oneline
   ```

3. Switch to a known-good commit:

   ```powershell
   git switch --detach <known-good-commit>
   ```

4. Run from that commit:

   ```powershell
   cd discord-alumni-reminder-bot
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   python -m unittest discover -s tests
   python bot.py
   ```

5. After testing, return to a branch:

   ```powershell
   cd C:\Users\andre\Documents\SCLK-Notifier
   git switch main
   ```

## Notes

- The bot only runs while `python bot.py` is running.
- If the PC sleeps, shuts down, loses internet, or the process stops, reminders will not send.
- Choose an always-on host before adding true automatic deployment.
