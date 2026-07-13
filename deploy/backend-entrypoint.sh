#!/bin/sh
set -eu

mkdir -p \
    /app/workspace/runs \
    /app/workspace/chat_runs \
    /app/workspace/proposals \
    /app/workspace/subagent_discovery \
    /app/workspace/reports \
    /app/workspace/memory \
    /app/workspace/cache/finance_ashare \
    /app/workspace/paper_trading

# Add newly shipped templates without replacing operator-edited configuration.
cp -a -n /app/workspace_templates/. /app/workspace/

exec "$@"
