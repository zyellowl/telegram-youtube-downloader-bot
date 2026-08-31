import re
from urllib.parse import parse_qs, urlparse


URL_PATTERN = re.compile(r"https?://[^\s<>'\"]+")
YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"}
VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{3,64}$")


def extract_youtube_urls(text: str) -> list[str]:
    urls: list[str] = []
    for match in URL_PATTERN.finditer(text):
        url = match.group(0).rstrip(".,)]}")
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        if host in YOUTUBE_HOSTS:
            urls.append(url)
    return urls


def canonicalize_youtube_url(url: str) -> str | None:
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or host not in YOUTUBE_HOSTS:
        return None
    segments = [segment for segment in parsed.path.split("/") if segment]
    video_id: str | None = None
    if host == "youtu.be":
        video_id = segments[0] if len(segments) == 1 else None
    elif parsed.path == "/watch":
        values = parse_qs(parsed.query).get("v", [])
        video_id = values[0] if len(values) == 1 else None
    elif len(segments) == 2 and segments[0] in {"shorts", "embed", "live"}:
        video_id = segments[1]
    if not video_id or not VIDEO_ID_PATTERN.fullmatch(video_id):
        return None
    return canonical_url_for_video_id(video_id)


def canonical_url_for_video_id(video_id: str) -> str:
    if not VIDEO_ID_PATTERN.fullmatch(video_id):
        raise ValueError("Invalid YouTube video ID")
    return f"https://www.youtube.com/watch?v={video_id}"


def video_id_from_url(url: str | None) -> str | None:
    canonical = canonicalize_youtube_url(url) if url else None
    return parse_qs(urlparse(canonical).query)["v"][0] if canonical else None
