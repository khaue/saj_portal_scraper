# /workspaces/addons/saj_portal_scraper/mqtt_utils.py
import paho.mqtt.client as mqtt
import json
import logging
import os
import time
from datetime import date

from const import (
    DOMAIN,
    MQTT_BASE_TOPIC,
    MQTT_AVAILABILITY_TOPIC,
    MQTT_PAYLOAD_ONLINE,
    MQTT_PAYLOAD_OFFLINE,
    MQTT_DISCOVERY_PREFIX,
    PLANT_DEVICE_NAME,
    PEAK_POWER_TODAY_NAME,
    UNIT_MAPPING,
    DEVICE_CLASS_MAPPING,
    STATE_CLASS_MAPPING
)

_LOGGER = logging.getLogger(__name__)

_DISCOVERED_ENTITIES = set() # Keep track of discovered entities to avoid re-publishing

def get_mqtt_config(addon_config):
    """Gets MQTT connection details from add-on config or Supervisor."""
    host = addon_config.get("mqtt_host")
    port = addon_config.get("mqtt_port")
    username = addon_config.get("mqtt_username")
    password = addon_config.get("mqtt_password")

    # If host is not manually configured, try to get from Supervisor service discovery
    if not host:
        try:
            _LOGGER.info("MQTT host not found in config, trying Supervisor service discovery...")
            host = os.getenv("MQTT_BROKER")
            port = int(os.getenv("MQTT_PORT", 1883))
            username = os.getenv("MQTT_USERNAME")
            password = os.getenv("MQTT_PASSWORD")
            if host:
                _LOGGER.info("Using Supervisor-provided MQTT configuration.")
            else:
                # This case might happen if the MQTT service is not available/linked
                _LOGGER.error("MQTT host not found in Supervisor-provided MQTT configuration.")
        except (json.JSONDecodeError, KeyError, TypeError, FileNotFoundError) as e:
            _LOGGER.warning(f"Could not parse Supervisor MQTT service info: {e}. Manual config needed or MQTT might fail.")
            # Continue, manual config might still work
    elif not host:
         # Explicitly check if host is still missing after trying Supervisor
         _LOGGER.error("MQTT host not configured and Supervisor service unavailable.")
         return None

    if not port:
        port = 1883 # Default MQTT port

    if not host:
         _LOGGER.error("MQTT host could not be determined.")
         return None

    return {
        "host": host,
        "port": int(port),
        "username": username,
        "password": password
    }


def connect_mqtt(client_id: str, config: dict) -> mqtt.Client | None:
    """Connects to the MQTT broker."""
    mqtt_config = get_mqtt_config(config)
    if not mqtt_config:
        return None

    client = mqtt.Client(client_id=client_id)
    # Set Last Will and Testament (LWT)
    client.will_set(MQTT_AVAILABILITY_TOPIC, payload=MQTT_PAYLOAD_OFFLINE, qos=1, retain=True)

    # Set username/password if provided
    if mqtt_config.get("username"):
        client.username_pw_set(mqtt_config["username"], mqtt_config.get("password"))

    try:
        _LOGGER.info(f"Connecting to MQTT broker at {mqtt_config['host']}:{mqtt_config['port']}...")
        client.connect(mqtt_config['host'], mqtt_config['port'], 60)
        client.loop_start()
        time.sleep(1) # Short pause to allow connection establishment
        if client.is_connected():
             _LOGGER.info("MQTT connected successfully.")
             # Publish online status upon successful connection
             client.publish(MQTT_AVAILABILITY_TOPIC, payload=MQTT_PAYLOAD_ONLINE, qos=1, retain=True)
             return client
        else:
             _LOGGER.error("MQTT connection attempt failed (client not connected after connect call).")
             client.loop_stop()
             return None
    except Exception as e:
        _LOGGER.error(f"MQTT connection error: {e}", exc_info=True)
        # Ensure loop_stop is called even if connect fails
        if client: client.loop_stop()
        return None

def publish_discovery(client: mqtt.Client, device_data: dict, plant_data: dict, peak_power_state: dict, addon_version: str):
    """Publishes MQTT discovery messages for all sensors."""
    if not client or not client.is_connected():
        _LOGGER.warning("MQTT client not connected, skipping discovery.")
        return

    # Define Plant Device Info ONCE
    plant_sn = "plant_aggregator"
    # Unique identifier string for the aggregated plant device
    plant_unique_identifier = f"{DOMAIN}_{plant_sn}"
    plant_device_info = {
        "identifiers": [plant_unique_identifier], # List containing ONE identifier string
        "name": PLANT_DEVICE_NAME,
        "manufacturer": "SAJ",
        "model": "Aggregated Plant Data",
        "sw_version": addon_version,
    }

    # --- Individual Device Discovery ---
    for sn, data in device_data.items():
        alias = data.get("Alias", sn)
        # Unique identifier string for the individual microinverter device
        device_unique_identifier = f"{DOMAIN}_{sn}"
        device_info = {
            "identifiers": [device_unique_identifier], # List containing ONE identifier string
            "name": alias,
            "manufacturer": "SAJ",
            "model": "Microinverter SAJ M2", # Assuming M2 model, adjust if needed
            "sw_version": addon_version,
            # Link this device to the parent plant device
            "via_device": plant_unique_identifier,
            "serial_number": sn,
        }
        for attribute, value in data.items():
            if attribute == "Alias": continue # Alias is part of device_info, not a sensor

            attribute_slug = attribute.lower().replace(" ", "_").replace("-", "_").replace(".", "_")
            unique_id = f"saj_{sn}_{attribute_slug}"

            # Avoid re-publishing discovery for entities already discovered in this session
            if unique_id in _DISCOVERED_ENTITIES: continue

            unit = None
            device_class = None
            state_class = None
            base_attribute = attribute # Default to the full attribute name
            identified_panel_attribute = False

            panel_suffixes = {
                "_Panel_Voltage": "Panel_Voltage",
                "_Panel_Current": "Panel_Current",
                "_Panel_Power": "Panel_Power",
            }

            for suffix, base_name in panel_suffixes.items():
                if attribute.endswith(suffix):
                    base_attribute = base_name # Use the generic name for mapping lookup
                    identified_panel_attribute = True
                    break # Found the panel type

            # Look up mappings using the determined base_attribute
            unit = UNIT_MAPPING.get(base_attribute)
            device_class = DEVICE_CLASS_MAPPING.get(base_attribute)
            state_class = STATE_CLASS_MAPPING.get(base_attribute)
            # --- MODIFICATION END ---

            icon = None # Reset icon for each attribute

            # Skip attributes that don't map to a sensor property (unless it's a timestamp)
            # This check now works correctly even if the panel prefix wasn't "PV"
            if unit is None and device_class is None and attribute not in ["Update_time", "Server_Time"]:
                 _LOGGER.debug(f"Skipping discovery for {unique_id} due to missing unit and device_class (Base Attribute: {base_attribute}).")
                 continue

            # Ensure timestamps have the correct device class (overrides mapping if needed)
            if attribute in ["Update_time", "Server_Time"]:
                 device_class = "timestamp"

            discovery_topic = f"{MQTT_DISCOVERY_PREFIX}/sensor/{unique_id}/config"
            state_topic = f"{MQTT_BASE_TOPIC}/{sn}/state"

            # Use the ORIGINAL attribute name for the sensor name and value template
            payload = {
                "name": f"{attribute.replace('_', ' ').title()}",
                "unique_id": unique_id,
                "state_topic": state_topic,
                "value_template": f"{{{{ value_json.{attribute} | default('unknown') }}}}", # Uses original attribute
                "device": device_info,
                "availability_topic": MQTT_AVAILABILITY_TOPIC,
                "payload_available": MQTT_PAYLOAD_ONLINE,
                "payload_not_available": MQTT_PAYLOAD_OFFLINE,
                "json_attributes_topic": state_topic,
                "json_attributes_template": "{{ value_json | tojson }}",
            }
            # Add properties based on mappings found using base_attribute
            if unit: payload["unit_of_measurement"] = unit
            if device_class: payload["device_class"] = device_class
            if state_class: payload["state_class"] = state_class
            if icon: payload["icon"] = icon

            _LOGGER.debug(f"Discovery Check: For unique_id '{unique_id}', defining state_topic as: '{payload['state_topic']}'")

            try:
                _LOGGER.debug(f"Attempting to publish discovery for {unique_id} (Base Attribute: {base_attribute}) with payload: {json.dumps(payload)}")
                client.publish(discovery_topic, json.dumps(payload), qos=1, retain=True)
                _DISCOVERED_ENTITIES.add(unique_id)
                _LOGGER.debug(f"Published discovery for: {unique_id}")
            except Exception as e:
                 _LOGGER.error(f"Failed to publish discovery for {unique_id}: {e}")

    # --- Aggregated Plant Discovery ---
    # Reuses plant_device_info defined earlier
    plant_state_topic = f"{MQTT_BASE_TOPIC}/plant/state"
    for attribute, value in plant_data.items():
        attribute_slug = attribute.lower().replace(" ", "_").replace("-", "_").replace(".", "_")
        unique_id = f"saj_plant_{attribute_slug}"

        if unique_id in _DISCOVERED_ENTITIES: continue

        unit = UNIT_MAPPING.get(attribute)
        device_class = DEVICE_CLASS_MAPPING.get(attribute)
        state_class = STATE_CLASS_MAPPING.get(attribute)
        icon = None

        # Skip non-sensor attributes, except timestamps
        if unit is None and device_class is None and attribute not in ["Update_time", "Server_Time"]: continue
        if attribute in ["Update_time", "Server_Time"]: device_class = "timestamp"

        discovery_topic = f"{MQTT_DISCOVERY_PREFIX}/sensor/{unique_id}/config"
        payload = {
            "name": f"{attribute.replace('_', ' ').title()}",
            "unique_id": unique_id,
            "state_topic": plant_state_topic,
            "value_template": f"{{{{ value_json.{attribute} | default('unknown') }}}}",
            "device": plant_device_info,
            "availability_topic": MQTT_AVAILABILITY_TOPIC,
            "payload_available": MQTT_PAYLOAD_ONLINE,
            "payload_not_available": MQTT_PAYLOAD_OFFLINE,
            "json_attributes_topic": plant_state_topic,
            "json_attributes_template": "{{ value_json | tojson }}",
        }
        if unit: payload["unit_of_measurement"] = unit
        if device_class: payload["device_class"] = device_class
        if state_class: payload["state_class"] = state_class
        if icon: payload["icon"] = icon

        try:
            client.publish(discovery_topic, json.dumps(payload), qos=1, retain=True)
            _DISCOVERED_ENTITIES.add(unique_id)
            _LOGGER.debug(f"Published discovery for: {unique_id}")
        except Exception as e:
            _LOGGER.error(f"Failed to publish discovery for {unique_id}: {e}")


    # --- Peak Power Discovery ---
    # Reuses plant_device_info defined earlier
    peak_unique_id = "saj_plant_peak_power_today"
    if peak_unique_id not in _DISCOVERED_ENTITIES:
        peak_discovery_topic = f"{MQTT_DISCOVERY_PREFIX}/sensor/{peak_unique_id}/config"
        peak_state_topic = f"{MQTT_BASE_TOPIC}/plant/peak_power_today"
        peak_payload = {
            "name": PEAK_POWER_TODAY_NAME,
            "unique_id": peak_unique_id,
            "state_topic": peak_state_topic,
            "value_template": "{{ value_json.value | default(0) }}",
            "device": plant_device_info,
            "availability_topic": MQTT_AVAILABILITY_TOPIC,
            "payload_available": MQTT_PAYLOAD_ONLINE,
            "payload_not_available": MQTT_PAYLOAD_OFFLINE,
            "unit_of_measurement": UNIT_MAPPING.get("Power"),
            "device_class": DEVICE_CLASS_MAPPING.get("Power"),
            "state_class": STATE_CLASS_MAPPING.get("Power"),
            "icon": "mdi:weather-sunny-alert",
            "json_attributes_topic": peak_state_topic,
            # Extract last_reset_date as an attribute
            "json_attributes_template": "{{ {'last_reset_date': value_json.last_reset_date} | tojson if value_json is mapping else None }}",
        }
        try:
            client.publish(peak_discovery_topic, json.dumps(peak_payload), qos=1, retain=True)
            _DISCOVERED_ENTITIES.add(peak_unique_id)
            _LOGGER.debug(f"Published discovery for: {peak_unique_id}")
        except Exception as e:
            _LOGGER.error(f"Failed to publish discovery for {peak_unique_id}: {e}")

def publish_state(client: mqtt.Client, device_data: dict, plant_data: dict, peak_power: float, last_reset_date: date | None):
    """Publishes the current state data to MQTT topics."""
    if not client or not client.is_connected():
        _LOGGER.warning("MQTT client not connected, skipping state publish.")
        return

    # Publish individual device states
    for sn, data in device_data.items():
        state_topic = f"{MQTT_BASE_TOPIC}/{sn}/state"
        try:
            update_time_val = data.get("Update_time", "N/A") # Use .get() with default for logging
            server_time_val = data.get("Server_Time", "N/A")
            _LOGGER.debug(f"State Publish Check: Publishing state for device '{sn}' TO topic: '{state_topic}'. Update_time='{update_time_val}', Server_Time='{server_time_val}'")

            # --- ADDED LOGGING ---
            try:
                json_payload = json.dumps(data)
                _LOGGER.debug(f"State Publish Payload for {sn} TO {state_topic}: {json_payload}")
            except (TypeError, ValueError) as json_err:
                _LOGGER.error(f"Error serializing state data for {sn} to JSON: {json_err}. Data: {data}")
                continue # Skip publishing if JSON fails
            # --- END ADDED LOGGING ---

            # Publish the full data dictionary as JSON
            client.publish(state_topic, json_payload, qos=1, retain=False) # Use the generated json_payload

        except Exception as e:
            # Catch errors during the publish call itself
            _LOGGER.error(f"Failed to publish state for device {sn} TO {state_topic}: {e}")

    # Publish aggregated plant state
    plant_state_topic = f"{MQTT_BASE_TOPIC}/plant/state"
    try:
        plant_update_time = plant_data.get("Update_time", "N/A")
        plant_server_time = plant_data.get("Server_Time", "N/A")
        _LOGGER.debug(f"Publishing aggregated plant state to {plant_state_topic}. Update_time='{plant_update_time}', Server_Time='{plant_server_time}'")

        # --- ADDED LOGGING ---
        try:
            json_payload_plant = json.dumps(plant_data)
            _LOGGER.debug(f"State Publish Payload for Plant TO {plant_state_topic}: {json_payload_plant}")
        except (TypeError, ValueError) as json_err:
            _LOGGER.error(f"Error serializing plant state data to JSON: {json_err}. Data: {plant_data}")
            json_payload_plant = None # Prevent publishing if JSON fails
        # --- END ADDED LOGGING ---

        if json_payload_plant: # Only publish if JSON serialization succeeded
            client.publish(plant_state_topic, json_payload_plant, qos=1, retain=False)

    except Exception as e:
        # Catch errors during the publish call itself
        _LOGGER.error(f"Failed to publish plant state TO {plant_state_topic}: {e}")

    # Publish peak power state
    peak_state_topic = f"{MQTT_BASE_TOPIC}/plant/peak_power_today"
    peak_payload_dict = { # Renamed variable for clarity
        "value": peak_power,
        "last_reset_date": last_reset_date.isoformat() if last_reset_date else None
    }
    try:

        try:
            json_payload_peak = json.dumps(peak_payload_dict)
            _LOGGER.debug(f"State Publish Payload for Peak Power TO {peak_state_topic}: {json_payload_peak}")
        except (TypeError, ValueError) as json_err:
            _LOGGER.error(f"Error serializing peak power state data to JSON: {json_err}. Data: {peak_payload_dict}")
            json_payload_peak = None # Prevent publishing if JSON fails


        if json_payload_peak: # Only publish if JSON serialization succeeded
            # Retain peak power state so it's available on HA restart
            client.publish(peak_state_topic, json_payload_peak, qos=1, retain=True)

    except Exception as e:
        # Catch errors during the publish call itself
        _LOGGER.error(f"Failed to publish peak power state TO {peak_state_topic}: {e}")

