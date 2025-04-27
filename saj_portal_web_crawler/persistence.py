# /workspaces/addons/saj_portal_web_crawler/persistence.py
import json
import logging
from datetime import date

from const import PERSISTENCE_FILE

_LOGGER = logging.getLogger(__name__)

def load_peak_power_state() -> tuple[float, date | None]:
    """Loads the peak power value and last reset date from the persistence file."""
    try:
        with open(PERSISTENCE_FILE, 'r') as f:
            state_data = json.load(f)
            peak = float(state_data.get("peak_power_today", 0.0))
            reset_date_str = state_data.get("last_reset_date")
            reset_date = date.fromisoformat(reset_date_str) if reset_date_str else None
            _LOGGER.info(f"Loaded peak power state: Peak={peak:.2f}, ResetDate={reset_date}")
            return peak, reset_date
    except FileNotFoundError:
        _LOGGER.info(f"Persistence file '{PERSISTENCE_FILE}' not found. Initializing peak power state.")
        return 0.0, None
    except (json.JSONDecodeError, ValueError, TypeError) as e:
        _LOGGER.warning(f"Error loading peak power state from {PERSISTENCE_FILE}: {e}. Re-initializing.")
        return 0.0, None

def save_peak_power_state(peak: float, reset_date: date | None):
    """Saves the peak power value and last reset date to the persistence file."""
    state_data = {
        "peak_power_today": peak,
        "last_reset_date": reset_date.isoformat() if reset_date else None
    }
    try:
        with open(PERSISTENCE_FILE, 'w') as f:
            json.dump(state_data, f, indent=2)
        _LOGGER.debug(f"Saved peak power state to {PERSISTENCE_FILE}: {state_data}")
    except IOError as e:
        _LOGGER.error(f"Error saving peak power state to {PERSISTENCE_FILE}: {e}")
