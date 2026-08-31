from pathlib import Path

import pytest

from ytdl_bot import transcode
from ytdl_bot.models import DownloadKind, FormatChoice, SelectionPlan


def _video_plan(duration: float = 100.0, edge: int = 1080) -> SelectionPlan:
    return SelectionPlan(
        plan_id="p",
        video_id="AbC_123-xYz",
        canonical_url="https://www.youtube.com/watch?v=AbC_123-xYz",
        choice=FormatChoice.VIDEO_1080,
        selector="137+140",
        target_short_edge=edge,
        require_audio=True,
        expected_kind=DownloadKind.VIDEO,
        source_duration=duration,
        expires_at=9999999999.0,
    )


def test_validate_probe_accepts_complete_horizontal_and_vertical_video():
    for width, height in [(1920, 1080), (1080, 1920)]:
        probe = {
            "format": {"duration": "100.5", "format_name": "mov,mp4"},
            "streams": [
                {"codec_type": "video", "codec_name": "h264", "width": width, "height": height},
                {"codec_type": "audio", "codec_name": "aac"},
            ],
        }
        result = transcode.validate_probe(probe, _video_plan(), DownloadKind.VIDEO)
        assert result.short_edge == 1080


def test_validate_probe_requires_exact_inspected_width_and_height():
    plan = SelectionPlan(
        plan_id="exact",
        video_id="AbC_123-xYz",
        canonical_url="https://www.youtube.com/watch?v=AbC_123-xYz",
        choice=FormatChoice.VIDEO_1080,
        selector="137+140",
        target_short_edge=1080,
        require_audio=True,
        expected_kind=DownloadKind.VIDEO,
        source_duration=100.0,
        expires_at=9999999999.0,
        target_width=1920,
        target_height=1080,
    )
    wrong_aspect = {
        "format": {"duration": "100", "format_name": "mov,mp4"},
        "streams": [
            {"codec_type": "video", "codec_name": "h264", "width": 1440, "height": 1080},
            {"codec_type": "audio", "codec_name": "aac"},
        ],
    }

    with pytest.raises(RuntimeError, match="width"):
        transcode.validate_probe(wrong_aspect, plan, DownloadKind.VIDEO)


def test_video_segment_command_copies_streams_without_reencoding(tmp_path: Path):
    args = transcode.build_ffmpeg_segment_args(
        tmp_path / "source.mp4",
        tmp_path / "segments",
        30.0,
    )

    assert args[args.index("-c") + 1] == "copy"
    assert "-vf" not in args
    assert "libx264" not in args
    assert args[-1].endswith("segment_%03d.mp4")


@pytest.mark.parametrize(
    "probe",
    [
        {"format": {"duration": "100"}, "streams": [{"codec_type": "video", "width": 1920, "height": 1080}]},
        {"format": {"duration": "92"}, "streams": [{"codec_type": "video", "width": 1920, "height": 1080}, {"codec_type": "audio"}]},
        {"format": {"duration": "100"}, "streams": [{"codec_type": "video", "width": 1280, "height": 720}, {"codec_type": "audio"}]},
    ],
)
def test_validate_probe_rejects_missing_audio_truncation_or_wrong_resolution(probe):
    with pytest.raises(RuntimeError):
        transcode.validate_probe(probe, _video_plan(), DownloadKind.VIDEO)


def test_validate_probe_requires_audio_only_for_mp3():
    plan = SelectionPlan(
        plan_id="a",
        video_id="AbC_123-xYz",
        canonical_url="https://www.youtube.com/watch?v=AbC_123-xYz",
        choice=FormatChoice.MP3,
        selector="140",
        target_short_edge=None,
        require_audio=True,
        expected_kind=DownloadKind.AUDIO,
        source_duration=30.0,
        expires_at=9999999999.0,
    )
    valid = {"format": {"duration": "30.2", "format_name": "mp3"}, "streams": [{"codec_type": "audio", "codec_name": "mp3"}]}
    assert transcode.validate_probe(valid, plan, DownloadKind.AUDIO).audio_streams == 1
    with pytest.raises(RuntimeError):
        transcode.validate_probe({"format": {"duration": "30"}, "streams": [{"codec_type": "video"}]}, plan, DownloadKind.AUDIO)


@pytest.mark.asyncio
async def test_compress_video_retries_with_lower_bitrate_when_output_is_still_too_large(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    input_path = tmp_path / "input.mp4"
    input_path.write_bytes(b"video")
    attempts: list[list[str]] = []

    async def fake_probe_duration_seconds(file_path: Path) -> float:
        assert file_path == input_path
        return 1.0

    async def fake_probe_media(file_path: Path) -> dict:
        return {
            "format": {"duration": "1.0", "format_name": "mov,mp4"},
            "streams": [
                {"codec_type": "video", "codec_name": "h264", "width": 320, "height": 180},
                {"codec_type": "audio", "codec_name": "aac"},
            ],
        }

    async def fake_run_ffmpeg(args: list[str]) -> None:
        attempts.append(args)
        output_path = Path(args[-1])
        output_size = 11_000 if len(attempts) == 1 else 9_000
        output_path.write_bytes(b"x" * output_size)

    monkeypatch.setattr(transcode, "probe_duration_seconds", fake_probe_duration_seconds)
    monkeypatch.setattr(transcode, "probe_media", fake_probe_media)
    monkeypatch.setattr(transcode, "_run_ffmpeg", fake_run_ffmpeg)

    output_path = await transcode.compress_video_for_upload(input_path, max_upload_bytes=10_000)

    assert output_path.stat().st_size == 9_000
    assert len(attempts) == 2
    first_bitrate = _arg_value(attempts[0], "-b:v")
    second_bitrate = _arg_value(attempts[1], "-b:v")
    assert second_bitrate < first_bitrate


def _arg_value(args: list[str], flag: str) -> int:
    return int(args[args.index(flag) + 1])
