#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
ROOT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"

COMMAND="deploy"
ENV_FILE="${COGNITRADE_DEPLOY_ENV_FILE:-$ROOT_DIR/.env}"
COMPOSE_FILE="${COGNITRADE_DEPLOY_COMPOSE_FILE:-$ROOT_DIR/compose.yaml}"
WEB_HOST_OVERRIDE=""
WEB_PORT_OVERRIDE=""
READY_TIMEOUT="${COGNITRADE_DEPLOY_TIMEOUT_SECONDS:-300}"
ASSUME_YES=0
SKIP_SYSTEM_INSTALL=0
NO_BUILD=0
PULL_BASE_IMAGES=0
DRY_RUN=0
REQUIRE_OPENAI=0
BUILD_RETRIES="${COGNITRADE_DEPLOY_BUILD_RETRIES:-2}"
LOG_TARGET="all"
PROVIDER_SMOKE_SYMBOL="${COGNITRADE_PROVIDER_SMOKE_SYMBOL:-600519}"
PROVIDER_SMOKE_SECTOR="${COGNITRADE_PROVIDER_SMOKE_SECTOR:-}"
PROVIDER_SMOKE_DIMENSIONS="${COGNITRADE_PROVIDER_SMOKE_DIMENSIONS:-}"
DOCKER_BIN="${COGNITRADE_DOCKER_BIN:-docker}"
DOCKER_COMMAND=()

usage() {
    cat <<'EOF'
Usage:
  ./scripts/deploy.sh [deploy] [options]       Install/check, build, and start API + frontend web
  ./scripts/deploy.sh install [options]        Install/check Docker and initialize .env
  ./scripts/deploy.sh init [options]           Initialize secure deployment configuration
  ./scripts/deploy.sh check [options]          Validate Docker, configuration, and Compose
  ./scripts/deploy.sh build [options]          Build API and frontend web images
  ./scripts/deploy.sh migrate [options]        Explicitly migrate an existing workspace volume once
  ./scripts/deploy.sh start [options]          Start API + frontend web from existing images
  ./scripts/deploy.sh restart [options]        Recreate API + frontend web containers
  ./scripts/deploy.sh stop [options]           Stop API + frontend web without removing them
  ./scripts/deploy.sh down [options]           Remove containers/network; keep workspace data
  ./scripts/deploy.sh status [options]         Show container and health status
  ./scripts/deploy.sh runtime-check [options]  Verify all API, browser, paper, and backtest dependencies
  ./scripts/deploy.sh provider-smoke [options] Run explicit UZI real-provider checks
  ./scripts/deploy.sh logs [api|web|all]        Follow deployment logs
  ./scripts/deploy.sh token [options]          Print the generated browser Bearer token

Options:
  --yes                         Approve supported system package installation
  --skip-system-install         Fail instead of installing Docker when it is absent
  --no-build                    Do not rebuild images during deploy
  --pull                        Refresh base images while building
  --web-host HOST               Published web bind address; default: 127.0.0.1
  --web-port PORT               Published web port; default: 8080
  --timeout SECONDS             Readiness timeout; default: 300
  --env-file PATH               Deployment environment file; default: .env
  --compose-file PATH           Compose file; default: compose.yaml
  --require-openai              Fail when OPENAI_API_KEY is empty
  --dry-run                     Print mutating commands without running them
  -h, --help                    Show this help

Supported automatic Docker installation:
  - macOS with Homebrew (Docker Desktop)
  - Debian/Ubuntu with apt (official Docker repository)
  - Fedora/CentOS/RHEL with dnf (official Docker repository)

Examples:
  ./scripts/deploy.sh --yes                    # Build and start API + frontend web
  ./scripts/deploy.sh start                    # Start both from existing images
  ./scripts/deploy.sh deploy --pull --web-host 0.0.0.0 --web-port 8080
  ./scripts/deploy.sh token
  COGNITRADE_PROVIDER_SMOKE_SECTOR=白酒 ./scripts/deploy.sh provider-smoke
  ./scripts/deploy.sh logs api
  ./scripts/deploy.sh down
EOF
}

log() {
    printf '[cognitrade-deploy] %s\n' "$*"
}

warn() {
    printf '[cognitrade-deploy] warning: %s\n' "$*" >&2
}

fail() {
    printf '[cognitrade-deploy] error: %s\n' "$*" >&2
    exit 1
}

render_command() {
    local rendered=""
    local argument=""
    for argument in "$@"; do
        printf -v argument '%q' "$argument"
        rendered+=" $argument"
    done
    printf '%s' "${rendered# }"
}

run_command() {
    log "+ $(render_command "$@")"
    (( DRY_RUN )) || "$@"
}

absolute_path() {
    local value="$1"
    if [[ "$value" == /* ]]; then
        printf '%s\n' "$value"
    else
        printf '%s/%s\n' "$ROOT_DIR" "${value#./}"
    fi
}

validate_positive_integer() {
    local label="$1"
    local value="$2"
    [[ "$value" =~ ^[0-9]+$ && "$value" -gt 0 ]] || fail "$label must be a positive integer"
}

validate_port() {
    local value="$1"
    [[ "$value" =~ ^[0-9]+$ && "$value" -ge 1 && "$value" -le 65535 ]] \
        || fail "web port must be between 1 and 65535"
}

if (( $# )) && [[ "$1" != -* ]]; then
    COMMAND="$1"
    shift
fi

if [[ "$COMMAND" == "logs" ]] && (( $# )) && [[ "$1" != -* ]]; then
    LOG_TARGET="$1"
    shift
fi

while (( $# )); do
    case "$1" in
        --yes)
            ASSUME_YES=1
            shift
            ;;
        --skip-system-install)
            SKIP_SYSTEM_INSTALL=1
            shift
            ;;
        --no-build)
            NO_BUILD=1
            shift
            ;;
        --pull)
            PULL_BASE_IMAGES=1
            shift
            ;;
        --web-host)
            (( $# >= 2 )) || fail "--web-host requires a value"
            WEB_HOST_OVERRIDE="$2"
            shift 2
            ;;
        --web-port)
            (( $# >= 2 )) || fail "--web-port requires a value"
            WEB_PORT_OVERRIDE="$2"
            validate_port "$WEB_PORT_OVERRIDE"
            shift 2
            ;;
        --timeout)
            (( $# >= 2 )) || fail "--timeout requires a value"
            READY_TIMEOUT="$2"
            validate_positive_integer "timeout" "$READY_TIMEOUT"
            shift 2
            ;;
        --env-file)
            (( $# >= 2 )) || fail "--env-file requires a value"
            ENV_FILE="$2"
            shift 2
            ;;
        --compose-file)
            (( $# >= 2 )) || fail "--compose-file requires a value"
            COMPOSE_FILE="$2"
            shift 2
            ;;
        --require-openai)
            REQUIRE_OPENAI=1
            shift
            ;;
        --dry-run)
            DRY_RUN=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            fail "unknown option: $1"
            ;;
    esac
done

case "$COMMAND" in
    deploy|install|init|check|build|migrate|start|restart|stop|down|status|runtime-check|provider-smoke|logs|token) ;;
    *) fail "unknown command: $COMMAND" ;;
esac

case "$LOG_TARGET" in
    api|web|all) ;;
    *) fail "logs target must be api, web, or all" ;;
esac

ENV_FILE="$(absolute_path "$ENV_FILE")"
COMPOSE_FILE="$(absolute_path "$COMPOSE_FILE")"
validate_positive_integer "timeout" "$READY_TIMEOUT"
validate_positive_integer "build retries" "$BUILD_RETRIES"
[[ -f "$ROOT_DIR/.env.example" ]] || fail "missing $ROOT_DIR/.env.example"
[[ -f "$COMPOSE_FILE" ]] || fail "missing Compose file: $COMPOSE_FILE"

confirm() {
    local prompt="$1"
    if (( ASSUME_YES )); then
        return 0
    fi
    [[ -t 0 ]] || fail "$prompt; rerun with --yes to approve"
    local answer=""
    read -r -p "$prompt [y/N] " answer
    [[ "$answer" =~ ^[Yy]$ ]] || fail "installation cancelled"
}

sudo_prefix() {
    if (( EUID == 0 )); then
        return 0
    fi
    command -v sudo >/dev/null 2>&1 || fail "sudo is required for system package installation"
    printf '%s\n' sudo
}

install_docker_macos() {
    command -v brew >/dev/null 2>&1 || fail "Homebrew is required on macOS: https://brew.sh"
    confirm "Install Docker Desktop with Homebrew"
    run_command brew install --cask docker
    run_command open -a Docker
}

install_docker_debian() {
    local sudo=()
    local sudo_bin=""
    sudo_bin="$(sudo_prefix)"
    [[ -z "$sudo_bin" ]] || sudo=("$sudo_bin")
    # shellcheck disable=SC1091
    source /etc/os-release
    local distro="${ID:-}"
    local codename="${VERSION_CODENAME:-}"
    [[ "$distro" == "debian" || "$distro" == "ubuntu" ]] || fail "unsupported apt distribution: $distro"
    [[ -n "$codename" ]] || fail "VERSION_CODENAME is missing from /etc/os-release"
    confirm "Install Docker Engine from Docker's official $distro repository"

    run_command "${sudo[@]}" apt-get update
    run_command "${sudo[@]}" apt-get install -y ca-certificates curl
    run_command "${sudo[@]}" install -m 0755 -d /etc/apt/keyrings
    if (( DRY_RUN )); then
        log "+ curl -fsSL https://download.docker.com/linux/$distro/gpg -o /etc/apt/keyrings/docker.asc"
    else
        curl -fsSL "https://download.docker.com/linux/$distro/gpg" \
            | "${sudo[@]}" tee /etc/apt/keyrings/docker.asc >/dev/null
    fi
    run_command "${sudo[@]}" chmod a+r /etc/apt/keyrings/docker.asc

    local architecture=""
    architecture="$(dpkg --print-architecture)"
    local repository="deb [arch=$architecture signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/$distro $codename stable"
    if (( DRY_RUN )); then
        log "+ write /etc/apt/sources.list.d/docker.list"
    else
        printf '%s\n' "$repository" | "${sudo[@]}" tee /etc/apt/sources.list.d/docker.list >/dev/null
    fi
    run_command "${sudo[@]}" apt-get update
    run_command "${sudo[@]}" apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    run_command "${sudo[@]}" systemctl enable --now docker
}

install_docker_rpm() {
    local sudo=()
    local sudo_bin=""
    sudo_bin="$(sudo_prefix)"
    [[ -z "$sudo_bin" ]] || sudo=("$sudo_bin")
    # shellcheck disable=SC1091
    source /etc/os-release
    local distro="${ID:-}"
    local repository_distro="$distro"
    [[ "$distro" != "rhel" ]] || repository_distro="centos"
    confirm "Install Docker Engine from Docker's official $repository_distro repository"

    run_command "${sudo[@]}" dnf -y install dnf-plugins-core curl ca-certificates
    run_command "${sudo[@]}" dnf config-manager --add-repo "https://download.docker.com/linux/$repository_distro/docker-ce.repo"
    run_command "${sudo[@]}" dnf -y install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    run_command "${sudo[@]}" systemctl enable --now docker
}

install_docker() {
    case "$(uname -s)" in
        Darwin)
            install_docker_macos
            ;;
        Linux)
            [[ -r /etc/os-release ]] || fail "cannot identify Linux distribution"
            # shellcheck disable=SC1091
            source /etc/os-release
            case "${ID:-}" in
                debian|ubuntu) install_docker_debian ;;
                fedora|centos|rhel) install_docker_rpm ;;
                *) fail "automatic Docker installation is not supported for ${ID:-this Linux distribution}" ;;
            esac
            ;;
        *)
            fail "automatic Docker installation is not supported on $(uname -s)"
            ;;
    esac
}

docker_works() {
    local candidate=("$DOCKER_BIN")
    if "${candidate[@]}" info >/dev/null 2>&1 \
        && "${candidate[@]}" compose version >/dev/null 2>&1; then
        DOCKER_COMMAND=("${candidate[@]}")
        return 0
    fi
    if (( EUID != 0 )) && command -v sudo >/dev/null 2>&1 \
        && sudo -n "$DOCKER_BIN" info >/dev/null 2>&1 \
        && sudo -n "$DOCKER_BIN" compose version >/dev/null 2>&1; then
        DOCKER_COMMAND=(sudo "$DOCKER_BIN")
        return 0
    fi
    return 1
}

wait_for_docker() {
    local deadline=$((SECONDS + 180))
    while (( SECONDS < deadline )); do
        docker_works && return 0
        sleep 2
    done
    return 1
}

start_docker_daemon() {
    case "$(uname -s)" in
        Darwin)
            command -v open >/dev/null 2>&1 && run_command open -a Docker
            ;;
        Linux)
            local sudo=()
            local sudo_bin=""
            sudo_bin="$(sudo_prefix)"
            [[ -z "$sudo_bin" ]] || sudo=("$sudo_bin")
            command -v systemctl >/dev/null 2>&1 && run_command "${sudo[@]}" systemctl start docker
            ;;
    esac
}

ensure_docker() {
    local allow_install="$1"
    if (( DRY_RUN )); then
        DOCKER_COMMAND=("$DOCKER_BIN")
        command -v "$DOCKER_BIN" >/dev/null 2>&1 || {
            (( allow_install )) && (( ! SKIP_SYSTEM_INSTALL )) \
                && log "would install Docker for $(uname -s)" \
                || fail "Docker is required"
        }
        return 0
    fi

    if ! command -v "$DOCKER_BIN" >/dev/null 2>&1; then
        (( allow_install )) && (( ! SKIP_SYSTEM_INSTALL )) || fail "Docker is required; run deploy.sh install or remove --skip-system-install"
        install_docker
    fi
    if ! docker_works; then
        start_docker_daemon
        wait_for_docker || fail "Docker daemon or Compose plugin is unavailable"
    fi
}

read_env_value() {
    local key="$1"
    local value=""
    [[ -f "$ENV_FILE" ]] || return 0
    value="$(awk -v key="$key" 'index($0, key "=") == 1 {sub(/^[^=]*=/, ""); print; exit}' "$ENV_FILE")"
    value="${value%$'\r'}"
    if [[ ${#value} -ge 2 ]]; then
        if [[ "${value:0:1}" == '"' && "${value: -1}" == '"' ]] \
            || [[ "${value:0:1}" == "'" && "${value: -1}" == "'" ]]; then
            value="${value:1:${#value}-2}"
        fi
    fi
    printf '%s' "$value"
}

set_env_value() {
    local key="$1"
    local value="$2"
    local temporary="$ENV_FILE.tmp.$$"
    awk -v key="$key" -v value="$value" '
        BEGIN { found = 0 }
        index($0, key "=") == 1 { print key "=" value; found = 1; next }
        { print }
        END { if (!found) print key "=" value }
    ' "$ENV_FILE" > "$temporary"
    chmod 600 "$temporary"
    mv "$temporary" "$ENV_FILE"
}

generate_token() {
    if command -v openssl >/dev/null 2>&1; then
        openssl rand -hex 32
        return
    fi
    [[ -r /dev/urandom ]] || fail "openssl or /dev/urandom is required to generate an API token"
    od -An -N32 -tx1 /dev/urandom | tr -d ' \n'
}

initialize_environment() {
    if (( DRY_RUN )); then
        [[ -f "$ENV_FILE" ]] || log "would create secure environment file: $ENV_FILE"
        [[ -z "$WEB_HOST_OVERRIDE" ]] || log "would set COGNITRADE_WEB_HOST=$WEB_HOST_OVERRIDE"
        [[ -z "$WEB_PORT_OVERRIDE" ]] || log "would set COGNITRADE_WEB_PORT=$WEB_PORT_OVERRIDE"
        return 0
    fi

    mkdir -p "$(dirname "$ENV_FILE")"
    if [[ ! -f "$ENV_FILE" ]]; then
        cp "$ROOT_DIR/.env.example" "$ENV_FILE"
        log "created $ENV_FILE from .env.example"
    fi
    chmod 600 "$ENV_FILE"

    # These exact values were Cognitrade's former defaults. Public ECR is
    # frequently unreachable on otherwise healthy networks, so migrate only
    # untouched legacy defaults; custom operator image locations are preserved.
    if [[ "$(read_env_value COGNITRADE_PYTHON_IMAGE)" == "public.ecr.aws/docker/library/python:3.11-slim-bookworm" ]]; then
        set_env_value COGNITRADE_PYTHON_IMAGE "python:3.11-slim-bookworm"
        log "updated the legacy Python base-image default to Docker Hub"
    fi
    if [[ "$(read_env_value COGNITRADE_NODE_IMAGE)" == "public.ecr.aws/docker/library/node:22-alpine" ]]; then
        set_env_value COGNITRADE_NODE_IMAGE "node:22-alpine"
        log "updated the legacy Node base-image default to Docker Hub"
    fi
    if [[ "$(read_env_value COGNITRADE_NGINX_IMAGE)" == "public.ecr.aws/docker/library/nginx:alpine" ]]; then
        set_env_value COGNITRADE_NGINX_IMAGE "nginx:alpine"
        log "updated the legacy Nginx base-image default to Docker Hub"
    fi

    local token=""
    token="$(read_env_value COGNITRADE_API_TOKEN)"
    if [[ -z "$token" ]]; then
        token="$(generate_token)"
        set_env_value COGNITRADE_API_TOKEN "$token"
        log "generated a 256-bit API token"
    fi
    set_env_value COGNITRADE_API_ALLOW_UNAUTHENTICATED false
    local workspace_volume=""
    workspace_volume="$(read_env_value COGNITRADE_WORKSPACE_VOLUME)"
    set_env_value COGNITRADE_WORKSPACE_VOLUME "${workspace_volume:-cognitrade_workspace}"

    local web_host="${WEB_HOST_OVERRIDE:-$(read_env_value COGNITRADE_WEB_HOST)}"
    local web_port="${WEB_PORT_OVERRIDE:-$(read_env_value COGNITRADE_WEB_PORT)}"
    web_host="${web_host:-127.0.0.1}"
    web_port="${web_port:-8080}"
    [[ -n "$web_host" && "$web_host" != *[[:space:]]* ]] || fail "web host is invalid"
    validate_port "$web_port"
    set_env_value COGNITRADE_WEB_HOST "$web_host"
    set_env_value COGNITRADE_WEB_PORT "$web_port"

    if [[ "$web_host" != "127.0.0.1" && "$web_host" != "localhost" && "$web_host" != "::1" ]]; then
        warn "web binds to $web_host; put TLS and firewall controls in front of port $web_port"
    fi
}

validate_environment() {
    [[ -f "$ENV_FILE" ]] || fail "missing $ENV_FILE; run deploy.sh init"
    local token=""
    local web_host=""
    local web_port=""
    token="$(read_env_value COGNITRADE_API_TOKEN)"
    web_host="$(read_env_value COGNITRADE_WEB_HOST)"
    web_port="$(read_env_value COGNITRADE_WEB_PORT)"
    [[ ${#token} -ge 32 && "$token" != *[[:space:]]* ]] || fail "COGNITRADE_API_TOKEN must contain at least 32 non-whitespace characters"
    [[ -n "$web_host" ]] || fail "COGNITRADE_WEB_HOST is required"
    validate_port "$web_port"

    local openai_key=""
    openai_key="$(read_env_value OPENAI_API_KEY)"
    if [[ -z "$openai_key" ]]; then
        (( ! REQUIRE_OPENAI )) || fail "OPENAI_API_KEY is required by --require-openai"
        warn "OPENAI_API_KEY is empty; research will use supported degraded behavior"
    fi
    [[ -d "$ROOT_DIR/workspace/config" ]] \
        || fail "workspace config templates are required"
}

compose() {
    export COGNITRADE_ENV_FILE="$ENV_FILE"
    "${DOCKER_COMMAND[@]}" compose \
        --project-directory "$ROOT_DIR" \
        --env-file "$ENV_FILE" \
        -f "$COMPOSE_FILE" \
        "$@"
}

run_compose() {
    log "+ $(render_command "${DOCKER_COMMAND[@]}" compose --project-directory "$ROOT_DIR" --env-file "$ENV_FILE" -f "$COMPOSE_FILE" "$@")"
    (( DRY_RUN )) || compose "$@"
}

check_compose_configuration() {
    if (( DRY_RUN )); then
        run_compose config
        return 0
    fi
    compose config >/dev/null
    log "Compose configuration is valid"
}

build_images() {
    local arguments=(build)
    local attempt=1
    (( ! PULL_BASE_IMAGES )) || arguments+=(--pull)
    if (( DRY_RUN )); then
        run_compose "${arguments[@]}"
        return 0
    fi
    while (( attempt <= BUILD_RETRIES )); do
        log "building deployment images (attempt $attempt/$BUILD_RETRIES)"
        if compose "${arguments[@]}"; then
            return 0
        fi
        if (( attempt == BUILD_RETRIES )); then
            break
        fi
        warn "image build failed; retrying after a transient backoff"
        sleep $((attempt * 5))
        ((attempt += 1))
    done
    fail "deployment image build failed after $BUILD_RETRIES attempts"
}

probe_host() {
    local host="$1"
    case "$host" in
        0.0.0.0|::|'[::]') printf '127.0.0.1' ;;
        *) printf '%s' "$host" ;;
    esac
}

web_url() {
    local host=""
    local port=""
    host="$(probe_host "$(read_env_value COGNITRADE_WEB_HOST)")"
    port="$(read_env_value COGNITRADE_WEB_PORT)"
    if [[ "$host" == *:* && "$host" != \[*\] ]]; then
        host="[$host]"
    fi
    printf 'http://%s:%s' "$host" "$port"
}

wait_for_ready() {
    command -v curl >/dev/null 2>&1 || fail "curl is required for deployment readiness checks"
    local base_url=""
    local token=""
    local deadline=$((SECONDS + READY_TIMEOUT))
    base_url="$(web_url)"
    token="$(read_env_value COGNITRADE_API_TOKEN)"
    log "waiting for frontend web and API readiness at $base_url"
    while (( SECONDS < deadline )); do
        if curl --noproxy '*' -fsS --max-time 3 "$base_url/" >/dev/null 2>&1 \
            && curl --noproxy '*' -fsS --max-time 5 \
                -H "Authorization: Bearer $token" \
                "$base_url/api/v1/system/status" >/dev/null 2>&1; then
            log "frontend web and API ready: $base_url"
            log "API docs: $base_url/docs"
            log "browser token: ./scripts/deploy.sh token"
            return 0
        fi
        sleep 1
    done
    compose ps >&2 || true
    compose logs --tail=100 api web >&2 || true
    fail "deployment readiness timed out after ${READY_TIMEOUT}s"
}

check_runtime_dependencies() {
    log "verifying API, Playwright Chromium, VeighNa PaperAccount, and Hikyuu runtime"
    run_compose exec -T api \
        python -m research_system.runtime.diagnostics.status.deployment_preflight \
        --workspace /app/workspace
}

perform_check() {
    ensure_docker 0
    validate_environment
    check_compose_configuration
    log "Docker: $("${DOCKER_COMMAND[@]}" version --format '{{.Server.Version}}')"
    log "Compose: $("${DOCKER_COMMAND[@]}" compose version --short)"
    log "deployment preflight passed"
}

case "$COMMAND" in
    init)
        initialize_environment
        ;;
    install)
        ensure_docker 1
        initialize_environment
        (( DRY_RUN )) || validate_environment
        check_compose_configuration
        log "deployment environment installed"
        ;;
    check)
        perform_check
        ;;
    build)
        ensure_docker "$((1 - SKIP_SYSTEM_INSTALL))"
        initialize_environment
        validate_environment
        check_compose_configuration
        build_images
        ;;
    migrate)
        ensure_docker 0
        validate_environment
        check_compose_configuration
        log "migrating the workspace volume to canonical Run filenames"
        run_compose run --rm --no-deps api \
            python -m scripts.migrate_workspace --workspace /app/workspace
        ;;
    start)
        ensure_docker "$((1 - SKIP_SYSTEM_INSTALL))"
        validate_environment
        check_compose_configuration
        log "starting API and frontend web containers"
        run_compose up -d --no-build --remove-orphans
        (( DRY_RUN )) || wait_for_ready
        check_runtime_dependencies
        ;;
    restart)
        ensure_docker "$((1 - SKIP_SYSTEM_INSTALL))"
        validate_environment
        check_compose_configuration
        log "recreating API and frontend web containers"
        run_compose up -d --no-build --force-recreate --remove-orphans
        (( DRY_RUN )) || wait_for_ready
        check_runtime_dependencies
        ;;
    stop)
        ensure_docker 0
        validate_environment
        log "stopping API and frontend web containers"
        run_compose stop
        ;;
    down)
        ensure_docker 0
        validate_environment
        run_compose down --remove-orphans
        log "containers removed; workspace data retained in Docker volume $(read_env_value COGNITRADE_WORKSPACE_VOLUME)"
        ;;
    status)
        ensure_docker 0
        validate_environment
        compose ps
        ;;
    runtime-check)
        ensure_docker 0
        validate_environment
        check_compose_configuration
        check_runtime_dependencies
        ;;
    provider-smoke)
        ensure_docker 0
        validate_environment
        check_compose_configuration
        smoke_arguments=(
            run --rm --no-deps api
            python -m research_system.runtime.diagnostics.status.uzi_provider_smoke
            --symbol "$PROVIDER_SMOKE_SYMBOL"
        )
        if [[ -n "$PROVIDER_SMOKE_SECTOR" ]]; then
            smoke_arguments+=(--sector "$PROVIDER_SMOKE_SECTOR")
        fi
        if [[ -n "$PROVIDER_SMOKE_DIMENSIONS" ]]; then
            smoke_arguments+=(--dimensions "$PROVIDER_SMOKE_DIMENSIONS")
        fi
        run_compose "${smoke_arguments[@]}"
        ;;
    logs)
        ensure_docker 0
        validate_environment
        if [[ "$LOG_TARGET" == "all" ]]; then
            compose logs -f --tail=200
        else
            compose logs -f --tail=200 "$LOG_TARGET"
        fi
        ;;
    token)
        validate_environment
        read_env_value COGNITRADE_API_TOKEN
        printf '\n'
        ;;
    deploy)
        ensure_docker "$((1 - SKIP_SYSTEM_INSTALL))"
        initialize_environment
        if (( ! DRY_RUN )); then
            validate_environment
            check_compose_configuration
        else
            check_compose_configuration
        fi
        if (( ! NO_BUILD )); then
            build_images
        fi
        log "starting API and frontend web containers"
        run_compose up -d --remove-orphans
        (( DRY_RUN )) || wait_for_ready
        check_runtime_dependencies
        ;;
esac
