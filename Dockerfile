FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 BOT_BOARD_DB=/data/bot_board.db

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY AGENTS.md ./AGENTS.md
COPY bin ./bin

# Set by deploy/release.sh to the git tag being released; "dev" for ad-hoc builds.
ARG BOT_BOARD_VERSION=dev
ENV BOT_BOARD_VERSION=$BOT_BOARD_VERSION
LABEL org.opencontainers.image.title="bot_board" \
      org.opencontainers.image.version="$BOT_BOARD_VERSION"

RUN useradd -u 10001 -m board && mkdir -p /data && chown board:board /data
USER board
VOLUME /data
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz',timeout=2).status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
