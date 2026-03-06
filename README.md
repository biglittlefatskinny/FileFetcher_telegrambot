# 📥 File Fetcher Bot

A Telegram bot that downloads files from direct URLs and sends them to the requester.
Built for users in restricted areas where only Telegram is accessible.

---

## One-Line Install

Run this on your Linux server as root (Ubuntu/Debian/RHEL/Fedora supported):

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/biglittlefatskinny/FileFetcher_telegrambot/main/install.sh)
```

The installer will:
1. Install Python and system dependencies
2. Clone the repository to `/opt/filefetcher-bot`
3. Ask for your bot token and limits (all have sensible defaults)
4. Create a systemd service and start the bot automatically
5. Install the `filefetcher` management command globally

> **Get a bot token:** Message `@BotFather` on Telegram → `/newbot`

---

## Managing the Bot

After install, use the `filefetcher` command from anywhere:

```bash
filefetcher status      # service state, current limits, recent logs
filefetcher logs        # live log output  (Ctrl+C to exit)
filefetcher config      # edit settings
filefetcher restart     # restart after config changes
filefetcher update      # pull latest version and restart
filefetcher stop        # stop the bot
filefetcher start       # start the bot
filefetcher uninstall   # remove everything cleanly
```

---

## Configuration

Settings live in `/opt/filefetcher-bot/.env`. Edit with `sudo filefetcher config`, then `sudo filefetcher restart`.

| Variable | Default | Description |
|---|---|---|
| `FILEFETCHER_BOT_TOKEN` | *(required)* | Telegram bot token from @BotFather |
| `FILEFETCHER_MAX_FILE_SIZE_MB` | `45` | Max size per file (Telegram cap: 50 MB) |
| `FILEFETCHER_MAX_HOURLY_MB` | `200` | Per-user rolling 1-hour download quota |
| `FILEFETCHER_MAX_DAILY_MB` | `1000` | Per-user rolling 24-hour download quota |
| `FILEFETCHER_MAX_CONCURRENT` | `6` | Max simultaneous downloads |
| `FILEFETCHER_RATE_LIMIT_RPM` | `10` | Max URL requests per minute per user |
| `FILEFETCHER_RATE_LIMIT_BURST` | `3` | Initial burst allowance per user |
| `FILEFETCHER_DOWNLOAD_TIMEOUT` | `120` | Seconds before a download times out |
| `FILEFETCHER_DOMAIN_ALLOWLIST` | *(empty)* | Comma-separated allowed domains — empty = all |
| `FILEFETCHER_LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `FILEFETCHER_JSON_LOGS` | `0` | Set to `1` for JSON logs (Loki, Datadog, etc.) |

> **Note on the allowlist:** it applies only to the URL the user submits, not to redirect destinations.
> Sites like GitHub redirect to CDN servers (CloudFront, etc.) — those are allowed through automatically
> as long as they don't resolve to a private IP. Only the initial domain needs to be on the list.
>
> **GitHub example:**
> ```
> FILEFETCHER_DOMAIN_ALLOWLIST=github.com,githubusercontent.com
> ```

---

## Bot Commands (for users)

| Command | Description |
|---|---|
| `/start` | Welcome message with current limits |
| `/help` | Usage instructions |
| `/status` | Your current hourly and daily quota usage |
| `/cancel` | Cancel your active download |

---

## How It Works

1. User sends a direct download URL
2. Bot validates the URL (blocks internal IPs — SSRF protection)
3. Bot streams the file to a temp location (enforcing the size cap)
4. Bot checks the user's rolling quota — rejects if exceeded
5. Bot uploads the file to Telegram and immediately deletes the temp file

---

## Security

- **SSRF protection** — blocks all private/loopback/metadata IPs (AWS IMDS, etc.)
- **Redirect validation** — final redirect destination is also validated
- **Domain allowlist** — optionally restrict to specific domains only
- **Per-user rate limiting** — token bucket prevents request flooding
- **Temp files** — deleted immediately after sending, never stored permanently
- **Least privilege** — bot runs as an unprivileged `filefetcher` system user

---

## Manual Install (alternative to one-liner)

```bash
git clone https://github.com/biglittlefatskinny/FileFetcher_telegrambot.git
cd FileFetcher_telegrambot
sudo bash install.sh
```

---

## Project Structure

```
filefetcher/
    main.py          Entry point and app wiring
    config.py        Settings loaded from environment variables
    handlers.py      Telegram commands and message handlers
    downloader.py    Streaming file downloader with size limit
    quota.py         Per-user rolling hourly/daily quota tracking
    limiter.py       Token-bucket rate limiter and /cancel tracker
    security.py      URL validation and SSRF protection
    log_setup.py     Structured logging (plain or JSON)
install.sh           One-line installer
manage.sh            Management script (installed as `filefetcher` command)
requirements.txt     Python dependencies
.env.example         Configuration template
```

---

## License

MIT
