import asyncio
import json
import sys
from pathlib import Path

import pytest

from ytdl_bot import downloader
from ytdl_bot.downloader import build_ytdlp_args, download_media, parse_result_path, summarize_ytdlp_progress, task_output_size
from ytdl_bot.models import DownloadKind, FormatChoice, SelectionPlan


def _plan(choice: FormatChoice = FormatChoice.VIDEO_1080) -> SelectionPlan:
    return SelectionPlan(
        plan_id="plan-1080",
        video_id="AbC_123-xYz",
        canonical_url="https://www.youtube.com/watch?v=AbC_123-xYz",
        choice=choice,
        selector="137+140/399+140",
        target_short_edge=choice.height,
        require_audio=True,
        expected_kind=DownloadKind.VIDEO,
        source_duration=60.0,
        expires_at=9999999999.0,
    )


def test_build_ytdlp_video_args_use_argument_list_not_shell_string(tmp_path):
    args = build_ytdlp_args(
        url="https://youtu.be/abc",
        plan=_plan(FormatChoice.VIDEO_1080),
        output_dir=tmp_path,
        task_id="task-1",
    )

    assert isinstance(args, list)
    assert args[:3] == [sys.executable, "-m", "yt_dlp"]
    assert args[-1] == "https://www.youtube.com/watch?v=AbC_123-xYz"
    assert args[args.index("-f") + 1] == "137+140/399+140"


def test_build_ytdlp_mp3_args_extract_audio(tmp_path):
    args = build_ytdlp_args(
        url="https://youtu.be/abc",
        choice=FormatChoice.MP3,
        output_dir=tmp_path,
        task_id="task-2",
    )

    assert "--extract-audio" in args
    assert "--audio-format" in args
    assert "mp3" in args


def test_build_ytdlp_args_enable_structured_progress(tmp_path):
    args = build_ytdlp_args(
        url="https://youtu.be/abc",
        choice=FormatChoice.VIDEO_360,
        output_dir=tmp_path,
        task_id="task-3",
    )

    assert "--progress-template" in args
    template = args[args.index("--progress-template") + 1]
    assert template.startswith("download:%(progress._percent_str)s")
    assert args[args.index("--proxy") + 1] == ""
    assert args[args.index("--concurrent-fragments") + 1] == "4"
    assert args[args.index("--progress-delta") + 1] == "1"


def test_build_ytdlp_video_args_never_put_progressive_low_quality_first(tmp_path):
    args = build_ytdlp_args(
        url="https://youtu.be/abc",
        plan=_plan(),
        output_dir=tmp_path,
        task_id="task-4",
    )
    selector = args[args.index("-f") + 1]

    assert selector == "137+140/399+140"
    assert "height<=" not in selector


def test_build_args_abort_on_missing_fragments_and_print_unique_result(tmp_path):
    args = build_ytdlp_args(plan=_plan(), output_dir=tmp_path, task_id="task-5")
    assert "--check-formats" in args
    assert "--abort-on-unavailable-fragments" in args
    assert "--progress" in args
    assert [args[index + 1] for index, value in enumerate(args) if value == "--print"] == [
        "after_move:RESULT:%(filepath)j"
    ]
    assert "fragment:exp=1:20" in args


def test_parse_result_path_accepts_json_unicode_and_rejects_missing_or_multiple(tmp_path: Path):
    media = tmp_path / "有 空格.mp4"
    media.write_bytes(b"ok")
    result = parse_result_path([f'RESULT:"{media}"'], tmp_path)
    assert result == media.resolve()
    with pytest.raises(RuntimeError):
        parse_result_path([], tmp_path)
    with pytest.raises(RuntimeError):
        parse_result_path([f'RESULT:"{media}"', f'RESULT:"{media}"'], tmp_path)


def test_parse_result_path_rejects_escape_temporary_and_empty_files(tmp_path: Path):
    outside = tmp_path.parent / "outside.mp4"
    outside.write_bytes(b"x")
    with pytest.raises(RuntimeError):
        parse_result_path([f'RESULT:"{outside}"'], tmp_path)
    temporary = tmp_path / "video.part"
    temporary.write_bytes(b"x")
    with pytest.raises(RuntimeError):
        parse_result_path([f'RESULT:"{temporary}"'], tmp_path)
    empty = tmp_path / "empty.mp4"
    empty.touch()
    with pytest.raises(RuntimeError):
        parse_result_path([f'RESULT:"{empty}"'], tmp_path)


def test_summarize_ytdlp_progress_formats_download_line():
    summary = summarize_ytdlp_progress("download: 42.3% 1.2MiB/s ETA 00:19")

    assert summary == "42.3% at 1.2MiB/s, ETA 00:19"
    assert summarize_ytdlp_progress("[debug] download: 10.0% 2MiB/s ETA 00:09") == "10.0% at 2MiB/s, ETA 00:09"


def test_task_output_size_ignores_resume_metadata(tmp_path: Path):
    (tmp_path / "video.part").write_bytes(b"12345")
    (tmp_path / "video.ytdl").write_bytes(b"metadata")

    assert task_output_size(tmp_path) == 5


@pytest.mark.asyncio
async def test_download_media_reads_real_progress_from_stderr(tmp_path: Path, monkeypatch):
    task_id = "stderr-progress"
    output_path = tmp_path / task_id / f"{task_id}.mp3"
    output_path.parent.mkdir()
    output_path.write_bytes(b"audio")

    class FakeProcess:
        returncode = 0

        def __init__(self):
            self.stdout = asyncio.StreamReader()
            self.stdout.feed_data(f"RESULT:{json.dumps(str(output_path))}\n".encode())
            self.stdout.feed_eof()
            self.stderr = asyncio.StreamReader()
            self.stderr.feed_data(b"download: 42.3% 1.2MiB/s ETA 00:19\n")
            self.stderr.feed_eof()

        async def wait(self):
            return self.returncode

    async def fake_subprocess(*_args, **_kwargs):
        return FakeProcess()

    async def fake_probe(_path):
        return {}

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_subprocess)
    monkeypatch.setattr(downloader, "probe_media", fake_probe)
    monkeypatch.setattr(downloader, "validate_probe", lambda *_args: None)
    events: list[str] = []

    async def capture_progress(event: str) -> None:
        events.append(event)

    result = await download_media(
        url="https://youtu.be/demo",
        choice=FormatChoice.MP3,
        download_dir=tmp_path,
        task_id=task_id,
        progress_callback=capture_progress,
    )

    assert result.path == output_path.resolve()
    assert events == ["download: 42.3% 1.2MiB/s ETA 00:19", "stage:processing"]
