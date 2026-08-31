from __future__ import annotations

import asyncio
import hashlib
import time
from typing import Any

from yt_dlp import YoutubeDL

from ytdl_bot.models import DownloadKind, FormatChoice, MediaChoice, MediaInfo, SelectionPlan
from ytdl_bot.runtime import youtube_js_runtimes
from ytdl_bot.url_utils import canonical_url_for_video_id, video_id_from_url

VIDEO_CHOICES = [FormatChoice.VIDEO_360, FormatChoice.VIDEO_480, FormatChoice.VIDEO_720, FormatChoice.VIDEO_1080]
PLAN_TTL_SECONDS = 30 * 60


def normalize_media_info(raw: dict[str, Any], max_duration_seconds: int | None = None) -> MediaInfo:
    title = str(raw.get("title") or "Untitled")
    duration = _float_or_none(raw.get("duration"))
    thumbnail = raw.get("thumbnail")
    if duration and max_duration_seconds and duration > max_duration_seconds:
        return _rejected(title, duration, thumbnail, f"Video is too long. Maximum is {max_duration_seconds} seconds.")
    if raw.get("_type") == "playlist" or isinstance(raw.get("entries"), (list, tuple)):
        return _rejected(title, duration, thumbnail, "Playlist downloads are not supported; send one video URL.")
    if raw.get("is_live") or raw.get("live_status") in {"is_live", "is_upcoming"}:
        return _rejected(title, duration, thumbnail, "Live streams are not supported yet.")

    video_id = str(raw.get("id") or video_id_from_url(raw.get("webpage_url") or raw.get("original_url")) or "").strip()
    try:
        canonical_url = canonical_url_for_video_id(video_id)
    except ValueError:
        return _rejected(title, duration, thumbnail, "Could not identify one YouTube video.")
    extractor = str(raw.get("extractor_key") or raw.get("extractor") or "youtube").lower()
    if "youtube" not in extractor:
        return _rejected(title, duration, thumbnail, "The inspected media is not a YouTube video.")
    inspected_url_id = video_id_from_url(raw.get("webpage_url"))
    if inspected_url_id and inspected_url_id != video_id:
        return _rejected(title, duration, thumbnail, "The inspected video identity did not match the link.")

    formats = [fmt for fmt in (raw.get("formats") or []) if isinstance(fmt, dict)]
    audio_formats = sorted((fmt for fmt in formats if _is_audio_only(fmt)), key=_audio_score, reverse=True)
    expiry = time.time() + PLAN_TTL_SECONDS
    plans: list[SelectionPlan] = []
    choices: list[MediaChoice] = []
    audio_selector = "/".join(_format_id(fmt) for fmt in audio_formats if _format_id(fmt)) or "bestaudio"
    audio_plan = _make_plan(video_id, canonical_url, FormatChoice.MP3, audio_selector, None, duration, expiry, DownloadKind.AUDIO)
    plans.append(audio_plan)
    audio_size = _estimated_format_size(audio_formats[0], duration) if audio_formats else None
    choices.append(MediaChoice(
        FormatChoice.MP3,
        _format_choice_label("MP3", None, None, audio_size),
        ext="mp3",
        estimated_size=audio_size,
        plan_id=audio_plan.plan_id,
    ))

    for choice in VIDEO_CHOICES:
        target = choice.height
        exact = [
            fmt for fmt in formats
            if _short_edge(fmt) == target and _has_video(fmt) and _is_native_telegram_mp4(fmt)
        ]
        all_video_only = sorted((fmt for fmt in exact if _is_video_only(fmt)), key=_video_score, reverse=True)
        all_combined = sorted((fmt for fmt in exact if _has_audio(fmt)), key=_video_score, reverse=True)
        if not all_video_only and not all_combined:
            continue
        first = (all_video_only or all_combined)[0]
        target_width = first.get("width") if isinstance(first.get("width"), int) else None
        target_height = first.get("height") if isinstance(first.get("height"), int) else None
        video_only = [fmt for fmt in all_video_only if _same_dimensions(fmt, target_width, target_height)]
        combined = [fmt for fmt in all_combined if _same_dimensions(fmt, target_width, target_height)]
        selectors: list[str] = []
        if audio_formats:
            audio_id = _format_id(audio_formats[0])
            if audio_id:
                selectors.extend(f"{video_format_id}+{audio_id}" for fmt in video_only if (video_format_id := _format_id(fmt)))
        selectors.extend(_format_id(fmt) for fmt in combined if _format_id(fmt))
        selectors = list(dict.fromkeys(selectors))
        if not selectors:
            continue
        plan = _make_plan(
            video_id,
            canonical_url,
            choice,
            "/".join(selectors),
            target,
            duration,
            expiry,
            DownloadKind.VIDEO,
            target_width=target_width,
            target_height=target_height,
        )
        plans.append(plan)
        if video_only:
            video_size = _estimated_format_size(video_only[0], duration)
            selected_audio_size = _estimated_format_size(audio_formats[0], duration) if audio_formats else None
            estimated_size = video_size + selected_audio_size if video_size is not None and selected_audio_size is not None else None
        else:
            estimated_size = _estimated_format_size(combined[0], duration)
        label = _format_choice_label(choice.label, target_width, target_height, estimated_size)
        choices.append(MediaChoice(
            choice,
            label,
            _format_id(first),
            target,
            first.get("ext"),
            estimated_size,
            plan.plan_id,
        ))

    return MediaInfo(
        title=title,
        duration=int(duration) if duration is not None else None,
        webpage_url=canonical_url,
        thumbnail=thumbnail,
        choices=choices,
        video_id=video_id,
        canonical_url=canonical_url,
        plans=tuple(plans),
    )


async def inspect_url(url: str, max_duration_seconds: int | None = None) -> MediaInfo:
    raw = await asyncio.to_thread(_extract_info, url)
    return normalize_media_info(raw, max_duration_seconds=max_duration_seconds)


def _extract_info(url: str) -> dict[str, Any]:
    options = {
        "quiet": True,
        "skip_download": True,
        "noplaylist": True,
        "js_runtimes": youtube_js_runtimes(),
    }
    with YoutubeDL(options) as ydl:
        return ydl.extract_info(url, download=False)


def _make_plan(
    video_id: str,
    canonical_url: str,
    choice: FormatChoice,
    selector: str,
    target: int | None,
    duration: float | None,
    expiry: float,
    kind: DownloadKind,
    *,
    target_width: int | None = None,
    target_height: int | None = None,
) -> SelectionPlan:
    digest = hashlib.sha256(f"{video_id}\0{choice.value}\0{selector}\0{expiry}".encode()).hexdigest()[:16]
    return SelectionPlan(
        digest,
        video_id,
        canonical_url,
        choice,
        selector,
        target,
        True,
        kind,
        duration,
        expiry,
        target_width=target_width,
        target_height=target_height,
    )


def _rejected(title: str, duration: float | None, thumbnail: str | None, reason: str) -> MediaInfo:
    return MediaInfo(title, int(duration) if duration else None, None, thumbnail, [], reason)


def _format_id(fmt: dict[str, Any]) -> str | None:
    value = fmt.get("format_id")
    return str(value) if value is not None else None


def _has_video(fmt: dict[str, Any]) -> bool:
    return fmt.get("vcodec") != "none" and _short_edge(fmt) is not None


def _has_audio(fmt: dict[str, Any]) -> bool:
    value = fmt.get("acodec")
    return value not in {None, "none"} or ("acodec" not in fmt and _has_video(fmt))


def _is_video_only(fmt: dict[str, Any]) -> bool:
    return _has_video(fmt) and fmt.get("acodec") == "none"


def _is_audio_only(fmt: dict[str, Any]) -> bool:
    return fmt.get("vcodec") == "none" and fmt.get("acodec") not in {None, "none"}


def _is_native_telegram_mp4(fmt: dict[str, Any]) -> bool:
    if fmt.get("ext") != "mp4":
        return False
    video_codec = str(fmt.get("vcodec") or "").lower()
    if video_codec and not video_codec.startswith(("avc1", "h264")):
        return False
    audio_codec = str(fmt.get("acodec") or "").lower()
    if audio_codec not in {"", "none"} and not audio_codec.startswith(("mp4a", "aac")):
        return False
    return True


def _same_dimensions(fmt: dict[str, Any], width: int | None, height: int | None) -> bool:
    return fmt.get("width") == width and fmt.get("height") == height


def _short_edge(fmt: dict[str, Any]) -> int | None:
    width, height = fmt.get("width"), fmt.get("height")
    return min(width, height) if isinstance(width, int) and isinstance(height, int) else height if isinstance(height, int) else None


def _video_score(fmt: dict[str, Any]) -> tuple[int, float, int]:
    return (2 if fmt.get("ext") == "mp4" else 1, float(fmt.get("tbr") or 0), _format_size(fmt) or 0)


def _audio_score(fmt: dict[str, Any]) -> tuple[int, float]:
    return (2 if fmt.get("ext") in {"m4a", "mp4"} else 1, float(fmt.get("abr") or fmt.get("tbr") or 0))


def _format_size(fmt: dict[str, Any]) -> int | None:
    size = fmt.get("filesize") or fmt.get("filesize_approx")
    return int(size) if isinstance(size, (int, float)) else None


def _estimated_format_size(fmt: dict[str, Any], duration: float | None) -> int | None:
    exact_or_approx = _format_size(fmt)
    if exact_or_approx is not None:
        return exact_or_approx
    bitrate_kbps = fmt.get("tbr") or fmt.get("vbr") or fmt.get("abr")
    if duration and isinstance(bitrate_kbps, (int, float)) and bitrate_kbps > 0:
        return int(duration * float(bitrate_kbps) * 1000 / 8)
    return None


def _format_choice_label(
    name: str,
    width: int | None,
    height: int | None,
    estimated_size: int | None,
) -> str:
    dimensions = f" · {width}×{height}" if width and height else ""
    size = f" · 约 {_human_size(estimated_size)}" if estimated_size is not None else " · 大小未知"
    return f"{name}{dimensions}{size}"


def _human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
