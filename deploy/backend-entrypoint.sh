#!/bin/sh
set -eu

object_store_root="${COGNITRADE_OBJECT_STORE_ROOT:-/app/workspace/strategy_objects}"

mkdir -p \
    /app/workspace/runs \
    /app/workspace/chat_runs \
    /app/workspace/reports \
    /app/workspace/memory \
    /app/workspace/cache/finance_ashare \
    /app/workspace/paper_trading \
    /app/workspace/backtests \
    "$object_store_root"

# Add newly shipped templates without replacing operator-edited configuration.
cp -a -n /app/workspace_templates/. /app/workspace/

if [ "$(id -u)" -eq 0 ]; then
    # Named volumes outlive images and may contain directories created by an
    # older root-running release. Repair ownership before dropping privileges
    # so final report memory/cache persistence cannot fail after completion.
    chown -R 10001:10001 /app/workspace
    case "$object_store_root" in
        /app/workspace|/app/workspace/*) ;;
        *) chown -R 10001:10001 "$object_store_root" ;;
    esac
    exec setpriv \
        --reuid=10001 \
        --regid=10001 \
        --init-groups \
        --no-new-privs \
        "$@"
fi

exec "$@"
