#!/usr/bin/env bash
# ============================================================
# OraCool AI — always-on supervisor
# Keeps the server AND the public tunnel alive: if either
# process crashes, it is restarted automatically within seconds.
#
#   ./keepalive.sh
#
# Logs:  /tmp/oracool-server.log   (app output)
#        /tmp/oracool-tunnel.log   (public URL lives here)
#        /tmp/oracool-supervisor.log
# ============================================================
cd "$(dirname "$0")"
chmod +x bin/cloudflared 2>/dev/null || true

LOG=/tmp/oracool-supervisor.log
echo "[supervisor] boot $(date '+%F %T')" >> "$LOG"

run_server(){
  while true; do
    echo "[supervisor] starting server $(date '+%F %T')" >> "$LOG"
    python3 server.py >> /tmp/oracool-server.log 2>&1
    echo "[supervisor] server exited — restarting in 2s $(date '+%F %T')" >> "$LOG"
    sleep 2
  done
}

run_tunnel(){
  while true; do
    echo "[supervisor] starting tunnel $(date '+%F %T')" >> "$LOG"
    ./bin/cloudflared tunnel --url http://127.0.0.1:8000 --no-autoupdate >> /tmp/oracool-tunnel.log 2>&1
    echo "[supervisor] tunnel exited — restarting in 3s $(date '+%F %T')" >> "$LOG"
    sleep 3
  done
}

run_server &
echo "[supervisor] server pid $!" >> "$LOG"
run_tunnel &
echo "[supervisor] tunnel pid $!" >> "$LOG"
wait
