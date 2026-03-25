#!/usr/bin/env bash
# AutoSkill service lifecycle manager.
# Usage: bash scripts/start-service.sh [start|stop|status]
set -euo pipefail

PLUGIN_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${AUTOSKILL_PORT:-8321}"
PID_FILE="$PLUGIN_ROOT/.service.pid"
LOG_FILE="$PLUGIN_ROOT/.service.log"
HEALTH_URL="http://localhost:$PORT/health"

# ── Locate Python ────────────────────────────────────────────────────────────
find_python() {
    for cmd in python3 python; do
        if command -v "$cmd" >/dev/null 2>&1; then
            echo "$cmd"
            return
        fi
    done
    for p in \
        "$LOCALAPPDATA/Programs/Python/Python313/python.exe" \
        "$LOCALAPPDATA/Programs/Python/Python312/python.exe" \
        "$LOCALAPPDATA/Programs/Python/Python311/python.exe" \
        "$LOCALAPPDATA/Programs/Python/Python310/python.exe" \
        "$LOCALAPPDATA/Programs/Python/Python39/python.exe"; do
        if [ -x "$p" ] 2>/dev/null; then
            echo "$p"
            return
        fi
    done
    echo "ERROR: Python not found. Install Python 3.9+ and ensure it is on PATH." >&2
    return 1
}

# ── Check running state ──────────────────────────────────────────────────────
is_running() {
    if [ -f "$PID_FILE" ]; then
        local pid
        pid=$(cat "$PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            if curl -sf "$HEALTH_URL" >/dev/null 2>&1; then
                return 0
            fi
        fi
        rm -f "$PID_FILE"
    fi
    return 1
}

# ── Commands ─────────────────────────────────────────────────────────────────
cmd_start() {
    if is_running; then
        echo "AutoSkill is already running (PID $(cat "$PID_FILE")) on port $PORT"
        return 0
    fi

    local py
    py=$(find_python) || exit 1

    # Ensure dependencies
    if ! "$py" -c "import fastapi" 2>/dev/null; then
        echo "Installing dependencies..."
        "$py" -m pip install -q -r "$PLUGIN_ROOT/requirements.txt"
    fi

    echo "Starting AutoSkill on port $PORT..."
    PYTHONPATH="$PLUGIN_ROOT/src" "$py" -m uvicorn skill_orchestrator.app:app \
        --host 127.0.0.1 --port "$PORT" \
        --log-level info \
        >"$LOG_FILE" 2>&1 &
    local pid=$!
    echo "$pid" > "$PID_FILE"

    # Wait for readiness
    for i in $(seq 1 10); do
        if curl -sf "$HEALTH_URL" >/dev/null 2>&1; then
            echo "AutoSkill running (PID $pid) on http://localhost:$PORT"
            return 0
        fi
        sleep 0.5
    done

    echo "ERROR: Service did not become ready within 5 seconds."
    echo "Check logs: $LOG_FILE"
    return 1
}

cmd_stop() {
    if [ -f "$PID_FILE" ]; then
        local pid
        pid=$(cat "$PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            echo "Stopping AutoSkill (PID $pid)..."
            kill "$pid" 2>/dev/null || true
            sleep 1
            kill -0 "$pid" 2>/dev/null && kill -9 "$pid" 2>/dev/null || true
        fi
        rm -f "$PID_FILE"
        echo "AutoSkill stopped."
    else
        echo "AutoSkill is not running."
    fi
}

cmd_status() {
    if is_running; then
        echo "AutoSkill is running (PID $(cat "$PID_FILE")) on port $PORT"
        curl -s "$HEALTH_URL" && echo
    else
        echo "AutoSkill is not running."
    fi
}

# ── Main ─────────────────────────────────────────────────────────────────────
case "${1:-start}" in
    start)  cmd_start ;;
    stop)   cmd_stop ;;
    status) cmd_status ;;
    *)
        echo "Usage: $0 [start|stop|status]"
        exit 1
        ;;
esac
