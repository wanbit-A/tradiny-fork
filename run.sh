#!/bin/bash

echo "Starting Backend..."
(
  cd ~/tradiny-fork/backend || exit 1
  source venv/bin/activate || exit 1
  # Run populate first; if it succeeds, start the server
  python3 populate.py && python3 server.py
) &
PID1=$!

echo "Starting Frontend..."
(
  cd ~/tradiny-fork/frontend || exit 1
  npm start
) &
PID2=$!

# Trap Ctrl+C (INT) and kill (TERM) to stop both background processes cleanly
trap 'echo -e "\nStopping apps..."; kill $PID1 $PID2' INT TERM

# npm run build &&
# Keep the script running in the foreground so you can see the logs
# and interrupt it with Ctrl+C when you are done.
wait