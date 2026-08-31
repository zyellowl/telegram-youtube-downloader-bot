from types import SimpleNamespace

import pytest

from ytdl_bot.config import Settings
from ytdl_bot.handlers import (
    BotDependencies,
    DownloadRequest,
    _prune_requests,
    broadcast_text_from_message,
    build_format_keyboard,
    download_callback,
    format_elapsed,
    format_bytes,
    link_message,
    start_text,
)
from ytdl_bot.media import normalize_media_info


def test_start_text_explains_usage_boundary():
    text = start_text()

    assert "YouTube" in text
    assert "authorized" in text.lower()


def test_format_keyboard_contains_available_choices():
    info = normalize_media_info(
        {
            "id": "demo",
            "title": "Demo",
            "duration": 120,
            "formats": [{"format_id": "18", "height": 360, "ext": "mp4"}],
        }
    )

    keyboard = build_format_keyboard("task-1", info)

    assert keyboard.inline_keyboard[0][0].text == "MP3 · 大小未知"
    assert keyboard.inline_keyboard[1][0].text == "360p · 大小未知"
    callback = keyboard.inline_keyboard[1][0].callback_data
    assert info.get_choice(next(choice.choice for choice in info.choices if choice.choice.value == "360p")).plan_id in callback
    assert "youtube.com" not in callback


def test_broadcast_text_from_message_parses_admin_message():
    assert broadcast_text_from_message("/broadcast hello users") == "hello users"
    assert broadcast_text_from_message("/broadcast") is None


def test_format_elapsed_keeps_long_running_progress_readable():
    assert format_elapsed(9.9) == "9 秒"
    assert format_elapsed(75) == "1 分 15 秒"
    assert format_elapsed(3671) == "1 小时 01 分 11 秒"
    assert format_bytes(1024 * 1024) == "1.0 MB"


@pytest.mark.asyncio
async def test_download_callback_does_not_mask_download_error_when_status_edit_fails(tmp_path):
    async def failing_download(**kwargs):
        raise RuntimeError("yt-dlp failed")

    info = normalize_media_info(
        {
            "id": "demo",
            "title": "Demo",
            "duration": 120,
            "formats": [{"format_id": "18", "height": 360, "ext": "mp4"}],
        }
    )
    deps = BotDependencies(
        settings=Settings(telegram_bot_token="token", download_dir=tmp_path),
        download=failing_download,
    )
    deps.requests["task-1"] = DownloadRequest(url="https://youtu.be/demo", info=info)
    plan_id = info.get_choice(next(choice.choice for choice in info.choices if choice.choice.value == "360p")).plan_id
    query = FailingStatusEditQuery(data=f"download:task-1:{plan_id}")
    update = SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(id=123))
    context = SimpleNamespace(application=SimpleNamespace(bot_data={"deps": deps}))

    await download_callback(update, context)

    assert "正在排队" in query.edits[0]
    assert any("正在下载" in edit for edit in query.edits)
    assert "Download failed" in query.edits[-1]
    assert "yt-dlp failed" not in query.edits[-1]
    assert "task-1" not in deps.requests


class FailingStatusEditQuery:
    def __init__(self, data: str):
        self.data = data
        self.message = object()
        self.edits: list[str] = []

    async def answer(self):
        return None

    async def edit_message_text(self, text: str, **kwargs):
        self.edits.append(text)
        if text == "Download failed. Please retry or choose another format. [DOWNLOAD_FAILED]":
            raise RuntimeError("Telegram edit failed")


class LinkStatus:
    def __init__(self):
        self.text = ""
        self.reply_markup = None

    async def edit_text(self, text: str, **kwargs):
        self.text = text
        self.reply_markup = kwargs.get("reply_markup")


class LinkMessage:
    def __init__(self, text: str):
        self.text = text
        self.statuses: list[LinkStatus] = []

    async def reply_text(self, _text: str):
        status = LinkStatus()
        self.statuses.append(status)
        return status


def _demo_info():
    return normalize_media_info(
        {
            "id": "same-video",
            "title": "Demo",
            "duration": 30,
            "formats": [{"format_id": "18", "width": 640, "height": 360, "ext": "mp4"}],
        }
    )


@pytest.mark.asyncio
async def test_same_url_messages_get_unique_isolated_task_ids(tmp_path):
    info = _demo_info()

    async def inspect(_url, _max_duration):
        return info

    deps = BotDependencies(Settings(telegram_bot_token="token", download_dir=tmp_path), inspect=inspect)
    context = SimpleNamespace(application=SimpleNamespace(bot_data={"deps": deps}))
    messages = [LinkMessage("https://youtu.be/same-video"), LinkMessage("https://youtu.be/same-video")]
    for user_id, message in enumerate(messages, start=1):
        update = SimpleNamespace(effective_message=message, effective_user=SimpleNamespace(id=user_id))
        await link_message(update, context)

    assert len(deps.requests) == 2
    callbacks = [message.statuses[0].reply_markup.inline_keyboard[0][0].callback_data for message in messages]
    task_ids = [callback.split(":")[1] for callback in callbacks]
    assert task_ids[0] != task_ids[1]
    assert all(task_id.replace("_", "").replace("-", "").isalnum() for task_id in task_ids)
    assert all(len(callback.encode()) <= 64 for callback in callbacks)


@pytest.mark.asyncio
async def test_callback_cleanup_for_same_url_does_not_remove_other_request_or_directory(tmp_path):
    info = _demo_info()

    async def failing_download(**_kwargs):
        raise RuntimeError("failed")

    deps = BotDependencies(Settings(telegram_bot_token="token", download_dir=tmp_path), download=failing_download)
    deps.requests["request-a"] = DownloadRequest("https://www.youtube.com/watch?v=same-video", info)
    deps.requests["request-b"] = DownloadRequest("https://www.youtube.com/watch?v=same-video", info)
    for task_id in deps.requests:
        task_dir = tmp_path / task_id
        task_dir.mkdir()
        (task_dir / "keep.tmp").write_text(task_id)
    plan_id = info.choices[0].plan_id
    query = FailingStatusEditQuery(f"download:request-a:{plan_id}")
    update = SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(id=1))
    context = SimpleNamespace(application=SimpleNamespace(bot_data={"deps": deps}))

    await download_callback(update, context)

    assert "request-a" not in deps.requests
    assert not (tmp_path / "request-a").exists()
    assert "request-b" in deps.requests
    assert (tmp_path / "request-b" / "keep.tmp").read_text() == "request-b"


def test_request_cache_prunes_expired_and_oldest_entries(tmp_path):
    settings = Settings(
        telegram_bot_token="token",
        download_dir=tmp_path,
        request_cache_ttl_seconds=10,
        max_cached_requests=2,
    )
    deps = BotDependencies(settings)
    info = _demo_info()
    deps.requests = {
        "expired": DownloadRequest("https://youtu.be/same-video", info, created_at=1),
        "oldest": DownloadRequest("https://youtu.be/same-video", info, created_at=92),
        "newer": DownloadRequest("https://youtu.be/same-video", info, created_at=94),
        "newest": DownloadRequest("https://youtu.be/same-video", info, created_at=96),
    }

    removed = _prune_requests(deps, now=100)

    assert set(removed) == {"expired", "oldest"}
    assert list(deps.requests) == ["newer", "newest"]
