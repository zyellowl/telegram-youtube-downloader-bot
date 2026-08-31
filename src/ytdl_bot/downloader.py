from __future__ import annotations

import asyncio
from contextlib import suppress
import json
import shutil
import sys
import time
from pathlib import Path
from typing import Awaitable, Callable
from uuid import uuid4

from ytdl_bot.errors import BotError, ErrorCode, redact
from ytdl_bot.models import DownloadKind, DownloadResult, FormatChoice, SelectionPlan
from ytdl_bot.runtime import youtube_js_runtimes
from ytdl_bot.transcode import probe_media, validate_probe

ProgressCallback = Callable[[str], Awaitable[None]]
PROGRESS_TEMPLATE = "download:%(progress._percent_str)s %(progress._speed_str)s ETA %(progress._eta_str)s"
RESULT_TEMPLATE = "after_move:RESULT:%(filepath)j"
TEMP_SUFFIXES = {".part", ".ytdl", ".tmp", ".temp"}
SIDECAR_SUFFIXES = {".json", ".jpg", ".jpeg", ".png", ".webp", ".description", ".vtt", ".srt", ".ass"}


def build_ytdlp_args(
    url: str | None = None,
    choice: FormatChoice | None = None,
    output_dir: Path | None = None,
    task_id: str = "task",
    plan: SelectionPlan | None = None,
) -> list[str]:
    if output_dir is None:
        raise ValueError("output_dir is required")
    output_dir.mkdir(parents=True, exist_ok=True)
    if plan is not None:
        url, choice, selector = plan.canonical_url, plan.choice, plan.selector
    else:
        if not url or choice is None:
            raise ValueError("A selection plan or URL and choice are required")
        if choice is FormatChoice.MP3:
            selector = "bestaudio"
        else:
            height = choice.height
            selector = f"bestvideo[height={height}]+bestaudio/best[height={height}]"

    runtime_args: list[str] = []
    for name, config in youtube_js_runtimes().items():
        runtime = f"{name}:{config['path']}" if config.get("path") else name
        runtime_args.extend(["--js-runtimes", runtime])

    args = [
        sys.executable, "-m", "yt_dlp",
        "--no-playlist",
        "--proxy", "",
        "--check-formats",
        "--abort-on-unavailable-fragments",
        "--restrict-filenames",
        "--no-colors",
        "--concurrent-fragments", "4",
        "--progress",
        "--newline",
        "--progress-delta", "1",
        "--progress-template", PROGRESS_TEMPLATE,
        "--print", RESULT_TEMPLATE,
        "--socket-timeout", "30",
        "--retries", "5",
        "--fragment-retries", "5",
        "--retry-sleep", "fragment:exp=1:20",
        "-o", str(output_dir / f"{task_id}.%(ext)s"),
        "-f", selector,
    ]
    args[args.index("--progress"):args.index("--progress")] = runtime_args
    if choice is FormatChoice.MP3:
        args.extend(["--extract-audio", "--audio-format", "mp3", "--audio-quality", "0"])
    else:
        args.extend(["--merge-output-format", "mp4"])
    args.append(str(url))
    return args


async def download_media(
    url: str | None = None,
    choice: FormatChoice | None = None,
    download_dir: Path = Path("downloads"),
    title: str = "Untitled",
    task_id: str | None = None,
    progress_callback: ProgressCallback | None = None,
    plan: SelectionPlan | None = None,
    max_output_bytes: int | None = None,
) -> DownloadResult:
    task_id = task_id or uuid4().hex
    task_dir = download_dir / task_id
    args = build_ytdlp_args(url=url, choice=choice, output_dir=task_dir, task_id=task_id, plan=plan)
    process = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stdout_lines: list[str] = []
    stderr_lines: list[str] = []
    oversized_bytes = 0
    progress_queue: asyncio.Queue[str | None] | None = asyncio.Queue(maxsize=1) if progress_callback else None

    async def deliver_progress() -> None:
        assert progress_queue is not None and progress_callback is not None
        while True:
            event = await progress_queue.get()
            if event is None:
                return
            try:
                await progress_callback(event)
            except Exception:
                # Telegram status updates must never block yt-dlp's pipe or
                # interrupt the media download.
                pass

    progress_task = asyncio.create_task(deliver_progress()) if progress_queue else None

    def queue_progress(line: str) -> bool:
        if progress_queue is None:
            return False
        marker = line.find("download:")
        if marker < 0:
            return False
        normalized = line[marker:]
        if progress_queue.full():
            with suppress(asyncio.QueueEmpty):
                progress_queue.get_nowait()
        progress_queue.put_nowait(normalized)
        return True

    async def read_stream(stream, lines: list[str]) -> None:
        if stream is None:
            return
        async for raw_line in stream:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            # Never wait for a Telegram API request while draining yt-dlp's
            # pipes. A blocked status edit can otherwise stall the downloader.
            if queue_progress(line):
                continue
            lines.append(line)
            if len(lines) > 300:
                del lines[:-300]

    async def stop_if_oversized() -> None:
        nonlocal oversized_bytes
        if not max_output_bytes or max_output_bytes <= 0:
            return
        while process.returncode is None:
            await asyncio.sleep(1)
            current_size = task_output_size(task_dir)
            if current_size > max_output_bytes:
                oversized_bytes = current_size
                process.terminate()
                return

    size_task = asyncio.create_task(stop_if_oversized())

    await asyncio.gather(
        read_stream(process.stdout, stdout_lines),
        read_stream(process.stderr, stderr_lines),
    )
    await process.wait()
    size_task.cancel()
    with suppress(asyncio.CancelledError):
        await size_task
    if progress_queue and progress_task:
        if progress_queue.full():
            with suppress(asyncio.QueueEmpty):
                progress_queue.get_nowait()
        progress_queue.put_nowait(None)
        try:
            await asyncio.wait_for(progress_task, timeout=3)
        except TimeoutError:
            progress_task.cancel()
            with suppress(asyncio.CancelledError):
                await progress_task
    stderr = "\n".join(stderr_lines)
    if oversized_bytes:
        shutil.rmtree(task_dir, ignore_errors=True)
        raise BotError(
            ErrorCode.UPLOAD_TOO_LARGE,
            "download",
            "原视频超过了本机允许的最大源文件大小，任务已提前停止。",
            f"download exceeded {max_output_bytes} bytes at {oversized_bytes} bytes",
        )
    if process.returncode != 0:
        shutil.rmtree(task_dir, ignore_errors=True)
        raise BotError(ErrorCode.DOWNLOAD_FAILED, "download", "Download failed or the selected format became unavailable. Please retry or choose another format.", redact(stderr or "\n".join(stdout_lines)))
    try:
        if progress_callback:
            await progress_callback("stage:processing")
        path = parse_result_path(stdout_lines, task_dir)
        effective_choice = plan.choice if plan else choice
        kind = DownloadKind.AUDIO if effective_choice is FormatChoice.MP3 else DownloadKind.VIDEO
        effective_plan = plan or _legacy_plan(str(url), effective_choice, kind)
        if kind is DownloadKind.VIDEO:
            probe = validate_probe(await probe_media(path), effective_plan, kind)
            if not _is_original_streamable_mp4(probe):
                raise BotError(
                    ErrorCode.VALIDATION_FAILED,
                    "validation",
                    "YouTube did not provide an original MP4 stream that Telegram can play. The file was not recompressed.",
                    "original stream is not H.264/AAC MP4",
                )
        else:
            probe = validate_probe(await probe_media(path), effective_plan, kind)
        return DownloadResult(path, title, kind, path.stat().st_size, probe)
    except BotError:
        shutil.rmtree(task_dir, ignore_errors=True)
        raise
    except Exception as exc:
        shutil.rmtree(task_dir, ignore_errors=True)
        raise BotError(ErrorCode.VALIDATION_FAILED, "validation", "The downloaded file was incomplete or did not match the selected quality, so it was not sent.", redact(str(exc))) from exc


def parse_result_path(lines: list[str], task_dir: Path) -> Path:
    markers = [line.removeprefix("RESULT:") for line in lines if line.startswith("RESULT:")]
    if len(markers) != 1:
        raise RuntimeError("yt-dlp did not report exactly one final output path")
    try:
        decoded = json.loads(markers[0])
    except json.JSONDecodeError as exc:
        raise RuntimeError("yt-dlp reported an invalid final output path") from exc
    if not isinstance(decoded, str) or not decoded:
        raise RuntimeError("yt-dlp reported an invalid final output path")
    root = task_dir.resolve()
    candidate = Path(decoded)
    if not candidate.is_absolute():
        candidate = root / candidate
    resolved = candidate.resolve()
    if root not in resolved.parents:
        raise RuntimeError("yt-dlp final output escaped the task directory")
    if resolved.suffix.lower() in TEMP_SUFFIXES or resolved.suffix.lower() in SIDECAR_SUFFIXES:
        raise RuntimeError("yt-dlp final output is a temporary or sidecar file")
    if not resolved.is_file() or resolved.stat().st_size <= 0:
        raise RuntimeError("yt-dlp final output is missing or empty")
    return resolved


def summarize_ytdlp_progress(line: str) -> str | None:
    marker = line.find("download:")
    if marker < 0:
        return None
    parts = line[marker:].removeprefix("download:").split()
    if len(parts) < 4:
        return None
    return f"{parts[0]} at {parts[1]}, ETA {parts[-1]}"


def task_output_size(task_dir: Path) -> int:
    total = 0
    if not task_dir.is_dir():
        return total
    for path in task_dir.iterdir():
        try:
            if path.is_file() and path.suffix.lower() != ".ytdl":
                total += path.stat().st_size
        except OSError:
            continue
    return total


def _legacy_plan(url: str, choice: FormatChoice | None, kind: DownloadKind) -> SelectionPlan:
    actual_choice = choice or FormatChoice.VIDEO_720
    return SelectionPlan("legacy", "unknown", url, actual_choice, "", actual_choice.height, True, kind, None, time.time() + 1)


def _is_original_streamable_mp4(probe) -> bool:
    containers = set((probe.format_name or "").split(","))
    return (
        probe.video_codec == "h264"
        and probe.audio_codec in {"aac", "mp3"}
        and bool(containers & {"mov", "mp4"})
    )
