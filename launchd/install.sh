#!/bin/bash
# Load both jobs into launchd so the pipeline runs without anyone starting it.
set -e
cd "$(dirname "$0")"
for plist in com.alex.clip-pipeline.*.plist; do
    label="${plist%.plist}"
    dest="$HOME/Library/LaunchAgents/$plist"
    cp "$plist" "$dest"
    launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$dest"
    echo "loaded $label"
done
echo
echo "Listener is polling Telegram; the runner fires at 08:30 and 18:30."
echo "Stop either with: launchctl bootout gui/$(id -u)/<label>"
