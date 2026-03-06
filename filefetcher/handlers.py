"""Telegram command and message handlers for File Fetcher Bot.

Handler layout
--------------
Commands : start, help, status, cancel
Messages : text_router — extracts the first URL and triggers a download

Flow for each download
----------------------
1. Extract and validate URL (format + SSRF check).
2. Rate-limit check (token bucket per user).
3. Register the asyncio Task so /cancel can interrupt it.
4. Stream the file to a temp path (size-limited).
5. Check per-user quota (hourly + daily).
6. Send the file via Telegram reply_document.
7. Record usage and delete the temp file.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re

import telegram.error

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import ContextTypes

from .downloader import DownloadError, Downloader, FileTooLargeError
from .limiter import RateLimitError, RateLimiter, TaskTracker
from .quota import QuotaExceeded, QuotaTracker
from .security import UrlValidationError, validate_url

logger = logging.getLogger(__name__)

_URL_RE = re.compile(r"https?://\S{3,}", re.IGNORECASE)


# ── Context helpers ────────────────────────────────────────────────────────────

def _uid(update: Update) -> int:
    return update.effective_user.id if update.effective_user else 0


def _settings(context: ContextTypes.DEFAULT_TYPE):
    return context.application.bot_data["settings"]


def _downloader(context: ContextTypes.DEFAULT_TYPE) -> Downloader:
    return context.application.bot_data["downloader"]


def _quota(context: ContextTypes.DEFAULT_TYPE) -> QuotaTracker:
    return context.application.bot_data["quota"]


def _tracker(context: ContextTypes.DEFAULT_TYPE) -> TaskTracker:
    return context.application.bot_data["task_tracker"]


def _limiter(context: ContextTypes.DEFAULT_TYPE) -> RateLimiter:
    return context.application.bot_data["rate_limiter"]


def _fmt_time(seconds: float) -> str:
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def _fmt_mb(b: int) -> str:
    return f"{b / (1024 * 1024):.1f}"


# ── Rate-limit guard ───────────────────────────────────────────────────────────

async def _rate_check(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Return False (and send an error) if the user is rate-limited."""
    try:
        await _limiter(context).check(_uid(update))
        return True
    except RateLimitError as exc:
        await update.effective_message.reply_text(
            f"Too many requests. Please wait {exc.retry_after:.0f}s before trying again."
        )
        return False


# ── Commands ──────────────────────────────────────────────────────────────────

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    s = _settings(context)
    await update.effective_message.reply_text(
        "📥 *File Fetcher Bot*\n\n"
        "Send me any direct download URL and I'll fetch the file and deliver it to you\\.\n\n"
        "*Limits per user:*\n"
        f"• Max file size: `{s.max_file_size_mb} MB`\n"
        f"• Hourly quota: `{s.max_hourly_mb} MB`\n"
        f"• Daily quota: `{s.max_daily_mb} MB`\n\n"
        "Just paste a URL to get started\\!\n"
        "Use /help for more info or /status to check your quota\\.",
        parse_mode="MarkdownV2",
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    s = _settings(context)
    await update.effective_message.reply_text(
        "*File Fetcher Bot — Help*\n\n"
        "*How to use:*\n"
        "1\\. Paste any direct download link\n"
        "2\\. The bot downloads the file\n"
        "3\\. You receive it right here\n\n"
        "*Commands:*\n"
        "`/start` — Welcome message\n"
        "`/help` — This help message\n"
        "`/status` — Your current quota usage\n"
        "`/cancel` — Cancel your active download\n\n"
        "*Limits:*\n"
        f"• Max file size: `{s.max_file_size_mb} MB`\n"
        f"• Per\\-user hourly quota: `{s.max_hourly_mb} MB`\n"
        f"• Per\\-user daily quota: `{s.max_daily_mb} MB`\n\n"
        "*Tips:*\n"
        "• Works with any direct file link \\(ZIP, PDF, MP3, MP4, etc\\.\\)\n"
        "• Telegram bots cannot send files larger than 50 MB\n"
        "• Quotas reset on a rolling basis, not at a fixed midnight",
        parse_mode="MarkdownV2",
    )


async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = _uid(update)
    u = await _quota(context).usage(uid)
    active = _tracker(context).active_count

    h_reset = (
        f" \\(resets in {_fmt_time(u['hourly_resets_in'])}\\)"
        if u["hourly_used"] > 0 else ""
    )
    d_reset = (
        f" \\(resets in {_fmt_time(u['daily_resets_in'])}\\)"
        if u["daily_used"] > 0 else ""
    )

    await update.effective_message.reply_text(
        "*Your Download Status*\n\n"
        f"*Hourly:* `{_fmt_mb(u['hourly_used'])}` / `{_fmt_mb(u['hourly_limit'])}` MB{h_reset}\n"
        f"*Daily:* `{_fmt_mb(u['daily_used'])}` / `{_fmt_mb(u['daily_limit'])}` MB{d_reset}\n\n"
        f"*Active downloads on bot:* `{active}`",
        parse_mode="MarkdownV2",
    )


async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cancelled = await _tracker(context).cancel(_uid(update))
    if cancelled:
        await update.effective_message.reply_text("✅ Download cancelled.")
    else:
        await update.effective_message.reply_text("ℹ️ No active download to cancel.")


# ── Message router ─────────────────────────────────────────────────────────────

async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if msg is None:
        return

    text = (msg.text or "").strip()
    m = _URL_RE.search(text)
    if not m:
        await msg.reply_text(
            "Please send a direct download URL starting with http:// or https://"
        )
        return

    if not await _rate_check(update, context):
        return

    await _process_download(update, context, m.group(0))


# ── Core download handler ──────────────────────────────────────────────────────

async def _process_download(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    raw_url: str,
) -> None:
    msg = update.effective_message
    if msg is None:
        return

    uid = _uid(update)
    settings = _settings(context)
    downloader = _downloader(context)
    quota = _quota(context)
    tracker = _tracker(context)

    # Validate URL: format check + DNS SSRF pre-flight
    try:
        url = validate_url(raw_url, settings)
    except UrlValidationError as exc:
        await msg.reply_text(f"❌ Invalid URL: {exc}")
        return

    # Register this coroutine's task so /cancel can interrupt it
    current_task = asyncio.current_task()
    if current_task:
        await tracker.register(uid, current_task)

    status_msg = await msg.reply_text("⏳ Downloading…")
    tmp_path: str | None = None

    try:
        # Keep showing an upload indicator while we work
        action_task = asyncio.create_task(_upload_action_loop(context.bot, msg.chat_id))
        try:
            tmp_path, filename, byte_count = await downloader.download(url)
        finally:
            action_task.cancel()
            try:
                await action_task
            except asyncio.CancelledError:
                pass

        # Check whether this file fits inside the user's rolling quotas
        await quota.check(uid, byte_count)

        # Send the file
        await status_msg.edit_text("📤 Sending file…")
        with open(tmp_path, "rb") as f:
            await msg.reply_document(document=f, filename=filename)

        # Only record usage after a successful upload
        await quota.record(uid, byte_count)

        try:
            await status_msg.delete()
        except Exception:
            pass

        logger.info(
            "event=download_ok uid=%d url=%s filename=%s size_mb=%.2f",
            uid, url, filename, byte_count / (1024 * 1024),
        )

    except asyncio.CancelledError:
        try:
            await status_msg.edit_text("❌ Download cancelled.")
        except Exception:
            pass
        raise

    except FileTooLargeError as exc:
        await status_msg.edit_text(
            f"❌ File too large. Maximum allowed size is {exc.limit_mb} MB.\n\n"
            "Telegram bots cannot send files larger than 50 MB."
        )

    except QuotaExceeded as exc:
        await status_msg.edit_text(
            f"❌ {exc.window.capitalize()} quota of {exc.limit_mb} MB reached.\n"
            f"Resets in {_fmt_time(exc.resets_in)}.\n\n"
            "Use /status to see your current usage."
        )

    except DownloadError as exc:
        await status_msg.edit_text(f"❌ Download failed: {exc}")
        logger.warning("event=download_fail uid=%d url=%s error=%r", uid, url, exc)

    except UrlValidationError as exc:
        # Can happen when the URL redirects to a blocked destination
        await status_msg.edit_text(f"❌ Blocked redirect: {exc}")

    except telegram.error.NetworkError as exc:
        if "entity too large" in str(exc).lower():
            await status_msg.edit_text(
                "❌ File too large for Telegram.\n\n"
                "Telegram bots have a hard 50 MB upload limit. "
                "This file exceeds that limit and cannot be delivered."
            )
            logger.warning("event=telegram_too_large uid=%d url=%s", uid, url)
        else:
            await status_msg.edit_text(f"❌ Telegram network error: {exc}")
            logger.warning("event=telegram_error uid=%d url=%s error=%r", uid, url, exc)

    except Exception:
        await status_msg.edit_text("❌ An unexpected error occurred. Please try again.")
        logger.exception("event=unexpected_error uid=%d url=%s", uid, url)

    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        await tracker.unregister(uid)


# ── Background helpers ─────────────────────────────────────────────────────────

async def _upload_action_loop(bot, chat_id: int) -> None:
    """Send UPLOAD_DOCUMENT chat action every 4 s until cancelled."""
    while True:
        try:
            await bot.send_chat_action(chat_id=chat_id, action=ChatAction.UPLOAD_DOCUMENT)
        except Exception:
            pass
        await asyncio.sleep(4)
