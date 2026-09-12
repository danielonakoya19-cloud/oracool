#!/usr/bin/env bash
# OraCool AI launcher — starts the app server + a public HTTPS tunnel.
set -e
cd "$(dirname "$0")"

# ensure cloudflared binary is executable (bit can drop across restarts)
chmod +x bin/cloudflared 2>/dev/null || true

# start the app server on 0.0.0.0:8000
pkill -f "python3 server.py" 2>/dev/null || true
sleep 1
nohup python3 server.py > /tmp/oracool-server.log 2>&1 &

# start the public tunnel (prints a fresh trycloudflare.com URL)
exec ./bin/cloudflared tunnel --url http://127.0.0.1:8000 --no-autoupdate
