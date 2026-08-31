from collections.abc import Awaitable, Callable
from pathlib import Path

from ytdl_bot.models import DeliveryDecision, DownloadKind, DownloadResult
from ytdl_bot.transcode import split_video_for_upload
SPLIT_UPLOAD_RESERVE_BYTES = 1024 * 1024
PART_READ_BYTES = 1024 * 1024
StatusCallback = Callable[[str], Awaitable[None]]


def choose_delivery_method(result: DownloadResult, max_upload_bytes: int) -> DeliveryDecision:
    if max_upload_bytes <= 0:
        return DeliveryDecision(
            can_send=False,
            method=None,
            reason="Telegram upload size limit is not configured.",
        )
    if result.size_bytes > max_upload_bytes:
        if result.kind is DownloadKind.VIDEO:
            return DeliveryDecision(
                can_send=True,
                method="split_video",
                reason=(
                    f"原视频大小为 {_human_bytes(result.size_bytes)}，超过当前 Telegram 上传限制。"
                    "将按关键帧无损分段，分辨率和画质保持不变。"
                ),
            )
        return DeliveryDecision(
            can_send=True,
            method="split_document",
            reason=f"File is too large for Telegram upload ({_human_bytes(result.size_bytes)}).",
        )
    if result.kind is DownloadKind.AUDIO:
        return DeliveryDecision(can_send=True, method="audio")
    if result.kind is DownloadKind.VIDEO:
        return DeliveryDecision(can_send=True, method="video")
    return DeliveryDecision(can_send=True, method="document")


async def send_download_result(
    message,
    result: DownloadResult,
    max_upload_bytes: int,
    status_callback: StatusCallback | None = None,
) -> DeliveryDecision:
    decision = choose_delivery_method(result, max_upload_bytes)
    if not decision.can_send:
        await message.reply_text(f"{decision.reason} Please choose a lower quality or MP3.")
        return decision

    filename = _safe_display_name(result)
    if decision.method == "split_video":
        if status_callback:
            await status_callback("splitting")
        segments = await split_video_for_upload(result.path, max_upload_bytes)
        await message.reply_text(f"{decision.reason}\n共 {len(segments)} 段，每段都可以直接播放和横屏。")
        for index, (segment, probe) in enumerate(segments, start=1):
            if status_callback:
                await status_callback(f"uploading_part:{index}:{len(segments)}")
            with segment.open("rb") as file:
                await message.reply_video(
                    video=file,
                    caption=f"{result.title}（{index}/{len(segments)}）",
                    filename=_video_part_display_name(filename, index, len(segments)),
                    width=probe.width,
                    height=probe.height,
                    duration=int(probe.duration),
                    supports_streaming=True,
                )
    elif decision.method == "split_document":
        if status_callback:
            await status_callback("splitting")
        chunk_bytes = _safe_split_chunk_bytes(max_upload_bytes)
        parts = split_file_for_upload(result.path, chunk_bytes)
        await message.reply_text(
            f"{decision.reason} Splitting into {len(parts)} parts.\n"
            f"Recombine on Mac/Linux with: cat '{filename}.part'* > '{filename}'"
        )
        for index, part in enumerate(parts, start=1):
            if status_callback:
                await status_callback(f"uploading_part:{index}:{len(parts)}")
            with part.open("rb") as file:
                await message.reply_document(
                    document=file,
                    filename=_part_display_name(filename, index, len(parts)),
                    caption=f"{result.title} ({index}/{len(parts)})",
                )
    elif decision.method == "audio":
        if status_callback:
            await status_callback("uploading")
        with result.path.open("rb") as file:
            await message.reply_audio(audio=file, title=result.title, filename=filename)
    elif decision.method == "video":
        if status_callback:
            await status_callback("uploading")
        with result.path.open("rb") as file:
            await message.reply_video(
                video=file,
                caption=result.title,
                filename=filename,
                width=result.probe.width if result.probe else None,
                height=result.probe.height if result.probe else None,
                duration=int(result.probe.duration) if result.probe else None,
                supports_streaming=True,
            )
    else:
        if status_callback:
            await status_callback("uploading")
        with result.path.open("rb") as file:
            await message.reply_document(document=file, filename=filename, caption=result.title)
    return decision


def split_file_for_upload(file_path: Path, chunk_bytes: int) -> list[Path]:
    if chunk_bytes <= 0:
        raise ValueError("chunk_bytes must be greater than 0")

    total_parts = max(1, (file_path.stat().st_size + chunk_bytes - 1) // chunk_bytes)
    width = max(3, len(str(total_parts)))
    parts_dir = file_path.parent / f"{file_path.name}.parts"
    parts_dir.mkdir(exist_ok=True)

    for old_part in parts_dir.glob(f"{file_path.name}.part*"):
        old_part.unlink()

    parts: list[Path] = []
    with file_path.open("rb") as source:
        for index in range(1, total_parts + 1):
            part_path = parts_dir / f"{file_path.name}.part{index:0{width}d}of{total_parts:0{width}d}"
            remaining = chunk_bytes
            with part_path.open("wb") as target:
                while remaining > 0:
                    chunk = source.read(min(PART_READ_BYTES, remaining))
                    if not chunk:
                        break
                    target.write(chunk)
                    remaining -= len(chunk)
            parts.append(part_path)

    return parts


def _safe_display_name(result: DownloadResult) -> str:
    suffix = result.path.suffix or ".bin"
    title = "".join(char if char.isalnum() or char in {" ", "-", "_"} else "_" for char in result.title)
    title = " ".join(title.split()).strip() or "download"
    return f"{title[:80]}{suffix}"


def _part_display_name(filename: str, index: int, total_parts: int) -> str:
    width = max(3, len(str(total_parts)))
    return f"{filename}.part{index:0{width}d}of{total_parts:0{width}d}"


def _video_part_display_name(filename: str, index: int, total_parts: int) -> str:
    stem = Path(filename).stem
    width = max(2, len(str(total_parts)))
    return f"{stem}.part{index:0{width}d}of{total_parts:0{width}d}.mp4"


def _safe_split_chunk_bytes(max_upload_bytes: int) -> int:
    if max_upload_bytes <= SPLIT_UPLOAD_RESERVE_BYTES:
        return max_upload_bytes
    return max_upload_bytes - SPLIT_UPLOAD_RESERVE_BYTES


def _human_bytes(size: int) -> str:
    units = ["B", "KB", "MB", "GB"]
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}"
        value /= 1024
