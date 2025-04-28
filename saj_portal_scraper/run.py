#!/usr/bin/env python3

import time
import json
import logging
import signal
import sys
import os
from datetime import date, datetime

from const import (
    DOMAIN,
    MQTT_BASE_TOPIC,
    MQTT_AVAILABILITY_TOPIC,
    MQTT_PAYLOAD_ONLINE,
    MQTT_PAYLOAD_OFFLINE,
    DEFAULT_MICROINVERTERS,
    DEFAULT_INACTIVITY_ENABLED,
    DEFAULT_INACTIVITY_START_TIME,
    DEFAULT_INACTIVITY_END_TIME,
    UPDATE_INTERVAL,
    DEFAULT_LOG_LEVEL,
    DEFAULT_USERNAME,
    DEFAULT_PASSWORD,
    CONF_DATA_INACTIVITY_THRESHOLD,
    CONF_EXTENDED_UPDATE_INTERVAL,
    DEFAULT_DATA_INACTIVITY_THRESHOLD,
    DEFAULT_EXTENDED_UPDATE_INTERVAL,
)
import web_scraper
import utils
import mqtt_utils
import persistence

OPTIONS_FILE = "/data/options.json"
CONFIG = {}
# Attempt to get add-on version from environment variable
ADDON_VERSION = os.environ.get("VERSION", "unknown")

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
_LOGGER = logging.getLogger(__name__)

# --- Global State ---
mqtt_client = None
webdriver = None
shutdown_requested = False
current_peak_power: float = 0.0
last_reset_date: date | None = None
initial_setup_done = False

# Variables for dynamic interval logic
last_known_update_times: dict[str, str] = {} # Stores the last Update_time (ISO UTC string) per SN
last_data_change_timestamp: float | None = None # Monotonic time of the last data change detected
using_extended_interval: bool = False # Flag indicating if the extended interval is active
# --- End Global State ---

def handle_shutdown(signum, frame):
    global shutdown_requested
    _LOGGER.info(f"Received signal {signum}. Requesting shutdown...")
    shutdown_requested = True

signal.signal(signal.SIGTERM, handle_shutdown)
signal.signal(signal.SIGINT, handle_shutdown)


def load_config():
    global CONFIG
    _LOGGER.info(f"Loading configuration from {OPTIONS_FILE}")
    try:
        with open(OPTIONS_FILE, 'r') as f:
            CONFIG = json.load(f)
            log_level = CONFIG.get("log_level", DEFAULT_LOG_LEVEL).upper()
            logging.getLogger().setLevel(log_level)
            _LOGGER.info(f"Configuration loaded successfully. Log level set to {log_level}")

            # Basic validation warnings
            if not CONFIG.get("saj_username"):
                 _LOGGER.warning(f"SAJ username missing in config. Consider using the default: {DEFAULT_USERNAME}")
            if not CONFIG.get("saj_password"):
                 _LOGGER.warning("SAJ password missing in config.")
            if not CONFIG.get("microinverters"):
                 _LOGGER.warning(f"Microinverters list missing in config. Consider using the default: {DEFAULT_MICROINVERTERS}")

            # Load dynamic interval settings, ensuring they are integers
            try:
                CONFIG[CONF_DATA_INACTIVITY_THRESHOLD] = int(CONFIG.get(CONF_DATA_INACTIVITY_THRESHOLD, DEFAULT_DATA_INACTIVITY_THRESHOLD))
            except (ValueError, TypeError):
                _LOGGER.warning(f"Invalid value for {CONF_DATA_INACTIVITY_THRESHOLD}, using default: {DEFAULT_DATA_INACTIVITY_THRESHOLD}s")
                CONFIG[CONF_DATA_INACTIVITY_THRESHOLD] = DEFAULT_DATA_INACTIVITY_THRESHOLD

            try:
                CONFIG[CONF_EXTENDED_UPDATE_INTERVAL] = int(CONFIG.get(CONF_EXTENDED_UPDATE_INTERVAL, DEFAULT_EXTENDED_UPDATE_INTERVAL))
            except (ValueError, TypeError):
                 _LOGGER.warning(f"Invalid value for {CONF_EXTENDED_UPDATE_INTERVAL}, using default: {DEFAULT_EXTENDED_UPDATE_INTERVAL}s")
                 CONFIG[CONF_EXTENDED_UPDATE_INTERVAL] = DEFAULT_EXTENDED_UPDATE_INTERVAL

            _LOGGER.info(f"Data inactivity threshold set to {CONFIG[CONF_DATA_INACTIVITY_THRESHOLD]} seconds.")
            _LOGGER.info(f"Extended update interval set to {CONFIG[CONF_EXTENDED_UPDATE_INTERVAL]} seconds.")

    except FileNotFoundError:
        _LOGGER.error(f"Configuration file {OPTIONS_FILE} not found. Cannot start.")
        sys.exit(1)
    except (json.JSONDecodeError, Exception) as e:
        _LOGGER.error(f"Error loading configuration: {e}")
        sys.exit(1)

def cleanup():
    global mqtt_client, webdriver
    _LOGGER.info("Performing cleanup...")
    if mqtt_client:
        try:
            _LOGGER.info("Publishing offline status to MQTT...")
            mqtt_client.publish(MQTT_AVAILABILITY_TOPIC, payload=MQTT_PAYLOAD_OFFLINE, qos=1, retain=True)
            time.sleep(0.5)
            _LOGGER.info("Disconnecting MQTT client...")
            mqtt_client.loop_stop()
            mqtt_client.disconnect()
            _LOGGER.info("MQTT client disconnected.")
        except Exception as e:
            _LOGGER.error(f"Error during MQTT cleanup: {e}")
        mqtt_client = None
    if webdriver:
        try:
            _LOGGER.info("Quitting WebDriver...")
            webdriver.quit()
            _LOGGER.info("WebDriver quit.")
        except Exception as e:
            _LOGGER.error(f"Error quitting WebDriver: {e}")
        webdriver = None
    _LOGGER.info("Cleanup finished.")


def run_cycle():
     global webdriver, current_peak_power, last_reset_date, mqtt_client, initial_setup_done
     global last_known_update_times, last_data_change_timestamp, using_extended_interval

     # High priority check: Configured inactivity period
     if initial_setup_done and utils.is_inactive(CONFIG):
         _LOGGER.info("Currently in configured inactivity period. Skipping data fetch cycle.")
         # Reset extended interval flag if entering the main inactivity period
         if using_extended_interval:
             _LOGGER.info("Resetting extended interval flag due to entering inactivity period.")
             using_extended_interval = False
         return

     # Ensure WebDriver is running
     if not webdriver:
         try:
             _LOGGER.info("WebDriver not active. Attempting to validate connection and log in...")
             webdriver = web_scraper.validate_connection(CONFIG)
             _LOGGER.info("Connection validated and logged in successfully.")
         except (ValueError, RuntimeError, Exception) as e:
             _LOGGER.error(f"Failed to establish WebDriver connection or log in: {e}. Will retry next cycle.")
             if webdriver:
                  try: webdriver.quit()
                  except: pass
             webdriver = None
             return

     # Fetch Data
     try:
         _LOGGER.info("Fetching microinverter data...")
         device_data = web_scraper._fetch_data_sync(CONFIG, webdriver)

         if not device_data:
             _LOGGER.warning("No device data fetched in this cycle.")
             # Cannot check for data changes if no data was fetched
             return

         # Check for data changes (only after initial setup)
         data_changed_this_cycle = False
         if initial_setup_done:
             for sn, data in device_data.items():
                 current_update_time = data.get("Update_time") # Should be ISO UTC string
                 previous_update_time = last_known_update_times.get(sn)

                 if current_update_time and isinstance(current_update_time, str) and 'Z' in current_update_time:
                     if current_update_time != previous_update_time:
                         _LOGGER.debug(f"New data detected for device {sn}: Update_time changed from '{previous_update_time}' to '{current_update_time}'")
                         last_known_update_times[sn] = current_update_time
                         data_changed_this_cycle = True
                     else:
                          _LOGGER.debug(f"No new data for device {sn}: Update_time ('{current_update_time}') is unchanged.")
                 else:
                      _LOGGER.warning(f"Could not check data change for device {sn}: Invalid or missing Update_time ('{current_update_time}')")

             if data_changed_this_cycle:
                 _LOGGER.debug("Data changed in this cycle. Updating last change timestamp.")
                 last_data_change_timestamp = time.monotonic()
                 if using_extended_interval:
                     _LOGGER.info("New data detected. Switching back to normal update interval.")
                     using_extended_interval = False
             else:
                 _LOGGER.debug("No data changed across all devices in this cycle.")
                 # Decision to switch to extended interval happens in the main loop

         # Aggregate Data
         _LOGGER.info("Aggregating plant data...")
         plant_data = utils.aggregate_plant_data(device_data)

         # Calculate Peak Power
         _LOGGER.info("Calculating peak power...")
         current_plant_power_str = plant_data.get("Power")
         current_plant_power = None
         if current_plant_power_str is not None:
             try:
                 current_plant_power = float(str(current_plant_power_str).replace(',', '.'))
             except (ValueError, TypeError):
                  _LOGGER.warning(f"Could not convert current plant power '{current_plant_power_str}' to float.")

         new_peak, new_reset_date, peak_state_changed = utils.calculate_peak_power(
             current_plant_power, current_peak_power, last_reset_date
         )

         # Persist Peak Power State (if changed)
         if peak_state_changed:
             _LOGGER.info(f"Peak power state changed. New Peak: {new_peak:.2f}, Reset Date: {new_reset_date}")
             current_peak_power = new_peak
             last_reset_date = new_reset_date
             persistence.save_peak_power_state(current_peak_power, last_reset_date)

         # Publish to MQTT
         if mqtt_client and mqtt_client.is_connected():
              _LOGGER.info("Publishing data to MQTT...")

              # Conditional Discovery (only on first successful fetch)
              if not initial_setup_done:
                  _LOGGER.info("Performing initial MQTT discovery...")
                  try:
                      mqtt_utils.publish_discovery(mqtt_client, device_data, plant_data, {"value": current_peak_power, "last_reset_date": last_reset_date}, ADDON_VERSION)
                      _LOGGER.info("Initial MQTT discovery published successfully.")
                      initial_setup_done = True
                      # Initialize timestamps after first successful discovery
                      _LOGGER.debug("Initializing last known update times and change timestamp.")
                      last_data_change_timestamp = time.monotonic()
                      for sn, data in device_data.items():
                          update_time = data.get("Update_time")
                          if update_time and isinstance(update_time, str) and 'Z' in update_time:
                              last_known_update_times[sn] = update_time
                  except Exception as discovery_err:
                      _LOGGER.error(f"Failed to publish initial MQTT discovery: {discovery_err}", exc_info=True)
                      # Abort cycle if initial discovery fails, prevents publishing state
                      return

              # Publish current state (only if initial setup is complete)
              if initial_setup_done:
                  try:
                      mqtt_utils.publish_state(mqtt_client, device_data, plant_data, current_peak_power, last_reset_date)
                      _LOGGER.info("Data state published successfully.")
                  except Exception as state_pub_err:
                       _LOGGER.error(f"Failed to publish state: {state_pub_err}", exc_info=True)

         else:
              _LOGGER.warning("MQTT client not connected. Cannot publish data.")
              if not mqtt_client:
                   _LOGGER.info("Attempting to reconnect MQTT...")
                   mqtt_client = mqtt_utils.connect_mqtt(f"saj-portal-addon-{os.getpid()}", CONFIG)

     # Error Handling
     except ValueError as e: # Typically login/config errors
          _LOGGER.error(f"Authentication or configuration error during cycle: {e}")
          if webdriver:
               try: webdriver.quit()
               except: pass
          webdriver = None
     except (web_scraper.WebDriverException, web_scraper.TimeoutException) as e: # Selenium errors
          _LOGGER.error(f"Selenium error during data fetch: {e}. WebDriver might be stale.")
          if webdriver:
               try: webdriver.quit()
               except: pass
          webdriver = None
     except Exception as e: # Unexpected errors
         _LOGGER.exception(f"Unexpected error during processing cycle: {e}")


# --- Main Execution ---
if __name__ == "__main__":
    _LOGGER.info("Starting SAJ Portal Scraper Add-on...")
    load_config()

    current_peak_power, last_reset_date = persistence.load_peak_power_state()

    client_id = f"{DOMAIN}-addon-{os.getpid()}"

    mqtt_client = mqtt_utils.connect_mqtt(client_id, CONFIG)
    if not mqtt_client:
        _LOGGER.warning("Initial MQTT connection failed. Will retry within the loop.")

    # Get intervals from config
    normal_update_interval = CONFIG.get("update_interval_seconds", UPDATE_INTERVAL)
    data_inactivity_threshold = CONFIG.get(CONF_DATA_INACTIVITY_THRESHOLD, DEFAULT_DATA_INACTIVITY_THRESHOLD)
    extended_update_interval = CONFIG.get(CONF_EXTENDED_UPDATE_INTERVAL, DEFAULT_EXTENDED_UPDATE_INTERVAL)
    _LOGGER.info(f"Normal update interval set to {normal_update_interval} seconds.")

    # Main loop
    while not shutdown_requested:
        try:
            start_time = time.monotonic()
            if not initial_setup_done:
                 _LOGGER.info("Starting initial data fetch and discovery cycle attempt...")
            else:
                 # Log which interval is currently active
                 current_interval_in_use = extended_update_interval if using_extended_interval else normal_update_interval
                 _LOGGER.info(f"Starting new data fetch cycle (Interval: {current_interval_in_use}s {'[Extended]' if using_extended_interval else '[Normal]'})")

            run_cycle()

            end_time = time.monotonic()
            duration = end_time - start_time
            _LOGGER.info(f"Cycle finished in {duration:.2f} seconds.")

            # Dynamic Interval Logic
            current_interval_to_use = normal_update_interval

            if initial_setup_done: # Only apply dynamic logic after setup
                # Check if we should switch TO extended interval
                if not using_extended_interval and last_data_change_timestamp is not None:
                    time_since_last_change = time.monotonic() - last_data_change_timestamp
                    _LOGGER.debug(f"Time since last data change: {time_since_last_change:.0f}s (Threshold: {data_inactivity_threshold}s)")
                    if time_since_last_change > data_inactivity_threshold:
                        _LOGGER.info(f"Data inactivity threshold ({data_inactivity_threshold}s) exceeded. Switching to extended interval ({extended_update_interval}s).")
                        using_extended_interval = True

                # Use extended interval if the flag is set
                if using_extended_interval:
                    current_interval_to_use = extended_update_interval

            # Calculate sleep time
            sleep_time = max(0, current_interval_to_use - duration)
            _LOGGER.info(f"Sleeping for {sleep_time:.2f} seconds (Using {'Extended' if using_extended_interval else 'Normal'} Interval)...")

            # Sleep interruptibly to allow faster shutdown
            sleep_end_time = time.monotonic() + sleep_time
            while time.monotonic() < sleep_end_time and not shutdown_requested:
                 time.sleep(1) # Sleep in 1s increments

        except Exception as loop_err:
             _LOGGER.exception(f"Critical error in main loop: {loop_err}")
             # Prevent fast looping on critical errors
             time.sleep(60)

    # Shutdown
    _LOGGER.info("Shutdown requested. Exiting main loop.")
    cleanup()
    _LOGGER.info("SAJ Portal Scraper Add-on stopped.")
    sys.exit(0)

