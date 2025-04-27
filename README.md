# SAJ Portal Web Crawler - Home Assistant Add-on

[![GitHub Release][releases-shield]][releases]
[![License][license-shield]][license]
[![Maintainer][maintainer-shield]][maintainer]

<!-- Optional Badges -->
<!-- [![GitHub Activity][commits-shield]][commits] -->
<!-- [![GitHub Issues][issues-shield]][issues] -->

This Home Assistant add-on fetches data from SAJ microinverters directly from the eSolar Portal (SAJ) using web scraping and publishes the information via MQTT, allowing integration with Home Assistant through automatic discovery (MQTT Discovery).

**This add-on uses web scraping, which may be unstable if the layout or structure of the SAJ Portal website changes. Use at your own risk.**

## Prerequisites

1.  **Home Assistant:** A working Home Assistant installation.
2.  **MQTT Broker:** An MQTT broker configured and running, accessible by Home Assistant. The official [Mosquitto broker](https://github.com/home-assistant/add-ons/blob/master/mosquitto/README.md) add-on is recommended.
3.  **MQTT Credentials:** If your MQTT broker requires authentication, you will need the username and password. If using the Mosquitto add-on with the default integration, the Supervisor can provide the credentials automatically.
4.  **SAJ Portal Credentials:** Your username and password to access the [eSolar Portal (SAJ)](https://www.esolarportal.com/).

## Installation

1.  **Add Repository:**
    - Go to your Home Assistant interface.
    - Navigate to **Settings** > **Add-ons** > **Add-on Store**.
    - Click the three-dots menu in the top right corner and select **Repositories**.
    - Paste the URL of this Git repository (`https://github.com/khaue/saj_portal_web_crawler`) into the field and click **Add**.
    - Close the repositories window.
2.  **Install Add-on:**
    - Refresh the Add-on Store page (you might need to reload with Ctrl+Shift+R or Cmd+Shift+R).
    - Look for "SAJ Portal Web Crawler" in the local repositories section (the name defined in `repository.yaml`).
    - Click on the add-on and then **Install**. Wait for the installation to complete.

## Configuration

After installation, go to the **Configuration** tab of the add-on to set the required options:

| Option                              | Description                                                                                                                          | Type     | Required | Default                                                   |
| :---------------------------------- | :----------------------------------------------------------------------------------------------------------------------------------- | :------- | :------- | :-------------------------------------------------------- |
| `log_level`                         | Controls the detail level of the add-on's logs. Use `debug` for troubleshooting.                                                     | List     | No       | `info`                                                    |
| `timezone`                          | The timezone name where the add-on is running (e.g., `America/Sao_Paulo`, `Europe/Lisbon`). Crucial for correct time interpretation. | String   | No       | `Etc/UTC`                                                 |
| `saj_username`                      | Your username for the eSolar Portal (SAJ).                                                                                           | String   | **Yes**  | `""`                                                      |
| `saj_password`                      | Your password for the eSolar Portal (SAJ).                                                                                           | Password | **Yes**  | `""`                                                      |
| `microinverters`                    | List of microinverters in the format `SN1:Alias1,SN2:Alias2,...`. The Alias is used for naming devices in Home Assistant.            | String   | **Yes**  | `"SN12345:GarageInverter,SN67890:RoofInverter"` (Example) |
| `inactivity_enabled`                | Enables/disables the nightly inactivity period, during which the add-on will not fetch data.                                         | Boolean  | No       | `true`                                                    |
| `inactivity_start_time`             | Time (format `HH:MM`) when the inactivity period starts.                                                                             | String   | No       | `"21:00"`                                                 |
| `inactivity_end_time`               | Time (format `HH:MM`) when the inactivity period ends.                                                                               | String   | No       | `"05:30"`                                                 |
| `update_interval_seconds`           | Normal interval (in seconds) between data fetches. Minimum 60.                                                                       | Integer  | No       | `240` (4 minutes)                                         |
| `data_inactivity_threshold_seconds` | Time (in seconds) without receiving new data (`Update_time`) from _any_ microinverter before switching to the extended interval.     | Integer  | No       | `1800` (30 minutes)                                       |
| `extended_update_interval_seconds`  | Interval (in seconds) between fetches when the add-on detects no new data (likely end of day). Minimum 60.                           | Integer  | No       | `3600` (1 hour)                                           |
| `mqtt_host`                         | Address of your MQTT broker.                                                                                                         | String   | No       | `"core-mosquitto"` (Default for Mosquitto add-on)         |
| `mqtt_port`                         | Port of your MQTT broker.                                                                                                            | Integer  | No       | `1883`                                                    |
| `mqtt_username`                     | Username for the MQTT broker (leave blank if using Supervisor/Mosquitto credentials).                                                | String   | No       | `""`                                                      |
| `mqtt_password`                     | Password for the MQTT broker (leave blank if using Supervisor/Mosquitto credentials).                                                | Password | No       | `""`                                                      |

**Important about Timezone:** The `timezone` setting is essential for the add-on to correctly interpret the times read from the SAJ portal (`Update_time`) and convert them to UTC before sending them to Home Assistant. Ensure you use a valid timezone name from the TZ database (e.g., `America/Sao_Paulo`, `Europe/Lisbon`, `Australia/Sydney`).

**Important about Microinverters:** The format is crucial: `SERIAL_NUMBER:FRIENDLY_NAME`, separated by commas, with no extra spaces before or after the comma or colon. E.g., `SN123:GarageInverter,SN456:RoofInverter`.

## Usage

1.  After configuring the options, start the add-on.
2.  Check the **Log** tab of the add-on to ensure it's connecting to MQTT and the SAJ portal without errors.
3.  If the MQTT connection is successful, the add-on will send discovery messages to Home Assistant.
4.  New devices (one for the aggregated plant and one for each configured microinverter) and their entities (sensors for energy, power, voltage, etc.) should automatically appear under **Settings** > **Devices & Services** > **Entities** (filtering by the MQTT integration).

## Debugging

- **Check Logs:** The first step in troubleshooting is to check the add-on's **Log** tab.
- **Increase Log Level:** Change the `log_level` option to `debug` in the add-on configuration and restart it to get more detailed information.
- **Remote Debugging (Optional):** If port `5678` is mapped in `config.yaml` and `debugpy` is in `requirements.txt`, you can attach a Python debugger (like VS Code's) to that port for step-by-step debugging.

## Common Troubleshooting

- **Login Error:** Check your SAJ credentials. The SAJ Portal might have implemented CAPTCHAs or other protections that could break login via scraping. Check the logs for Timeout errors or elements not found.
- **MQTT Connection Error:** Check the `mqtt_host`, `mqtt_port`, `mqtt_username`, `mqtt_password` settings. Ensure the broker is running and accessible. Check the Mosquitto broker logs as well.
- **Entities Not Appearing:** Check if the add-on is running, connected to MQTT, and if there are no errors in the logs. Verify that the MQTT integration in Home Assistant is configured correctly.
- **Incorrect Times:** Check the `timezone` setting in the add-on. Ensure it matches your actual timezone and the name is correct. Check the add-on logs (in debug mode) to see how times are being processed and converted to UTC.

## License

Distributed under the MIT License. See `LICENSE` for more information

## Contributions and Support

Issues and Pull Requests are welcome. Please open an issue in this repository to report bugs or suggest improvements. Contact: Khaue Rezende Rodrigues <khaue.rodrigues@gmail.com>

[commits-shield]: https://img.shields.io/github/commit-activity/y/khaue/saj_portal_web_crawler.svg?style=for-the-badge
[commits]: https://github.com/khaue/saj_portal_web_crawler/commits/main
[issues-shield]: https://img.shields.io/github/issues/khaue/saj_portal_web_crawler.svg?style=for-the-badge
[issues]: https://github.com/khaue/saj_portal_web_crawler/issues
[license-shield]: https://img.shields.io/github/license/khaue/saj_portal_web_crawler.svg?style=for-the-badge
[license]: LICENSE
[maintainer-shield]: https://img.shields.io/badge/maintainer-Khaue%20Rezende%20Rodrigues-blue.svg?style=for-the-badge
[maintainer]: https://github.com/khaue
[releases-shield]: https://img.shields.io/github/release/khaue/saj_portal_web_crawler.svg?style=for-the-badge
[releases]: https://github.com/khaue/saj_portal_web_crawler/releases
