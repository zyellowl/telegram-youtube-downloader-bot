from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any


class FormatChoice(str, Enum):
    MP3 = "mp3"
    VIDEO_360 = "360p"
    VIDEO_480 = "480p"
    VIDEO_720 = "720p"
    VIDEO_1080 = "1080p"

    @property
    def label(self) -> str:
        return "MP3" if self is FormatChoice.MP3 else self.value

    @property
    def height(self) -> int | None:
        if self is FormatChoice.MP3:
            return None
        return int(self.value.removesuffix("p"))


class DownloadKind(str, Enum):
    AUDIO = "audio"
    VIDEO = "video"
    DOCUMENT = "document"


@dataclass(frozen=True)
class MediaChoice:
    choice: FormatChoice
    label: str
    format_id: str | None = None
    height: int | None = None
    ext: str | None = None
    estimated_size: int | None = None
    plan_id: str | None = None


@dataclass(frozen=True)
class SelectionPlan:
    """Immutable agreement between inspect-time enumeration and download."""

    plan_id: str
    video_id: str
    canonical_url: str
    choice: FormatChoice
    selector: str
    target_short_edge: int | None
    require_audio: bool
    expected_kind: DownloadKind
    source_duration: float | None
    expires_at: float
    target_width: int | None = None
    target_height: int | None = None


@dataclass(frozen=True)
class MediaInfo:
    title: str
    duration: int | None
    webpage_url: str | None
    thumbnail: str | None
    choices: list[MediaChoice]
    rejection_reason: str | None = None
    video_id: str | None = None
    canonical_url: str | None = None
    plans: tuple[SelectionPlan, ...] = ()

    @property
    def is_rejected(self) -> bool:
        return self.rejection_reason is not None

    def get_choice(self, choice: FormatChoice) -> MediaChoice | None:
        return next((item for item in self.choices if item.choice is choice), None)

    def get_plan(self, plan_id: str | None) -> SelectionPlan | None:
        return next((item for item in self.plans if item.plan_id == plan_id), None) if plan_id else None


@dataclass(frozen=True)
class DownloadResult:
    path: Path
    title: str
    kind: DownloadKind
    size_bytes: int
    probe: "MediaProbe | None" = None


@dataclass(frozen=True)
class MediaProbe:
    duration: float
    video_streams: int
    audio_streams: int
    width: int | None
    height: int | None
    short_edge: int | None
    video_codec: str | None
    audio_codec: str | None
    format_name: str | None
    raw: dict[str, Any] | None = None


@dataclass(frozen=True)
class DeliveryDecision:
    can_send: bool
    method: str | None
    reason: str | None = None
