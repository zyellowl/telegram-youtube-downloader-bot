from ytdl_bot.url_utils import canonicalize_youtube_url, extract_youtube_urls


def test_extracts_watch_and_short_urls():
    text = "one https://www.youtube.com/watch?v=abc123 and https://youtu.be/xyz987"

    assert extract_youtube_urls(text) == [
        "https://www.youtube.com/watch?v=abc123",
        "https://youtu.be/xyz987",
    ]


def test_ignores_non_youtube_links():
    assert extract_youtube_urls("https://example.com/watch?v=abc") == []


def test_canonicalizes_watch_shorts_and_share_urls_to_one_video():
    expected = "https://www.youtube.com/watch?v=AbC_123-xYz"
    assert canonicalize_youtube_url("https://www.youtube.com/watch?v=AbC_123-xYz&list=PL1&t=45s") == expected
    assert canonicalize_youtube_url("https://www.youtube.com/shorts/AbC_123-xYz?feature=share") == expected
    assert canonicalize_youtube_url("https://youtu.be/AbC_123-xYz?si=tracking") == expected


def test_rejects_playlist_only_and_forged_youtube_hosts():
    assert canonicalize_youtube_url("https://www.youtube.com/playlist?list=PL1") is None
    assert canonicalize_youtube_url("https://youtube.com.evil.test/watch?v=AbC_123-xYz") is None
