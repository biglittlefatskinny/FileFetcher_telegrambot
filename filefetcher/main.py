"""File Fetcher Bot — entry point.

Run directly:
    python -m filefetcher.main

Or via the systemd service defined in file-fetcher-bot.service.
"""
from __future__ import annotations

import logging

from dotenv import load_dotenv
load_dotenv()  # load .env before anything reads os.environ

from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
)

from . import handlers as h
from . import log_setup
from .config import load_settings
from .downloader import Downloader
from .limiter import RateLimiter, TaskTracker
from .quota import QuotaTracker

logger = logging.getLogger("filefetcher.main")


async def _on_startup(app: Application) -> None:  # type: ignore[type-arg]
    settings = app.bot_data["settings"]

    downloader = Downloader(settings)
    await downloader.start()
    app.bot_data["downloader"] = downloader

    app.bot_data["quota"] = QuotaTracker(
        max_hourly_bytes=settings.max_hourly_mb * 1024 * 1024,
        max_daily_bytes=settings.max_daily_mb * 1024 * 1024,
    )
    app.bot_data["task_tracker"] = TaskTracker()
    app.bot_data["rate_limiter"] = RateLimiter(
        rpm=settings.rate_limit_rpm,
        burst=settings.rate_limit_burst,
    )

    logger.info(
        "event=bot_started max_file_mb=%d max_hourly_mb=%d max_daily_mb=%d",
        settings.max_file_size_mb,
        settings.max_hourly_mb,
        settings.max_daily_mb,
    )


async def _on_shutdown(app: Application) -> None:  # type: ignore[type-arg]
    downloader: Downloader | None = app.bot_data.get("downloader")
    if downloader is not None:
        await downloader.stop()
    logger.info("event=bot_stopped")


def build_app(token: str, settings) -> Application:  # type: ignore[type-arg]
    app = (
        Application.builder()
        .token(token)
        .post_init(_on_startup)
        .post_shutdown(_on_shutdown)
        .concurrent_updates(True)
        .build()
    )
    app.bot_data["settings"] = settings

    app.add_handler(CommandHandler("start",  h.start_cmd))
    app.add_handler(CommandHandler("help",   h.help_cmd))
    app.add_handler(CommandHandler("status", h.status_cmd))
    app.add_handler(CommandHandler("cancel", h.cancel_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, h.text_router))

    return app


def main() -> None:
    settings = load_settings()

    if not settings.bot_token:
        raise RuntimeError(
            "FILEFETCHER_BOT_TOKEN environment variable is not set.\n"
            "Copy .env.example to .env, fill in your token, then run again."
        )

    log_setup.setup(level=settings.log_level, json_logs=settings.json_logs)
    logger.info(
        "event=starting max_file_mb=%d max_hourly_mb=%d max_daily_mb=%d "
        "max_concurrent=%d",
        settings.max_file_size_mb,
        settings.max_hourly_mb,
        settings.max_daily_mb,
        settings.max_concurrent_downloads,
    )

    app = build_app(settings.bot_token, settings)
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
