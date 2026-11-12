#!/data/data/com.termux/files/usr/bin/bash
# Start the all-day market collector in the background.
cd "$HOME/Hello" || { echo "Not installed. Run the install command first."; exit 1; }
mkdir -p data
if pgrep -f "python -m market run" >/dev/null 2>&1; then
  echo "Collector is already running. (market-status to check, market-stop to end)"
  exit 0
fi
if [ ! -d "${MARKET_DATA_DIR:-$HOME/market-data}/.git" ]; then
  echo "Not connected to your data repository yet. Run: market-setup"; exit 1
fi
termux-wake-lock 2>/dev/null   # keep Android from pausing it
nohup python -m market run >> data/collector.out 2>&1 &
sleep 2
if pgrep -f "python -m market run" >/dev/null 2>&1; then
  echo "Collector started. It works on trading days at set times and uploads automatically."
  echo "Keep Termux open in the background. Check anytime with: market-status"
else
  echo "Collector failed to start. Last lines:"; tail -n 20 data/collector.out
fi
