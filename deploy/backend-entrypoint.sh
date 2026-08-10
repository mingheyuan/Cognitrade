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

if [ "$(id -u)" -eq 0 ]; then
    # Named volumes outlive images and may contain directories created by an
    # older root-running release. Repair ownership before dropping privileges
    # so final report memory/cache persistence cannot fail after completion.
    chown -R 10001:10001 /app/workspace
    exec setpriv \
        --reuid=10001 \
        --regid=10001 \
        --init-groups \
        --no-new-privs \
        "$@"
fi

exec "$@"
