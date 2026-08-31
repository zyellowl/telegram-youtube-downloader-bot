from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from ytdl_bot.downloader import download_media
from ytdl_bot.media import inspect_url
from ytdl_bot.models import FormatChoice
from ytdl_bot.url_utils import canonicalize_youtube_url

pytestmark = pytest.mark.live
SAMPLES_PATH = Path(__file__).with_name("samples.json")


def _samples() -> list[dict]:
    if os.environ.get("RUN_LIVE_YOUTUBE") != "1" or not SAMPLES_PATH.exists():
        return []
    return json.loads(SAMPLES_PATH.read_text())


@pytest.mark.parametrize("case", _samples(), ids=lambda case: case["case_id"])
@pytest.mark.asyncio
async def test_authorized_public_youtube_sample(case: dict, tmp_path: Path):
    canonical = canonicalize_youtube_url(case["url"])
    assert canonical is not None
    info = await inspect_url(canonical)
    assert info.video_id == case["expected_video_id"]
    assert info.title == case["expected_title"]
    choice = FormatChoice(case["target"])
    media_choice = info.get_choice(choice)
    assert media_choice is not None, "The exact requested quality is unavailable; do not silently downgrade"
    plan = info.get_plan(media_choice.plan_id)
    assert plan is not None
    result = await download_media(plan=plan, download_dir=tmp_path, title=info.title, task_id=case["case_id"])
    try:
        assert result.probe is not None
        assert result.probe.short_edge == case["expected_short_edge"] if choice is not FormatChoice.MP3 else True
        assert abs(result.probe.duration - float(case["source_duration"])) <= max(2, float(case["source_duration"]) * 0.01)
    finally:
        shutil.rmtree(tmp_path / case["case_id"], ignore_errors=True)


def test_live_suite_requires_explicit_samples_when_enabled():
    if os.environ.get("RUN_LIVE_YOUTUBE") == "1":
        assert SAMPLES_PATH.exists(), "Create ignored tests/live/samples.json from samples.example.json"
        assert _samples(), "At least one authorized public sample is required"
