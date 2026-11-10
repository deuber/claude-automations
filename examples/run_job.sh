#!/bin/bash
# Run one scheduled automation, log it, and email me (only) if it fails.
#   run_job.sh <name> <command> [args...]
# Logs: ~/automations/logs/<name>.log (appended; the last 2000 lines are kept).
NAME="$1"; shift
DIR="$HOME/automations"
LOG="$DIR/logs/$NAME.log"
ALERT_TO="you@example.com"
export PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin:$HOME/.local/bin"
mkdir -p "$DIR/logs"

# A subshell, so the exit below ends only the job block, not this script (the alert must still run).
(
  echo "===== $(date '+%Y-%m-%d %H:%M:%S %Z') start $NAME"
  "$@"
  status=$?
  echo "===== $(date '+%Y-%m-%d %H:%M:%S %Z') end $NAME (exit $status)"
  exit $status
) >> "$LOG" 2>&1 < /dev/null
status=$?

# Keep the log from growing forever.
tail -n 2000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"

if [ $status -ne 0 ]; then
  tail -n 40 "$LOG" | /usr/bin/python3 "$DIR/bin/send_email.py" --to "$ALERT_TO" \
    --subject "⚠️ Automation failed: $NAME ($(date '+%a %b %-d'))" --text - >> "$LOG" 2>&1
fi
exit $status
