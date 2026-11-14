import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from datetime import date, datetime, timezone, tzinfo
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests
from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

from const import (
    DEFAULT_BASE_SAJ_URL,
    DEFAULT_PASSWORD,
    DEFAULT_USERNAME,
    SAJ_API_PREFIX,
)

_LOGGER = logging.getLogger(__name__)

_APP_PROJECT_NAME = "elekeeper"
_CLIENT_ID = "esolar-monitor-admin"
_CLIENT_CODE = "organization"
_APP_SECRET = "b389a704-31f1-463d-8db7-435b18d1311d"
_LOGIN_AES_KEY = bytes.fromhex("ec1840a7c53cf0709eb784be480379b6")
_LANGUAGE = "en"
_THEME_COLOR = "dark"
_REQUEST_TIMEOUT = 30


class SajApiError(RuntimeError):
    """Raised when a SAJ API request cannot be completed."""


class SajApiAuthenticationError(SajApiError):
    """Raised when SAJ rejects the current login/session."""


class SajApiClient:
    def __init__(self, config: dict):
        self.base_url = config.get("base_saj_url", DEFAULT_BASE_SAJ_URL).rstrip("/")
        self.username = config.get("saj_username", DEFAULT_USERNAME)
        self.password = config.get("saj_password", DEFAULT_PASSWORD)
        self.session = requests.Session()
        self.token = ""
        self.org_code = ""

    @property
    def is_authenticated(self) -> bool:
        return bool(self.token)

    def close(self) -> None:
        self.token = ""
        self.session.close()

    def login(self) -> None:
        if not self.username or not self.password:
            raise ValueError("SAJ username and password must be configured.")

        common = self._common_payload()
        path = "/api/v2/sys/user/login"
        login_data = {
            "username": self.username.strip(),
            "password": self._encrypt_password(self.password),
            "loginType": 1,
            "lang": "zh",
            "rememberMe": False,
            "captchaUuid": "",
        }
        data = {**login_data, **common}
        body = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        headers = self._headers(common, "application/json;charset=utf-8")
        headers["X-Sign"] = self._make_signature(headers, json_body=body)
        response = self._post(path, headers, data=body.encode("utf-8"))
        payload = self._read_json_response(response, path, authentication_request=True)
        result = payload.get("data", payload)
        token = None
        if isinstance(result, dict):
            token = result.get("token") or result.get("access_token")
        if not token:
            raise SajApiAuthenticationError(
                "SAJ login succeeded without returning an access token. "
                "The portal may require a captcha, tenant selection, or another login step."
            )

        self.token = str(token).removeprefix("Bearer ").strip()
        self._load_org_code()
        _LOGGER.info("Authenticated with SAJ API.")

    def fetch_raw_data(self, device_sn: str, query_date: date) -> list[dict]:
        day = query_date.strftime("%Y-%m-%d")
        payload = {
            "deviceSn": device_sn,
            "deviceType": 0,
            "timeStr": day,
            "startTime": f"{day} 00:00:00",
            "endTime": f"{day} 23:59:59",
            "pageNo": 1,
            "pageSize": 10,
        }
        response = self._post_json("/api/v2/monitor/deviceData/findRawdataPageList", payload)
        result = response.get("data")
        if not isinstance(result, dict):
            raise SajApiError("SAJ raw-data response did not contain a data object.")
        rows = result.get("list")
        if not isinstance(rows, list):
            raise SajApiError("SAJ raw-data response did not contain a row list.")
        return [row for row in rows if isinstance(row, dict)]

    def _load_org_code(self) -> None:
        response = self._post_json("/api/v2/sys/user/getInfo", {})
        data = response.get("data")
        if isinstance(data, dict) and isinstance(data.get("data"), dict):
            data = data["data"]
        user_info = data.get("userInfo") if isinstance(data, dict) else None
        if not isinstance(user_info, dict):
            user_info = data if isinstance(data, dict) else {}
        org_code = user_info.get("orgCode")
        if not org_code and isinstance(data, dict):
            org_code = data.get("orgCode")
        if org_code:
            self.org_code = str(org_code)
        else:
            _LOGGER.warning(
                "SAJ user profile did not provide an organization code; "
                "subsequent API requests may be rejected."
            )

    def _post_json(self, path: str, payload: dict) -> dict:
        if not self.token:
            raise SajApiAuthenticationError("SAJ API session is not authenticated.")

        common = self._common_payload()
        data = {**payload, **common}
        body = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        headers = self._headers(common, "application/json;charset=utf-8")
        headers["Authorization"] = f"Bearer {self.token}"
        headers["X-Sign"] = self._make_signature(headers, json_body=body)
        response = self._post(path, headers, data=body.encode("utf-8"))
        return self._read_json_response(response, path)

    def _common_payload(self) -> dict:
        timestamp = int(time.time() * 1000)
        common = {
            "appProjectName": _APP_PROJECT_NAME,
            "clientDate": date.today().strftime("%Y-%m-%d"),
            "lang": _LANGUAGE,
            "timeStamp": timestamp,
            "clientId": _CLIENT_ID,
            "clientCode": _CLIENT_CODE,
            "themeColor": _THEME_COLOR,
        }
        if self.org_code:
            common["orgCode"] = self.org_code
        return common

    def _headers(self, common: dict, content_type: str) -> dict:
        headers = {
            "Content-Type": content_type,
            "Content-Language": "zh_CN",
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
            "lang": _LANGUAGE,
            "rdp-origin": "pc",
            "vue-version": "3",
            "Accept-Language": _LANGUAGE,
            "X-Trace-Id": secrets.token_hex(8),
            "X-Client-Code": _CLIENT_CODE,
            "X-App-Project-Name": _APP_PROJECT_NAME,
            "X-Client-Date": common["clientDate"],
            "X-Lang": _LANGUAGE,
            "X-Timestamp": str(common["timeStamp"]),
            "X-Theme-Color": _THEME_COLOR,
        }
        if self.org_code:
            headers["X-Org-Code"] = self.org_code
        return headers

    def _make_signature(
        self,
        headers: dict,
        query_params: dict | None = None,
        form_params: dict | None = None,
        json_body: str | None = None,
    ) -> str:
        values = {}
        excluded_headers = {"x-timestamp", "x-trace-id", "x-sign", "x-sign-enable"}
        for name, value in headers.items():
            normalized = name.lower()
            if normalized.startswith("x-") and normalized not in excluded_headers and value not in (None, ""):
                values[normalized[2:]] = str(value)

        for params in (query_params, form_params):
            if params:
                values.update({key: str(value) for key, value in params.items() if value is not None})
        if json_body is not None:
            values["body"] = hashlib.md5(json_body.encode("utf-8")).hexdigest()

        canonical = "&".join(f"{key}={values[key]}" for key in sorted(values))
        canonical += f"&nonce={headers['X-Trace-Id']}&timestamp={headers['X-Timestamp']}"
        return hmac.new(
            _APP_SECRET.encode("utf-8"),
            canonical.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    @staticmethod
    def _encrypt_password(password: str) -> str:
        encrypted = AES.new(_LOGIN_AES_KEY, AES.MODE_ECB).encrypt(
            pad(password.encode("utf-8"), AES.block_size)
        )
        return encrypted.hex()

    def _post(self, path: str, headers: dict, **kwargs) -> requests.Response:
        url = urljoin(f"{self.base_url}{SAJ_API_PREFIX}/", path.lstrip("/"))
        try:
            response = self.session.post(url, headers=headers, timeout=_REQUEST_TIMEOUT, **kwargs)
        except requests.RequestException as err:
            raise SajApiError(f"SAJ API request failed for {path}: {err.__class__.__name__}.") from err
        return response

    @staticmethod
    def _read_json_response(
        response: requests.Response,
        path: str,
        authentication_request: bool = False,
    ) -> dict:
        endpoint = path
        response_url = getattr(response, "url", None)
        if response_url:
            parsed_url = urlsplit(response_url)
            if parsed_url.netloc:
                endpoint = f"{parsed_url.scheme}://{parsed_url.netloc}{parsed_url.path}"
        if response.status_code in (401, 403):
            raise SajApiAuthenticationError(
                f"SAJ rejected the session for {endpoint} (HTTP {response.status_code})."
            )
        if not response.ok:
            error_type = SajApiAuthenticationError if authentication_request else SajApiError
            raise error_type(f"SAJ API request failed for {endpoint} (HTTP {response.status_code}).")
        try:
            payload = response.json()
        except requests.exceptions.JSONDecodeError as err:
            raise SajApiError(f"SAJ API returned invalid JSON for {path}.") from err
        if not isinstance(payload, dict):
            raise SajApiError(f"SAJ API returned an unexpected response for {path}.")

        error_code = payload.get("errCode", payload.get("code"))
        if error_code not in (None, 0, "0", 200, "200"):
            message = payload.get("errMsg") or payload.get("msg") or "request rejected"
            error_type = SajApiAuthenticationError if authentication_request else SajApiError
            raise error_type(f"SAJ API rejected {path}: {message} (code {error_code}).")
        if payload.get("connOk") is False:
            message = payload.get("errMsg") or payload.get("msg") or "connection rejected"
            error_type = SajApiAuthenticationError if authentication_request else SajApiError
            raise error_type(f"SAJ API rejected {path}: {message}.")
        return payload

def validate_connection(config: dict) -> SajApiClient:
    """Create an authenticated API session without starting a browser."""
    client = SajApiClient(config)
    try:
        client.login()
    except Exception:
        client.close()
        raise
    return client


def _configured_microinverters(config: dict) -> dict[str, str]:
    value = config.get("microinverters", "")
    if isinstance(value, dict):
        return {str(sn).strip(): str(alias).strip() for sn, alias in value.items() if str(sn).strip()}
    result = {}
    for pair in str(value).split(","):
        if ":" not in pair:
            continue
        device_sn, alias = pair.split(":", 1)
        device_sn, alias = device_sn.strip(), alias.strip()
        if device_sn and alias:
            result[device_sn] = alias
    return result


def _to_utc_iso(raw_value: object, local_timezone: tzinfo) -> str | None:
    if not raw_value:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw_value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=local_timezone)
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _local_timezone() -> tzinfo:
    local_tz_name = os.environ.get("TZ", "UTC")
    if local_tz_name in ("UTC", "Etc/UTC", "Z"):
        return timezone.utc
    try:
        return ZoneInfo(local_tz_name)
    except ZoneInfoNotFoundError:
        _LOGGER.warning("Timezone '%s' is unavailable; interpreting SAJ timestamps as UTC.", local_tz_name)
        return timezone.utc


def _display_value(row: dict, value_key: str, string_key: str | None = None) -> str:
    value = row.get(string_key) if string_key else None
    if value is None:
        value = row.get(value_key)
    return "" if value is None else str(value)


def _row_to_sensor_data(
    row: dict,
    device_sn: str,
    device_alias: str,
    config: dict,
) -> dict | None:
    local_tz = _local_timezone()
    update_time = _to_utc_iso(row.get("datetimeStr") or row.get("datetime"), local_tz)
    if not update_time:
        _LOGGER.warning("Skipping SAJ data row for %s because its update time is invalid.", device_alias)
        return None

    data = {
        "ID": row.get("index", device_sn),
        "Update_time": update_time,
        "Phase": "",
        "Voltage": "",
        "Current": "",
        "Frequency": "",
        "Power": "",
        "Energy_Today": _display_value(row, "todayPVEnergy", "todayPVEnergyStr"),
        "Energy_This_Month": _display_value(row, "monthPVEnergy", "monthPVEnergyStr"),
        "Energy_This_Year": _display_value(row, "yearPVEnergy", "yearPVEnergyStr"),
        "Energy_Total": _display_value(row, "totalPVEnergy", "totalPVEnergyStr"),
        "Strength_Signal": row.get("moduleSignal", ""),
        "Alias": device_alias,
    }

    channels = row.get("pvChannelList") or []
    if not channels:
        _LOGGER.warning("SAJ returned no PV channel data for %s.", device_alias)
    for index, channel in enumerate(channels, start=1):
        channel_name = f"PV{index}"
        data[f"{channel_name}_Panel_Voltage"] = _display_value(channel, "pvVolt", "pvVoltStr")
        data[f"{channel_name}_Panel_Current"] = _display_value(channel, "pvCurr", "pvCurrStr")
        data[f"{channel_name}_Panel_Power"] = _display_value(channel, "pvPower", "pvPowerStr")

        if config.get("string_current_enabled", False):
            for string_index in range(1, 5):
                data[f"{channel_name}_String_Current_{string_index}"] = _display_value(
                    channel,
                    f"pvStrCurr{string_index}",
                    f"pvStrCurr{string_index}Str",
                )

    grid_rows = row.get("gridList") or []
    data["Phase"] = "\n".join(f"L{index}" for index, _ in enumerate(grid_rows, start=1))
    for attribute, field in (
        ("Voltage", "gridVolt"),
        ("Current", "gridCurr"),
        ("Frequency", "gridFreq"),
        ("Power", "gridPowerwatt"),
    ):
        data[attribute] = "\n".join(
            str(grid.get(field) if grid.get(field) is not None else "")
            for grid in grid_rows
            if isinstance(grid, dict)
        )
    return data


def _fetch_data_sync(
    config: dict,
    client: SajApiClient,
    force_relogin: bool = False,
) -> tuple[dict, SajApiClient]:
    """Fetch the latest operational-data row for each configured inverter."""
    if force_relogin or not client.is_authenticated:
        _LOGGER.info("Refreshing SAJ API authentication.")
        client.login()

    microinverters = _configured_microinverters(config)
    if not microinverters:
        raise ValueError("No valid microinverters are configured.")

    fetched_data = {}
    query_date = date.today()
    for device_sn, device_alias in microinverters.items():
        rows = None
        for attempt in range(2):
            try:
                rows = client.fetch_raw_data(device_sn, query_date)
                break
            except SajApiAuthenticationError:
                if attempt:
                    raise
                _LOGGER.info("SAJ API session expired; logging in again.")
                client.login()

        if not rows:
            _LOGGER.warning("SAJ returned no operational data for %s (%s).", device_alias, device_sn)
            continue

        converted = [
            (converted_row["Update_time"], converted_row)
            for row in rows
            if (converted_row := _row_to_sensor_data(row, device_sn, device_alias, config)) is not None
        ]
        if converted:
            fetched_data[device_sn] = max(converted, key=lambda item: item[0])[1]

    if not fetched_data:
        _LOGGER.warning("No valid operational data was returned for any configured SAJ inverter.")
    _LOGGER.info("Finished fetching data for all configured SAJ inverters.")
    return fetched_data, client
