#!/usr/bin/env bash

set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd -P)"
SCRIPT_PATH="$SCRIPT_DIR/$(basename "${BASH_SOURCE[0]}")"
if [[ "${COGNITRADE_CANONICAL_EXEC:-0}" != 1 || "$0" != "$SCRIPT_PATH" ]]; then
    exec env COGNITRADE_CANONICAL_EXEC=1 "$SCRIPT_PATH" "$@"
fi
FRONTEND_DIR="$ROOT_DIR/frontend"
REQUIREMENTS_FILE="$ROOT_DIR/requirements.txt"
RAW_ARGS=("$@")
cd "$ROOT_DIR"

log() {
    printf '[cognitrade] %s\n' "$*"
}

warn() {
    printf '[cognitrade] warning: %s\n' "$*" >&2
}

fail() {
    printf '[cognitrade] error: %s\n' "$*" >&2
    exit 1
}

usage() {
    cat <<'EOF'
Usage:
  ./scripts/service.sh [start] [options]
  ./scripts/service.sh check [options]
  ./scripts/service.sh stop
  ./scripts/service.sh status
  ./scripts/service.sh logs [api|frontend|supervisor|all]

Start/check options:
  --backend-only               Only manage the FastAPI service.
  --frontend-only              Only manage the Vite frontend.
  --preview                    Build and serve Vite preview (local verification only).
  --install [auto|always|never]
                               Dependency policy; default: auto.
  --detach                     Run the supervisor in the background.
  --reload                     Enable the Uvicorn development reloader.
  --local-unauthenticated      Allow tokenless loopback-only API startup explicitly.
  --require-openai             Fail when OPENAI_API_KEY is missing.
  --backend-host HOST          Override COGNITRADE_API_HOST.
  --backend-port PORT          Override COGNITRADE_API_PORT.
  --frontend-host HOST         Vite bind host; default: 127.0.0.1.
  --frontend-port PORT         Vite port; default: 4173.
  --startup-timeout SECONDS    Detached total startup timeout; default: 300.
  --ready-timeout SECONDS      Readiness timeout; default: 45.
  --shutdown-timeout SECONDS   Graceful shutdown timeout; default: 30.
  --runtime-dir PATH           PID/state/log directory; default: .runtime.
  -h, --help                   Show this help.

Environment equivalents:
  COGNITRADE_PYTHON_BIN
  COGNITRADE_FRONTEND_HOST / COGNITRADE_FRONTEND_PORT
  COGNITRADE_STARTUP_TIMEOUT_SECONDS
  COGNITRADE_READY_TIMEOUT_SECONDS / COGNITRADE_SHUTDOWN_TIMEOUT_SECONDS
  COGNITRADE_RUNTIME_DIR

Examples:
  ./scripts/service.sh --install auto
  ./scripts/service.sh --local-unauthenticated
  ./scripts/service.sh --detach
  ./scripts/service.sh status
  ./scripts/service.sh stop
EOF
}

require_value() {
    local option="$1"
    local value="${2:-}"
    if [[ -z "$value" || "$value" == --* ]]; then
        fail "$option requires a value"
    fi
}

validate_positive_integer() {
    local label="$1"
    local value="$2"
    case "$value" in
        ''|*[!0-9]*) fail "$label must be a positive integer" ;;
    esac
    (( 10#$value > 0 )) || fail "$label must be a positive integer"
}

validate_port() {
    local label="$1"
    local value="$2"
    validate_positive_integer "$label" "$value"
    (( 10#$value <= 65535 )) || fail "$label must be between 1 and 65535"
}

file_checksum() {
    cksum "$1" | awk '{print $1 ":" $2}'
}

read_pid_file() {
    local path="$1"
    local value=""
    [[ -r "$path" ]] && value="$(sed -n '1p' "$path" 2>/dev/null || true)"
    case "$value" in
        ''|*[!0-9]*) return 1 ;;
    esac
    printf '%s' "$value"
}

pid_is_alive() {
    local state=""
    kill -0 "$1" 2>/dev/null || return 1
    state="$(ps -p "$1" -o stat= 2>/dev/null | tr -d '[:space:]' || true)"
    [[ -n "$state" && "$state" != Z* ]]
}

process_group_is_alive() {
    local pgid="$1"
    kill -0 -- "-$pgid" 2>/dev/null || return 1
    ps -axo pgid=,stat= 2>/dev/null | awk -v target="$pgid" '
        $1 == target && $2 !~ /^Z/ { found = 1 }
        END { exit(found ? 0 : 1) }
    '
}

operation_is_alive() {
    pid_is_alive "$1" || process_group_is_alive "$1"
}

pid_command_contains() {
    local pid="$1"
    local expected="$2"
    local command_line=""
    command_line="$(ps -p "$pid" -o command= 2>/dev/null || true)"
    [[ -n "$command_line" && "$command_line" == *"$expected"* ]]
}

url_host() {
    local host="$1"
    local normalized=""
    case "$host" in
        0.0.0.0|'') printf '127.0.0.1' ;;
        ::|'[::]') printf '[::1]' ;;
        *:*)
            normalized="${host#[}"
            normalized="${normalized%]}"
            printf '[%s]' "$normalized"
            ;;
        *) printf '%s' "$host" ;;
    esac
}

RUNTIME_DIR="${COGNITRADE_RUNTIME_DIR:-$ROOT_DIR/.runtime}"
PROJECT_STATE_DIR="$ROOT_DIR/.runtime"
PROJECT_LOCK_FILE="$PROJECT_STATE_DIR/operation.lock"
LOCK_DIR=""
READY_FILE=""
LOG_DIR=""
SUPERVISOR_LOG=""
API_LOG=""
FRONTEND_LOG=""

refresh_runtime_paths() {
    LOCK_DIR="$RUNTIME_DIR/lock"
    READY_FILE="$RUNTIME_DIR/ready"
    LOG_DIR="$RUNTIME_DIR/logs"
    SUPERVISOR_LOG="$LOG_DIR/supervisor.log"
    API_LOG="$LOG_DIR/api.log"
    FRONTEND_LOG="$LOG_DIR/frontend.log"
}
refresh_runtime_paths

normalize_runtime_dir() {
    case "$RUNTIME_DIR" in
        *$'\n'*|*$'\r'*) fail "runtime directory must not contain line breaks" ;;
    esac
    case "$RUNTIME_DIR" in
        /*) ;;
        *) RUNTIME_DIR="$ROOT_DIR/$RUNTIME_DIR" ;;
    esac
    refresh_runtime_paths
}
normalize_runtime_dir

supervisor_pid() {
    read_pid_file "$LOCK_DIR/supervisor.pid"
}

service_is_running() {
    local pid=""
    pid="$(supervisor_pid 2>/dev/null || true)"
    [[ -n "$pid" ]] && pid_is_alive "$pid" && pid_command_contains "$pid" "$SCRIPT_PATH"
}

project_operation_pid() {
    read_pid_file "$PROJECT_LOCK_FILE"
}

project_operation_runtime_dir() {
    sed -n '2p' "$PROJECT_LOCK_FILE" 2>/dev/null || true
}

project_operation_is_running() {
    local pid=""
    pid="$(project_operation_pid 2>/dev/null || true)"
    [[ -n "$pid" ]] && pid_is_alive "$pid" && pid_command_contains "$pid" "$SCRIPT_PATH"
}

clear_stale_project_lock() {
    [[ -f "$PROJECT_LOCK_FILE" ]] || return 0
    project_operation_is_running && return 1
    local owner_runtime_dir=""
    owner_runtime_dir="$(project_operation_runtime_dir)"
    [[ "$owner_runtime_dir" == /* ]] || fail "stale project lock is missing a valid runtime directory: $PROJECT_LOCK_FILE"
    if ! clear_stale_runtime_at "$owner_runtime_dir"; then
        fail "stale project operation still has a live supervisor: $owner_runtime_dir"
    fi
    rm -f "$PROJECT_LOCK_FILE"
}

clear_stale_runtime_at() {
    local runtime_dir="$1"
    local lock_dir="$runtime_dir/lock"
    local ready_file="$runtime_dir/ready"
    local owner_pid=""
    local name=""
    local pid=""

    [[ -d "$lock_dir" ]] || {
        rm -f "$ready_file"
        return 0
    }
    owner_pid="$(read_pid_file "$lock_dir/supervisor.pid" 2>/dev/null || true)"
    if [[ -n "$owner_pid" ]] && pid_is_alive "$owner_pid" && pid_command_contains "$owner_pid" "$SCRIPT_PATH"; then
        return 1
    fi
    for name in active api frontend; do
        pid="$(read_pid_file "$lock_dir/$name.pid" 2>/dev/null || true)"
        if [[ -n "$pid" ]] && operation_is_alive "$pid"; then
            fail "stale runtime has a live orphan $name process group (pid=$pid); stop it manually before retrying: $runtime_dir"
        fi
    done
    rm -f "$lock_dir/supervisor.pid" "$lock_dir/api.pid" "$lock_dir/frontend.pid" \
        "$lock_dir/active.pid" "$lock_dir/shutdown_timeout" "$ready_file"
    rmdir "$lock_dir" 2>/dev/null || fail "stale lock contains unknown files: $lock_dir"
}

clear_stale_runtime_state() {
    clear_stale_runtime_at "$RUNTIME_DIR"
}

wait_for_pid_exit() {
    local pid="$1"
    local timeout_seconds="$2"
    local ticks=$((10#$timeout_seconds * 5))
    local index=0
    while pid_is_alive "$pid" && (( index < ticks )); do
        sleep 0.2
        index=$((index + 1))
    done
    ! pid_is_alive "$pid"
}

stop_recorded_child() {
    local name="$1"
    local expected="$2"
    local signal_name="$3"
    local pid=""
    pid="$(read_pid_file "$LOCK_DIR/$name.pid" 2>/dev/null || true)"
    if [[ -n "$pid" ]] && pid_is_alive "$pid" && pid_command_contains "$pid" "$expected"; then
        kill -"$signal_name" -- "-$pid" 2>/dev/null || kill -"$signal_name" "$pid" 2>/dev/null || true
    fi
}

stop_recorded_operation() {
    local signal_name="$1"
    local pid=""
    local parent_pid=""
    local owner_pid=""
    pid="$(read_pid_file "$LOCK_DIR/active.pid" 2>/dev/null || true)"
    owner_pid="$(supervisor_pid 2>/dev/null || true)"
    [[ -n "$pid" && -n "$owner_pid" ]] || return 0
    parent_pid="$(ps -p "$pid" -o ppid= 2>/dev/null | tr -d '[:space:]' || true)"
    [[ "$parent_pid" == "$owner_pid" ]] || return 0
    kill -"$signal_name" -- "-$pid" 2>/dev/null || kill -"$signal_name" "$pid" 2>/dev/null || true
}

stop_service() {
    local timeout_seconds="${COGNITRADE_SHUTDOWN_TIMEOUT_SECONDS:-30}"
    local recorded_timeout=""
    recorded_timeout="$(read_pid_file "$LOCK_DIR/shutdown_timeout" 2>/dev/null || true)"
    [[ -z "$recorded_timeout" ]] || timeout_seconds="$recorded_timeout"
    validate_positive_integer "shutdown timeout" "$timeout_seconds"
    if ! service_is_running; then
        clear_stale_runtime_state || true
        clear_stale_project_lock || true
        log "service is not running"
        return 0
    fi

    local pid=""
    pid="$(supervisor_pid)"
    log "stopping supervisor pid=$pid"
    kill -TERM "$pid" 2>/dev/null || true
    if wait_for_pid_exit "$pid" "$((10#$timeout_seconds + 2))"; then
        log "service stopped"
        return 0
    fi

    warn "graceful shutdown exceeded ${timeout_seconds}s; terminating recorded children"
    stop_recorded_operation TERM
    stop_recorded_child frontend "$FRONTEND_DIR/node_modules/.bin/vite" TERM
    stop_recorded_child api "-m research_system.api" TERM
    sleep 1
    stop_recorded_operation KILL
    stop_recorded_child frontend "$FRONTEND_DIR/node_modules/.bin/vite" KILL
    stop_recorded_child api "-m research_system.api" KILL
    kill -KILL "$pid" 2>/dev/null || true
    wait_for_pid_exit "$pid" 2 || true
    clear_stale_runtime_state || true
    clear_stale_project_lock || true
    log "service force-stopped"
}

print_status() {
    if ! service_is_running; then
        clear_stale_runtime_state || true
        clear_stale_project_lock || true
        log "service is not running"
        return 1
    fi
    log "service is running (supervisor pid=$(supervisor_pid))"
    if [[ -r "$READY_FILE" ]]; then
        sed 's/^/[cognitrade] /' "$READY_FILE"
    else
        warn "supervisor is still starting"
    fi
}

show_logs() {
    local target="${1:-all}"
    local candidates=()
    case "$target" in
        api) candidates=("$API_LOG") ;;
        frontend) candidates=("$FRONTEND_LOG") ;;
        supervisor) candidates=("$SUPERVISOR_LOG") ;;
        all) candidates=("$SUPERVISOR_LOG" "$API_LOG" "$FRONTEND_LOG") ;;
        *) fail "logs target must be api, frontend, supervisor, or all" ;;
    esac
    local files=()
    local path=""
    for path in "${candidates[@]}"; do
        [[ -f "$path" ]] && files+=("$path")
    done
    (( ${#files[@]} > 0 )) || fail "no service logs found under $LOG_DIR"
    exec tail -n 100 -f "${files[@]}"
}

COMMAND="start"
if (( $# > 0 )); then
    case "$1" in
        start|check|stop|status|logs)
            COMMAND="$1"
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        --*) ;;
        *) fail "unknown command: $1" ;;
    esac
fi

if [[ "$COMMAND" == stop ]]; then
    (( $# == 0 )) || fail "stop accepts no options; use COGNITRADE_RUNTIME_DIR for a custom path"
    stop_service
    exit 0
fi
if [[ "$COMMAND" == status ]]; then
    (( $# == 0 )) || fail "status accepts no options; use COGNITRADE_RUNTIME_DIR for a custom path"
    print_status
    exit $?
fi
if [[ "$COMMAND" == logs ]]; then
    (( $# <= 1 )) || fail "logs accepts at most one target"
    show_logs "${1:-all}"
fi

START_BACKEND=1
START_FRONTEND=1
SCOPE="all"
FRONTEND_MODE="dev"
INSTALL_MODE="auto"
DETACH=0
CHECK_ONLY=0
ENABLE_RELOAD=0
LOCAL_UNAUTHENTICATED=0
REQUIRE_OPENAI=0
BACKEND_HOST_OVERRIDE=""
BACKEND_PORT_OVERRIDE=""
FRONTEND_HOST="${COGNITRADE_FRONTEND_HOST:-127.0.0.1}"
FRONTEND_PORT="${COGNITRADE_FRONTEND_PORT:-4173}"
READY_TIMEOUT="${COGNITRADE_READY_TIMEOUT_SECONDS:-45}"
STARTUP_TIMEOUT="${COGNITRADE_STARTUP_TIMEOUT_SECONDS:-300}"
SHUTDOWN_TIMEOUT="${COGNITRADE_SHUTDOWN_TIMEOUT_SECONDS:-30}"
[[ "$COMMAND" == check ]] && CHECK_ONLY=1

while (( $# > 0 )); do
    case "$1" in
        --backend-only)
            [[ "$SCOPE" == all || "$SCOPE" == backend ]] || fail "--backend-only and --frontend-only are mutually exclusive"
            SCOPE="backend"
            START_BACKEND=1
            START_FRONTEND=0
            shift
            ;;
        --frontend-only)
            [[ "$SCOPE" == all || "$SCOPE" == frontend ]] || fail "--backend-only and --frontend-only are mutually exclusive"
            SCOPE="frontend"
            START_BACKEND=0
            START_FRONTEND=1
            shift
            ;;
        --preview)
            FRONTEND_MODE="preview"
            shift
            ;;
        --install)
            if (( $# > 1 )) && [[ "$2" != --* ]]; then
                INSTALL_MODE="$2"
                shift 2
            else
                INSTALL_MODE="always"
                shift
            fi
            ;;
        --detach) DETACH=1; shift ;;
        --reload) ENABLE_RELOAD=1; shift ;;
        --local-unauthenticated) LOCAL_UNAUTHENTICATED=1; shift ;;
        --require-openai) REQUIRE_OPENAI=1; shift ;;
        --backend-host)
            require_value "$1" "${2:-}"
            BACKEND_HOST_OVERRIDE="$2"
            shift 2
            ;;
        --backend-port)
            require_value "$1" "${2:-}"
            BACKEND_PORT_OVERRIDE="$2"
            shift 2
            ;;
        --frontend-host)
            require_value "$1" "${2:-}"
            FRONTEND_HOST="$2"
            shift 2
            ;;
        --frontend-port)
            require_value "$1" "${2:-}"
            FRONTEND_PORT="$2"
            shift 2
            ;;
        --ready-timeout)
            require_value "$1" "${2:-}"
            READY_TIMEOUT="$2"
            shift 2
            ;;
        --startup-timeout)
            require_value "$1" "${2:-}"
            STARTUP_TIMEOUT="$2"
            shift 2
            ;;
        --shutdown-timeout)
            require_value "$1" "${2:-}"
            SHUTDOWN_TIMEOUT="$2"
            shift 2
            ;;
        --runtime-dir)
            require_value "$1" "${2:-}"
            RUNTIME_DIR="$2"
            refresh_runtime_paths
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *) fail "unknown option: $1" ;;
    esac
done

normalize_runtime_dir
if (( CHECK_ONLY && DETACH )); then
    fail "--detach cannot be combined with the check command"
fi

case "$INSTALL_MODE" in
    auto|always|never) ;;
    *) fail "--install must be auto, always, or never" ;;
esac
validate_port "frontend port" "$FRONTEND_PORT"
validate_positive_integer "startup timeout" "$STARTUP_TIMEOUT"
validate_positive_integer "ready timeout" "$READY_TIMEOUT"
validate_positive_integer "shutdown timeout" "$SHUTDOWN_TIMEOUT"

export COGNITRADE_SHUTDOWN_TIMEOUT_SECONDS="$SHUTDOWN_TIMEOUT"
export COGNITRADE_API_RELOAD=false
(( ENABLE_RELOAD )) && export COGNITRADE_API_RELOAD=true
(( LOCAL_UNAUTHENTICATED )) && export COGNITRADE_API_ALLOW_UNAUTHENTICATED=true
[[ -z "$BACKEND_HOST_OVERRIDE" ]] || export COGNITRADE_API_HOST="$BACKEND_HOST_OVERRIDE"
if [[ -n "$BACKEND_PORT_OVERRIDE" ]]; then
    validate_port "backend port" "$BACKEND_PORT_OVERRIDE"
    export COGNITRADE_API_PORT="$BACKEND_PORT_OVERRIDE"
fi

mkdir -p "$PROJECT_STATE_DIR" "$RUNTIME_DIR" "$LOG_DIR"
chmod 700 "$PROJECT_STATE_DIR" "$RUNTIME_DIR" "$LOG_DIR" 2>/dev/null || true

if (( DETACH )) && [[ "${COGNITRADE_INTERNAL_DETACH:-0}" != 1 ]]; then
    project_operation_is_running && fail "another Cognitrade service operation is already running"
    service_is_running && fail "service is already running"
    clear_stale_project_lock || true
    clear_stale_runtime_state || true
    rm -f "$READY_FILE"
    : > "$SUPERVISOR_LOG"
    log "starting detached supervisor; log=$SUPERVISOR_LOG"
    nohup env COGNITRADE_INTERNAL_DETACH=1 "$SCRIPT_PATH" "${RAW_ARGS[@]}" </dev/null >>"$SUPERVISOR_LOG" 2>&1 &
    detached_pid=$!
    ready_ticks=$((10#$STARTUP_TIMEOUT * 5))
    ready_index=0
    while [[ ! -f "$READY_FILE" ]] && pid_is_alive "$detached_pid" && (( ready_index < ready_ticks )); do
        sleep 0.2
        ready_index=$((ready_index + 1))
    done
    if [[ -f "$READY_FILE" ]] && pid_is_alive "$detached_pid"; then
        log "detached service is ready (supervisor pid=$detached_pid)"
        sed 's/^/[cognitrade] /' "$READY_FILE"
        exit 0
    fi
    warn "detached startup failed"
    tail -n 80 "$SUPERVISOR_LOG" >&2 || true
    if pid_is_alive "$detached_pid"; then
        warn "stopping the failed detached supervisor"
        kill -TERM "$detached_pid" 2>/dev/null || true
        if ! wait_for_pid_exit "$detached_pid" "$((10#$SHUTDOWN_TIMEOUT + 2))"; then
            stop_recorded_operation TERM
            stop_recorded_child frontend "$FRONTEND_DIR/node_modules/.bin/vite" TERM
            stop_recorded_child api "-m research_system.api" TERM
            sleep 1
            stop_recorded_operation KILL
            stop_recorded_child frontend "$FRONTEND_DIR/node_modules/.bin/vite" KILL
            stop_recorded_child api "-m research_system.api" KILL
            kill -KILL "$detached_pid" 2>/dev/null || true
        fi
    fi
    wait "$detached_pid" 2>/dev/null || true
    clear_stale_runtime_state || true
    clear_stale_project_lock || true
    exit 1
fi

PROJECT_LOCK_HELD=0
LOCK_HELD=0
BACKEND_PID=""
FRONTEND_PID=""
ACTIVE_PID=""
ACTIVE_LABEL=""
CLEANUP_STARTED=0
PROCESS_GROUP_PYTHON="${COGNITRADE_PYTHON_BIN:-${PYTHON:-python3}}"
command -v "$PROCESS_GROUP_PYTHON" >/dev/null 2>&1 || fail "python3 is required for process supervision"

wait_for_operation_exit() {
    local pid="$1"
    local timeout_seconds="$2"
    local ticks=$((10#$timeout_seconds * 5))
    local index=0
    while operation_is_alive "$pid" && (( index < ticks )); do
        sleep 0.2
        index=$((index + 1))
    done
    ! operation_is_alive "$pid"
}

signal_operation() {
    local pid="$1"
    local signal_name="$2"
    kill -"$signal_name" -- "-$pid" 2>/dev/null || kill -"$signal_name" "$pid" 2>/dev/null || true
}

spawn_new_session() {
    exec "$PROCESS_GROUP_PYTHON" - "$@" <<'PY'
import os
import sys

try:
    os.setsid()
except PermissionError:
    os.setpgid(0, 0)
os.execvp(sys.argv[1], sys.argv[1:])
PY
}

run_interruptible() {
    local label="$1"
    local log_path="$2"
    shift 2
    local operation_pid=""
    local status=0

    ACTIVE_LABEL="$label"
    if [[ -n "$log_path" ]]; then
        spawn_new_session "$@" > >(tee -a "$log_path") 2>&1 &
    else
        spawn_new_session "$@" &
    fi
    ACTIVE_PID=$!
    operation_pid="$ACTIVE_PID"
    printf '%s\n' "$operation_pid" > "$LOCK_DIR/active.pid"
    if wait "$operation_pid"; then
        status=0
    else
        status=$?
    fi
    ACTIVE_PID=""
    ACTIVE_LABEL=""
    rm -f "$LOCK_DIR/active.pid"

    if process_group_is_alive "$operation_pid"; then
        warn "$label left background processes; terminating its process group"
        signal_operation "$operation_pid" TERM
        wait_for_operation_exit "$operation_pid" "$SHUTDOWN_TIMEOUT" || signal_operation "$operation_pid" KILL
    fi
    return "$status"
}

terminate_active_operation() {
    local pid="$ACTIVE_PID"
    local label="$ACTIVE_LABEL"
    [[ -n "$pid" ]] && operation_is_alive "$pid" || return 0
    log "stopping ${label:-startup operation} pid=$pid"
    signal_operation "$pid" TERM
    if ! wait_for_operation_exit "$pid" "$SHUTDOWN_TIMEOUT"; then
        warn "${label:-startup operation} exceeded graceful shutdown; sending SIGKILL"
        signal_operation "$pid" KILL
        wait_for_operation_exit "$pid" 2 || true
    fi
    wait "$pid" 2>/dev/null || true
    ACTIVE_PID=""
    ACTIVE_LABEL=""
    rm -f "$LOCK_DIR/active.pid"
}

terminate_service_children() {
    local ticks=$((10#$SHUTDOWN_TIMEOUT * 5))
    local index=0
    if [[ -n "$FRONTEND_PID" ]] && operation_is_alive "$FRONTEND_PID"; then
        log "stopping frontend pid=$FRONTEND_PID"
        signal_operation "$FRONTEND_PID" TERM
    fi
    if [[ -n "$BACKEND_PID" ]] && operation_is_alive "$BACKEND_PID"; then
        log "stopping api pid=$BACKEND_PID"
        signal_operation "$BACKEND_PID" TERM
    fi
    while (( index < ticks )); do
        if { [[ -z "$FRONTEND_PID" ]] || ! operation_is_alive "$FRONTEND_PID"; } \
            && { [[ -z "$BACKEND_PID" ]] || ! operation_is_alive "$BACKEND_PID"; }; then
            break
        fi
        sleep 0.2
        index=$((index + 1))
    done
    if [[ -n "$FRONTEND_PID" ]] && operation_is_alive "$FRONTEND_PID"; then
        warn "frontend exceeded graceful shutdown; sending SIGKILL"
        signal_operation "$FRONTEND_PID" KILL
    fi
    if [[ -n "$BACKEND_PID" ]] && operation_is_alive "$BACKEND_PID"; then
        warn "api exceeded graceful shutdown; sending SIGKILL"
        signal_operation "$BACKEND_PID" KILL
    fi
    [[ -z "$FRONTEND_PID" ]] || wait "$FRONTEND_PID" 2>/dev/null || true
    [[ -z "$BACKEND_PID" ]] || wait "$BACKEND_PID" 2>/dev/null || true
    FRONTEND_PID=""
    BACKEND_PID=""
}

cleanup() {
    local status=$?
    (( CLEANUP_STARTED )) && return
    CLEANUP_STARTED=1
    trap - EXIT INT TERM HUP
    rm -f "$READY_FILE" "$READY_FILE.$$"
    terminate_active_operation
    terminate_service_children
    if (( LOCK_HELD )); then
        rm -f "$LOCK_DIR/frontend.pid" "$LOCK_DIR/api.pid" "$LOCK_DIR/active.pid" \
            "$LOCK_DIR/supervisor.pid" \
            "$LOCK_DIR/shutdown_timeout"
        rmdir "$LOCK_DIR" 2>/dev/null || true
    fi
    if (( PROJECT_LOCK_HELD )); then
        if [[ "$(project_operation_pid 2>/dev/null || true)" == "$$" ]]; then
            rm -f "$PROJECT_LOCK_FILE"
        fi
    fi
    if (( status == 0 && LOCK_HELD && ! CHECK_ONLY )); then
        log "service supervisor stopped"
    fi
    exit "$status"
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM HUP

project_operation_is_running && fail "another Cognitrade service operation is already running"
service_is_running && fail "service is already running"
clear_stale_project_lock || true
clear_stale_runtime_state || true
if ! (set -o noclobber; printf '%s\n%s\n' "$$" "$RUNTIME_DIR" > "$PROJECT_LOCK_FILE") 2>/dev/null; then
    fail "could not acquire project operation lock: $PROJECT_LOCK_FILE"
fi
PROJECT_LOCK_HELD=1
mkdir "$LOCK_DIR" 2>/dev/null || fail "could not acquire service state lock: $LOCK_DIR"
LOCK_HELD=1
printf '%s\n' "$$" > "$LOCK_DIR/supervisor.pid"
printf '%s\n' "$SHUTDOWN_TIMEOUT" > "$LOCK_DIR/shutdown_timeout"

PYTHON_BIN="${COGNITRADE_PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

ensure_backend_dependencies() {
    [[ -f "$REQUIREMENTS_FILE" ]] || fail "missing requirements.txt"
    local requirements_checksum=""
    local stamp_file="$ROOT_DIR/.venv/.requirements.cksum"
    requirements_checksum="$(file_checksum "$REQUIREMENTS_FILE")"

    if [[ -n "${COGNITRADE_PYTHON_BIN:-}" ]]; then
        [[ -x "$PYTHON_BIN" ]] || fail "COGNITRADE_PYTHON_BIN is not executable"
        [[ "$INSTALL_MODE" != always ]] || fail "--install always cannot modify COGNITRADE_PYTHON_BIN"
    else
        local base_python="${PYTHON:-python3}"
        command -v "$base_python" >/dev/null 2>&1 || fail "python3 is required"
        "$base_python" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' || fail "Python 3.11+ is required"
        local needs_install=0
        [[ -x "$PYTHON_BIN" ]] || needs_install=1
        if [[ "$needs_install" == 0 && "$INSTALL_MODE" != never ]]; then
            [[ -r "$stamp_file" && "$(sed -n '1p' "$stamp_file" 2>/dev/null || true)" == "$requirements_checksum" ]] || needs_install=1
        fi
        if [[ "$INSTALL_MODE" == always || ( "$INSTALL_MODE" == auto && "$needs_install" == 1 ) ]]; then
            if [[ ! -x "$PYTHON_BIN" ]]; then
                log "creating $ROOT_DIR/.venv"
                run_interruptible "Python virtual environment creation" "" \
                    "$base_python" -m venv "$ROOT_DIR/.venv"
            fi
            log "installing Python dependencies"
            run_interruptible "Python dependency installation" "" \
                "$PYTHON_BIN" -m pip install -r "$REQUIREMENTS_FILE"
            printf '%s\n' "$requirements_checksum" > "$stamp_file.tmp"
            mv "$stamp_file.tmp" "$stamp_file"
        elif [[ "$needs_install" == 1 ]]; then
            fail "Python dependencies are missing or stale; use --install auto"
        fi
    fi

    "$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' || fail "selected Python must be 3.11+"
    "$PYTHON_BIN" -m pip check >/dev/null || fail "Python dependency check failed"
    "$PYTHON_BIN" -c 'import fastapi, qdrant_client, uvicorn; import research_system.api.app' || fail "Python service imports failed"
}

ensure_frontend_dependencies() {
    command -v node >/dev/null 2>&1 || fail "Node.js is required"
    command -v npm >/dev/null 2>&1 || fail "npm is required"
    node -e '
const [major, minor] = process.versions.node.split(".").map(Number)
if (!((major === 20 && minor >= 19) || (major === 22 && minor >= 12) || major > 22)) process.exit(1)
' || fail "Vite requires Node 20.19+, Node 22.12+, or newer"
    [[ -f "$FRONTEND_DIR/package-lock.json" ]] || fail "frontend/package-lock.json is required"

    local checksum=""
    local stamp_file="$FRONTEND_DIR/node_modules/.package-lock.cksum"
    local needs_install=0
    checksum="$(file_checksum "$FRONTEND_DIR/package-lock.json")"
    [[ -x "$FRONTEND_DIR/node_modules/.bin/vite" ]] || needs_install=1
    if [[ "$needs_install" == 0 && "$INSTALL_MODE" != never ]]; then
        [[ -r "$stamp_file" && "$(sed -n '1p' "$stamp_file" 2>/dev/null || true)" == "$checksum" ]] || needs_install=1
    fi
    if [[ "$INSTALL_MODE" == always || ( "$INSTALL_MODE" == auto && "$needs_install" == 1 ) ]]; then
        log "installing frontend dependencies with npm ci"
        run_interruptible "frontend dependency installation" "" \
            npm --prefix "$FRONTEND_DIR" ci
        printf '%s\n' "$checksum" > "$stamp_file.tmp"
        mv "$stamp_file.tmp" "$stamp_file"
    elif [[ "$needs_install" == 1 ]]; then
        fail "frontend dependencies are missing or stale; use --install auto"
    fi
}

(( ! START_BACKEND )) || ensure_backend_dependencies
(( ! START_FRONTEND )) || ensure_frontend_dependencies

if (( START_BACKEND )); then
    frontend_origin=""
    (( ! START_FRONTEND )) || frontend_origin="http://$(url_host "$FRONTEND_HOST"):$FRONTEND_PORT"
    config_assignments=""
    if ! config_assignments="$("$PYTHON_BIN" - "$ROOT_DIR" "$frontend_origin" "$REQUIRE_OPENAI" <<'PY'
from __future__ import annotations

import os
import shlex
import sys
from dataclasses import replace
from pathlib import Path

from research_system.api.config import ApiConfig
from research_system.runtime.platform.settings import Settings, ensure_workspace_layout

def emit(name: str, value: object) -> None:
    print(f"{name}={shlex.quote(str(value))}")

try:
    root = Path(sys.argv[1]).resolve()
    frontend_origin = sys.argv[2].strip()
    require_openai = sys.argv[3] == "1"
    api = ApiConfig.from_env(root)
    origins = list(api.cors_origins)
    if frontend_origin and frontend_origin not in origins:
        origins.append(frontend_origin)
        api = replace(api, cors_origins=tuple(origins))
    api.validate()
    if api.log_level not in {"critical", "error", "warning", "info", "debug", "trace"}:
        raise ValueError("COGNITRADE_API_LOG_LEVEL is not supported by Uvicorn")
    settings = Settings.from_env(root)
    settings.validate()
    ensure_workspace_layout(settings)
    settings.load_yaml_configs()
    if not os.access(root, os.W_OK) or not os.access(settings.workspace_root, os.W_OK):
        raise PermissionError("repository root and workspace must be writable")
    if require_openai and not settings.openai_api_key:
        raise ValueError("OPENAI_API_KEY is required by --require-openai")
    emit("EFFECTIVE_API_HOST", api.host)
    emit("EFFECTIVE_API_PORT", api.port)
    emit("EFFECTIVE_API_CORS_ORIGINS", ",".join(origins))
    emit("EFFECTIVE_AUTH_MODE", "token" if api.authentication_enabled else "local-unauthenticated")
    emit("EFFECTIVE_OPENAI_CONFIGURED", "1" if settings.openai_api_key else "0")
    emit("EFFECTIVE_API_RELOAD", "1" if api.reload else "0")
except Exception as exc:
    print(f"Cognitrade configuration preflight failed: {exc}", file=sys.stderr)
    raise SystemExit(2)
PY
)"; then
        fail "backend configuration preflight failed"
    fi
    eval "$config_assignments"
    export COGNITRADE_API_HOST="$EFFECTIVE_API_HOST"
    export COGNITRADE_API_PORT="$EFFECTIVE_API_PORT"
    export COGNITRADE_API_CORS_ORIGINS="$EFFECTIVE_API_CORS_ORIGINS"
    validate_port "backend port" "$EFFECTIVE_API_PORT"
    [[ "$EFFECTIVE_OPENAI_CONFIGURED" == 1 ]] || warn "OPENAI_API_KEY is missing; research may use degraded fallback mode"
    [[ "$EFFECTIVE_AUTH_MODE" != local-unauthenticated ]] || warn "API authentication is disabled for explicit loopback development"
    [[ "$EFFECTIVE_API_RELOAD" != 1 ]] || warn "Uvicorn reload is enabled; do not use it for shared deployments"
    case "$EFFECTIVE_API_HOST" in
        127.0.0.1|localhost|::1) ;;
        *) warn "API binds to $EFFECTIVE_API_HOST; use TLS/reverse-proxy controls because docs are public" ;;
    esac
else
    EFFECTIVE_API_HOST=""
    EFFECTIVE_API_PORT=""
fi

PROBE_PYTHON="$PYTHON_BIN"
if (( ! START_BACKEND )); then
    PROBE_PYTHON="${COGNITRADE_PYTHON_BIN:-${PYTHON:-python3}}"
    command -v "$PROBE_PYTHON" >/dev/null 2>&1 || fail "python3 is required for port/readiness checks"
fi

assert_port_available() {
    local host="$1"
    local port="$2"
    local label="$3"
    "$PROBE_PYTHON" - "$host" "$port" "$label" <<'PY'
import errno
import socket
import sys

host, raw_port, label = sys.argv[1:]
port = int(raw_port)
last_error = None
bound_any = False
for family, socktype, proto, _, sockaddr in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM, flags=socket.AI_PASSIVE):
    sock = socket.socket(family, socktype, proto)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(sockaddr)
        bound_any = True
    except OSError as exc:
        last_error = exc
        if exc.errno == errno.EADDRINUSE:
            print(f"{label} port {port} is already in use on {host}", file=sys.stderr)
            raise SystemExit(1)
    finally:
        sock.close()
if bound_any:
    raise SystemExit(0)
print(f"cannot bind {label} to {host}:{port}: {last_error}", file=sys.stderr)
raise SystemExit(1)
PY
}

if (( START_BACKEND && START_FRONTEND )) && [[ "$EFFECTIVE_API_PORT" == "$FRONTEND_PORT" ]]; then
    fail "API and frontend ports must be different"
fi
(( ! START_BACKEND )) || assert_port_available "$EFFECTIVE_API_HOST" "$EFFECTIVE_API_PORT" API || fail "API port preflight failed"
(( ! START_FRONTEND )) || assert_port_available "$FRONTEND_HOST" "$FRONTEND_PORT" frontend || fail "frontend port preflight failed"

if (( CHECK_ONLY )); then
    log "preflight passed"
    (( ! START_BACKEND )) || log "API: http://$(url_host "$EFFECTIVE_API_HOST"):$EFFECTIVE_API_PORT"
    (( ! START_FRONTEND )) || log "frontend: http://$(url_host "$FRONTEND_HOST"):$FRONTEND_PORT"
    exit 0
fi

if [[ "$FRONTEND_MODE" == preview && "$START_FRONTEND" == 1 ]]; then
    warn "Vite preview is for local verification, not a production web server"
    : > "$FRONTEND_LOG"
    run_interruptible "frontend preview build" "$FRONTEND_LOG" \
        npm --prefix "$FRONTEND_DIR" run build
fi

wait_for_backend_ready() {
    local base_url="$1"
    local pid="$2"
    local timeout_seconds="$3"
    local probe_code=""
    probe_code="$(cat <<'PY'
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from research_system.api.config import ApiConfig

root = Path(sys.argv[1])
base_url = sys.argv[2].rstrip("/")
pid = int(sys.argv[3])
deadline = time.monotonic() + float(sys.argv[4])
config = ApiConfig.from_env(root)
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
last_error = "service did not answer"
while time.monotonic() < deadline:
    try:
        os.kill(pid, 0)
    except OSError:
        print("API process exited before readiness", file=sys.stderr)
        raise SystemExit(1)
    try:
        with opener.open(f"{base_url}/openapi.json", timeout=1.0) as response:
            if response.status != 200:
                raise RuntimeError(f"OpenAPI returned HTTP {response.status}")
        headers = {"Authorization": f"Bearer {config.api_token}"} if config.api_token else {}
        request = urllib.request.Request(f"{base_url}/api/v1/system/status", headers=headers)
        with opener.open(request, timeout=2.0) as response:
            payload = json.loads(response.read().decode("utf-8"))
            if response.status == 200 and isinstance(payload, dict):
                raise SystemExit(0)
    except (OSError, ValueError, RuntimeError, urllib.error.URLError) as exc:
        last_error = f"{exc.__class__.__name__}: {exc}"
    time.sleep(0.2)
print(f"API readiness timed out: {last_error}", file=sys.stderr)
raise SystemExit(1)
PY
)"
    run_interruptible "API readiness probe" "" \
        "$PYTHON_BIN" -c "$probe_code" "$ROOT_DIR" "$base_url" "$pid" "$timeout_seconds"
}

wait_for_frontend_ready() {
    local base_url="$1"
    local pid="$2"
    local timeout_seconds="$3"
    local probe_code=""
    probe_code="$(cat <<'PY'
import os
import sys
import time
import urllib.error
import urllib.request

base_url = sys.argv[1].rstrip("/")
pid = int(sys.argv[2])
deadline = time.monotonic() + float(sys.argv[3])
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
last_error = "service did not answer"
while time.monotonic() < deadline:
    try:
        os.kill(pid, 0)
    except OSError:
        print("frontend process exited before readiness", file=sys.stderr)
        raise SystemExit(1)
    try:
        with opener.open(f"{base_url}/", timeout=1.0) as response:
            if 200 <= response.status < 300:
                raise SystemExit(0)
    except (OSError, urllib.error.URLError) as exc:
        last_error = f"{exc.__class__.__name__}: {exc}"
    time.sleep(0.2)
print(f"frontend readiness timed out: {last_error}", file=sys.stderr)
raise SystemExit(1)
PY
)"
    run_interruptible "frontend readiness probe" "" \
        "$PROBE_PYTHON" -c "$probe_code" "$base_url" "$pid" "$timeout_seconds"
}

show_log_tail() {
    warn "$1 log tail ($2):"
    tail -n 60 "$2" >&2 || true
}

if (( START_BACKEND )); then
    : > "$API_LOG"
    log "starting API"
    (
        cd "$ROOT_DIR"
        spawn_new_session "$PYTHON_BIN" -m research_system.api
    ) > >(tee -a "$API_LOG") 2>&1 &
    BACKEND_PID=$!
    printf '%s\n' "$BACKEND_PID" > "$LOCK_DIR/api.pid"
    BACKEND_URL="http://$(url_host "$EFFECTIVE_API_HOST"):$EFFECTIVE_API_PORT"
    if ! wait_for_backend_ready "$BACKEND_URL" "$BACKEND_PID" "$READY_TIMEOUT"; then
        show_log_tail API "$API_LOG"
        fail "API failed readiness"
    fi
    log "API ready: $BACKEND_URL (docs: $BACKEND_URL/docs)"
fi

if (( START_FRONTEND )); then
    FRONTEND_API_BASE_URL="${VITE_COGNITRADE_API_BASE_URL:-}"
    if [[ -z "$FRONTEND_API_BASE_URL" ]]; then
        if (( START_BACKEND )); then
            FRONTEND_API_BASE_URL="$BACKEND_URL/api/v1"
        else
            FRONTEND_API_HOST="${COGNITRADE_API_HOST:-127.0.0.1}"
            FRONTEND_API_PORT="${COGNITRADE_API_PORT:-8000}"
            FRONTEND_API_BASE_URL="http://$(url_host "$FRONTEND_API_HOST"):$FRONTEND_API_PORT/api/v1"
        fi
    fi
    [[ "$FRONTEND_MODE" == preview ]] || : > "$FRONTEND_LOG"
    log "starting frontend ($FRONTEND_MODE)"
    (
        cd "$FRONTEND_DIR"
        export VITE_COGNITRADE_API_BASE_URL="$FRONTEND_API_BASE_URL"
        if [[ "$FRONTEND_MODE" == preview ]]; then
            spawn_new_session "$FRONTEND_DIR/node_modules/.bin/vite" preview \
                --host "$FRONTEND_HOST" --port "$FRONTEND_PORT" --strictPort
        fi
        spawn_new_session "$FRONTEND_DIR/node_modules/.bin/vite" \
            --host "$FRONTEND_HOST" --port "$FRONTEND_PORT" --strictPort
    ) > >(tee -a "$FRONTEND_LOG") 2>&1 &
    FRONTEND_PID=$!
    printf '%s\n' "$FRONTEND_PID" > "$LOCK_DIR/frontend.pid"
    FRONTEND_URL="http://$(url_host "$FRONTEND_HOST"):$FRONTEND_PORT"
    if ! wait_for_frontend_ready "$FRONTEND_URL" "$FRONTEND_PID" "$READY_TIMEOUT"; then
        show_log_tail frontend "$FRONTEND_LOG"
        fail "frontend failed readiness"
    fi
    log "frontend ready: $FRONTEND_URL"
    log "frontend API target: $FRONTEND_API_BASE_URL"
fi

ready_tmp="$READY_FILE.$$"
{
    printf 'scope=%s\nfrontend_mode=%s\nsupervisor_pid=%s\n' "$SCOPE" "$FRONTEND_MODE" "$$"
    [[ -z "$BACKEND_PID" ]] || printf 'api_pid=%s\napi_url=%s\n' "$BACKEND_PID" "$BACKEND_URL"
    [[ -z "$FRONTEND_PID" ]] || printf 'frontend_pid=%s\nfrontend_url=%s\n' "$FRONTEND_PID" "$FRONTEND_URL"
    printf 'logs=%s\n' "$LOG_DIR"
} > "$ready_tmp"
mv "$ready_tmp" "$READY_FILE"

log "all requested services are ready; press Ctrl+C to stop"
while true; do
    if [[ -n "$BACKEND_PID" ]] && ! pid_is_alive "$BACKEND_PID"; then
        status=0
        wait "$BACKEND_PID" || status=$?
        (( status != 0 )) || status=1
        warn "API exited unexpectedly (status=$status)"
        exit "$status"
    fi
    if [[ -n "$FRONTEND_PID" ]] && ! pid_is_alive "$FRONTEND_PID"; then
        status=0
        wait "$FRONTEND_PID" || status=$?
        (( status != 0 )) || status=1
        warn "frontend exited unexpectedly (status=$status)"
        exit "$status"
    fi
    sleep 1
done
