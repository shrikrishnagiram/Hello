#!/data/data/com.termux/files/usr/bin/bash
cd "$HOME/Hello" || exit 1
if pgrep -f "python -m market run" >/dev/null 2>&1; then echo "Collector: RUNNING"; else echo "Collector: STOPPED (start with market-start)"; fi
python -m market status
echo; echo "Last log lines:"; tail -n 8 data/collector.log 2>/dev/null || echo "(no log yet)"
