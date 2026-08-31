from importlib import metadata
from pathlib import Path

from ytdl_bot.runtime import check_runtime_capabilities, youtube_js_runtimes


def test_project_declares_current_ytdlp_default_extra():
    project = Path("pyproject.toml").read_text()
    assert 'yt-dlp[default]>=2026.08.19' in project


def test_runtime_reports_named_missing_capabilities_without_values(tmp_path: Path):
    report = check_runtime_capabilities(
        tmp_path,
        executable_lookup=lambda _name: None,
        package_version=lambda _name: (_ for _ in ()).throw(metadata.PackageNotFoundError()),
    )

    assert report.ready is False
    assert {issue.capability for issue in report.issues} >= {
        "yt-dlp",
        "yt-dlp-ejs",
        "javascript-runtime",
        "ffmpeg",
        "ffprobe",
    }
    assert "TOKEN" not in report.summary.upper()


def test_runtime_accepts_explicit_node_and_media_tools(tmp_path: Path):
    versions = {"yt-dlp": "2026.8.19", "yt-dlp-ejs": "0.8.0"}
    report = check_runtime_capabilities(
        tmp_path,
        executable_lookup=lambda name: f"/safe/{name}" if name in {"node", "ffmpeg", "ffprobe"} else None,
        package_version=lambda name: versions[name],
        runtime_supported=lambda _name, _path: True,
    )

    assert report.ready is True
    assert report.js_runtime == "node"


def test_runtime_rejects_an_installed_but_unsupported_js_version(tmp_path: Path):
    versions = {"yt-dlp": "2026.8.19", "yt-dlp-ejs": "0.8.0"}
    report = check_runtime_capabilities(
        tmp_path,
        executable_lookup=lambda name: f"/safe/{name}" if name in {"node", "ffmpeg", "ffprobe"} else None,
        package_version=lambda name: versions[name],
        runtime_supported=lambda _name, _path: False,
    )
    assert report.ready is False
    assert "javascript-runtime" in {issue.capability for issue in report.issues}


def test_youtube_runtime_options_enable_deno_and_node_together():
    runtimes = youtube_js_runtimes(lambda name: f"/safe/{name}" if name in {"deno", "node"} else None)
    assert runtimes["deno"] == {"path": "/safe/deno"}
    assert runtimes["node"] == {"path": "/safe/node"}
    assert set(runtimes) >= {"deno", "node"}
