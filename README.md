# Telegram YouTube Downloader Bot

A Telegram bot that mirrors the core GodzillaBot-style downloader experience without copying its brand: send a YouTube link, choose `MP3`, `360p`, `480p`, `720p`, or `1080p`, then receive the downloaded file in chat.

This bot is intended for authorized public content only. It does not bypass DRM, paid content, private videos, login walls, or regional restrictions.

## Features

- Direct YouTube link handling in Telegram chat.
- Inline format buttons for MP3 and available video qualities.
- Live download percentage, speed, ETA, phase labels, and a 10-second heartbeat.
- Direct YouTube connections with four concurrent HLS/DASH fragment downloads;
  local macOS proxy settings do not silently throttle media transfers.
- `yt-dlp` downloads and `ffmpeg` audio/video processing.
- Original-resolution video delivery with explicit width, height, and streaming metadata.
- No automatic video recompression; oversized originals are split losslessly at
  keyframes into multiple playable MP4 messages under the cloud API limit.
- Per-user and global concurrency limits.
- Automatic cleanup for old temporary files.
- Admin commands: `/status`, `/cleanup`, `/broadcast`.
- Optional Docker Compose support for server deployment and Local Bot API Server configuration.

## Requirements

- Python 3.12+
- `ffmpeg` (including `ffprobe`)
- Node.js 22+, Deno 2.3+, Bun 1.2.11+, or current QuickJS. The project installs
  a supported Deno package so Docker does not rely on an outdated OS Node.js.
- A Telegram bot token from [BotFather](https://t.me/BotFather)

Docker is not required. The bot is a normal Python process that connects to Telegram, receives messages, downloads media with `yt-dlp`, and sends files back through the Telegram Bot API.

## Run Without Docker

Create and configure a bot token:

```bash
cp .env.example .env
```

Edit `.env` and set:

```bash
TELEGRAM_BOT_TOKEN=your-token-from-botfather
ADMIN_USER_IDS=your-telegram-user-id
```

If your machine needs a proxy to reach Telegram, also set:

```bash
TELEGRAM_PROXY_URL=http://127.0.0.1:7890
```

Install dependencies:

```bash
python3 -m venv .venv
./.venv/bin/pip install -e ".[dev]"
```

The dependency is intentionally declared as `yt-dlp[default]>=2026.08.19`; the
default extra installs the EJS support package required by current YouTube
player extraction. Verify all download prerequisites without printing secrets:

```bash
./.venv/bin/python -c 'from pathlib import Path; from ytdl_bot.runtime import check_runtime_capabilities; r=check_runtime_capabilities(Path("downloads")); print(r.summary); raise SystemExit(not r.ready)'
```

Run the bot:

```bash
./.venv/bin/python -m ytdl_bot
```

Keep that command running on your Mac, VPS, or server. Telegram talks to this running process through polling, so users only interact with the Telegram bot chat.

## macOS Always-On Service

This Mac uses a per-user LaunchAgent named `com.wsejoy.telegram-ytdl-bot`.
It starts automatically after login and restarts the bot if the process exits.
Temporary network loss does not require a manual restart: the Telegram polling
loop keeps retrying, and `launchd` restarts the process if it ever terminates.

Inspect the service:

```bash
launchctl print gui/$(id -u)/com.wsejoy.telegram-ytdl-bot
```

Restart it after changing code or configuration:

```bash
launchctl kickstart -k gui/$(id -u)/com.wsejoy.telegram-ytdl-bot
```

Because macOS restricts background access to `Documents`, the LaunchAgent runs
a deployed copy from `~/Library/Application Support/TelegramYTDLBot`. Logs are
written to that directory under `logs/launchd.out.log` and
`logs/launchd.err.log`.

## Optional Docker Deployment

Docker is only a packaging option. It is useful when you want the server to install Python dependencies and `ffmpeg` the same way every time, or when you want an easy restart policy.

Run the bot service:

```bash
docker compose up --build bot
```

The `downloads/` directory is mounted into the container and used for temporary files.

## Larger Telegram Uploads

Telegram's normal cloud Bot API has a 50 MB upload limit for bots. This bot never recompresses videos: an original above the configured limit is split at keyframes into multiple directly playable MP4 messages, preserving dimensions, aspect ratio, codecs, and encoded quality.

Oversized non-video files are still sent as ordered document parts such as `file.bin.part001of005`. Recombine split files on Mac/Linux with:

```bash
cat 'video.mp4.part'* > 'video.mp4'
```

If you want original large videos to upload as one Telegram file without compression, run a Local Bot API Server and point the bot at it:

```bash
TELEGRAM_API_BASE_URL=http://telegram-bot-api:8081
MAX_UPLOAD_BYTES=2000000000
```

Then start the optional profile:

```bash
docker compose --profile local-bot-api up --build
```

On this Mac, put `TELEGRAM_API_ID` and `TELEGRAM_API_HASH` from
`https://my.telegram.org` in the protected runtime `.env`, then run:

```bash
./scripts/start_local_bot_api.zsh
```

Before switching the bot to the local endpoint, call the cloud Bot API
`logOut` method once. The application enables python-telegram-bot's local mode
and configures both the API and file endpoints automatically.

You still need enough server disk, bandwidth, and Telegram-side allowance for large uploads.

## Telegram Commands

- `/start` shows the quick-start message.
- `/help` explains usage and limitations.
- `/status` shows runtime state for admins.
- `/cleanup` removes expired temporary files for admins.
- `/broadcast <text>` sends a message to users who have interacted with this running bot process.

## Limits

`MAX_DURATION_SECONDS=0` means the bot will not reject videos by duration before showing format choices. This is the default, so longer videos can still show MP3/quality buttons first.

Pending format-button requests are held only in memory. `REQUEST_CACHE_TTL_SECONDS`
defaults to 1800 seconds and `MAX_CACHED_REQUESTS` defaults to 512; expired and
oldest unused requests are pruned lazily when a new link or callback arrives.
Running downloads are never evicted by this cache cleanup.

Videos that exceed `MAX_UPLOAD_BYTES` are losslessly remuxed into ordered playable video segments. Non-video files that exceed the limit are sent as split document parts. Configure a Local Bot API Server only if you require one large Telegram message instead of several playable parts.

The downloader also enforces `MAX_SOURCE_BYTES` (2 GB by default) to prevent
unexpectedly huge source files from exhausting local disk space.

## Testing

Run the unit test suite:

```bash
./.venv/bin/pytest -v
```

Run a syntax compile check:

```bash
./.venv/bin/python -m compileall src tests
```

The normal suite is offline. Real YouTube and Telegram tests are an explicit
release gate because mock tests cannot prove that a selected adaptive stream is
currently downloadable or that Telegram received the complete file. Put only
authorized public sample URLs in the ignored `tests/live/samples.json`, then run
the opt-in live suite described by `tests/live/samples.example.json`. Never put
the bot token or proxy credentials in a test report.

## Download correctness

- Watch, Shorts, and `youtu.be` links are reduced to one canonical video URL;
  playlist, timestamp, and tracking parameters are removed.
- Quality buttons are backed by an immutable inspect-time plan. A 1080p plan
  contains only exact-1080p format IDs and pairs adaptive video with audio
  before considering an exact-resolution combined format.
- yt-dlp aborts when a selected format or fragment is unavailable. The only
  accepted output path is yt-dlp's machine-readable `after_move` result.
- Every file is checked by ffprobe for streams, complete duration, and exact
  inspected width and height before upload. Video choices use YouTube's native
  H.264 MP4 stream and are never re-encoded. Invalid, resized, or silently
  downgraded files are deleted instead of sent.

## Safety Notes

- Use this only for content you have rights to download or store.
- The bot does not bypass DRM, paid walls, private videos, login requirements, or access controls.
- Files are stored temporarily and should be removed by task cleanup or `/cleanup`.
- Set `MAX_UPLOAD_BYTES` and concurrency limits conservatively on small servers.
