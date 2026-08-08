ARG PYTHON_IMAGE=python:3.11-slim-bookworm
FROM ${PYTHON_IMAGE}

ARG PIP_INDEX_URL=https://pypi.org/simple
ARG DEBIAN_MIRROR=http://deb.debian.org/debian
ARG DEBIAN_SECURITY_MIRROR=http://deb.debian.org/debian-security

USER root

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    sed -i \
        -e "s|^URIs: http://deb.debian.org/debian-security$|URIs: ${DEBIAN_SECURITY_MIRROR}|" \
        -e "s|^URIs: http://deb.debian.org/debian$|URIs: ${DEBIAN_MIRROR}|" \
        /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates libgomp1

COPY requirements.txt requirements-quant.txt ./
RUN --mount=type=cache,target=/root/.cache/pip \
    PIP_NO_CACHE_DIR=0 python -m pip install --index-url "${PIP_INDEX_URL}" \
        --retries 10 --timeout 60 --upgrade pip \
    && PIP_NO_CACHE_DIR=0 python -m pip install --index-url "${PIP_INDEX_URL}" \
        --retries 10 --resume-retries 20 --timeout 60 -r requirements.txt
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    python -m playwright install-deps chromium
RUN python -m playwright install chromium

COPY research_system ./research_system
COPY domain_packs ./domain_packs
COPY workspace/config ./workspace_templates/config
COPY scripts/migrate_workspace.py ./scripts/migrate_workspace.py
COPY deploy/backend-entrypoint.sh /usr/local/bin/cognitrade-entrypoint

RUN if ! getent group cognitrade >/dev/null; then groupadd --gid 10001 cognitrade; fi \
    && if ! id -u cognitrade >/dev/null 2>&1; then useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin cognitrade; fi \
    && chmod 0755 /usr/local/bin/cognitrade-entrypoint \
    && mkdir -p workspace/runs workspace/chat_runs workspace/reports workspace/memory workspace/cache/finance_ashare workspace/paper_trading workspace/backtests \
    && cp -a workspace_templates/. workspace/ \
    && chown -R cognitrade:cognitrade workspace /home/cognitrade /ms-playwright \
    && python -c "import research_system.api.app"

USER 10001:10001

RUN python -m research_system.runtime.diagnostics.status.deployment_preflight \
    --workspace /app/workspace

EXPOSE 8000

ENTRYPOINT ["/usr/local/bin/cognitrade-entrypoint"]
CMD ["python", "-m", "research_system.api"]
