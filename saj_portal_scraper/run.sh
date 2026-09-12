#!/usr/bin/env bash
set -e

echo "[run.sh] Starting the Python script..."

# Read timezone from add-on configuration, use UTC as fallback
CONFIG_PATH=/data/options.json

# Check if TZ is already set by the Supervisor
if [ -z "$TZ" ]; then
  echo "[run.sh] TZ variable not set by Supervisor. Reading from add-on configuration..."
  USER_TZ=$(jq --raw-output '.timezone // "Etc/UTC"' $CONFIG_PATH)
  export TZ="$USER_TZ"
else
  echo "[run.sh] Using TZ variable set by Supervisor."
fi

echo "[run.sh] Final timezone set to: $TZ"
echo "[run.sh] Checking current system time:"
date

############################ OLD CODE ############################
# Execute the main Python script located at /app/run.py
#exec python3 /app/run.py

# --- DEBUG ---
# Uncomment the line below to enable debugging with debugpy
#echo "[run.sh] Starting with debugpy on port 5678. Waiting for debugger connection..."
#exec python3 -m debugpy --listen 0.0.0.0:5678 --wait-for-client /app/run.py
############################ OLD CODE ############################
echo "[run.sh] SUPERVISOR_TOKEN: $SUPERVISOR_TOKEN"
echo "[run.sh] SUPERVISOR_TOKEN (raw): ${SUPERVISOR_TOKEN}"

DEBUG_MODE=$(jq --raw-output '.debug_mode // false' $CONFIG_PATH)


if [ "$DEBUG_MODE" = "true" ]; then
  echo "[run.sh] Debug mode enabled. Starting with debugpy on port 5678. Waiting for debugger connection..."
  exec python3 -m debugpy --listen 0.0.0.0:5678 --wait-for-client /app/run.py
else
  echo "[run.sh] Starting..."
  exec python3 /app/run.py
fi