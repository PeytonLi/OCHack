#!/usr/bin/env bash
# Local Redis lifecycle manager backed by a workspace build.
# Usage: bash scripts/start-redis.sh [start|stop|status|logs]
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
REDIS_ROOT="$ROOT_DIR/.redis"
BIN_DIR="$REDIS_ROOT/bin"
DATA_DIR="$REDIS_ROOT/data"
LOG_FILE="$REDIS_ROOT/redis.log"
PID_FILE="$REDIS_ROOT/redis.pid"
CONF_FILE="$REDIS_ROOT/redis.conf"
HOST="${REDIS_HOST:-127.0.0.1}"
PORT="${REDIS_PORT:-6379}"

SERVER_BIN="$BIN_DIR/redis-server"
CLI_BIN="$BIN_DIR/redis-cli"

find_cli() {
    if [ -x "$CLI_BIN" ]; then
        echo "$CLI_BIN"
        return 0
    fi
    if command -v redis-cli >/dev/null 2>&1; then
        command -v redis-cli
        return 0
    fi
    echo "ERROR: redis-cli not found. Run bash scripts/setup-redis.sh first." >&2
    return 1
}

ensure_server() {
    if [ -x "$SERVER_BIN" ]; then
        return 0
    fi
    echo "ERROR: Redis server is not installed in this repo yet." >&2
    echo "Run: bash scripts/setup-redis.sh" >&2
    return 1
}

is_running() {
    if [ -f "$PID_FILE" ]; then
        local pid
        pid="$(cat "$PID_FILE")"
        if kill -0 "$pid" >/dev/null 2>&1; then
            return 0
        fi
        rm -f "$PID_FILE"
    fi
    return 1
}

write_config() {
    mkdir -p "$DATA_DIR"
    cat >"$CONF_FILE" <<EOF
bind $HOST
port $PORT
dir $DATA_DIR
dbfilename dump.rdb
appendonly yes
appendfilename appendonly.aof
pidfile $PID_FILE
logfile $LOG_FILE
daemonize yes
protected-mode yes
EOF
}

wait_for_ping() {
    local cli
    cli="$(find_cli)"
    for _ in $(seq 1 20); do
        if "$cli" -h "$HOST" -p "$PORT" ping >/dev/null 2>&1; then
            return 0
        fi
        sleep 0.5
    done
    return 1
}

cmd_start() {
    ensure_server

    if is_running; then
        echo "Redis is already running on $HOST:$PORT (PID $(cat "$PID_FILE"))"
        return 0
    fi

    write_config
    "$SERVER_BIN" "$CONF_FILE"

    if wait_for_ping; then
        echo "Redis is ready at redis://localhost:$PORT"
        return 0
    fi

    echo "ERROR: Redis did not become ready in time." >&2
    echo "Check logs with: bash scripts/start-redis.sh logs" >&2
    return 1
}

cmd_stop() {
    local cli
    cli="$(find_cli)"

    if ! is_running; then
        echo "Redis is not running."
        return 0
    fi

    "$cli" -h "$HOST" -p "$PORT" shutdown nosave >/dev/null 2>&1 || true
    for _ in $(seq 1 10); do
        if ! is_running; then
            echo "Redis stopped."
            return 0
        fi
        sleep 0.5
    done

    local pid
    pid="$(cat "$PID_FILE")"
    kill "$pid" >/dev/null 2>&1 || true
    rm -f "$PID_FILE"
    echo "Redis stopped."
}

cmd_status() {
    local cli
    cli="$(find_cli)"

    if ! is_running; then
        echo "Redis is not running."
        return 1
    fi

    echo "Redis is running on $HOST:$PORT (PID $(cat "$PID_FILE"))"
    if "$cli" -h "$HOST" -p "$PORT" ping >/dev/null 2>&1; then
        echo "PING ok"
        return 0
    fi

    echo "PING failed"
    return 1
}

cmd_logs() {
    if [ ! -f "$LOG_FILE" ]; then
        echo "Redis log file does not exist yet."
        return 1
    fi
    cat "$LOG_FILE"
}

case "${1:-start}" in
    start)  cmd_start ;;
    stop)   cmd_stop ;;
    status) cmd_status ;;
    logs)   cmd_logs ;;
    *)
        echo "Usage: $0 [start|stop|status|logs]" >&2
        exit 1
        ;;
esac
