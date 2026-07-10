ARG PYTHON_IMAGE=public.ecr.aws/docker/library/python:3.11-slim-bookworm
FROM ${PYTHON_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./requirements.txt
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.txt

COPY research_system ./research_system
COPY domain_packs ./domain_packs
COPY workspace/config ./workspace_templates/config
COPY workspace/taxonomy ./workspace_templates/taxonomy
COPY deploy/backend-entrypoint.sh /usr/local/bin/cognitrade-entrypoint

RUN groupadd --gid 10001 cognitrade \
    && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin cognitrade \
    && chmod 0755 /usr/local/bin/cognitrade-entrypoint \
    && mkdir -p workspace/runs workspace/chat_runs workspace/proposals workspace/reports workspace/memory \
    && cp -a workspace_templates/. workspace/ \
    && chown -R cognitrade:cognitrade workspace /home/cognitrade \
    && python -c "import research_system.api.app"

USER 10001:10001

EXPOSE 8000

ENTRYPOINT ["/usr/local/bin/cognitrade-entrypoint"]
CMD ["python", "-m", "research_system.api"]
