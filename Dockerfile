FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 BOT_BOARD_DB=/data/bot_board.db ROUTINES_CONFIG_DIR=/data/routines

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# The routines runner runs inside the board (BOT_BOARD_ROUTINES=1), and checks get
# bash, python3, git and gh (a pinned release).
ARG GH_VERSION=2.88.1
RUN apt-get update && apt-get install -y --no-install-recommends git curl ca-certificates \
 && curl -fsSL "https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_linux_$(dpkg --print-architecture).tar.gz" \
    | tar -xz -C /tmp && mv /tmp/gh_*/bin/gh /usr/local/bin/gh && rm -rf /tmp/gh_* \
 && apt-get purge -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

COPY app ./app
COPY bin/routines bin/bot ./bin/
COPY AGENTS.md ADMIN.md ./

# Set by deploy/release.sh to the git tag being released; "dev" for ad-hoc builds.
ARG BOT_BOARD_VERSION=dev
ENV BOT_BOARD_VERSION=$BOT_BOARD_VERSION
# The commit released: keepers update to it, not past it (#240), and are compared with it on /fleet (#239);
# empty for ad-hoc builds.
ARG BOT_BOARD_COMMIT=
ENV BOT_BOARD_COMMIT=$BOT_BOARD_COMMIT
LABEL org.opencontainers.image.title="bot_board" \
      org.opencontainers.image.version="$BOT_BOARD_VERSION"

RUN useradd -u 10001 -m board && mkdir -p /data && chown board:board /data
USER board
VOLUME /data
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz',timeout=2).status==200 else 1)"

# On a deploy's SIGTERM the board answers its long-polls at once and finishes the requests in
# flight, for up to 20 s (#243); docker-compose.yml gives it 30 s before a kill.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--timeout-graceful-shutdown", "20"]
