#!/usr/bin/env bash
# Install, start and stop the ORDERSYS web app from a virtualenv in .venv/.
#
#   ./start.sh -install   create or reuse .venv and install target/requirements.txt
#   ./start.sh -start     run the app in the background on http://127.0.0.1:8000
#   ./start.sh -stop      stop the instance that -start launched
#
# Works from any directory. Runtime files live in the ignored .venv/:
#   .venv/ordersys-server.pid   record of the running instance (pid, start time, ...)
#   .venv/ordersys-server.log   server output of the latest start
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
TARGET_DIR="$ROOT/target"
REQUIREMENTS="$TARGET_DIR/requirements.txt"
VENV="$ROOT/.venv"
VENV_PY="$VENV/bin/python"
PID_FILE="$VENV/ordersys-server.pid"
LOG_FILE="$VENV/ordersys-server.log"
HOST=127.0.0.1
PORT=8000
URL="http://$HOST:$PORT"
START_TIMEOUT=30
STOP_TIMEOUT=10
# Everything after the interpreter path; also how -stop recognises its own server.
SERVER_ARGS=(-m uvicorn app.main:app --app-dir "$TARGET_DIR" --host "$HOST" --port "$PORT")

say() { printf 'start.sh: %s\n' "$*"; }
err() { printf 'start.sh: ERROR: %s\n' "$*" >&2; }

usage() {
  cat >&2 <<EOF
Usage: $0 -install | -start | -stop
  -install  Create or reuse $VENV and install target/requirements.txt into it.
  -start    Start the web app in the background at $URL/customers
            (log: $LOG_FILE).
  -stop     Stop the app instance started by -start.
EOF
}

python_ok() {
  "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null
}

deps_ok() {
  "$VENV_PY" -c 'import fastapi, jinja2, python_multipart, uvicorn' 2>/dev/null
}

do_install() {
  if [ -x "$VENV_PY" ]; then
    say "Reusing virtualenv $VENV"
  else
    local base_py="${PYTHON:-python3}"
    if ! command -v "$base_py" >/dev/null 2>&1; then
      err "$base_py not found. Install Python 3.11 or newer, or set PYTHON=/path/to/python3."
      return 1
    fi
    if ! python_ok "$base_py"; then
      err "$base_py is $("$base_py" --version 2>&1); Python 3.11 or newer is required (set PYTHON=...)."
      return 1
    fi
    say "Creating virtualenv $VENV with $("$base_py" --version 2>&1)"
    if ! "$base_py" -m venv "$VENV"; then
      err "Could not create $VENV."
      return 1
    fi
  fi
  if ! python_ok "$VENV_PY"; then
    err "$VENV uses $("$VENV_PY" --version 2>&1); Python 3.11+ is required. Delete $VENV and run -install again."
    return 1
  fi
  say "Installing $REQUIREMENTS into $VENV"
  if ! "$VENV_PY" -m pip install --disable-pip-version-check -r "$REQUIREMENTS"; then
    err "pip install failed (see the output above). Nothing was started."
    return 1
  fi
  if ! deps_ok; then
    err "Packages were installed but cannot be imported from $VENV_PY."
    return 1
  fi
  say "Install complete. Start the app with: $ROOT/start.sh -start"
}

record_field() {
  sed -n "s/^$1=//p" "$PID_FILE" 2>/dev/null | head -n 1
}

proc_start_time() {
  ps -p "$1" -o lstart= 2>/dev/null | sed 's/^ *//; s/ *$//'
}

proc_alive() {
  local stat
  stat=$(ps -p "$1" -o stat= 2>/dev/null | tr -d ' ')
  [ -n "$stat" ] && [ "${stat#Z}" = "$stat" ]
}

# Classifies the instance record and sets REC_PID:
#   0 = the recorded process is alive and is the server this script started
#   1 = no record
#   2 = stale: invalid record or the recorded process has exited
#   3 = the PID is alive but belongs to another process (PID reuse)
# A PID alone never proves ownership: PIDs are reused, so the start time saved
# at launch and this checkout's server command line must match as well.
check_record() {
  REC_PID=""
  [ -f "$PID_FILE" ] || return 1
  REC_PID=$(record_field pid)
  case "$REC_PID" in
    '' | *[!0-9]*) REC_PID="?"; return 2 ;;
  esac
  proc_alive "$REC_PID" || return 2
  local started
  started=$(record_field started)
  [ -n "$started" ] && [ "$(proc_start_time "$REC_PID")" = "$started" ] || return 3
  ps -ww -p "$REC_PID" -o command= 2>/dev/null | grep -qF -- "${SERVER_ARGS[*]}" || return 3
  return 0
}

health_ok() {
  curl -sf --max-time 2 "$URL/health" 2>/dev/null | grep -q '"status":"ok"'
}

# Anything that accepts a connection on the address counts, HTTP or not.
port_in_use() {
  curl -s -o /dev/null --max-time 2 "$URL/" 2>/dev/null
  [ $? -ne 7 ]
}

port_owner() {
  command -v lsof >/dev/null 2>&1 || return 0
  lsof -nP -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null | sed -n '2,$p'
}

listening_pid_is() {
  command -v lsof >/dev/null 2>&1 || return 0
  lsof -nP -a -p "$1" -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1
}

# Only called for a PID this script spawned or one check_record accepted.
terminate() {
  local pid=$1 waited=0
  kill -TERM "$pid" 2>/dev/null
  while proc_alive "$pid" && [ "$waited" -lt $((STOP_TIMEOUT * 4)) ]; do
    sleep 0.25
    waited=$((waited + 1))
  done
  if proc_alive "$pid"; then
    kill -KILL "$pid" 2>/dev/null
    sleep 0.5
  fi
  ! proc_alive "$pid"
}

show_log_tail() {
  if [ -s "$LOG_FILE" ]; then
    printf '%s\n' "--- last lines of $LOG_FILE ---" >&2
    tail -n 20 "$LOG_FILE" >&2
    printf '%s\n' "---" >&2
  fi
}

do_start() {
  if [ ! -x "$VENV_PY" ]; then
    err "No virtualenv at $VENV. Run: $ROOT/start.sh -install"
    return 1
  fi
  if ! deps_ok; then
    err "Required packages are missing from $VENV. Run: $ROOT/start.sh -install"
    return 1
  fi
  if ! command -v curl >/dev/null 2>&1; then
    err "curl is required to check that the server is ready."
    return 1
  fi

  check_record
  case $? in
    0)
      if health_ok; then
        say "Already running (PID $REC_PID) at $URL/customers. Not starting another instance."
        return 0
      fi
      err "Instance PID $REC_PID is running but $URL/health is not responding. See $LOG_FILE, or run $ROOT/start.sh -stop and start again."
      return 1
      ;;
    2)
      say "Removing stale record $PID_FILE (PID $REC_PID is not running)."
      rm -f "$PID_FILE"
      ;;
    3)
      say "Removing stale record $PID_FILE (PID $REC_PID is now another process; it was not touched)."
      rm -f "$PID_FILE"
      ;;
  esac

  if port_in_use; then
    err "$HOST:$PORT is already in use by another process, so the app was not started."
    port_owner >&2
    err "Stop that process or free port $PORT, then run $ROOT/start.sh -start again."
    return 1
  fi

  : >"$LOG_FILE"
  # set -m gives the server its own process group, so signals aimed at the
  # invoking shell's job (for example Ctrl-C) do not reach it after we return.
  set -m
  nohup "$VENV_PY" "${SERVER_ARGS[@]}" </dev/null >>"$LOG_FILE" 2>&1 &
  local pid=$!
  set +m
  disown "$pid"
  {
    printf 'pid=%s\n' "$pid"
    printf 'started=%s\n' "$(proc_start_time "$pid")"
    printf 'url=%s\n' "$URL"
    printf 'command=%s %s\n' "$VENV_PY" "${SERVER_ARGS[*]}"
    printf 'log=%s\n' "$LOG_FILE"
  } >"$PID_FILE"

  local waited=0
  while [ "$waited" -lt $((START_TIMEOUT * 4)) ]; do
    if ! proc_alive "$pid"; then
      rm -f "$PID_FILE"
      err "The server exited during startup. Diagnostics are in $LOG_FILE."
      show_log_tail
      return 1
    fi
    if health_ok && listening_pid_is "$pid"; then
      say "Started (PID $pid). Open $URL/customers"
      say "Log: $LOG_FILE   Record: $PID_FILE   Stop with: $ROOT/start.sh -stop"
      return 0
    fi
    sleep 0.25
    waited=$((waited + 1))
  done

  err "The server did not become healthy at $URL/health within ${START_TIMEOUT}s; stopping it."
  terminate "$pid"
  rm -f "$PID_FILE"
  err "Diagnostics are in $LOG_FILE."
  show_log_tail
  return 1
}

do_stop() {
  check_record
  case $? in
    1)
      say "Not running: no instance record at $PID_FILE. Nothing was stopped."
      ;;
    2)
      say "Not running: the recorded PID $REC_PID is not running. Removed stale record $PID_FILE."
      rm -f "$PID_FILE"
      ;;
    3)
      say "Not stopping PID $REC_PID: it is no longer the server started by this script. Removed stale record $PID_FILE."
      rm -f "$PID_FILE"
      ;;
    0)
      say "Stopping PID $REC_PID..."
      if ! terminate "$REC_PID"; then
        err "PID $REC_PID did not exit. The record $PID_FILE was kept."
        return 1
      fi
      rm -f "$PID_FILE"
      say "Stopped."
      return 0
      ;;
  esac
  if command -v curl >/dev/null 2>&1 && port_in_use; then
    say "Note: $HOST:$PORT is in use by a process this script did not start; it was left running."
  fi
  return 0
}

if [ $# -ne 1 ]; then
  usage
  exit 2
fi

case "$1" in
  -install) do_install ;;
  -start) do_start ;;
  -stop) do_stop ;;
  -h | --help) usage; exit 0 ;;
  *) err "Unknown option: $1"; usage; exit 2 ;;
esac
