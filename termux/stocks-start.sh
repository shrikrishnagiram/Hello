#!/data/data/com.termux/files/usr/bin/bash
# Start the Swing stocks app (port 8060) and open it in the browser.
cd "$HOME/Hello" || { echo "Not installed. Run the install command first."; exit 1; }
mkdir -p data
URL="http://127.0.0.1:8060"
up() { python - <<'PY'
import socket, sys
s = socket.socket(); s.settimeout(0.5)
sys.exit(0 if s.connect_ex(("127.0.0.1", 8060)) == 0 else 1)
PY
}
if up; then
  echo "Swing is already running."
else
  pkill -f "python stocks_app.py" 2>/dev/null
  ARGS="--no-browser"; [ -n "$STOCKS_DEMO" ] && ARGS="$ARGS --demo"
  termux-wake-lock 2>/dev/null
  nohup python stocks_app.py $ARGS > data/stocks.log 2>&1 &
  for _ in 1 2 3 4 5 6 7 8 9 10 11 12; do up && break; sleep 0.5; done
  if ! up; then echo "Swing failed to start. Last lines:"; tail -n 20 data/stocks.log; exit 1; fi
  echo "Swing started${STOCKS_DEMO:+ with PRACTICE data}."
fi
termux-open-url "$URL" 2>/dev/null || echo "Open $URL in Chrome."
