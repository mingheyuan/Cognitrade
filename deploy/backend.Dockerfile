ARG PYTHON_IMAGE=public.ecr.aws/docker/library/python:3.11-slim-bookworm
FROM ${PYTHON_IMAGE}

USER root

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./requirements.txt
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt \
    && python -m playwright install --with-deps chromium

COPY research_system ./research_system
COPY domain_packs ./domain_packs
COPY workspace/config ./workspace_templates/config
COPY scripts/migrate_workspace.py ./scripts/migrate_workspace.py
COPY deploy/backend-entrypoint.sh /usr/local/bin/cognitrade-entrypoint

RUN if ! getent group cognitrade >/dev/null; then groupadd --gid 10001 cognitrade; fi \
    && if ! id -u cognitrade >/dev/null 2>&1; then useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin cognitrade; fi \
    && chmod 0755 /usr/local/bin/cognitrade-entrypoint \
    && mkdir -p workspace/runs workspace/chat_runs workspace/reports workspace/memory workspace/paper_trading workspace/backtests \
    && cp -a workspace_templates/. workspace/ \
    && chown -R cognitrade:cognitrade workspace /home/cognitrade /ms-playwright \
    && python -c "import research_system.api.app"

USER 10001:10001

EXPOSE 8000

ENTRYPOINT ["/usr/local/bin/cognitrade-entrypoint"]
CMD ["python", "-m", "research_system.api"]
