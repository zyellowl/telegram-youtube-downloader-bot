from pathlib import Path

from ytdl_bot.config import Settings


def test_settings_parse_admin_ids_and_defaults(tmp_path: Path):
    settings = Settings(
        telegram_bot_token="123:abc",
        admin_user_ids="1, 2,3",
        download_dir=tmp_path,
    )

    assert settings.admin_ids == {1, 2, 3}
    assert settings.max_concurrent_downloads == 2
    assert settings.max_tasks_per_user == 1
    assert settings.max_duration_seconds == 0
    assert settings.max_source_bytes == 2_000_000_000
    assert settings.request_cache_ttl_seconds == 1800
    assert settings.max_cached_requests == 512


def test_settings_local_bot_api_flag(tmp_path: Path):
    settings = Settings(
        telegram_bot_token="123:abc",
        admin_user_ids="",
        download_dir=tmp_path,
        telegram_api_base_url="http://telegram-bot-api:8081",
    )

    assert settings.uses_local_bot_api is True


def test_settings_accepts_explicit_telegram_proxy(tmp_path: Path):
    settings = Settings(
        telegram_bot_token="123:abc",
        admin_user_ids="",
        download_dir=tmp_path,
        telegram_proxy_url="http://127.0.0.1:7890",
    )

    assert settings.telegram_proxy_url == "http://127.0.0.1:7890"
