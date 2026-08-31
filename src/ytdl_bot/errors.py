from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class ErrorCode(str, Enum):
    INVALID_URL = "INVALID_URL"
    INSPECT_FAILED = "INSPECT_FAILED"
    MEDIA_REJECTED = "MEDIA_REJECTED"
    FORMAT_UNAVAILABLE = "FORMAT_UNAVAILABLE"
    LIMIT_REACHED = "LIMIT_REACHED"
    DOWNLOAD_FAILED = "DOWNLOAD_FAILED"
    MERGE_FAILED = "MERGE_FAILED"
    TRANSCODE_FAILED = "TRANSCODE_FAILED"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    UPLOAD_FAILED = "UPLOAD_FAILED"
    UPLOAD_TOO_LARGE = "UPLOAD_TOO_LARGE"
    REQUEST_EXPIRED = "REQUEST_EXPIRED"


@dataclass
class BotError(RuntimeError):
    code: ErrorCode
    stage: str
    user_message: str
    diagnostic: str = ""

    def __str__(self) -> str:
        return self.user_message


def redact(text: str) -> str:
    value = str(text)
    value = re.sub(r"https://api\.telegram\.org/bot[^/\s]+", "[REDACTED_BOT_API]", value, flags=re.I)
    value = re.sub(r"(?i)(token|password|passwd|proxy)(=|:)[^\s]+", r"\1\2[REDACTED]", value)
    value = re.sub(r"\b\d{6,}:[A-Za-z0-9_-]{20,}\b", "[REDACTED_TOKEN]", value)
    value = re.sub(r"(?i)https?://[^\s/@:]+:[^\s/@]+@", "https://[REDACTED]@", value)
    value = re.sub(r"/(?:Users|home|private|tmp|var)/[^\s]+", "[REDACTED_PATH]", value)
    return value[:4000]
