from __future__ import annotations

import asyncio
import logging
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Awaitable, Callable

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from ytdl_bot.cleanup import cleanup_old_files, remove_task_directory
from ytdl_bot.config import Settings
from ytdl_bot.delivery import send_download_result
from ytdl_bot.downloader import download_media, summarize_ytdlp_progress
from ytdl_bot.errors import BotError, ErrorCode, redact
from ytdl_bot.limits import UserTaskLimiter
from ytdl_bot.media import inspect_url
from ytdl_bot.models import FormatChoice, MediaInfo
from ytdl_bot.url_utils import canonicalize_youtube_url, extract_youtube_urls


logger = logging.getLogger(__name__)
InspectFn = Callable[[str, int | None], Awaitable[MediaInfo]]
DownloadFn = Callable[..., Awaitable]
PROGRESS_HEARTBEAT_SECONDS = 10
PROGRESS_EDIT_INTERVAL_SECONDS = 3


@dataclass
class DownloadRequest:
    url: str
    info: MediaInfo
    created_at: float = field(default_factory=time.time)
    claimed: bool = False


@dataclass
class BotDependencies:
    settings: Settings
    inspect: InspectFn = inspect_url
    download: DownloadFn = download_media
    limiter: UserTaskLimiter | None = None
    global_semaphore: asyncio.Semaphore | None = None
    requests: dict[str, DownloadRequest] = field(default_factory=dict)
    user_ids: set[int] = field(default_factory=set)

    def __post_init__(self) -> None:
        if self.limiter is None:
            self.limiter = UserTaskLimiter(self.settings.max_tasks_per_user)
        if self.global_semaphore is None:
            self.global_semaphore = asyncio.Semaphore(self.settings.max_concurrent_downloads)


def start_text() -> str:
    return (
        "Send me a YouTube link and I will help download authorized public content.\n\n"
        "Available choices usually include MP3, 360p, 480p, 720p, and 1080p. "
        "Oversized videos are compressed into a playable Telegram MP4."
    )


def help_text() -> str:
    return (
        "How to use:\n"
        "1. Send a public YouTube URL.\n"
        "2. Choose MP3 or a video quality.\n"
        "3. Wait while I download, process, and upload the file.\n\n"
        "I do not bypass DRM, paid, private, or login-restricted content."
    )


def build_format_keyboard(task_id: str, info: MediaInfo) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(choice.label, callback_data=f"download:{task_id}:{choice.plan_id}")
        for choice in info.choices
        if choice.plan_id
    ]
    rows = [[button] for button in buttons]
    return InlineKeyboardMarkup(rows)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    deps = get_deps(context)
    remember_user(update, deps)
    if update.effective_message:
        await update.effective_message.reply_text(start_text())


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    deps = get_deps(context)
    remember_user(update, deps)
    if update.effective_message:
        await update.effective_message.reply_text(help_text())


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    deps = get_deps(context)
    if not _is_admin(update, deps.settings):
        await update.effective_message.reply_text("Admin only.")
        return
    active_users = len(deps.limiter._active if deps.limiter else {})
    await update.effective_message.reply_text(
        f"Bot is running.\nActive users: {active_users}\nCached requests: {len(deps.requests)}"
    )


async def cleanup_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    deps = get_deps(context)
    if not _is_admin(update, deps.settings):
        await update.effective_message.reply_text("Admin only.")
        return
    removed = cleanup_old_files(deps.settings.download_dir, deps.settings.cleanup_max_age_seconds)
    await update.effective_message.reply_text(f"Removed {len(removed)} expired file(s).")


async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    deps = get_deps(context)
    if not _is_admin(update, deps.settings):
        await update.effective_message.reply_text("Admin only.")
        return
    text = broadcast_text_from_message(update.effective_message.text or "")
    if text is None:
        await update.effective_message.reply_text("Usage: /broadcast message text")
        return

    sent = 0
    failed = 0
    for user_id in sorted(deps.user_ids):
        try:
            await context.bot.send_message(chat_id=user_id, text=text)
            sent += 1
        except Exception:
            failed += 1
    await update.effective_message.reply_text(f"Broadcast sent to {sent} user(s). Failed: {failed}.")


async def link_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    deps = get_deps(context)
    _prune_requests(deps)
    remember_user(update, deps)
    message = update.effective_message
    if not message or not message.text:
        return

    urls = extract_youtube_urls(message.text)
    if not urls:
        await message.reply_text("Please send a valid YouTube link.")
        return

    url = canonicalize_youtube_url(urls[0])
    if url is None:
        await message.reply_text("Please send one valid YouTube video link, not a playlist.")
        return
    status = await message.reply_text("Parsing your link...")
    try:
        info = await deps.inspect(url, deps.settings.max_duration_seconds)
    except Exception:
        logger.error("YouTube inspection failed with code=INSPECT_FAILED.")
        await status.edit_text("Could not inspect this link. The video may be unavailable or restricted. [INSPECT_FAILED]")
        return

    if info.is_rejected:
        await status.edit_text(info.rejection_reason or "This media cannot be downloaded.")
        return

    _prune_requests(deps)
    task_id = _new_task_id(deps)
    deps.requests[task_id] = DownloadRequest(url=url, info=info)
    _prune_requests(deps)
    await status.edit_text(
        f"{info.title}\n\n请选择格式（大小为 YouTube 提供的估算值）：\n"
        + "\n".join(f"• {choice.label}" for choice in info.choices),
        reply_markup=build_format_keyboard(task_id, info),
    )


async def download_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    deps = get_deps(context)
    _prune_requests(deps)
    query = update.callback_query
    if not query:
        return
    await query.answer()

    try:
        _, task_id, plan_id = query.data.split(":", 2)
    except (AttributeError, ValueError):
        await query.edit_message_text("Invalid download request.")
        return

    request = deps.requests.get(task_id)
    if request is None:
        await query.edit_message_text("This download request expired. Please send the link again.")
        return
    plan = request.info.get_plan(plan_id)
    if plan is None or plan.expires_at < time.time():
        deps.requests.pop(task_id, None)
        await query.edit_message_text("This download request expired. Please send the link again. [REQUEST_EXPIRED]")
        return
    if request.claimed:
        await query.edit_message_text("This download is already running. [LIMIT_REACHED]")
        return
    choice = plan.choice

    user_id = update.effective_user.id if update.effective_user else 0
    message = query.message
    if message is None:
        return
    request.claimed = True

    result = None
    started_at = time.monotonic()
    last_activity_at = started_at
    last_progress_update = 0.0
    stage = "⏳ 正在排队"
    detail = "等待空闲下载位置…"
    edit_lock = asyncio.Lock()
    heartbeat_stop = asyncio.Event()
    observed_bytes = 0
    observed_at = started_at

    def progress_text(now: float | None = None) -> str:
        current = time.monotonic() if now is None else now
        lines = [
            f"{stage}：{request.info.title}",
            f"格式：{choice.label}",
            detail,
            f"已用时：{format_elapsed(current - started_at)}",
        ]
        if stage.startswith("⬇️"):
            quiet_seconds = max(0, int(current - last_activity_at))
            if quiet_seconds >= PROGRESS_HEARTBEAT_SECONDS:
                lines.append(f"最近数据：{quiet_seconds} 秒前（任务仍在运行）")
        return "\n".join(lines)

    async def render_progress() -> None:
        async with edit_lock:
            await safe_edit_message_text(query, progress_text())

    async def set_stage(new_stage: str, new_detail: str) -> None:
        nonlocal stage, detail, last_activity_at
        stage = new_stage
        detail = new_detail
        last_activity_at = time.monotonic()
        await render_progress()

    async def heartbeat() -> None:
        nonlocal stage, detail, last_activity_at, observed_bytes, observed_at
        while True:
            try:
                await asyncio.wait_for(heartbeat_stop.wait(), timeout=PROGRESS_HEARTBEAT_SECONDS)
                return
            except TimeoutError:
                now = time.monotonic()
                current_bytes = task_downloaded_bytes(deps.settings.download_dir / task_id)
                if stage.startswith("⬇️") and current_bytes > observed_bytes:
                    elapsed = max(0.001, now - observed_at)
                    speed = (current_bytes - observed_bytes) / elapsed
                    observed_bytes = current_bytes
                    observed_at = now
                    last_activity_at = now
                    detail = f"已下载：{format_bytes(current_bytes)} · 最近速度：{format_bytes(speed)}/s"
                await render_progress()

    heartbeat_task = asyncio.create_task(heartbeat())

    async def update_download_progress(line: str) -> None:
        nonlocal stage, detail, last_activity_at, last_progress_update
        if line == "stage:processing":
            await set_stage("⚙️ 正在处理", "正在合并音视频并校验完整性…")
            return
        summary = summarize_ytdlp_progress(line)
        if summary is None:
            return
        now = time.monotonic()
        stage = "⬇️ 正在下载"
        detail = f"进度：{summary}"
        last_activity_at = now
        if now - last_progress_update < PROGRESS_EDIT_INTERVAL_SECONDS:
            return
        last_progress_update = now
        await render_progress()

    async def update_delivery_stage(delivery_stage: str) -> None:
        if delivery_stage == "compressing":
            await set_stage("🗜️ 正在压缩", "文件超过 Telegram 限制，正在压缩…")
        elif delivery_stage == "splitting":
            await set_stage("✂️ 正在分割", "文件超过 Telegram 限制，正在分割…")
        elif delivery_stage.startswith("uploading_part:"):
            _, index, total = delivery_stage.split(":", 2)
            await set_stage("⬆️ 正在上传", f"正在上传第 {index}/{total} 个分片；Telegram 不提供上传百分比。")
        else:
            await set_stage("⬆️ 正在上传", "正在发送到 Telegram；Telegram 不提供上传百分比。")

    try:
        await render_progress()
        async with deps.limiter.reserve(user_id):
            async with deps.global_semaphore:
                await set_stage("⬇️ 正在下载", "正在连接 YouTube，等待第一批数据…")
                result = await deps.download(
                    plan=plan,
                    download_dir=deps.settings.download_dir,
                    title=request.info.title,
                    task_id=task_id,
                    progress_callback=update_download_progress,
                    max_output_bytes=deps.settings.max_source_bytes,
                )
                await set_stage("⬆️ 正在上传", "正在准备发送到 Telegram…")
                delivery = await send_download_result(
                    message,
                    result,
                    deps.settings.max_upload_bytes,
                    status_callback=update_delivery_stage,
                )
                if not delivery.can_send:
                    await set_stage("⚠️ 原视频过大", "为保持原尺寸和画质，文件没有被压缩。请降低一档清晰度。")
                    return
                await set_stage("✅ 已完成", "文件已经发送。")
    except BotError as exc:
        logger.error("task=%s video=%s stage=%s code=%s diagnostic=%s", task_id, plan.video_id, exc.stage, exc.code.value, redact(exc.diagnostic))
        await safe_edit_message_text(query, f"{exc.user_message} [{exc.code.value}]")
    except RuntimeError:
        logger.error("task=%s video=%s stage=download code=DOWNLOAD_FAILED", task_id, plan.video_id)
        await safe_edit_message_text(query, "Download failed. Please retry or choose another format. [DOWNLOAD_FAILED]")
    except Exception:
        logger.error("task=%s video=%s stage=download code=DOWNLOAD_FAILED", task_id, plan.video_id)
        await safe_edit_message_text(query, "Download failed. Please retry or choose another format. [DOWNLOAD_FAILED]")
    finally:
        heartbeat_stop.set()
        await heartbeat_task
        deps.requests.pop(task_id, None)
        if result is not None:
            remove_task_directory(result.path, deps.settings.download_dir)
        else:
            remove_task_directory(deps.settings.download_dir / task_id / "result", deps.settings.download_dir)


def get_deps(context: ContextTypes.DEFAULT_TYPE) -> BotDependencies:
    return context.application.bot_data["deps"]


async def safe_edit_message_text(target, text: str, **kwargs) -> None:
    try:
        await target.edit_message_text(text, **kwargs)
    except Exception:
        logger.warning("Failed to edit Telegram status message.")


def format_elapsed(seconds: float) -> str:
    total = max(0, int(seconds))
    minutes, seconds = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours} 小时 {minutes:02d} 分 {seconds:02d} 秒"
    if minutes:
        return f"{minutes} 分 {seconds:02d} 秒"
    return f"{seconds} 秒"


def format_bytes(size: float) -> str:
    value = max(0.0, float(size))
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def task_downloaded_bytes(task_dir: Path) -> int:
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


def remember_user(update: Update, deps: BotDependencies) -> None:
    if update.effective_user:
        deps.user_ids.add(update.effective_user.id)


def broadcast_text_from_message(text: str) -> str | None:
    parts = text.split(maxsplit=1)
    if len(parts) != 2 or not parts[1].strip():
        return None
    return parts[1].strip()


def _is_admin(update: Update, settings: Settings) -> bool:
    user = update.effective_user
    return bool(user and user.id in settings.admin_ids)


def _new_task_id(deps: BotDependencies) -> str:
    for _ in range(8):
        task_id = secrets.token_urlsafe(9)
        if task_id not in deps.requests:
            return task_id
    raise RuntimeError("Could not allocate a unique task ID.")


def _prune_requests(deps: BotDependencies, now: float | None = None) -> list[str]:
    now = time.time() if now is None else now
    removed: list[str] = []
    ttl = max(1, deps.settings.request_cache_ttl_seconds)
    for task_id, request in list(deps.requests.items()):
        plans_expired = bool(request.info.plans) and all(plan.expires_at < now for plan in request.info.plans)
        if not request.claimed and (now - request.created_at > ttl or plans_expired):
            deps.requests.pop(task_id, None)
            removed.append(task_id)

    capacity = max(1, deps.settings.max_cached_requests)
    overflow = len(deps.requests) - capacity
    if overflow > 0:
        candidates = sorted(
            ((task_id, request) for task_id, request in deps.requests.items() if not request.claimed),
            key=lambda item: item[1].created_at,
        )
        for task_id, _request in candidates[:overflow]:
            deps.requests.pop(task_id, None)
            removed.append(task_id)
    return removed
