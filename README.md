# SCLK Notifier

SCLK Notifier is a Discord bot for alumni meeting reminders and agenda collection.

The bot uses native Discord Scheduled Events as the source of truth. Admins create and edit meetings in Discord, and the bot reads those events, tracks reminder state in SQLite, and posts custom reminders to a configured announcement channel.

## Member Guide

For basic Discord commands and how to add agenda items, see the [bot guide](docs/BOT_GUIDE.md).

## Quick Start For Testers

The app lives in `discord-alumni-reminder-bot/`.

For exact local install, test, run, redeploy, and rollback steps, see:

- [Local deploy guide](docs/LOCAL_DEPLOY.md)

Short version for an existing checkout on Windows:

```powershell
$RepoRoot = Join-Path $env:USERPROFILE 'Documents\SCLK-Notifier'
cd (Join-Path $RepoRoot 'discord-alumni-reminder-bot')
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m unittest discover -s tests
python bot.py
```

Keep `.env`, bot tokens, logs, and `alumni_bot.db` private.

## How Releases Work

This project's MVP release process is intentionally simple:

1. Open a pull request.
2. Wait for GitHub Actions CI to pass.
3. Merge to `main`.
4. Create and push a version tag:

   ```powershell
   git switch main
   git pull
   git tag v0.1.0
   git push origin v0.1.0
   ```

5. GitHub Actions runs the release workflow and creates a GitHub Release for the tag.

This is not automatic production deployment yet. Redeploying the bot still means updating the machine where the Python process runs and restarting `python bot.py`.

## How Friends Can Contribute

- Start with [CONTRIBUTING.md](CONTRIBUTING.md).
- Pick work from [TODO.md](TODO.md).
- Read [SECURITY.md](SECURITY.md) before touching tokens, deployment, or permissions.

Use small pull requests. Keep secrets out of commits.

## CI

GitHub Actions runs on pushes and pull requests to `main` and `codex/**` branches.

CI currently checks:

- Python syntax compilation for `bot.py`.
- Unit tests under `discord-alumni-reminder-bot/tests`.

## Project Layout

```text
discord-alumni-reminder-bot/  Discord bot source, tests, and local README
docs/                         Deployment and operations guides
specs/                        Spec Kit planning documents
.specify/                     Project constitution
.github/workflows/            CI and release workflows
```

## License

MIT. See [LICENSE](LICENSE).
