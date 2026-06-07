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
echo "Sleep Tracker is starting at  http://127.0.0.1:8000"
echo "Leave this window open. Press Control-C here to stop it."
( sleep 2; open http://127.0.0.1:8000 ) >/dev/null 2>&1 &
exec uvicorn app:app
