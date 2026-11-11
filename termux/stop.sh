#!/data/data/com.termux/files/usr/bin/bash
pkill -f "python app.py" && echo "Scanner stopped." || echo "Scanner was not running."
termux-wake-unlock 2>/dev/null
