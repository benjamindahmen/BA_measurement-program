from __future__ import annotations

from .cellulink_api import CellulinkApiClient
from .cellulink_auth import CellulinkAuthenticator
from .config import AppConfig


def read_active_sim_config(config: AppConfig) -> str | None:
    try:
        value = config.sim.active_state_path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return None
    return value or None


def set_active_sim_config(config: AppConfig, label: str) -> str:
    normalized = label.strip().upper()
    profile = config.sim.profiles.get(normalized)
    if profile is None:
        available = ", ".join(sorted(config.sim.profiles)) or "keine"
        raise ValueError(f"Unbekannte SIM-Konfiguration {label!r}. Verfügbar: {available}")
    if not profile.pin:
        raise ValueError(f"Für SIM-Konfiguration {normalized} ist keine PIN in config.ini gesetzt.")

    authenticator = CellulinkAuthenticator(config.cellulink)
    authenticator.reachability_check()
    access_token = authenticator.login()
    api_client = CellulinkApiClient(config.cellulink, access_token)
    try:
        api_client.set_sim_pin(profile.pin)
    except Exception as exc:
        raise RuntimeError("SIM-PIN konnte über die Cellulink-API nicht gesetzt werden.") from exc

    config.sim.active_state_path.parent.mkdir(parents=True, exist_ok=True)
    config.sim.active_state_path.write_text(f"{normalized}\n", encoding="utf-8")
    return normalized
