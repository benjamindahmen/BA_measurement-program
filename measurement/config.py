from __future__ import annotations

import configparser
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


def _bool(value: str, default: bool = False) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _int(value: str, default: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _float(value: str, default: float) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def _csv_ints(value: str) -> list[int]:
    result: list[int] = []
    for item in str(value or "").split(","):
        item = item.strip()
        if not item:
            continue
        try:
            result.append(int(item))
        except ValueError:
            continue
    return result


@dataclass(frozen=True)
class CellulinkConfig:
    ip_address: str
    user: str
    password: str
    client_id: str
    redirect_uri: str
    modem_id: int
    profile_id: int
    verify_tls: bool

    @property
    def base_url(self) -> str:
        return f"https://{self.ip_address}"


@dataclass(frozen=True)
class ReferenceGnssConfig:
    port: str
    baudrate: int
    read_timeout_s: float


@dataclass(frozen=True)
class MeasurementConfig:
    database_path: Path
    route_id: str
    direction: str
    vehicle: str
    notes: str
    sample_interval_s: float


@dataclass(frozen=True)
class PingConfig:
    enabled: bool
    target: str
    interval_s: float
    count: int
    timeout_s: int


@dataclass(frozen=True)
class IperfConfig:
    enabled: bool
    server: str
    port: int
    protocol: str
    interval_s: float
    mode: str
    bytes_upload: str
    bytes_download: str
    parallel_streams: int
    timeout_s: int
    fallback_ports: list[int]


@dataclass(frozen=True)
class GpioConfig:
    button_gpio: int
    button_bounce_time_s: float
    stop_hold_time_s: float
    shutdown_hold_time_s: float


@dataclass(frozen=True)
class StatusLedConfig:
    enabled: bool
    gpio: int


@dataclass(frozen=True)
class SimProfileConfig:
    label: str
    pin: str


@dataclass(frozen=True)
class SimConfig:
    active_state_path: Path
    profiles: dict[str, SimProfileConfig]


@dataclass(frozen=True)
class StartupConfig:
    modem_toggle_enabled: bool
    modem_toggle_settle_s: float
    ready_timeout_s: float
    check_interval_s: float
    ping_count: int
    ping_timeout_s: int


@dataclass(frozen=True)
class AppConfig:
    cellulink: CellulinkConfig
    reference_gnss: ReferenceGnssConfig
    measurement: MeasurementConfig
    ping: PingConfig
    iperf: IperfConfig
    gpio: GpioConfig
    status_led: StatusLedConfig
    sim: SimConfig
    startup: StartupConfig
    config_path: Path

    def redacted_json(self) -> str:
        data: dict[str, Any] = asdict(self)
        data["config_path"] = str(self.config_path)
        data["measurement"]["database_path"] = str(self.measurement.database_path)
        data["cellulink"]["password"] = "***redacted***"
        data["sim"]["active_state_path"] = str(self.sim.active_state_path)
        for profile in data["sim"]["profiles"].values():
            profile["pin"] = "***redacted***"
        return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)


def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path).resolve()
    parser = configparser.ConfigParser()
    read_files = parser.read(config_path)
    if not read_files:
        raise FileNotFoundError(f"Configuration file not found: {config_path}")

    base_dir = config_path.parent
    database_path = Path(parser.get("Measurement", "DATABASE_PATH", fallback="data/measurement.sqlite"))
    if not database_path.is_absolute():
        database_path = base_dir / database_path
    active_sim_state_path = Path(parser.get("SimConfig", "ACTIVE_STATE_PATH", fallback="data/active_sim_config.txt"))
    if not active_sim_state_path.is_absolute():
        active_sim_state_path = base_dir / active_sim_state_path

    sim_profiles: dict[str, SimProfileConfig] = {}
    for section in parser.sections():
        if not section.lower().startswith("simconfig."):
            continue
        label = section.split(".", 1)[1].strip()
        if not label:
            continue
        sim_profiles[label.upper()] = SimProfileConfig(
            label=label.upper(),
            pin=parser.get(section, "PIN", fallback="").strip(),
        )

    return AppConfig(
        cellulink=CellulinkConfig(
            ip_address=parser.get("Cellulink", "IP_ADDRESS"),
            user=parser.get("Cellulink", "USER"),
            password=parser.get("Cellulink", "PASSWORD"),
            client_id=parser.get("Cellulink", "CLIENT_ID"),
            redirect_uri=parser.get("Cellulink", "REDIRECT_URI"),
            modem_id=_int(parser.get("Cellulink", "MODEM_ID", fallback="1"), 1),
            profile_id=_int(parser.get("Cellulink", "PROFILE_ID", fallback="1"), 1),
            verify_tls=_bool(parser.get("Cellulink", "VERIFY_TLS", fallback="false")),
        ),
        reference_gnss=ReferenceGnssConfig(
            port=parser.get("ReferenceGNSS", "PORT", fallback="/dev/serial0"),
            baudrate=_int(parser.get("ReferenceGNSS", "BAUDRATE", fallback="9600"), 9600),
            read_timeout_s=_float(parser.get("ReferenceGNSS", "READ_TIMEOUT_S", fallback="1.0"), 1.0),
        ),
        measurement=MeasurementConfig(
            database_path=database_path,
            route_id=parser.get("Measurement", "ROUTE_ID", fallback=""),
            direction=parser.get("Measurement", "DIRECTION", fallback="unknown"),
            vehicle=parser.get("Measurement", "VEHICLE", fallback="unknown"),
            notes=parser.get("Measurement", "NOTES", fallback=""),
            sample_interval_s=_float(parser.get("Measurement", "SAMPLE_INTERVAL_S", fallback="1"), 1.0),
        ),
        ping=PingConfig(
            enabled=_bool(parser.get("Ping", "ENABLED", fallback="true"), True),
            target=parser.get("Ping", "TARGET", fallback="google.com").strip(),
            interval_s=_float(parser.get("Ping", "INTERVAL_S", fallback="10"), 10.0),
            count=_int(parser.get("Ping", "COUNT", fallback="4"), 4),
            timeout_s=_int(parser.get("Ping", "TIMEOUT_S", fallback="5"), 5),
        ),
        iperf=IperfConfig(
            enabled=_bool(parser.get("Iperf", "ENABLED", fallback="true"), True),
            server=parser.get("Iperf", "SERVER", fallback="iperf3.moji.fr"),
            port=_int(parser.get("Iperf", "PORT", fallback="5201"), 5201),
            protocol=parser.get("Iperf", "PROTOCOL", fallback="tcp").lower(),
            interval_s=_float(parser.get("Iperf", "INTERVAL_S", fallback="10"), 10.0),
            mode=parser.get("Iperf", "MODE", fallback="bytes"),
            bytes_upload=parser.get("Iperf", "BYTES_UPLOAD", fallback="5M"),
            bytes_download=parser.get("Iperf", "BYTES_DOWNLOAD", fallback="5M"),
            parallel_streams=_int(parser.get("Iperf", "PARALLEL_STREAMS", fallback="1"), 1),
            timeout_s=_int(parser.get("Iperf", "TIMEOUT_S", fallback="30"), 30),
            fallback_ports=_csv_ints(parser.get("Iperf", "FALLBACK_PORTS", fallback="5202,5203,5204,5205")),
        ),
        gpio=GpioConfig(
            button_gpio=_int(parser.get("GPIO", "BUTTON_GPIO", fallback="17"), 17),
            button_bounce_time_s=_float(
                parser.get("GPIO", "BUTTON_BOUNCE_TIME_S", fallback="0.2"), 0.2
            ),
            stop_hold_time_s=_float(
                parser.get("GPIO", "STOP_HOLD_TIME_S", fallback="3.0"), 3.0
            ),
            shutdown_hold_time_s=_float(
                parser.get("GPIO", "SHUTDOWN_HOLD_TIME_S", fallback="8.0"), 8.0
            ),
        ),
        status_led=StatusLedConfig(
            enabled=_bool(parser.get("StatusLED", "ENABLED", fallback="true"), True),
            gpio=_int(parser.get("StatusLED", "GPIO", fallback="27"), 27),
        ),
        sim=SimConfig(
            active_state_path=active_sim_state_path,
            profiles=sim_profiles,
        ),
        startup=StartupConfig(
            modem_toggle_enabled=_bool(
                parser.get("Startup", "MODEM_TOGGLE_ENABLED", fallback="true"), True
            ),
            modem_toggle_settle_s=_float(
                parser.get("Startup", "MODEM_TOGGLE_SETTLE_S", fallback="5.0"), 5.0
            ),
            ready_timeout_s=_float(
                parser.get("Startup", "READY_TIMEOUT_S", fallback="180.0"), 180.0
            ),
            check_interval_s=_float(
                parser.get("Startup", "CHECK_INTERVAL_S", fallback="2.0"), 2.0
            ),
            ping_count=_int(parser.get("Startup", "PING_COUNT", fallback="1"), 1),
            ping_timeout_s=_int(parser.get("Startup", "PING_TIMEOUT_S", fallback="5"), 5),
        ),
        config_path=config_path,
    )
