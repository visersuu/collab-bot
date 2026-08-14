#!/bin/bash
cd "$(dirname "$0")"
LOG="bot.log"
echo "$(date) | Watchdog started" | tee -a "$LOG"
while true; do
    echo "$(date) | Starting bot..." | tee -a "$LOG"
    python3 -u bot.py >> "$LOG" 2>&1
    echo "$(date) | Bot stopped. Restart in 5 sec..." | tee -a "$LOG"
    sleep 5
done
