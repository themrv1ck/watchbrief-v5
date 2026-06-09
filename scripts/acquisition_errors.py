"""Typed acquisition-layer errors for WatchBrief V5."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


PLATFORM_RESTRICTION = "platform_restriction"
RESOLVER_FAILED = "resolver_failed"
SUBTITLE_UNAVAILABLE = "subtitle_unavailable"
AUDIO_DOWNLOAD_FAILED = "audio_download_failed"
AUDIO_DOWNLOAD_TIMEOUT = "audio_download_timeout"
AUDIO_DOWNLOAD_BLOCKED = "audio_download_blocked"
TRANSCRIBE_FAILED = "transcribe_failed"
TRANSCRIBER_UNAVAILABLE = "transcriber_unavailable"
FORMAT_CONVERSION_FAILED = "format_conversion_failed"
BILIBILI_COOKIE_MISSING = "bilibili_cookie_missing"
BILIBILI_COOKIE_EXPIRED = "bilibili_cookie_expired"
BILIBILI_412_BLOCKED = "bilibili_412_blocked"
BILIBILI_METADATA_NOT_FOUND = "bilibili_metadata_not_found"
BILIBILI_LIST_EXPANSION_FAILED = "bilibili_list_expansion_failed"
BILIBILI_CID_NOT_FOUND = "bilibili_cid_not_found"
BILIBILI_PLAYINFO_UNAVAILABLE = "bilibili_playinfo_unavailable"
BILIBILI_PLAYURL_API_FAILED = "bilibili_playurl_api_failed"
BILIBILI_PLAYURL_API_FORBIDDEN = "bilibili_playurl_api_forbidden"
BILIBILI_PLAYURL_API_NO_DASH_AUDIO = "bilibili_playurl_api_no_dash_audio"
BILIBILI_AUDIO_URL_NOT_FOUND = "bilibili_audio_url_not_found"
BILIBILI_AUDIO_DOWNLOAD_FAILED = "bilibili_audio_download_failed"
BILIBILI_AUDIO_CONVERT_FAILED = "bilibili_audio_convert_failed"
XIAOHONGSHU_BOARD_RESOLVER_FAILED = "xiaohongshu_board_resolver_failed"
XIAOHONGSHU_BOARD_EMPTY = "xiaohongshu_board_empty"
LOGIN_REQUIRED_FOR_SUBTITLE = "login_required_for_subtitle"
NO_SUBTITLE_AVAILABLE = "no_subtitle_available"
BILIBILI_SUBTITLE_API_FAILED = "bilibili_subtitle_api_failed"
BILIBILI_SUBTITLE_DOWNLOAD_FAILED = "bilibili_subtitle_download_failed"
BILIBILI_SUBTITLE_JSON_INVALID = "bilibili_subtitle_json_invalid"


@dataclass
class AcquisitionError(RuntimeError):
    stage: str
    reason_code: str
    message: str
    command: Optional[list[str]] = None
    stderr: str = ""

    def __str__(self) -> str:
        return f"{self.stage}:{self.reason_code}: {self.message}"


class PlatformRestrictionError(AcquisitionError):
    def __init__(self, stage: str, message: str, command: Optional[list[str]] = None, stderr: str = "") -> None:
        super().__init__(stage, PLATFORM_RESTRICTION, message, command, stderr)


class ResolverError(AcquisitionError):
    def __init__(self, message: str, command: Optional[list[str]] = None, stderr: str = "") -> None:
        super().__init__("resolver", RESOLVER_FAILED, message, command, stderr)


class BilibiliResolverError(AcquisitionError):
    def __init__(
        self,
        reason_code: str,
        message: str,
        command: Optional[list[str]] = None,
        stderr: str = "",
        debug: Optional[dict] = None,
    ) -> None:
        super().__init__("resolver", reason_code, message, command, stderr)
        self.debug = debug or {}


class XiaohongshuBoardResolverError(AcquisitionError):
    def __init__(
        self,
        reason_code: str,
        message: str,
        command: Optional[list[str]] = None,
        stderr: str = "",
        debug: Optional[dict] = None,
    ) -> None:
        super().__init__("resolver", reason_code, message, command, stderr)
        self.debug = debug or {}


class SubtitleUnavailableError(AcquisitionError):
    def __init__(
        self,
        message: str,
        command: Optional[list[str]] = None,
        stderr: str = "",
        reason_code: str = SUBTITLE_UNAVAILABLE,
        debug: Optional[dict] = None,
    ) -> None:
        super().__init__("subtitle_fetcher", reason_code, message, command, stderr)
        self.debug = debug or {}


class BilibiliSubtitleError(AcquisitionError):
    def __init__(
        self,
        reason_code: str,
        message: str,
        command: Optional[list[str]] = None,
        stderr: str = "",
        debug: Optional[dict] = None,
    ) -> None:
        super().__init__("subtitle_fetcher", reason_code, message, command, stderr)
        self.debug = debug or {}


class AudioDownloadError(AcquisitionError):
    def __init__(
        self,
        message: str,
        command: Optional[list[str]] = None,
        stderr: str = "",
        reason_code: str = AUDIO_DOWNLOAD_FAILED,
    ) -> None:
        super().__init__("audio_downloader", reason_code, message, command, stderr)


class BilibiliAudioDownloadError(AcquisitionError):
    def __init__(
        self,
        reason_code: str,
        message: str,
        command: Optional[list[str]] = None,
        stderr: str = "",
        debug: Optional[dict] = None,
    ) -> None:
        super().__init__("audio_downloader", reason_code, message, command, stderr)
        self.debug = debug or {}


class AudioDownloadBlockedError(AcquisitionError):
    def __init__(self, message: str) -> None:
        super().__init__("audio_downloader", AUDIO_DOWNLOAD_BLOCKED, message)


class TranscriptionError(AcquisitionError):
    def __init__(self, message: str, command: Optional[list[str]] = None, stderr: str = "") -> None:
        super().__init__("transcriber", TRANSCRIBE_FAILED, message, command, stderr)


class TranscriberUnavailableError(AcquisitionError):
    def __init__(self, message: str, command: Optional[list[str]] = None, stderr: str = "") -> None:
        super().__init__("transcriber", TRANSCRIBER_UNAVAILABLE, message, command, stderr)


class FormatConversionError(AcquisitionError):
    def __init__(self, stage: str, message: str, command: Optional[list[str]] = None, stderr: str = "") -> None:
        super().__init__(stage, FORMAT_CONVERSION_FAILED, message, command, stderr)


def is_platform_restriction(stderr: str) -> bool:
    text = str(stderr or "").lower()
    markers = (
        "private video",
        "members-only",
        "sign in",
        "login required",
        "not available in your country",
        "copyright",
        "forbidden",
        "http error 403",
        "this video is unavailable",
        "permission",
        "not a bot",
        "bot check",
        "blocking requests from your ip",
        "confirm you are not a bot",
        "confirm you're not a bot",
        "confirm you’re not a bot",
    )
    return any(marker in text for marker in markers)


def is_format_conversion_failure(stderr: str) -> bool:
    text = str(stderr or "").lower()
    markers = ("ffmpeg", "conversion failed", "error converting", "postprocessing", "post-process")
    return any(marker in text for marker in markers)
