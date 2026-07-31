#!/bin/sh
set -eu

mkdir -p \
    /app/workspace/runs \
    /app/workspace/chat_runs \
    /app/workspace/reports \
    /app/workspace/memory \
    /app/workspace/cache/finance_ashare \
    /app/workspace/paper_trading \
    /app/workspace/backtests

# Add newly shipped templates without replacing operator-edited configuration.
cp -a -n /app/workspace_templates/. /app/workspace/

exec "$@"
