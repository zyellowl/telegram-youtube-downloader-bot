FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src ./src

RUN pip install --no-cache-dir . \
    && python -c "from pathlib import Path; from ytdl_bot.runtime import check_runtime_capabilities; r=check_runtime_capabilities(Path('/app/downloads')); assert r.ready, r.summary"

CMD ["python", "-m", "ytdl_bot"]
