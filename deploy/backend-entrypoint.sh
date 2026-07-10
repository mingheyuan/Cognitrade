#!/bin/sh
set -eu

mkdir -p \
    /app/workspace/runs \
    /app/workspace/chat_runs \
    /app/workspace/proposals \
    /app/workspace/subagent_discovery \
    /app/workspace/reports \
    /app/workspace/memory

# Add newly shipped templates without replacing operator-edited configuration.
cp -a -n /app/workspace_templates/. /app/workspace/

exec "$@"
