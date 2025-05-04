#!/usr/bin/env python3

import time
import json
import logging
import signal
import sys
import os
import subprocess # <-- ADDED for version checking
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
    FIREFOX_BINARY_PATH, # <-- ADDED for version checking
    GECKODRIVER_PATH,    # <-- ADDED for version checking
)
import web_scraper
import utils
import mqtt_utils
import persistence
import requests

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

_LOGGER = logging.getLogger(__name__)

def log_supervisor_info():
    supervisor_token = os.environ.get("SUPERVISOR_TOKEN")
    if not supervisor_token:
        _LOGGER.error("SUPERVISOR_TOKEN is not set. Cannot fetch Supervisor info.")
        return

    headers = {"Authorization": f"Bearer {supervisor_token}"}
    try:
        response = requests.get("http://supervisor/info", headers=headers)
        response.raise_for_status()
        data = response.json().get("data", {})
        _LOGGER.info(f"Supervisor Info: {data}")
    except requests.RequestException as e:
        _LOGGER.error(f"Error fetching Supervisor info: {e}")

# --- Function to Log Driver Versions ---
def log_driver_versions():
    """Logs the versions of Firefox and Geckodriver found."""
    _LOGGER.info("--- Checking WebDriver Component Versions ---")

    # Check Firefox Version
    try:
        if os.path.exists(FIREFOX_BINARY_PATH):
            result = subprocess.run(
                [FIREFOX_BINARY_PATH, '--version'],
                capture_output=True, text=True, check=True, encoding='utf-8'
            )
            # Pega a primeira linha da saída, que geralmente contém a versão
            version_line = result.stdout.strip().splitlines()[0]
            _LOGGER.info(f"Firefox Version ({FIREFOX_BINARY_PATH}): {version_line}")
        else:
            _LOGGER.warning(f"Firefox binary not found at specified path: {FIREFOX_BINARY_PATH}")
    except FileNotFoundError:
         _LOGGER.error(f"Firefox command '{FIREFOX_BINARY_PATH}' not found in the system.")
    except subprocess.CalledProcessError as e:
        _LOGGER.error(f"Failed to get Firefox version. Command failed: {e}")
        if e.stderr: _LOGGER.error(f"Stderr: {e.stderr.strip()}")
    except Exception as e:
        _LOGGER.error(f"An unexpected error occurred while checking Firefox version: {e}", exc_info=True)

    # Check Geckodriver Version
    try:
        if os.path.exists(GECKODRIVER_PATH):
            result = subprocess.run(
                [GECKODRIVER_PATH, '--version'],
                capture_output=True, text=True, check=True, encoding='utf-8'
            )
            # Geckodriver pode ter múltiplas linhas, a primeira geralmente tem a versão dele
            version_line = result.stdout.strip().splitlines()[0]
            _LOGGER.info(f"Geckodriver Version ({GECKODRIVER_PATH}): {version_line}")
            # Logar também a segunda linha se existir (pode conter info do Firefox compatível)
            if len(result.stdout.strip().splitlines()) > 1:
                 _LOGGER.info(f"Geckodriver Info Line 2: {result.stdout.strip().splitlines()[1]}")
        else:
             _LOGGER.warning(f"Geckodriver binary not found at specified path: {GECKODRIVER_PATH}")
    except FileNotFoundError:
         _LOGGER.error(f"Geckodriver command '{GECKODRIVER_PATH}' not found in the system.")
    except subprocess.CalledProcessError as e:
        _LOGGER.error(f"Failed to get Geckodriver version. Command failed: {e}")
        if e.stderr: _LOGGER.error(f"Stderr: {e.stderr.strip()}")
    except Exception as e:
        _LOGGER.error(f"An unexpected error occurred while checking Geckodriver version: {e}", exc_info=True)

    _LOGGER.info("--- Finished Checking WebDriver Component Versions ---")
# --- End Function to Log Driver Versions ---

# --- Function to Log Environment Info ---
def log_environment_info():
    """Logs Home Assistant environment information and Docker context."""
    _LOGGER.debug("--- Home Assistant Environment Information (DEBUG) ---")
    _LOGGER.debug(f"Env Var 'SUPERVISOR_VERSION': {os.environ.get('SUPERVISOR_VERSION', 'Not Set/Unavailable')}")
    _LOGGER.debug(f"Env Var 'SUPERVISOR_ARCH': {os.environ.get('SUPERVISOR_ARCH', 'Not Set/Unavailable')}")
    _LOGGER.debug(f"Env Var 'SUPERVISOR_MACHINE': {os.environ.get('SUPERVISOR_MACHINE', 'Not Set/Unavailable')}")
    _LOGGER.debug(f"Env Var 'SUPERVISOR_HOSTNAME': {os.environ.get('SUPERVISOR_HOSTNAME', 'Not Set/Unavailable')}")
    _LOGGER.debug(f"Env Var 'SUPERVISOR_TOKEN': {os.environ.get('SUPERVISOR_HOSTNAME', 'Not Set/Unavailable')}")
    _LOGGER.debug(f"Env Var 'TZ': {os.environ.get('TZ', 'Not Set/Unavailable')}")
    _LOGGER.debug(f"Running in Docker: {is_running_in_docker()}")
    _LOGGER.debug(f"Current PID: {os.getpid()}")
    _LOGGER.debug(f"Current working directory: {os.getcwd()}")
    _LOGGER.debug(f"Contents of root directory: {os.listdir('/')}")
    for key, value in os.environ.items():
        _LOGGER.debug(f"Env Var '{key}': {value}")

    _LOGGER.debug("--- End Home Assistant Environment Information ---")

def is_running_in_docker():
    """Check if the script is running inside a Docker container."""
    try:
        exists = os.path.exists('/.dockerenv')
        _LOGGER.debug(f"Checking for /.dockerenv: Exists={exists}, Current PID: {os.getpid()}, Current Working Directory: {os.getcwd()}")
        return exists
    except Exception as e:
        _LOGGER.error(f"Error checking for /.dockerenv: {e}", exc_info=True)
        return False

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

            if log_level == "DEBUG":
                _LOGGER.info("Setting log level for 'selenium' and 'urllib3' to WARNING to reduce noise.")
                logging.getLogger("selenium").setLevel(logging.WARNING)
                logging.getLogger("urllib3").setLevel(logging.WARNING)

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

                      _LOGGER.info("Waiting 5 seconds for Home Assistant to process discovery...")
                      time.sleep(5) # Adjust delay if needed

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

if __name__ == "__main__":
    _LOGGER.info("Starting SAJ Portal Scraper Add-on...")

    log_driver_versions()

    log_supervisor_info()
    # Load configuration
    load_config()

    current_peak_power, last_reset_date = persistence.load_peak_power_state()

    client_id = f"{DOMAIN}-addon-{os.getpid()}"

    mqtt_client = mqtt_utils.connect_mqtt(client_id, CONFIG)
    if not mqtt_client:
        _LOGGER.warning("Initial MQTT connection failed. Will retry within the loop.")

    # --- ADDED: Log HA environment details if log level is DEBUG ---
    if logging.getLogger().getEffectiveLevel() == logging.DEBUG:
        log_environment_info()

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
