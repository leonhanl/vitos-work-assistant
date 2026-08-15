#!/usr/bin/env bash

set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="${ROOT_DIR}/logs"
VENV_DIR="${ROOT_DIR}/.venv"

MCP_ENV="${ROOT_DIR}/vitos-m365-mcp/.env"
AGENT_ENV="${ROOT_DIR}/apps/agent/.env"
WEB_ENV="${ROOT_DIR}/apps/web/.env"

MCP_LOG="${LOG_DIR}/m365-mcp.log"
AGENT_LOG="${LOG_DIR}/agent.log"
WEB_LOG="${LOG_DIR}/web.log"

PIDS=""

fail() {
  printf '错误: %s\n' "$*" >&2
  exit 1
}

require_file() {
  [[ -f "$1" ]] || fail "缺少 $1，请先根据对应的 .env.example 创建并配置它。"
}

stop_services() {
  trap - EXIT

  if [[ -n "${PIDS}" ]]; then
    printf '\n正在停止本地服务...\n'
    # shellcheck disable=SC2086
    kill ${PIDS} 2>/dev/null || true
    # shellcheck disable=SC2086
    wait ${PIDS} 2>/dev/null || true
  fi
}

wait_for_url() {
  local name="$1"
  local url="$2"
  local pid="$3"
  local log_file="$4"
  local attempts=60
  local attempt=1

  while (( attempt <= attempts )); do
    if ! kill -0 "$pid" 2>/dev/null; then
      printf '\n%s 启动失败，最近的日志如下：\n' "$name" >&2
      tail -n 30 "$log_file" >&2 || true
      return 1
    fi

    if curl --silent --output /dev/null --max-time 1 "$url"; then
      printf '✓ %s 已就绪：%s\n' "$name" "$url"
      return 0
    fi

    sleep 1
    ((attempt += 1))
  done

  printf '\n等待 %s 就绪超时，最近的日志如下：\n' "$name" >&2
  tail -n 30 "$log_file" >&2 || true
  return 1
}

start_mcp() {
  (
    cd "$ROOT_DIR"
    set -a
    # shellcheck disable=SC1090
    source "$MCP_ENV"
    set +a
    export PYTHONUNBUFFERED=1
    exec "$VENV_DIR/bin/python" -m m365_mcp.server
  ) >>"$MCP_LOG" 2>&1 &
  local pid=$!
  PIDS="${PIDS} ${pid}"
  printf '正在启动 m365-mcp（PID %s）...\n' "$pid"
  wait_for_url "m365-mcp" "http://127.0.0.1:8001/health" "$pid" "$MCP_LOG"
  printf 'm365-mcp 已响应，额外等待 2 秒确保启动完成...\n'
  sleep 2
}

start_agent() {
  (
    cd "$ROOT_DIR"
    set -a
    # shellcheck disable=SC1090
    source "$AGENT_ENV"
    set +a
    export PYTHONUNBUFFERED=1
    exec "$VENV_DIR/bin/python" -m uvicorn work_assistant.app:app \
      --reload --host 127.0.0.1 --port 8000
  ) >>"$AGENT_LOG" 2>&1 &
  local pid=$!
  PIDS="${PIDS} ${pid}"
  printf '正在启动 Agent（PID %s）...\n' "$pid"
  wait_for_url "Agent" "http://127.0.0.1:8000/health" "$pid" "$AGENT_LOG"
}

start_web() {
  (
    cd "$ROOT_DIR/apps/web"
    exec npm run dev -- --host localhost
  ) >>"$WEB_LOG" 2>&1 &
  local pid=$!
  PIDS="${PIDS} ${pid}"
  printf '正在启动 Web（PID %s）...\n' "$pid"
  wait_for_url "Web" "http://localhost:5173" "$pid" "$WEB_LOG"
}

require_file "$MCP_ENV"
require_file "$AGENT_ENV"
require_file "$WEB_ENV"
[[ -x "$VENV_DIR/bin/python" ]] || fail "未找到 ${VENV_DIR}/bin/python，请先创建并安装 Python 虚拟环境。"
command -v npm >/dev/null 2>&1 || fail "未找到 npm，请先安装 Node.js/npm。"
command -v curl >/dev/null 2>&1 || fail "未找到 curl。"
[[ -d "$ROOT_DIR/apps/web/node_modules" ]] || fail "前端依赖尚未安装，请先在 apps/web 下运行 npm install。"

mkdir -p "$LOG_DIR"
find "$LOG_DIR" -maxdepth 1 -type f -name '*.log' -delete
touch "$MCP_LOG" "$AGENT_LOG" "$WEB_LOG"
printf '旧日志已清除：%s\n' "$LOG_DIR"

trap stop_services EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

start_mcp
start_agent
start_web

printf '\n本地手工测试环境已启动。\n'
printf 'Web:   http://localhost:5173\n'
printf 'Agent: http://127.0.0.1:8000\n'
printf 'MCP:   http://127.0.0.1:8001/mcp\n'
printf '日志:  %s\n' "$LOG_DIR"
printf '按 Ctrl+C 停止全部服务。\n\n'

# 保持脚本在前台，并在任一子进程退出时停止整个环境。
while true; do
  for pid in $PIDS; do
    if ! kill -0 "$pid" 2>/dev/null; then
      wait "$pid" || true
      fail "服务进程 ${pid} 已退出，请检查 ${LOG_DIR} 下的日志。"
    fi
  done
  sleep 1
done
