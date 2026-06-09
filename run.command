#!/bin/bash
# Sleep Tracker launcher — updates itself from GitHub, then starts the web app.
# Double-click this file, or run it from Terminal:  ~/sleeptracker/run.command
cd "$(dirname "$0")" || exit 1

echo "Updating Sleep Tracker from GitHub..."
git pull --ff-only || echo "(couldn't update — running the version you have)"

if [ ! -d .venv ]; then
  echo "First-time setup (creating environment)..."
  python3 -m venv .venv
fi
source .venv/bin/activate
pip install -q -r requirements.txt

echo ""
echo "Rested is starting at  http://127.0.0.1:8000"
LAN_IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null)
if [ -n "$LAN_IP" ]; then
  echo "On your phone (same WiFi):  http://$LAN_IP:8000"
  echo "  (open it in the phone browser, then 'Add to Home Screen' for the app feel)"
fi
echo "Leave this window open. Press Control-C here to stop it."
( sleep 2; open http://127.0.0.1:8000 ) >/dev/null 2>&1 &
exec uvicorn app:app --host 0.0.0.0 --port 8000
