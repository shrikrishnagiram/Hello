#!/data/data/com.termux/files/usr/bin/bash
pkill -f "python -m market run" && echo "Collector stopped." || echo "Collector was not running."
pgrep -f "python app.py" >/dev/null 2>&1 || termux-wake-unlock 2>/dev/null
