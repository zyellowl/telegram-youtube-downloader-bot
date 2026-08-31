#!/bin/zsh

set -eu

APP_ROOT="/Users/wsejoy/Library/Application Support/TelegramYTDLBot"
PROJECT_DIR="$APP_ROOT/app"
RECOVERED_ENV="$APP_ROOT/.env"

if [[ ! -r "$RECOVERED_ENV" ]]; then
  print -u2 "Bot environment file is unavailable: $RECOVERED_ENV"
  exit 78
fi

set -a
source "$RECOVERED_ENV"
set +a

# The recovered proxy pointed to a local port that is no longer listening.
# Direct Telegram connectivity has been verified on this Mac.
export TELEGRAM_PROXY_URL=""
export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$PROJECT_DIR/src"
export PATH="$APP_ROOT/.venv/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

cd "$PROJECT_DIR"
exec "$APP_ROOT/.venv/bin/python" -m ytdl_bot
