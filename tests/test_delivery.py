from pathlib import Path

import pytest

from ytdl_bot.delivery import choose_delivery_method, split_file_for_upload
from ytdl_bot.models import DownloadKind, DownloadResult, MediaProbe


def test_delivery_splits_oversized_video_without_compressing(tmp_path: Path):
    file_path = tmp_path / "big.mp4"
    file_path.write_bytes(b"x" * 20)
    result = DownloadResult(path=file_path, title="Big", kind=DownloadKind.VIDEO, size_bytes=20)

    decision = choose_delivery_method(result, max_upload_bytes=10)

    assert decision.can_send is True
    assert decision.method == "split_video"
    assert "无损分段" in decision.reason


def test_delivery_splits_non_video_files_over_limit(tmp_path: Path):
    file_path = tmp_path / "big.bin"
    file_path.write_bytes(b"x" * 20)
    result = DownloadResult(path=file_path, title="Big", kind=DownloadKind.DOCUMENT, size_bytes=20)

    decision = choose_delivery_method(result, max_upload_bytes=10)

    assert decision.can_send is True
    assert decision.method == "split_document"


def test_delivery_prefers_audio_for_audio_results(tmp_path: Path):
    file_path = tmp_path / "song.mp3"
    file_path.write_bytes(b"x" * 5)
    result = DownloadResult(path=file_path, title="Song", kind=DownloadKind.AUDIO, size_bytes=5)

    decision = choose_delivery_method(result, max_upload_bytes=10)

    assert decision.can_send is True
    assert decision.method == "audio"


def test_split_file_for_upload_creates_ordered_parts_under_limit(tmp_path: Path):
    file_path = tmp_path / "video.mp4"
    file_path.write_bytes(b"abcdefghijklmnopqrst")

    parts = split_file_for_upload(file_path, chunk_bytes=8)

    assert [part.name for part in parts] == [
        "video.mp4.part001of003",
        "video.mp4.part002of003",
        "video.mp4.part003of003",
    ]
    assert [part.read_bytes() for part in parts] == [b"abcdefgh", b"ijklmnop", b"qrst"]


class FakeMessage:
    def __init__(self):
        self.texts: list[str] = []
        self.documents: list[str] = []
        self.videos: list[str] = []
        self.video_kwargs: list[dict] = []

    async def reply_text(self, text: str):
        self.texts.append(text)

    async def reply_document(self, document, filename: str, caption: str | None = None):
        self.documents.append(filename)

    async def reply_video(self, video, caption: str | None = None, filename: str | None = None, **kwargs):
        self.videos.append(filename or "")
        self.video_kwargs.append(kwargs)


@pytest.mark.asyncio
async def test_send_download_result_sends_large_document_as_parts(tmp_path: Path):
    from ytdl_bot.delivery import send_download_result

    file_path = tmp_path / "big.bin"
    file_path.write_bytes(b"abcdefghijklmnopqrst")
    result = DownloadResult(path=file_path, title="Big File", kind=DownloadKind.DOCUMENT, size_bytes=20)
    message = FakeMessage()

    decision = await send_download_result(message, result, max_upload_bytes=8)

    assert decision.method == "split_document"
    assert "Splitting into 3 parts" in message.texts[0]
    assert message.documents == [
        "Big File.bin.part001of003",
        "Big File.bin.part002of003",
        "Big File.bin.part003of003",
    ]


@pytest.mark.asyncio
async def test_send_download_result_sends_large_video_as_playable_lossless_segments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from ytdl_bot import delivery

    file_path = tmp_path / "big.mp4"
    file_path.write_bytes(b"abcdefghijklmnopqrst")
    result = DownloadResult(path=file_path, title="Big Video", kind=DownloadKind.VIDEO, size_bytes=20)
    message = FakeMessage()

    probe = MediaProbe(10, 1, 1, 854, 480, 480, "h264", "aac", "mov,mp4")
    segments = []
    for index in range(3):
        segment = tmp_path / f"segment_{index:03d}.mp4"
        segment.write_bytes(b"part")
        segments.append((segment, probe))

    async def fake_split_video_for_upload(_path: Path, _limit: int):
        return segments

    monkeypatch.setattr(delivery, "split_video_for_upload", fake_split_video_for_upload)

    decision = await delivery.send_download_result(message, result, max_upload_bytes=8)

    assert decision.method == "split_video"
    assert "无损分段" in message.texts[0]
    assert message.videos == [
        "Big Video.part01of03.mp4",
        "Big Video.part02of03.mp4",
        "Big Video.part03of03.mp4",
    ]


@pytest.mark.asyncio
async def test_send_video_supplies_original_dimensions_and_streaming_flag(tmp_path: Path):
    from ytdl_bot.delivery import send_download_result

    file_path = tmp_path / "wide.mp4"
    file_path.write_bytes(b"video")
    probe = MediaProbe(
        duration=63.8,
        video_streams=1,
        audio_streams=1,
        width=1920,
        height=1080,
        short_edge=1080,
        video_codec="h264",
        audio_codec="aac",
        format_name="mov,mp4",
    )
    result = DownloadResult(file_path, "Wide", DownloadKind.VIDEO, 5, probe)
    message = FakeMessage()

    decision = await send_download_result(message, result, max_upload_bytes=10)

    assert decision.method == "video"
    assert message.video_kwargs == [{
        "width": 1920,
        "height": 1080,
        "duration": 63,
        "supports_streaming": True,
    }]
