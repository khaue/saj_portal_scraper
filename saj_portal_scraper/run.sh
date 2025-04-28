#!/usr/bin/env bash
set -e

echo "[run.sh] Starting the Python script..."

# Check if TZ is already set by the Supervisor
if [ -z "$TZ" ]; then
  echo "[run.sh] TZ variable not set by Supervisor. Reading from add-on configuration..."
  # Read timezone from add-on configuration, use UTC as fallback
  CONFIG_PATH=/data/options.json
  USER_TZ=$(jq --raw-output '.timezone // "Etc/UTC"' $CONFIG_PATH)
  export TZ="$USER_TZ"
else
  echo "[run.sh] Using TZ variable set by Supervisor."
fi

echo "[run.sh] Final timezone set to: $TZ"
echo "[run.sh] Checking current system time:"
date

# Execute the main Python script located at /app/run.py
exec python3 /app/run.py

# --- DEBUG ---
# Uncomment the line below to enable debugging with debugpy
#echo "[run.sh] Starting with debugpy on port 5678. Waiting for debugger connection..."
#exec python3 -m debugpy --listen 0.0.0.0:5678 --wait-for-client /app/run.py
