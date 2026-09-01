# Project Guide

This repository contains a Python 3.12 Telegram bot that downloads authorized
public YouTube media and sends it back through Telegram.

## Working agreement

- Work from this repository root and keep `main` tracking `origin/main`.
- Never commit `.env`, bot tokens, Telegram API credentials, downloaded media,
  runtime logs, live-test samples, or generated reports.
- Preserve the YouTube video's selected resolution, aspect ratio, and encoded
  quality. Do not add silent fallback to another resolution and do not
  re-encode video to make it smaller.
- Split oversized MP4 files losslessly at keyframes when needed for Telegram's
  upload limit. Validate final streams, duration, width, and height with
  `ffprobe` before delivery.
- Keep download progress visible, including phase, percentage, speed, ETA, and
  a heartbeat while no new bytes are reported.
- Do not add support for bypassing DRM, paid content, private videos, login
  walls, regional restrictions, or other access controls.

## Verification

Run before committing code changes:

```bash
./.venv/bin/pytest -v
./.venv/bin/python -m compileall src tests
git diff --check
```

The regular test suite is offline. Run live YouTube or Telegram checks only
when the task explicitly requires them and only with authorized public samples.

## Delivery

- Keep commits focused and use descriptive messages.
- After relevant tests pass, push completed updates to `origin/main`.
- The macOS LaunchAgent runs a deployed copy outside this repository. A Git
  push alone does not update that running copy; deploy and restart the service
  separately when the user asks for a live rollout.
