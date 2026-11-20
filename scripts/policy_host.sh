#!/bin/bash
# Start, stop or query the policy server (policy_server.py) on this machine.
# Copy of record; lives at ~/hebbot_tools/policy_host.sh on the Spark and
# ~/bin/policy_host.sh on omen. Environment: PY (the lerobot venv's python),
# CKPT (a LeRobot pretrained_model directory), PORT (8766), BIND (127.0.0.1;
# 0.0.0.0 to serve the LAN).
#
#   policy_host.sh start|stop|status
set -u
PY=${PY:-$HOME/venvs/lerobot/bin/python}
CKPT=${CKPT:-$HOME/policies/current/pretrained_model}
SERVER=${SERVER:-$(dirname "$0")/policy_server.py}
PORT=${PORT:-8766}
BIND=${BIND:-127.0.0.1}
LOG=/tmp/policy_server.log
pids() { pgrep -f "python.*policy_server.py" || true; }
case "${1:-status}" in
  start)
    if [ -n "$(pids)" ]; then echo "already running: $(pids)"; exit 0; fi
    setsid nohup "$PY" "$SERVER" --checkpoint "$CKPT" --port "$PORT" --bind "$BIND" > "$LOG" 2>&1 < /dev/null &
    for i in $(seq 1 90); do
      if curl -s -m 2 "localhost:$PORT/health" >/dev/null; then
        echo "up: $(curl -s -m 2 "localhost:$PORT/health" | cut -c1-120)"; exit 0
      fi
      sleep 2
    done
    echo "did not come up; log:"; tail -5 "$LOG"; exit 1 ;;
  stop)
    p=$(pids); if [ -n "$p" ]; then kill $p; echo "stopped $p"; else echo "not running"; fi ;;
  status)
    p=$(pids); if [ -n "$p" ]; then echo "running: $p"; curl -s -m 2 "localhost:$PORT/health"; echo; else echo "not running"; fi ;;
  *) echo "usage: $0 start|stop|status"; exit 2 ;;
esac
