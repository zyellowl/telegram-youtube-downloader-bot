from ytdl_bot.app import build_application, build_telegram_request
from ytdl_bot.config import Settings


def test_telegram_request_ignores_system_proxy_environment():
    request = build_telegram_request()

    assert request._client_kwargs["trust_env"] is False


def test_telegram_request_uses_explicit_proxy_only_when_configured():
    request = build_telegram_request(proxy_url="http://127.0.0.1:7890")

    assert request._client_kwargs["trust_env"] is False
    assert request._client_kwargs["proxy"] == "http://127.0.0.1:7890"


def test_telegram_request_uses_resilient_timeouts_for_proxy_flakiness():
    request = build_telegram_request()
    timeout = request._client_kwargs["timeout"]

    assert timeout.connect == 20.0
    assert timeout.read == 20.0
    assert timeout.write == 60.0
    assert timeout.pool == 10.0


def test_application_keeps_long_polling_direct_when_send_proxy_is_configured():
    application = build_application(
        Settings(telegram_bot_token="123:abc", telegram_proxy_url="http://127.0.0.1:7890")
    )
    get_updates_request, send_request = application.bot._request

    assert get_updates_request._client_kwargs["proxy"] is None
    assert send_request._client_kwargs["proxy"] == "http://127.0.0.1:7890"


def test_application_uses_bounded_concurrent_update_processing():
    application = build_application(Settings(telegram_bot_token="123:abc", max_concurrent_downloads=3))
    assert application.update_processor.max_concurrent_updates == 5


def test_application_enables_full_local_bot_api_mode():
    application = build_application(
        Settings(telegram_bot_token="123:abc", telegram_api_base_url="http://127.0.0.1:8081/")
    )

    assert str(application.bot.base_url) == "http://127.0.0.1:8081/bot123:abc"
    assert str(application.bot.base_file_url) == "http://127.0.0.1:8081/file/bot123:abc"
    assert application.bot.local_mode is True
