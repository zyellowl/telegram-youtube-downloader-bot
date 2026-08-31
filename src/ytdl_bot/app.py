from __future__ import annotations

import logging

from telegram.request import HTTPXRequest
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, MessageHandler, filters

from ytdl_bot.config import Settings
from ytdl_bot.handlers import (
    BotDependencies,
    broadcast_command,
    cleanup_command,
    download_callback,
    help_command,
    link_message,
    start_command,
    status_command,
)
from ytdl_bot.runtime import check_runtime_capabilities


def build_application(settings: Settings | None = None) -> Application:
    settings = settings or Settings()
    logging.basicConfig(level=settings.log_level.upper(), format="%(asctime)s %(levelname)s %(message)s")
    # Telegram tokens are embedded in Bot API URLs, so keep third-party request
    # logging quiet even when application-level logs are INFO.
    for logger_name in ("httpx", "httpcore", "telegram"):
        logging.getLogger(logger_name).setLevel(logging.WARNING)

    builder = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .request(build_telegram_request(proxy_url=settings.telegram_proxy_url))
        .get_updates_request(build_telegram_request(read_timeout=30.0))
        .concurrent_updates(settings.max_concurrent_downloads + 2)
    )
    if settings.telegram_api_base_url:
        api_root = settings.telegram_api_base_url.rstrip("/")
        builder = (
            builder
            .base_url(api_root + "/bot")
            .base_file_url(api_root + "/file/bot")
            .local_mode(True)
        )

    application = builder.build()
    application.bot_data["deps"] = BotDependencies(settings=settings)
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("cleanup", cleanup_command))
    application.add_handler(CommandHandler("broadcast", broadcast_command))
    application.add_handler(CallbackQueryHandler(download_callback, pattern=r"^download:"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, link_message))
    return application


def main() -> None:
    settings = Settings()
    report = check_runtime_capabilities(settings.download_dir)
    if not report.ready:
        raise RuntimeError(report.summary)
    logging.getLogger(__name__).info(
        "Runtime ready: yt-dlp=%s ejs=%s js=%s ffmpeg=yes ffprobe=yes",
        report.versions.get("yt-dlp", "unknown"),
        report.versions.get("yt-dlp-ejs", "unknown"),
        report.js_runtime,
    )
    application = build_application(settings)
    application.run_polling(allowed_updates=["message", "callback_query"], timeout=20)


def build_telegram_request(read_timeout: float = 20.0, proxy_url: str | None = None) -> HTTPXRequest:
    return HTTPXRequest(
        connect_timeout=20.0,
        read_timeout=read_timeout,
        write_timeout=60.0,
        pool_timeout=10.0,
        media_write_timeout=300.0,
        proxy=proxy_url or None,
        httpx_kwargs={"trust_env": False},
    )
