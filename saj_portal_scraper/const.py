# /workspaces/addons/saj_portal_scraper/const.py
"""Constants for the SAJ Portal Scraper Add-on."""

# Add-on domain/slug
DOMAIN = "saj_portal_scraper"

# --- Configuration Defaults ---
DEFAULT_USERNAME = ""
DEFAULT_PASSWORD = ""
DEFAULT_LOG_LEVEL = "info"
UPDATE_INTERVAL = 240  # Default update interval in seconds (4 minutes)

DEFAULT_MICROINVERTERS = {
    "YOUR_SN1": "Alias1",
    "YOUR_SN2": "Alias2"
}

# --- Paths (Inside Container) ---
GECKODRIVER_PATH = "/usr/bin/geckodriver"
# Path matches the one in the base Docker image
FIREFOX_BINARY_PATH = "/usr/bin/firefox-esr"

# --- SAJ Portal Specifics ---
# URLs for all possible SAJ portals
BASE_SAJ_URLS = [
    "https://eop.saj-electric.com",
    "https://iop.saj-electric.com",
    "https://op.saj-electric.cn"
]
DEFAULT_BASE_SAJ_URL = "https://iop.saj-electric.com"

def build_saj_urls(config):
    """
    Build the SAJ portal URLs dynamically based on the config['base_saj_url'] value.
    Returns a dict with LOGIN_URL, DASHBOARD_URL, DATA_URL_TEMPLATE.
    """
    base_url = config.get("base_saj_url", DEFAULT_BASE_SAJ_URL).rstrip("/")
    return {
        "LOGIN_URL": f"{base_url}/login",
        "DASHBOARD_URL": f"{base_url}/index",
        "DATA_URL_TEMPLATE": f"{base_url}/monitor/data-show-tab?deviceSn={{device_sn}}"
    }

USERNAME_SELECTOR = 'input[placeholder="Username/Email"]'
PASSWORD_SELECTOR = 'input[type="password"]'

# --- Data Extraction Mapping (Column Index on SAJ Portal Table) ---
COLUMN_MAPPING = {
    "ID": 0,
    "Update_time": 1,
    "Server_Time": 17,
    "Panel_Channel": 3,
    "Panel_Voltage": 4,
    "Panel_Current": 5,
    "Panel_Power": 6,
    "Phase": 8,
    "Voltage": 9,
    "Current": 10,
    "Frequency": 11,
    "Power": 12,
    "Energy_Today": 13,
    "Energy_This_Month": 14,
    "Energy_This_Year": 15,
    "Energy_Total": 16,
    "Strength_Signal": 18,
}

# --- Sensor Property Mappings (Used for MQTT Discovery) ---
UNIT_MAPPING = {
    "ID": None,
    "Update_time": None,
    "Server_Time": None,
    "Panel_Channel": None,
    "Panel_Voltage": "V",
    "Panel_Current": "A",
    "Panel_Power": "W",
    "Phase": None,
    "Voltage": "V",
    "Current": "A",
    "Frequency": "Hz",
    "Power": "W",
    "Energy_Today": "kWh",
    "Energy_This_Month": "kWh",
    "Energy_This_Year": "kWh",
    "Energy_Total": "kWh",
    "Strength_Signal": "dBm",
}

DEVICE_CLASS_MAPPING = {
    "ID": None,
    "Update_time": "timestamp", # Use string for device class
    "Server_Time": "timestamp", # Use string for device class
    "Panel_Channel": None,
    "Panel_Voltage": "voltage",
    "Panel_Current": "current",
    "Panel_Power": "power",
    "Phase": None,
    "Voltage": "voltage",
    "Current": "current",
    "Frequency": "frequency",
    "Power": "power",
    "Energy_Today": "energy",
    "Energy_This_Month": "energy",
    "Energy_This_Year": "energy",
    "Energy_Total": "energy",
    "Strength_Signal": "signal_strength",
}

STATE_CLASS_MAPPING = {
    "Energy_Today": "total_increasing",
    "Energy_This_Month": "total_increasing",
    "Energy_This_Year": "total_increasing",
    "Energy_Total": "total_increasing",
    "Voltage": "measurement",
    "Current": "measurement",
    "Power": "measurement",
    "Panel_Voltage": "measurement",
    "Panel_Current": "measurement",
    "Panel_Power": "measurement",
    "Frequency": "measurement",
    "Strength_Signal": "measurement",
}

# --- Inactivity Period Configuration ---
CONF_INACTIVITY_ENABLED = "inactivity_enabled"
CONF_INACTIVITY_START_TIME = "inactivity_start_time"
CONF_INACTIVITY_END_TIME = "inactivity_end_time"
DEFAULT_INACTIVITY_ENABLED = True
DEFAULT_INACTIVITY_START_TIME = "21:00"
DEFAULT_INACTIVITY_END_TIME = "05:30"

# --- Dynamic Interval Configuration ---
CONF_DATA_INACTIVITY_THRESHOLD = "data_inactivity_threshold_seconds"
CONF_EXTENDED_UPDATE_INTERVAL = "extended_update_interval_seconds"
DEFAULT_DATA_INACTIVITY_THRESHOLD = 1800  # Seconds (30 minutes)
DEFAULT_EXTENDED_UPDATE_INTERVAL = 3600   # Seconds (1 hour)

# --- MQTT Constants ---
MQTT_BASE_TOPIC = DOMAIN # Use the add-on slug as base topic
MQTT_AVAILABILITY_TOPIC = f"{MQTT_BASE_TOPIC}/bridge/state"
MQTT_PAYLOAD_ONLINE = "online"
MQTT_PAYLOAD_OFFLINE = "offline"
MQTT_DISCOVERY_PREFIX = "homeassistant" # Standard HA discovery prefix

# --- Plant/Peak Sensor Names ---
PLANT_DEVICE_NAME = "SAJ Solar Plant"
PEAK_POWER_TODAY_NAME = "Peak Power Today"

# --- Persistence ---
PERSISTENCE_FILE = "/data/peak_power_state.json" # Path inside container mapped to host
FIREFOX_PROFILE_PATH = "/data/firefox_profile"