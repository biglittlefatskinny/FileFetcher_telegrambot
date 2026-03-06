"""Env-driven configuration for File Fetcher Bot.

All settings are read from environment variables at startup. Defaults are
chosen to be reasonable for a single-user VPS; tune via your .env file.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import FrozenSet


def _int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, default))
    except ValueError:
        return default


def _bool(key: str, default: bool = False) -> bool:
    return os.environ.get(key, "1" if default else "0").strip().lower() in {
        "1", "true", "yes",
    }


@dataclass(frozen=True)
class Settings:
    # Core
    bot_token: str
    log_level: str
    json_logs: bool

    # Download limits (per file and per user)
    max_file_size_mb: int       # hard limit per individual file
    max_hourly_mb: int          # per-user rolling 1-hour quota
    max_daily_mb: int           # per-user rolling 24-hour quota

    # Concurrency
    max_concurrent_downloads: int

    # Rate limiting (per user, token bucket)
    rate_limit_rpm: int         # max requests per minute per user
    rate_limit_burst: int       # initial burst allowance

    # Network
    download_timeout: int       # seconds before a download is aborted

    # Security
    domain_allowlist: FrozenSet[str]   # empty set = open mode (all domains)


def load_settings() -> Settings:
    raw_allowlist = os.environ.get("FILEFETCHER_DOMAIN_ALLOWLIST", "").strip()
    allowlist: FrozenSet[str] = frozenset(
        d.strip().lower() for d in raw_allowlist.split(",") if d.strip()
    )

    return Settings(
        bot_token=os.environ.get("FILEFETCHER_BOT_TOKEN", "").strip(),
        log_level=os.environ.get("FILEFETCHER_LOG_LEVEL", "INFO").upper(),
        json_logs=_bool("FILEFETCHER_JSON_LOGS"),

        max_file_size_mb=_int("FILEFETCHER_MAX_FILE_SIZE_MB", 45),
        max_hourly_mb=_int("FILEFETCHER_MAX_HOURLY_MB", 200),
        max_daily_mb=_int("FILEFETCHER_MAX_DAILY_MB", 1000),

        max_concurrent_downloads=_int("FILEFETCHER_MAX_CONCURRENT", 6),

        rate_limit_rpm=_int("FILEFETCHER_RATE_LIMIT_RPM", 10),
        rate_limit_burst=_int("FILEFETCHER_RATE_LIMIT_BURST", 3),

        download_timeout=_int("FILEFETCHER_DOWNLOAD_TIMEOUT", 120),

        domain_allowlist=allowlist,
    )
