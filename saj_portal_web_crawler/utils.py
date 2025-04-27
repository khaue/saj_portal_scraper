# /workspaces/addons/saj_portal_web_crawler/utils.py
import logging
from datetime import datetime, time, date

from const import (
    CONF_INACTIVITY_ENABLED,
    CONF_INACTIVITY_START_TIME,
    CONF_INACTIVITY_END_TIME,
    DEFAULT_INACTIVITY_ENABLED,
    DEFAULT_INACTIVITY_START_TIME,
    DEFAULT_INACTIVITY_END_TIME,
)

_LOGGER = logging.getLogger(__name__)


def is_inactive(config: dict) -> bool:
    """Check if the current time falls within the configured inactivity period."""
    inactivity_enabled = config.get(CONF_INACTIVITY_ENABLED, DEFAULT_INACTIVITY_ENABLED)
    if not inactivity_enabled:
        return False

    start_time_str = config.get(CONF_INACTIVITY_START_TIME, DEFAULT_INACTIVITY_START_TIME)
    end_time_str = config.get(CONF_INACTIVITY_END_TIME, DEFAULT_INACTIVITY_END_TIME)

    try:
        now_local = datetime.now()
        current_time = now_local.time()
        start_time = datetime.strptime(start_time_str, "%H:%M").time()
        end_time = datetime.strptime(end_time_str, "%H:%M").time()

        _LOGGER.debug(
            "Checking inactivity: Current local time %s, Start %s, End %s",
            current_time.strftime("%H:%M:%S"),
            start_time.strftime("%H:%M"),
            end_time.strftime("%H:%M"),
        )

        # Handle overnight inactivity period (e.g., 21:00 to 05:30)
        if start_time > end_time:
            if current_time >= start_time or current_time < end_time:
                _LOGGER.debug("Current time is within overnight inactivity period.")
                return True
        # Handle daytime inactivity period (e.g., 10:00 to 14:00)
        else:
            if start_time <= current_time < end_time:
                _LOGGER.debug("Current time is within daytime inactivity period.")
                return True

    except ValueError:
        _LOGGER.warning(
            "Invalid time format '%s' or '%s' in configuration. Skipping inactivity check.",
            start_time_str,
            end_time_str,
        )
        return False

    _LOGGER.debug("Current time is outside inactivity period.")
    return False


def aggregate_plant_data(fetched_data: dict | None) -> dict:
    """Aggregate data from all devices into a single plant summary."""
    if not fetched_data:
        _LOGGER.warning("Aggregator: No fetched data provided for aggregation.")
        return {}

    plant_sum_power = 0.0
    plant_sum_energy_today = 0.0
    plant_sum_energy_month = 0.0
    plant_sum_energy_year = 0.0
    plant_sum_energy_total = 0.0
    plant_sum_panel_power = 0.0
    latest_update_time_str: str | None = None
    latest_server_time_str: str | None = None
    compare_update_time_obj: datetime | None = None
    compare_server_time_obj: datetime | None = None

    for device_sn, latest_row_data in fetched_data.items():
        if not latest_row_data or not isinstance(latest_row_data, dict):
            _LOGGER.debug("Aggregator: No data or invalid format for device %s, skipping.", device_sn)
            continue

        device_alias = latest_row_data.get("Alias", device_sn)

        try:
            for attribute, value_str in latest_row_data.items():
                is_summable_numeric = False
                try:
                    is_summable_numeric = attribute in [
                        "Power", "Energy_Today", "Energy_This_Month",
                        "Energy_This_Year", "Energy_Total"
                    ] or attribute.endswith("_Panel_Power") # Also sum individual panel powers

                    if is_summable_numeric and value_str is not None:
                        # Attempt conversion to float, handling commas
                        value_float = float(str(value_str).replace(',', '.'))

                        if attribute == "Power":
                            plant_sum_power += value_float
                        elif attribute == "Energy_Today":
                            plant_sum_energy_today += value_float
                        elif attribute == "Energy_This_Month":
                            plant_sum_energy_month += value_float
                        elif attribute == "Energy_This_Year":
                            plant_sum_energy_year += value_float
                        elif attribute == "Energy_Total":
                            plant_sum_energy_total += value_float
                        elif attribute.endswith("_Panel_Power"):
                             plant_sum_panel_power += value_float

                except (ValueError, TypeError):
                     if is_summable_numeric:
                        _LOGGER.warning(
                            "Aggregator: Could not convert value '%s' to float for summing attribute '%s' in device %s. Skipping value.",
                            value_str, attribute, device_alias
                        )

                # Find the latest timestamp across all devices
                if attribute == "Update_time" or attribute == "Server_Time":
                    if value_str:
                        try:
                            # Assumes 'YYYY-MM-DDTHH:MM:SSZ' format from web_crawler
                            iso_format_string = "%Y-%m-%dT%H:%M:%SZ"
                            current_time_obj = datetime.strptime(value_str, iso_format_string)

                            if attribute == "Update_time":
                                if compare_update_time_obj is None or current_time_obj > compare_update_time_obj:
                                    compare_update_time_obj = current_time_obj
                                    latest_update_time_str = value_str
                            elif attribute == "Server_Time":
                                if compare_server_time_obj is None or current_time_obj > compare_server_time_obj:
                                    compare_server_time_obj = current_time_obj
                                    latest_server_time_str = value_str
                        except (ValueError, TypeError):
                            _LOGGER.warning(
                                "Aggregator: Could not parse datetime '%s' for comparison (attribute '%s', device %s). Using raw string if latest.",
                                value_str, attribute, device_alias
                            )
                            # Fallback: use the first non-empty raw string encountered
                            if attribute == "Update_time" and latest_update_time_str is None: latest_update_time_str = value_str
                            if attribute == "Server_Time" and latest_server_time_str is None: latest_server_time_str = value_str

        except Exception as e:
            _LOGGER.error("Aggregator: Error processing data for device %s: %s", device_alias, e, exc_info=True)
            continue

    aggregated_data = {
        "Power": round(plant_sum_power, 2),
        "Energy_Today": round(plant_sum_energy_today, 2),
        "Energy_This_Month": round(plant_sum_energy_month, 2),
        "Energy_This_Year": round(plant_sum_energy_year, 2),
        "Energy_Total": round(plant_sum_energy_total, 2),
        "Panel_Power": round(plant_sum_panel_power, 2), # Total power from all panels
        "Update_time": latest_update_time_str,
        "Server_Time": latest_server_time_str,
    }
    _LOGGER.debug("Aggregator: Aggregated plant data: %s", aggregated_data)
    return aggregated_data


def calculate_peak_power(current_power: float | None, previous_peak: float, last_reset_date: date | None) -> tuple[float, date, bool]:
    """Calculates the new peak power, handling daily reset."""
    now = datetime.now()
    current_date = now.date()
    state_changed = False
    new_peak = previous_peak
    new_reset_date = last_reset_date

    # Reset peak power if it's a new day
    if new_reset_date is None or new_reset_date != current_date:
        _LOGGER.info(f"Peak Power: New day ({current_date}) detected. Resetting peak from {new_peak:.2f} to 0.0.")
        new_peak = 0.0
        new_reset_date = current_date
        state_changed = True

    # Update peak power if current power is higher
    if current_power is not None and current_power > new_peak:
        updated_peak = round(current_power, 2)
        _LOGGER.debug(f"Peak Power: New peak detected: {updated_peak:.2f} W (Previous: {new_peak:.2f} W)")
        new_peak = updated_peak
        state_changed = True

    return new_peak, new_reset_date, state_changed
