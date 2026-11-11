#!/data/data/com.termux/files/usr/bin/bash
# Start the scanner server (if not already running) and open it in the browser.
APP_DIR="$HOME/Hello"
URL="http://127.0.0.1:8050"
cd "$APP_DIR" || { echo "Scanner not installed. Run the install command first."; exit 1; }
mkdir -p data

running() { python - <<'PY'
import socket, sys
s = socket.socket(); s.settimeout(0.5)
sys.exit(0 if s.connect_ex(("127.0.0.1", 8050)) == 0 else 1)
PY
}

if running; then
  echo "Scanner already running."
else
  pkill -f "python app.py" 2>/dev/null
  ARGS="--no-browser"
  [ -n "$SCANNER_DEMO" ] && ARGS="$ARGS --demo"
  termux-wake-lock 2>/dev/null   # keep Android from pausing it while you use the browser
  nohup python app.py $ARGS > data/server.log 2>&1 &
  for _ in 1 2 3 4 5 6 7 8 9 10; do running && break; sleep 0.5; done
  if ! running; then
    echo "Scanner failed to start. Last lines of data/server.log:"
    tail -n 20 data/server.log
    exit 1
  fi
  echo "Scanner started${SCANNER_DEMO:+ in DEMO mode}."
fi
termux-open-url "$URL" 2>/dev/null || echo "Open $URL in Chrome."
