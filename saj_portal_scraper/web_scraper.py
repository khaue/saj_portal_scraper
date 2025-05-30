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
            self._key = "UTC"
        def __repr__(self): return f"ZoneInfo(key='{self._key}')"

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

def is_session_expired(driver):
    """Detects if the session has expired by checking if it is on the login screen."""
    _LOGGER.debug(f"Checking expired session...")
    try:
        # Check if on the login URL
        if LOGIN_URL in driver.current_url:
            return True
        # Check if the username field is present
        login_fields = driver.find_elements(By.CSS_SELECTOR, USERNAME_SELECTOR)
        if login_fields:
            return True
    except Exception as e:
        _LOGGER.debug(f"Error while checking session expiration: {e}")
    return False

def _is_data_url_in(driver):
    """Check if the current page is a data url page (ignoring the variable part at the end)."""
    _LOGGER.debug(f"Checking current URL...")
    try:
        base_data_url = DATA_URL_TEMPLATE.split("{")[0]
        _LOGGER.debug(f"Current URL: {driver.current_url}")
        if driver.current_url.startswith(base_data_url) and not is_session_expired(driver):
            return True
        _LOGGER.debug(f"Data URL Checked. URL: {driver.current_url}")
    except Exception as e:
        _LOGGER.debug(f"Error checking Data URL: {e}")
    return False

def _perform_login(driver, config):
    """Performs login using the provided driver and config. Returns True if successful."""
    try:
        _LOGGER.info("Attempting to log in to SAJ Portal...")
        username = config.get("saj_username", DEFAULT_USERNAME)
        password = config.get("saj_password", DEFAULT_PASSWORD)
        driver.get(LOGIN_URL)
        wait = WebDriverWait(driver, 30)
        username_field = wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, USERNAME_SELECTOR)))
        password_field = driver.find_element(By.CSS_SELECTOR, PASSWORD_SELECTOR)
        username_field.clear()
        username_field.send_keys(username)
        password_field.clear()
        password_field.send_keys(password)
        password_field.send_keys(Keys.RETURN)
        wait.until(EC.url_to_be(DASHBOARD_URL))
        _LOGGER.info("Login successful.")
        return True
    except Exception as login_err:
        _LOGGER.error("Login failed: %s", login_err)
        try:
            page_html = driver.page_source
            filename = f"/data/saj_debug_login_failed_{int(time.time())}.html"
            with open(filename, "w", encoding="utf-8") as f:
                f.write(f"<!-- URL: {driver.current_url} -->\n")
                f.write(page_html)
            _LOGGER.error(f"Page source saved to {filename} for debugging login failure. URL: {driver.current_url}")
        except Exception as dump_err:
            _LOGGER.error("Failed to save page source during login failure: %s", dump_err)
        return False

def validate_connection(config: dict) -> webdriver.Firefox:
    """Validate connection and return logged-in WebDriver instance."""
    options = Options()
    options.add_argument("--headless")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-software-rasterizer")
    options.set_preference("security.sandbox.content.level", 0)
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
        if not _perform_login(driver, config):
            raise ValueError("Login failed: Unable to complete login with provided credentials.")
        return driver
    except Exception as login_err:
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
        local_tz_str = os.environ.get("TZ", "UTC")
        local_tz = ZoneInfo(local_tz_str)
        _LOGGER.debug(f"Using local timezone: {local_tz_str} ({local_tz})")
    except ZoneInfoNotFoundError:
        _LOGGER.warning(f"Timezone '{local_tz_str}' not found. Falling back to UTC.")
        local_tz = ZoneInfo("UTC")
    except Exception as e:
        _LOGGER.error(f"Error getting timezone '{local_tz_str}': {e}. Falling back to UTC.")
        local_tz = ZoneInfo("UTC")
    utc_tz = timezone.utc

    recovery_attempted = False

    for device_sn, device_alias in microinverter_map.items():
        data_url = DATA_URL_TEMPLATE.format(device_sn=device_sn)
        _LOGGER.info("Fetching data for device %s (%s)...", device_alias, device_sn)

        # Log all microinverter aliases and serials at debug level
        for sn, alias in microinverter_map.items():
            _LOGGER.debug(f"Configured microinverter: SN={sn}, Alias={alias}")

        try:
            driver.get(data_url)
            WebDriverWait(driver, wait_timeout).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, ".el-table__body-wrapper tbody tr"))
            )

            if not _is_data_url_in(driver):
                _LOGGER.warning("Not logged in when trying to fetch data for device %s (%s).", device_alias, device_sn)
                try:
                    page_html = driver.page_source
                    filename = f"/data/saj_debug_data_not_logged_{device_alias}_{int(time.time())}.html"
                    with open(filename, "w", encoding="utf-8") as f:
                        f.write(f"<!-- URL: {driver.current_url} -->\n")
                        f.write(page_html)
                    _LOGGER.error(f"Page source saved to {filename} for debugging not-logged-in state. URL: {driver.current_url}")
                except Exception as dump_err:
                    _LOGGER.error("Failed to save page source during not-logged-in state: %s", dump_err)
                _perform_login(driver, config)
                continue  # Skip to the next microinverter

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
                    raw_row_data[column_name] = None

            row_data = {}
            raw_update_time = raw_row_data.get("Update_time")
            raw_server_time = raw_row_data.get("Server_Time")
            processed_update_time = raw_update_time
            processed_server_time = raw_server_time

            if raw_update_time:
                try:
                    naive_update_dt = datetime.strptime(raw_update_time, "%Y-%m-%d %H:%M:%S")
                    local_update_dt = naive_update_dt.replace(tzinfo=local_tz)
                    utc_update_dt = local_update_dt.astimezone(utc_tz)
                    processed_update_time = utc_update_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                    _LOGGER.debug(f"Processed Update_time for {device_alias}: Raw='{raw_update_time}' (Local TZ={local_tz}) -> UTC='{processed_update_time}'")
                except ValueError as parse_err:
                    _LOGGER.warning(f"Could not parse Update_time string for {device_alias}: '{raw_update_time}'. Error: {parse_err}. Using raw value.")
                except Exception as tz_err:
                    _LOGGER.error(f"Error processing Update_time timezone for {device_alias}: {tz_err}. Using raw value.", exc_info=True)

            if raw_server_time:
                try:
                    naive_server_dt = datetime.strptime(raw_server_time, "%Y-%m-%d %H:%M:%S")
                    utc_server_dt = naive_server_dt.replace(tzinfo=utc_tz)
                    processed_server_time = utc_server_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                    _LOGGER.debug(f"Processed Server_Time for {device_alias}: Raw='{raw_server_time}' (Assumed UTC) -> UTC='{processed_server_time}'")
                except ValueError as parse_err:
                    _LOGGER.warning(f"Could not parse Server_Time string for {device_alias}: '{raw_server_time}'. Error: {parse_err}. Using raw value.")
                except Exception as tz_err:
                    _LOGGER.error(f"Error processing Server_time timezone for {device_alias}: {tz_err}. Using raw value.", exc_info=True)

            row_data["Update_time"] = processed_update_time
            row_data["Server_Time"] = processed_server_time

            for column_name, raw_value in raw_row_data.items():
                if column_name not in ["Update_time", "Server_Time"]:
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
                                    channel_key = channel_values[i].strip().upper()
                                    if channel_key:
                                        row_data[f"{channel_key}_{column_name}"] = values[i].strip()
                            else:
                                row_data[column_name] = raw_value
                        elif raw_value is not None:
                            row_data[column_name] = raw_value
                    elif column_name != "Panel_Channel":
                        row_data[column_name] = raw_value

            row_data["Alias"] = device_alias
            update_time_key = row_data.get("Update_time")

            if not update_time_key or not isinstance(update_time_key, str) or 'Z' not in update_time_key:
                _LOGGER.warning("Skipping row for device %s because processed 'Update_time' ('%s') is invalid.", device_alias, update_time_key)
            else:
                device_data_rows[update_time_key] = row_data
                _LOGGER.debug("Processed row with UTC update time %s for device %s.", update_time_key, device_alias)

            if device_data_rows:
                latest_time = max(device_data_rows.keys())
                all_device_data[device_sn] = device_data_rows[latest_time]
                _LOGGER.debug("Stored latest data for %s (UTC update time: %s)", device_alias, latest_time)
            else:
                _LOGGER.warning("No valid rows with converted update_time processed for device %s.", device_alias)

        except TimeoutException as timeout_exc:
            _LOGGER.error("Timeout (%ds) waiting for data table for device %s (%s). URL: %s", wait_timeout, device_alias, device_sn, data_url, exc_info=timeout_exc)
            try:
                page_html = driver.page_source
                filename = f"/data/saj_debug_data_timeout_{device_alias}_{int(time.time())}.html"
                with open(filename, "w", encoding="utf-8") as f:
                    f.write(f"<!-- URL: {driver.current_url} -->\n")
                    f.write(page_html)
                _LOGGER.error(f"Page source saved to {filename} for debugging data timeout. URL: {driver.current_url}")
            except Exception as dump_err:
                _LOGGER.error("Failed to save page source during data timeout: %s", dump_err)
            if not recovery_attempted:
                _LOGGER.info("Quitting driver, waiting 10 seconds, and attempting to re-login due to timeout...")
                driver.quit()
                time.sleep(10)
                driver = validate_connection(config)
                recovery_attempted = True
                continue
            else:
                _LOGGER.error("Timeout occurred again after recovery attempt. Aborting this microinverter read.")
                break
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
