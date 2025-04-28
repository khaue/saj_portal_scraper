# /workspaces/addons/saj_portal_scraper/web_scraper.py
import logging
import time
from datetime import datetime, timezone, timedelta
import os
try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:
    _LOGGER.error("zoneinfo library not found. Please ensure Python 3.9+ or install backports.zoneinfo.")
    # Define a simple fallback if zoneinfo is not available
    class ZoneInfoNotFoundError(Exception): pass
    class ZoneInfo:
        def __init__(self, key):
            if key.upper() != "UTC":
                # Cannot determine local TZ without the library, so use UTC as fallback
                _LOGGER.warning(f"zoneinfo not available, cannot determine local timezone '{key}'. Using UTC.")
            self._key = "UTC" # Force UTC if lib doesn't exist
        def __repr__(self): return f"ZoneInfo(key='{self._key}')"
        # Dummy methods for basic compatibility if needed (astimezone will work with timezone.utc)


from selenium import webdriver
from selenium.common.exceptions import WebDriverException, TimeoutException, NoSuchElementException
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.firefox.service import Service
from selenium.webdriver.common.keys import Keys

from const import (
    GECKODRIVER_PATH,
    FIREFOX_BINARY_PATH,
    COLUMN_MAPPING,
    LOGIN_URL,
    DASHBOARD_URL,
    DATA_URL_TEMPLATE,
    USERNAME_SELECTOR,
    PASSWORD_SELECTOR,
    DEFAULT_USERNAME,
    DEFAULT_PASSWORD,
    DEFAULT_MICROINVERTERS
)

_LOGGER = logging.getLogger(__name__)


def validate_connection(config: dict) -> webdriver.Firefox:
    """Validate connection and return logged-in WebDriver instance."""
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.binary_location = FIREFOX_BINARY_PATH
    service = Service(GECKODRIVER_PATH)
    driver = None
    try:
        _LOGGER.debug("Initializing Firefox WebDriver...")
        driver = webdriver.Firefox(service=service, options=options)
        driver.set_page_load_timeout(60)
        driver.set_script_timeout(30)
        _LOGGER.debug("WebDriver initialized successfully.")
    except (WebDriverException, Exception) as init_err:
        _LOGGER.exception("Failed to initialize Firefox WebDriver. Check paths and permissions. Error: %s", init_err)
        raise RuntimeError(f"WebDriver Initialization Failed: {init_err}") from init_err
    try:
        _LOGGER.info("Attempting to log in to SAJ Portal...")
        driver.get(LOGIN_URL)
        username = config.get("saj_username", DEFAULT_USERNAME)
        password = config.get("saj_password", DEFAULT_PASSWORD)
        if not username or not password:
             _LOGGER.error("Username or password not configured.")
             raise ValueError("Missing SAJ Portal credentials in configuration.")
        wait = WebDriverWait(driver, 30)
        username_field = wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, USERNAME_SELECTOR)))
        password_field = driver.find_element(By.CSS_SELECTOR, PASSWORD_SELECTOR)
        _LOGGER.debug("Login elements found. Sending credentials...")
        username_field.send_keys(username)
        password_field.send_keys(password)
        password_field.send_keys(Keys.RETURN)
        wait.until(EC.url_to_be(DASHBOARD_URL))
        _LOGGER.info("Login successful. Dashboard loaded.")
        return driver
    except TimeoutException:
        _LOGGER.error("Timeout occurred during login process (finding elements or waiting for dashboard).")
        # Dump HTML on Login Failure for debugging
        try:
            page_html = driver.page_source
            filename = f"/data/saj_debug_login_timeout_{int(time.time())}.html"
            with open(filename, "w", encoding="utf-8") as f: f.write(page_html)
            _LOGGER.error("Page source saved to %s for debugging login timeout.", filename)
        except Exception as dump_err:
            _LOGGER.error("Failed to save page source during login timeout: %s", dump_err)
        if driver: driver.quit()
        raise ValueError("Login failed: Timeout waiting for elements or dashboard URL.")
    except (NoSuchElementException, Exception) as login_err:
        _LOGGER.exception("Error during login: %s", login_err)
        if driver: driver.quit()
        raise ValueError(f"Login failed: {login_err}") from login_err


def _fetch_data_sync(config: dict, driver: webdriver.Firefox) -> dict:
    """Synchronous function to fetch data using Selenium."""
    microinverters_str = config.get("microinverters", "")
    if not microinverters_str:
        _LOGGER.warning("No microinverters configured. Returning empty data.")
        return {}

    try:
        microinverter_map = {
            pair.split(":")[0].strip(): pair.split(":")[1].strip()
            for pair in microinverters_str.split(",")
            # Basic validation for "SN:Alias" format
            if ":" in pair and len(pair.split(":")) == 2
        }
        if not microinverter_map:
             _LOGGER.error("Microinverters string '%s' is invalid or empty after parsing.", microinverters_str)
             return {}
    except Exception as parse_err:
        _LOGGER.error("Invalid microinverters format in config: '%s'. Error: %s", microinverters_str, parse_err)
        return {}

    all_device_data = {}
    wait_timeout = 60

    # Determine the local timezone ONCE using the TZ environment variable
    try:
        local_tz_str = os.environ.get("TZ", "UTC") # Get TZ from environment, fallback to UTC
        local_tz = ZoneInfo(local_tz_str)
        _LOGGER.info(f"Using local timezone: {local_tz_str} ({local_tz})")
    except ZoneInfoNotFoundError:
        _LOGGER.warning(f"Timezone '{local_tz_str}' not found. Falling back to UTC.")
        local_tz = ZoneInfo("UTC") # Use UTC as a safe fallback
    except Exception as e:
        _LOGGER.error(f"Error getting timezone '{local_tz_str}': {e}. Falling back to UTC.")
        local_tz = ZoneInfo("UTC")
    utc_tz = timezone.utc

    for device_sn, device_alias in microinverter_map.items():
        data_url = DATA_URL_TEMPLATE.format(device_sn=device_sn)
        _LOGGER.info("Fetching data for device %s (%s)...", device_alias, device_sn)

        try:
            driver.get(data_url)
            WebDriverWait(driver, wait_timeout).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, ".el-table__body-wrapper tbody tr"))
            )
            _LOGGER.debug("Data table appeared for device %s.", device_alias)

            device_data_rows = {}
            rows = driver.find_elements(By.CSS_SELECTOR, ".el-table__body-wrapper tbody tr")
            _LOGGER.debug("Found %d rows in the table for device %s.", len(rows), device_alias)

            if not rows:
                 _LOGGER.warning("No rows found in table for device %s after waiting.", device_alias)
                 continue

            row = rows[0]
            cols = row.find_elements(By.TAG_NAME, "td")
            col_count = len(cols)

            # Extract all raw data first
            raw_row_data = {}
            for column_name, column_index in COLUMN_MAPPING.items():
                if column_index >= col_count:
                    _LOGGER.warning("Column index %d for '%s' out of range (max %d) for device %s.",
                                    column_index, column_name, col_count -1, device_alias)
                    continue
                try:
                    raw_row_data[column_name] = cols[column_index].text.strip()
                except Exception as col_err:
                     _LOGGER.error("Error extracting raw column '%s' for device %s: %s", column_name, device_alias, col_err)
                     raw_row_data[column_name] = None # Mark as None if extraction fails

            # Process Timestamps Separately (Hypothesis: Update=Local, Server=UTC)
            row_data = {}
            raw_update_time = raw_row_data.get("Update_time")
            raw_server_time = raw_row_data.get("Server_Time")
            processed_update_time = raw_update_time # Initial fallback
            processed_server_time = raw_server_time # Initial fallback

            if raw_update_time:
                try:
                    # Process Update_time (Assume it's LOCAL time)
                    naive_update_dt = datetime.strptime(raw_update_time, "%Y-%m-%d %H:%M:%S")
                    local_update_dt = naive_update_dt.replace(tzinfo=local_tz) # Apply local TZ
                    utc_update_dt = local_update_dt.astimezone(utc_tz) # Convert to UTC
                    processed_update_time = utc_update_dt.strftime("%Y-%m-%dT%H:%M:%SZ") # Format
                    _LOGGER.debug(f"Processed Update_time for {device_alias}: Raw='{raw_update_time}' (Local TZ={local_tz}) -> UTC='{processed_update_time}'")
                except ValueError as parse_err:
                    _LOGGER.warning(f"Could not parse Update_time string for {device_alias}: '{raw_update_time}'. Error: {parse_err}. Using raw value.")
                    # Keep the fallback (raw value)
                except Exception as tz_err:
                    _LOGGER.error(f"Error processing Update_time timezone for {device_alias}: {tz_err}. Using raw value.", exc_info=True)
                    # Keep the fallback (raw value)

            if raw_server_time:
                try:
                    # Process Server_Time (Assume it's ALREADY UTC)
                    naive_server_dt = datetime.strptime(raw_server_time, "%Y-%m-%d %H:%M:%S")
                    # No astimezone conversion needed, just make it timezone-aware UTC
                    utc_server_dt = naive_server_dt.replace(tzinfo=utc_tz) # Apply UTC TZ
                    processed_server_time = utc_server_dt.strftime("%Y-%m-%dT%H:%M:%SZ") # Format
                    _LOGGER.debug(f"Processed Server_Time for {device_alias}: Raw='{raw_server_time}' (Assumed UTC) -> UTC='{processed_server_time}'")
                except ValueError as parse_err:
                    _LOGGER.warning(f"Could not parse Server_Time string for {device_alias}: '{raw_server_time}'. Error: {parse_err}. Using raw value.")
                    # Keep the fallback (raw value)
                except Exception as tz_err:
                    # Less likely here, but for safety
                    _LOGGER.error(f"Error processing Server_time timezone for {device_alias}: {tz_err}. Using raw value.", exc_info=True)
                    # Keep the fallback (raw value)

            # Assemble the final row_data dictionary
            row_data["Update_time"] = processed_update_time
            row_data["Server_Time"] = processed_server_time

            # Copy/process other fields
            for column_name, raw_value in raw_row_data.items():
                 if column_name not in ["Update_time", "Server_Time"]:
                    # Handle multi-value panel data (repeated here for clarity)
                    if column_name in ["Panel_Voltage", "Panel_Current", "Panel_Power"]:
                        channel_col_index = COLUMN_MAPPING.get("Panel_Channel")
                        raw_channel_value = raw_row_data.get("Panel_Channel")
                        if channel_col_index is not None and raw_channel_value is not None and raw_value is not None:
                            channel_values = raw_channel_value.split("\n")
                            values = raw_value.split("\n")
                            num_channels = len(channel_values)
                            num_values = len(values)
                            if num_channels == num_values:
                                for i in range(num_channels):
                                    # In web_scraper.py, around line 243
                                    channel_key = channel_values[i].strip().upper() # Convert "pv1" to "PV1"
                                    if channel_key:
                                        row_data[f"{channel_key}_{column_name}"] = values[i].strip() # Now generates PV1_Panel_Voltage etc.
                            else:
                                # If mismatch, store the raw value of the main column
                                row_data[column_name] = raw_value
                        elif raw_value is not None:
                             # Store raw if Panel_Channel is missing
                             row_data[column_name] = raw_value
                    elif column_name != "Panel_Channel": # Avoid adding Panel_Channel directly
                        row_data[column_name] = raw_value

            row_data["Alias"] = device_alias
            update_time_key = row_data.get("Update_time") # Use the processed (should be UTC ISO) value

            # Check if Update_time conversion was successful before using as key
            if not update_time_key or not isinstance(update_time_key, str) or 'Z' not in update_time_key:
                _LOGGER.warning("Skipping row for device %s because processed 'Update_time' ('%s') is invalid.", device_alias, update_time_key)
            else:
                device_data_rows[update_time_key] = row_data
                _LOGGER.debug("Processed row with UTC update time %s for device %s.", update_time_key, device_alias)


            if device_data_rows:
                 # Compare ISO UTC strings to find the latest
                 latest_time = max(device_data_rows.keys())
                 all_device_data[device_sn] = device_data_rows[latest_time]
                 _LOGGER.debug("Stored latest data for %s (UTC update time: %s)", device_alias, latest_time)
            else:
                 _LOGGER.warning("No valid rows with converted update_time processed for device %s.", device_alias)

        except TimeoutException:
            _LOGGER.error("Timeout (%ds) waiting for data table for device %s (%s). URL: %s", wait_timeout, device_alias, device_sn, data_url)
            # Dump HTML on Data Fetch Failure for debugging
            try:
                page_html = driver.page_source
                filename = f"/data/saj_debug_data_timeout_{device_alias}_{int(time.time())}.html"
                with open(filename, "w", encoding="utf-8") as f: f.write(page_html)
                _LOGGER.error("Page source saved to %s for debugging data timeout.", filename)
            except Exception as dump_err:
                _LOGGER.error("Failed to save page source during data timeout: %s", dump_err)
            continue
        except (NoSuchElementException, WebDriverException) as fetch_err:
             _LOGGER.error("Selenium error fetching data for device %s (%s): %s",
                           device_alias, device_sn, fetch_err, exc_info=True)
             continue
        except Exception as e:
            _LOGGER.exception("Unexpected error fetching data for device %s (%s): %s",
                              device_alias, device_sn, e)
            continue

    _LOGGER.info("Finished fetching data for all configured devices.")
    return all_device_data
