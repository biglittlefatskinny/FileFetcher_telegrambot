"""Streaming file downloader for File Fetcher Bot.

Downloads a URL to a temporary file, enforcing a per-file size cap.
The caller is responsible for deleting the temp file after use.

SSRF protection: the final redirect destination is validated before
accepting any bytes, so the bot cannot be abused as an open-redirect
proxy to internal services.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile
from urllib.parse import unquote, urlparse

import aiohttp

from .config import Settings
from .security import validate_redirect_target

logger = logging.getLogger(__name__)

MB = 1024 * 1024

# Maps Content-Type base types to file extensions
_CONTENT_TYPE_EXTS: dict[str, str] = {
    "application/pdf": ".pdf",
    "application/zip": ".zip",
    "application/x-zip-compressed": ".zip",
    "application/gzip": ".gz",
    "application/x-tar": ".tar",
    "application/x-rar-compressed": ".rar",
    "application/x-7z-compressed": ".7z",
    "application/octet-stream": ".bin",
    "text/plain": ".txt",
    "text/html": ".html",
    "text/csv": ".csv",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "video/x-matroska": ".mkv",
    "audio/mpeg": ".mp3",
    "audio/ogg": ".ogg",
    "audio/wav": ".wav",
    "application/json": ".json",
    "application/xml": ".xml",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/msword": ".doc",
    "application/vnd.ms-excel": ".xls",
}

_CONTENT_DISP_RE = re.compile(
    r'filename\*?=["\']?(?:UTF-8\'\')?([^"\';\r\n]+)', re.IGNORECASE
)


class FileTooLargeError(Exception):
    def __init__(self, limit_mb: int) -> None:
        self.limit_mb = limit_mb
        super().__init__(f"File exceeds the {limit_mb} MB size limit.")


class DownloadError(Exception):
    pass


def _guess_filename(
    url: str,
    content_disposition: str | None,
    content_type: str | None,
) -> str:
    """Best-effort filename from Content-Disposition, URL path, or Content-Type."""
    if content_disposition:
        m = _CONTENT_DISP_RE.search(content_disposition)
        if m:
            return unquote(m.group(1).strip())

    path = urlparse(url).path
    name = os.path.basename(unquote(path))
    if name and "." in name:
        return name

    ext = ""
    if content_type:
        base_ct = content_type.split(";")[0].strip().lower()
        ext = _CONTENT_TYPE_EXTS.get(base_ct, "")
    return f"file{ext}" if ext else "file.bin"


class Downloader:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._session: aiohttp.ClientSession | None = None
        self.sem = asyncio.Semaphore(settings.max_concurrent_downloads)

    async def start(self) -> None:
        connector = aiohttp.TCPConnector(
            limit=self.settings.max_concurrent_downloads * 3,
            ssl=True,
            ttl_dns_cache=300,
            enable_cleanup_closed=True,
        )
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=self.settings.download_timeout),
            connector=connector,
            headers={"User-Agent": "FileFetcherBot/1.0 (Telegram file-fetching bot)"},
        )

    async def stop(self) -> None:
        if self._session:
            try:
                await self._session.close()
            except Exception:
                pass

    async def download(self, url: str) -> tuple[str, str, int]:
        """Download the file at *url*.

        Returns ``(tmp_path, filename, byte_count)``.
        The caller **must** delete ``tmp_path`` when done.

        Raises:
            FileTooLargeError  – file exceeds configured max_file_size_mb
            DownloadError      – HTTP error or network failure
        """
        if self._session is None:
            raise RuntimeError("Downloader not started.")

        max_bytes = self.settings.max_file_size_mb * MB

        async with self.sem:
            try:
                async with self._session.get(
                    url, allow_redirects=True, max_redirects=5
                ) as resp:
                    # SSRF check on the final redirect destination
                    validate_redirect_target(str(resp.url), self.settings)
                    resp.raise_for_status()

                    # Reject early if Content-Length already exceeds the limit
                    if resp.content_length is not None and resp.content_length > max_bytes:
                        raise FileTooLargeError(self.settings.max_file_size_mb)

                    filename = _guess_filename(
                        str(resp.url),
                        resp.headers.get("Content-Disposition"),
                        resp.headers.get("Content-Type"),
                    )
                    suffix = os.path.splitext(filename)[1] or ".bin"

                    # Stream to a temp file, enforcing the byte cap
                    tmp_fd, tmp_path = tempfile.mkstemp(suffix=suffix)
                    downloaded = 0
                    try:
                        with os.fdopen(tmp_fd, "wb") as f:
                            async for chunk in resp.content.iter_chunked(64 * 1024):
                                downloaded += len(chunk)
                                if downloaded > max_bytes:
                                    raise FileTooLargeError(self.settings.max_file_size_mb)
                                f.write(chunk)
                    except Exception:
                        try:
                            os.unlink(tmp_path)
                        except OSError:
                            pass
                        raise

                    return tmp_path, filename, downloaded

            except FileTooLargeError:
                raise
            except aiohttp.ClientResponseError as exc:
                raise DownloadError(f"HTTP {exc.status}: {exc.message}") from exc
            except aiohttp.ClientError as exc:
                raise DownloadError(str(exc)) from exc
            except asyncio.TimeoutError as exc:
                raise DownloadError("Download timed out.") from exc
