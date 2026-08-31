from __future__ import annotations

import asyncio
import math
import json
from pathlib import Path

from ytdl_bot.models import DownloadKind, FormatChoice, MediaProbe, SelectionPlan

UPLOAD_RESERVE_BYTES = 1024 * 1024
MIN_AUDIO_BITRATE = 24_000
DEFAULT_AUDIO_BITRATE = 48_000
MIN_VIDEO_BITRATE = 40_000
MAX_COMPRESSION_ATTEMPTS = 3
BITRATE_RETRY_FACTOR = 0.75
VIDEO_SEGMENT_RESERVE_BYTES = 4 * 1024 * 1024
MAX_SEGMENT_ATTEMPTS = 8


async def probe_media(file_path: Path) -> dict:
    if not file_path.is_file() or file_path.stat().st_size <= 0:
        raise RuntimeError("Media file is missing or empty.")
    process = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(file_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        raise RuntimeError("ffprobe could not read the media container.")
    try:
        value = json.loads(stdout.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        raise RuntimeError("ffprobe returned invalid metadata.") from exc
    if not isinstance(value, dict):
        raise RuntimeError("ffprobe returned incomplete metadata.")
    return value


def validate_probe(probe: dict, plan: SelectionPlan, kind: DownloadKind) -> MediaProbe:
    streams = probe.get("streams")
    format_info = probe.get("format")
    if not isinstance(streams, list) or not isinstance(format_info, dict):
        raise RuntimeError("Media metadata is incomplete.")
    duration = _positive_float(format_info.get("duration"))
    video_streams = [stream for stream in streams if isinstance(stream, dict) and stream.get("codec_type") == "video"]
    audio_streams = [stream for stream in streams if isinstance(stream, dict) and stream.get("codec_type") == "audio"]
    if kind is DownloadKind.AUDIO:
        if not audio_streams or video_streams:
            raise RuntimeError("Audio result has missing audio or an unexpected video stream.")
    elif kind is DownloadKind.VIDEO:
        if not video_streams:
            raise RuntimeError("Video result has no video stream.")
        if plan.require_audio and not audio_streams:
            raise RuntimeError("Video result has no audio stream.")
    if plan.source_duration is not None:
        tolerance = max(2.0, plan.source_duration * 0.01)
        if abs(duration - plan.source_duration) > tolerance:
            raise RuntimeError("Media duration does not match the source and may be truncated.")

    width = height = short_edge = None
    video_codec = None
    if video_streams:
        stream = video_streams[0]
        width, height = _positive_int(stream.get("width")), _positive_int(stream.get("height"))
        if width is None or height is None:
            raise RuntimeError("Video dimensions are missing.")
        short_edge = min(width, height)
        video_codec = _text_or_none(stream.get("codec_name"))
        if plan.target_short_edge is not None and short_edge != plan.target_short_edge:
            raise RuntimeError("Video resolution does not match the selected quality.")
        if plan.target_width is not None and width != plan.target_width:
            raise RuntimeError("Video width does not match the original YouTube stream.")
        if plan.target_height is not None and height != plan.target_height:
            raise RuntimeError("Video height does not match the original YouTube stream.")
    audio_codec = _text_or_none(audio_streams[0].get("codec_name")) if audio_streams else None
    return MediaProbe(
        duration=duration,
        video_streams=len(video_streams),
        audio_streams=len(audio_streams),
        width=width,
        height=height,
        short_edge=short_edge,
        video_codec=video_codec,
        audio_codec=audio_codec,
        format_name=_text_or_none(format_info.get("format_name")),
        raw=probe,
    )


async def compress_video_for_upload(file_path: Path, max_upload_bytes: int) -> Path:
    if max_upload_bytes <= 0:
        raise RuntimeError("Telegram upload size limit is not configured.")

    source_probe = await probe_media(file_path)
    duration = await probe_duration_seconds(file_path)
    if duration <= 0:
        raise RuntimeError("Could not detect video duration for compression.")

    output_path = file_path.with_name(f"{file_path.stem}.telegram.mp4")
    video_bitrate, audio_bitrate = target_bitrates(max_upload_bytes, duration)

    for attempt in range(MAX_COMPRESSION_ATTEMPTS):
        if output_path.exists():
            output_path.unlink()
        retry_factor = BITRATE_RETRY_FACTOR**attempt
        await _run_ffmpeg(
            build_ffmpeg_compress_args(
                file_path,
                output_path,
                max(MIN_VIDEO_BITRATE, int(video_bitrate * retry_factor)),
                max(MIN_AUDIO_BITRATE, int(audio_bitrate * retry_factor)),
            )
        )
        if not output_path.exists() or output_path.stat().st_size == 0:
            raise RuntimeError("Video compression did not produce an output file.")
        if output_path.stat().st_size <= max_upload_bytes:
            validate_derivative_probe(await probe_media(output_path), source_probe, require_mp4=True)
            return output_path

    raise RuntimeError("Compressed video is still too large for Telegram. Please choose MP3 or a lower quality.")


async def ensure_telegram_compatible(file_path: Path, plan: SelectionPlan) -> tuple[Path, MediaProbe]:
    source_data = await probe_media(file_path)
    source = validate_probe(source_data, plan, DownloadKind.VIDEO)
    if (
        source.video_codec == "h264"
        and source.audio_codec in {"aac", "mp3"}
        and source.format_name
        and any(name in source.format_name.split(",") for name in {"mov", "mp4"})
    ):
        return file_path, source
    output_path = file_path.with_name(f"{file_path.stem}.telegram.mp4")
    await _run_ffmpeg([
        "ffmpeg", "-y", "-i", str(file_path), "-map", "0:v:0", "-map", "0:a:0", "-sn",
        "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", "-movflags", "+faststart", str(output_path),
    ])
    converted_data = await probe_media(output_path)
    converted = validate_probe(converted_data, plan, DownloadKind.VIDEO)
    if converted.video_codec != "h264" or converted.audio_codec != "aac":
        raise RuntimeError("Converted video is not Telegram compatible.")
    return output_path, converted


def validate_derivative_probe(probe: dict, source_probe: dict, *, require_mp4: bool = False) -> MediaProbe:
    source_streams = source_probe.get("streams") or []
    source_format = source_probe.get("format") or {}
    source_duration = _positive_float(source_format.get("duration"))
    require_audio = any(isinstance(stream, dict) and stream.get("codec_type") == "audio" for stream in source_streams)
    target = SelectionPlan(
        "derivative", "unknown", "https://www.youtube.com/watch?v=unknown", FormatChoice.VIDEO_360, "",
        None, require_audio, DownloadKind.VIDEO, source_duration, 0,
    )
    result = validate_probe(probe, target, DownloadKind.VIDEO)
    if require_mp4 and (not result.format_name or not any(name in result.format_name.split(",") for name in {"mov", "mp4"})):
        raise RuntimeError("Processed video is not an MP4 container.")
    return result


async def probe_duration_seconds(file_path: Path) -> float:
    process = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(file_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        raise RuntimeError("ffprobe failed to read the media file.")
    return float(stdout.decode("utf-8", errors="replace").strip())


async def split_video_for_upload(file_path: Path, max_upload_bytes: int) -> list[tuple[Path, MediaProbe]]:
    """Split an MP4 at keyframes without re-encoding any audio or video."""
    if max_upload_bytes <= VIDEO_SEGMENT_RESERVE_BYTES:
        raise RuntimeError("Telegram upload limit is too small for playable video segments.")
    source_size = file_path.stat().st_size
    source_data = await probe_media(file_path)
    source_duration = _positive_float((source_data.get("format") or {}).get("duration"))
    target_bytes = max_upload_bytes - VIDEO_SEGMENT_RESERVE_BYTES
    part_count = max(2, math.ceil(source_size / target_bytes))
    segments_dir = file_path.parent / f"{file_path.stem}.segments"
    segments_dir.mkdir(exist_ok=True)

    for _attempt in range(MAX_SEGMENT_ATTEMPTS):
        for old_segment in segments_dir.glob("segment_*.mp4"):
            old_segment.unlink()
        segment_seconds = max(1.0, source_duration / part_count)
        await _run_ffmpeg(build_ffmpeg_segment_args(file_path, segments_dir, segment_seconds))
        segments = sorted(path for path in segments_dir.glob("segment_*.mp4") if path.stat().st_size > 0)
        if len(segments) < 2:
            part_count += 1
            continue
        largest = max(path.stat().st_size for path in segments)
        if largest > max_upload_bytes:
            part_count = max(part_count + 1, math.ceil(part_count * largest / target_bytes))
            continue

        results: list[tuple[Path, MediaProbe]] = []
        for segment in segments:
            segment_probe = validate_segment_probe(await probe_media(segment), source_data)
            results.append((segment, segment_probe))
        return results

    raise RuntimeError("Could not split the original video into Telegram-sized playable segments.")


def build_ffmpeg_segment_args(file_path: Path, segments_dir: Path, segment_seconds: float) -> list[str]:
    return [
        "ffmpeg",
        "-y",
        "-i",
        str(file_path),
        "-map",
        "0:v:0",
        "-map",
        "0:a:0",
        "-sn",
        "-c",
        "copy",
        "-f",
        "segment",
        "-segment_format",
        "mp4",
        "-segment_time",
        f"{segment_seconds:.3f}",
        "-reset_timestamps",
        "1",
        "-avoid_negative_ts",
        "make_zero",
        str(segments_dir / "segment_%03d.mp4"),
    ]


def validate_segment_probe(segment_data: dict, source_data: dict) -> MediaProbe:
    source_streams = source_data.get("streams") or []
    source_video = next((stream for stream in source_streams if stream.get("codec_type") == "video"), None)
    source_audio = next((stream for stream in source_streams if stream.get("codec_type") == "audio"), None)
    if not isinstance(source_video, dict) or not isinstance(source_audio, dict):
        raise RuntimeError("Original video streams are incomplete.")
    plan = SelectionPlan(
        "segment", "unknown", "https://www.youtube.com/watch?v=unknown", FormatChoice.VIDEO_360, "",
        None, True, DownloadKind.VIDEO, None, 0,
        target_width=_positive_int(source_video.get("width")),
        target_height=_positive_int(source_video.get("height")),
    )
    result = validate_probe(segment_data, plan, DownloadKind.VIDEO)
    if result.video_codec != _text_or_none(source_video.get("codec_name")):
        raise RuntimeError("Video segment codec changed unexpectedly.")
    if result.audio_codec != _text_or_none(source_audio.get("codec_name")):
        raise RuntimeError("Audio segment codec changed unexpectedly.")
    if not result.format_name or not set(result.format_name.split(",")) & {"mov", "mp4"}:
        raise RuntimeError("Video segment is not an MP4 container.")
    return result


def _positive_float(value) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Media duration is missing.") from exc
    if result <= 0:
        raise RuntimeError("Media duration is invalid.")
    return result


def _positive_int(value) -> int | None:
    return value if isinstance(value, int) and value > 0 else None


def _text_or_none(value) -> str | None:
    return str(value) if value not in {None, ""} else None


def build_ffmpeg_compress_args(
    input_path: Path,
    output_path: Path,
    video_bitrate: int,
    audio_bitrate: int,
) -> list[str]:
    return [
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-sn",
        "-vf",
        "scale=w='min(1280,iw)':h=-2",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-b:v",
        str(video_bitrate),
        "-maxrate",
        str(video_bitrate),
        "-bufsize",
        str(video_bitrate * 2),
        "-c:a",
        "aac",
        "-b:a",
        str(audio_bitrate),
        "-movflags",
        "+faststart",
        str(output_path),
    ]


def target_bitrates(max_upload_bytes: int, duration_seconds: float) -> tuple[int, int]:
    target_bytes = _target_payload_bytes(max_upload_bytes)
    total_bitrate = max(1, int((target_bytes * 8) / duration_seconds))
    audio_bitrate = min(DEFAULT_AUDIO_BITRATE, max(MIN_AUDIO_BITRATE, total_bitrate // 4))
    video_bitrate = max(MIN_VIDEO_BITRATE, total_bitrate - audio_bitrate)
    return video_bitrate, audio_bitrate


async def _run_ffmpeg(args: list[str]) -> None:
    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        raise RuntimeError("ffmpeg failed while processing the media file.")


def _target_payload_bytes(max_upload_bytes: int) -> int:
    if max_upload_bytes <= UPLOAD_RESERVE_BYTES:
        return max_upload_bytes
    return max_upload_bytes - UPLOAD_RESERVE_BYTES
