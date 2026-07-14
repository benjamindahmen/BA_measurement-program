from __future__ import annotations

from typing import Any

import requests

from .config import CellulinkConfig
from .models import deep_first_existing, json_dumps, to_float, to_int


class CellulinkApiError(RuntimeError):
    pass


class CellulinkApiClient:
    api_prefix = "/api/v1"

    def __init__(self, config: CellulinkConfig, access_token: str, timeout_s: float = 5.0):
        self.config = config
        self.timeout_s = timeout_s
        self.session = requests.Session()
        self.session.verify = config.verify_tls
        self.session.headers.update(
            {
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/json",
            }
        )

    def get_profile_status(self) -> dict[str, Any]:
        return self.get_json(
            f"/cellular/modems/{self.config.modem_id}/profiles/{self.config.profile_id}/status"
        )

    def get_profile_configuration(self) -> dict[str, Any]:
        return self.get_json(
            f"/cellular/modems/{self.config.modem_id}/profiles/{self.config.profile_id}/configuration"
        )

    def get_modem_configuration(self) -> dict[str, Any]:
        return self.get_json(f"/cellular/modems/{self.config.modem_id}/configuration")

    def get_modem_information(self) -> dict[str, Any]:
        return self.get_json(f"/cellular/modems/{self.config.modem_id}/information")

    def get_cellular_status(self) -> dict[str, Any]:
        return self.get_json(f"/cellular/modems/{self.config.modem_id}/status")

    def get_gnss_status(self) -> dict[str, Any]:
        return self.get_json("/gnss/status")

    def get_gnss_information(self) -> dict[str, Any]:
        return self.get_json("/gnss")

    def disconnect_cellular_profile(self, path_template: str, method: str = "POST") -> None:
        self.request_action(method, path_template)

    def connect_cellular_profile(self, path_template: str, method: str = "POST") -> None:
        self.request_action(method, path_template)

    def request_action(self, method: str, path_template: str, payload: Any = None) -> dict[str, Any] | None:
        path = self._format_path(path_template)
        endpoint = self._endpoint(path)
        response = self.session.request(
            method.upper(),
            f"{self.config.base_url}{endpoint}",
            json=payload,
            timeout=self.timeout_s,
        )
        if response.status_code >= 400:
            raise CellulinkApiError(_http_error(f"API {method.upper()} request failed", response, endpoint))
        if not response.content:
            return None
        content_type = response.headers.get("Content-Type", "")
        if "json" not in content_type.lower():
            return None
        try:
            parsed = response.json()
        except ValueError as exc:
            raise CellulinkApiError(_http_error("API action response is not JSON", response, endpoint)) from exc
        return parsed if isinstance(parsed, dict) else None

    def get_json(self, path: str) -> dict[str, Any]:
        endpoint = self._endpoint(path)
        response = self.session.get(f"{self.config.base_url}{endpoint}", timeout=self.timeout_s)
        content_type = response.headers.get("Content-Type", "")
        if response.status_code >= 400:
            raise CellulinkApiError(_http_error("API request failed", response, endpoint))
        try:
            payload = response.json()
        except ValueError as exc:
            raise CellulinkApiError(_http_error("API response is not JSON", response, endpoint)) from exc
        if not isinstance(payload, dict):
            raise CellulinkApiError(
                f"API response JSON is not an object for {endpoint}: Content-Type={content_type}"
            )
        return payload

    def _endpoint(self, path: str) -> str:
        if path.startswith(self.api_prefix):
            return path
        return f"{self.api_prefix}/{path.lstrip('/')}"

    def _format_path(self, path_template: str) -> str:
        return path_template.format(
            modem_id=self.config.modem_id,
            profile_id=self.config.profile_id,
        )


def _http_error(prefix: str, response: requests.Response, endpoint: str) -> str:
    body = response.text[:500] if response.text is not None else ""
    return (
        f"{prefix} for {endpoint}: HTTP {response.status_code}, "
        f"Content-Type={response.headers.get('Content-Type', '')!r}, body[0:500]={body!r}"
    )


def extract_cellular_fields(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "cellular_active_sim_profile": _as_text(deep_first_existing(payload, ["active_sim_profile", "activeSimProfile", "simProfile"])),
        "cellular_cell_id": _as_text(deep_first_existing(payload, ["cell_id", "cellId", "cellid", "cid"])),
        "cellular_technology": _as_text(deep_first_existing(payload, ["technology", "accessTechnology", "rat", "networkType"])),
        "cellular_frequency_band": _as_text(deep_first_existing(payload, ["frequency_band", "frequencyBand", "band"])),
        "cellular_lac": _as_text(deep_first_existing(payload, ["lac", "location_area_code", "locationAreaCode", "tac"])),
        "cellular_mcc": _as_text(deep_first_existing(payload, ["mcc"])),
        "cellular_mnc": _as_text(deep_first_existing(payload, ["mnc"])),
        "cellular_packet_data_online": _as_text(deep_first_existing(payload, ["packet_data_online", "packetDataOnline", "online"])),
        "cellular_registration_status": _as_text(deep_first_existing(payload, ["registration_status", "registrationStatus", "networkRegistration"])),
        "cellular_signal_rating": _as_text(deep_first_existing(payload, ["signal_rating", "signalRating", "signalQuality", "rating"])),
        "cellular_rsrp": to_float(deep_first_existing(payload, ["rsrp", "RSRP"])),
        "cellular_rsrq": to_float(deep_first_existing(payload, ["rsrq", "RSRQ"])),
        "cellular_rssi": to_float(deep_first_existing(payload, ["rssi", "RSSI"])),
        "cellular_sinr": to_float(deep_first_existing(payload, ["sinr", "SINR", "snr", "SNR"])),
        "cellular_status_json": json_dumps(payload),
    }


def extract_cellulink_gnss_fields(status_payload: dict[str, Any] | None, info_payload: dict[str, Any] | None) -> dict[str, Any]:
    status_payload = status_payload or {}
    info_payload = info_payload or {}
    merged = [status_payload, info_payload]
    speed_mps = to_float(deep_first_existing(merged, ["speed_mps", "speedMps", "speed"]))
    speed_kmh = to_float(deep_first_existing(merged, ["speed_kmh", "speedKmh"]))
    if speed_kmh is None and speed_mps is not None:
        speed_kmh = speed_mps * 3.6
    return {
        "cellulink_gnss_status": _as_text(deep_first_existing(merged, ["status", "gnss_status", "gnssStatus", "fixStatus"])),
        "cellulink_gnss_time": _as_text(deep_first_existing(merged, ["time", "utc_time", "utcTime"])),
        "cellulink_gnss_date": _as_text(deep_first_existing(merged, ["date", "utc_date", "utcDate"])),
        "cellulink_gnss_latitude": to_float(deep_first_existing(merged, ["latitude", "lat"])),
        "cellulink_gnss_longitude": to_float(deep_first_existing(merged, ["longitude", "lon", "lng"])),
        "cellulink_gnss_speed_kmh": speed_kmh,
        "cellulink_gnss_speed_mps": speed_mps,
        "cellulink_gnss_altitude_m": to_float(deep_first_existing(merged, ["altitude_m", "altitude", "height"])),
        "cellulink_gnss_mode": _as_text(deep_first_existing(merged, ["mode", "fixMode"])),
        "cellulink_gnss_used_satellites": to_int(deep_first_existing(merged, ["used_satellites", "usedSatellites", "satellitesUsed"])),
        "cellulink_gnss_visible_satellites": to_int(deep_first_existing(merged, ["visible_satellites", "visibleSatellites", "satellitesVisible"])),
        "cellulink_gnss_track_angle": to_float(deep_first_existing(merged, ["track_angle", "trackAngle", "course"])),
        "cellulink_gnss_status_json": json_dumps(status_payload) if status_payload else None,
        "cellulink_gnss_json": json_dumps(info_payload) if info_payload else None,
    }


def _as_text(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return str(value)
