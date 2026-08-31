#!/bin/zsh

set -eu

APP_ROOT="/Users/wsejoy/Library/Application Support/TelegramYTDLBot"
RUNTIME_ENV="$APP_ROOT/.env"
PROJECT_DIR="/Users/wsejoy/Documents/ChatGPT/telegram_bot"

if [[ ! -r "$RUNTIME_ENV" ]]; then
  print -u2 "Missing runtime environment: $RUNTIME_ENV"
  exit 78
fi

set -a
source "$RUNTIME_ENV"
set +a

if [[ -z "${TELEGRAM_API_ID:-}" || -z "${TELEGRAM_API_HASH:-}" ]]; then
  print -u2 "TELEGRAM_API_ID and TELEGRAM_API_HASH must be configured first."
  exit 78
fi

cd "$PROJECT_DIR"
exec docker compose --env-file "$RUNTIME_ENV" --profile local-bot-api up -d telegram-bot-api
