from ytdl_bot import media
from ytdl_bot.media import normalize_media_info
from ytdl_bot.models import FormatChoice


def test_normalizes_video_formats_into_godzilla_style_choices():
    raw = {
        "id": "abc",
        "title": "Demo",
        "duration": 120,
        "thumbnail": "https://img.example/thumb.jpg",
        "webpage_url": "https://youtu.be/abc",
        "formats": [
            {"format_id": "18", "height": 360, "ext": "mp4", "filesize": 10},
            {"format_id": "22", "height": 720, "ext": "mp4", "filesize": 20},
            {"format_id": "137", "height": 1080, "ext": "mp4", "filesize": 30},
        ],
    }

    info = normalize_media_info(raw)

    assert info.title == "Demo"
    assert [choice.label for choice in info.choices] == [
        "MP3 · 大小未知",
        "360p · 约 10.0 B",
        "720p · 约 20.0 B",
        "1080p · 约 30.0 B",
    ]
    assert info.get_choice(FormatChoice.VIDEO_720).format_id == "22"


def test_rejects_too_long_media():
    raw = {"title": "Long", "duration": 9999, "formats": []}

    info = normalize_media_info(raw, max_duration_seconds=300)

    assert info.is_rejected is True
    assert "too long" in info.rejection_reason.lower()


def test_adaptive_1080_plan_beats_low_progressive_and_uses_exact_ids():
    info = normalize_media_info(
        {
            "id": "AbC_123-xYz",
            "extractor_key": "Youtube",
            "title": "Adaptive",
            "duration": 60,
            "webpage_url": "https://www.youtube.com/watch?v=AbC_123-xYz&list=PL1&t=45",
            "formats": [
                {"format_id": "18", "width": 640, "height": 360, "ext": "mp4", "vcodec": "avc1", "acodec": "mp4a"},
                {"format_id": "137", "width": 1920, "height": 1080, "ext": "mp4", "vcodec": "avc1", "acodec": "none"},
                {"format_id": "140", "ext": "m4a", "vcodec": "none", "acodec": "mp4a", "abr": 128},
            ],
        }
    )

    choice = info.get_choice(FormatChoice.VIDEO_1080)
    plan = info.get_plan(choice.plan_id)
    assert info.canonical_url == "https://www.youtube.com/watch?v=AbC_123-xYz"
    assert plan.selector == "137+140"
    assert "18" not in plan.selector
    assert plan.target_short_edge == 1080
    assert (plan.target_width, plan.target_height) == (1920, 1080)
    assert choice.label == "1080p · 1920×1080 · 大小未知"


def test_vertical_video_is_bucketed_by_short_edge():
    info = normalize_media_info(
        {
            "id": "AbC_123-xYz",
            "title": "Vertical",
            "duration": 20,
            "formats": [
                {"format_id": "v1", "width": 1080, "height": 1920, "ext": "mp4", "vcodec": "avc1", "acodec": "none"},
                {"format_id": "a1", "ext": "m4a", "vcodec": "none", "acodec": "mp4a"},
            ],
        }
    )

    assert info.get_choice(FormatChoice.VIDEO_1080) is not None
    assert info.get_choice(FormatChoice.VIDEO_1080).label == "1080p · 1080×1920 · 大小未知"
    assert info.get_choice(FormatChoice.VIDEO_720) is None


def test_non_mp4_source_is_not_offered_when_recompression_is_disabled():
    info = normalize_media_info(
        {
            "id": "AbC_123-xYz",
            "title": "WebM only",
            "duration": 20,
            "formats": [
                {"format_id": "v1", "width": 1920, "height": 1080, "ext": "webm", "vcodec": "vp9", "acodec": "none"},
                {"format_id": "a1", "ext": "webm", "vcodec": "none", "acodec": "opus"},
            ],
        }
    )

    assert info.get_choice(FormatChoice.VIDEO_1080) is None


def test_estimated_size_includes_selected_video_and_audio_streams():
    info = normalize_media_info(
        {
            "id": "AbC_123-xYz",
            "title": "Sized",
            "duration": 60,
            "formats": [
                {"format_id": "v", "width": 1280, "height": 720, "ext": "mp4", "vcodec": "avc1", "acodec": "none", "filesize_approx": 40_000_000},
                {"format_id": "a", "ext": "m4a", "vcodec": "none", "acodec": "mp4a", "filesize_approx": 5_000_000},
            ],
        }
    )

    choice = info.get_choice(FormatChoice.VIDEO_720)
    assert choice.estimated_size == 45_000_000
    assert choice.label == "720p · 1280×720 · 约 42.9 MB"


def test_playlist_result_is_rejected_before_plan_creation():
    info = normalize_media_info({"_type": "playlist", "id": "PL1", "entries": [{"id": "abc"}]})
    assert info.is_rejected is True
    assert info.choices == []


def test_extract_info_uses_shared_deno_and_node_runtime_options(monkeypatch):
    captured = {}

    class FakeYoutubeDL:
        def __init__(self, options):
            captured.update(options)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def extract_info(self, url, download):
            return {"id": "demo", "url": url, "download": download}

    monkeypatch.setattr(media, "YoutubeDL", FakeYoutubeDL)
    monkeypatch.setattr(
        media,
        "youtube_js_runtimes",
        lambda: {"deno": {"path": "/safe/deno"}, "node": {"path": "/safe/node"}},
    )

    result = media._extract_info("https://www.youtube.com/watch?v=demo")

    assert set(captured["js_runtimes"]) >= {"deno", "node"}
    assert captured["noplaylist"] is True
    assert result["download"] is False
