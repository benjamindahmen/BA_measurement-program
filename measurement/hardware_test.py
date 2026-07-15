from __future__ import annotations

import select
import sys
import time
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from .config import AppConfig
    from .gnss_reference import ReferenceGnssReader
    from .models import GnssState


HardwareMode = Literal["none", "gnss", "cellulink", "led", "both"]
LedStateName = Literal["IDLE", "STARTING", "RUNNING", "STOPPING", "ERROR"]


@dataclass
class TestOptions:
    hardware: HardwareMode
    use_button: bool
    led_state: LedStateName
    duration_s: int


def run_hardware_test(
    config: AppConfig,
    hardware: str | None = None,
    use_button: bool = False,
    led_state: str | None = None,
    duration_s: int = 30,
) -> int:
    options = _interactive_options(hardware, use_button, led_state, duration_s)

    print()
    print("=== Messsystem-Testmodus ===")
    print(f"Hardware: {options.hardware}")
    if options.hardware == "led":
        print(f"LED-State: {options.led_state}")
    print(f"Taster:   {'aktiv' if options.use_button else 'nicht aktiv'}")
    print(f"Dauer:    {'bis q + Enter' if options.duration_s <= 0 else f'{options.duration_s} s'}")
    print()
    print("Tastaturbefehle während laufender Tests:")
    print("  q + Enter  Test beenden")
    print("  h + Enter  Hilfe anzeigen")
    print()

    button_probe = ButtonProbe(config) if options.use_button else None
    gnss_probe = GnssProbe(config) if options.hardware in {"gnss", "both"} else None
    led_probe = LedProbe(config) if options.hardware == "led" else None
    exit_code = 0

    try:
        if button_probe is not None and not button_probe.start():
            exit_code = 1

        if led_probe is not None:
            if not led_probe.start():
                exit_code = 1
            else:
                led_probe.set_state(options.led_state)
                _run_status_loop(options.duration_s, button_probe=button_probe)
            return exit_code

        if options.hardware == "none":
            print("Kein Hardwaretest gewählt. Tastatur/Taster können trotzdem geprüft werden.")
            _run_status_loop(options.duration_s, button_probe=button_probe)
            return exit_code

        if options.hardware in {"cellulink", "both"}:
            if not run_cellulink_test(config):
                exit_code = 1

        if gnss_probe is not None:
            if not gnss_probe.start():
                exit_code = 1
            else:
                ok = _run_status_loop(
                    options.duration_s,
                    button_probe=button_probe,
                    gnss_probe=gnss_probe,
                )
                if not ok:
                    exit_code = 1
        elif button_probe is not None:
            _run_status_loop(options.duration_s, button_probe=button_probe)

    except KeyboardInterrupt:
        print("\nTest per Ctrl+C beendet.")
    finally:
        if led_probe is not None:
            led_probe.stop()
        if gnss_probe is not None:
            gnss_probe.stop()
        if button_probe is not None:
            button_probe.stop()

    return exit_code


class LedProbe:
    def __init__(self, config: AppConfig):
        from .config import StatusLedConfig
        from .status_led import StatusLed

        self._led = StatusLed(StatusLedConfig(enabled=True, gpio=config.status_led.gpio))

    def start(self) -> bool:
        print("LED-Test: initialisiere Status-LED ...")
        try:
            self._led.start()
        except Exception as exc:
            print(f"FEHLER: Status-LED konnte nicht gestartet werden: {exc}")
            if _looks_like_missing_pin_factory(exc):
                print("Die GPIO-Pin-Factory fehlt oder ist in der .venv nicht sichtbar.")
            if _looks_like_busy_pin(exc):
                print("Der LED-Pin ist vermutlich schon durch den Messdienst oder einen zweiten Testprozess belegt.")
            return False
        print("OK: Status-LED aktiv.")
        return True

    def set_state(self, state_name: LedStateName) -> None:
        from .status_led import SystemState

        self._led.set_state(SystemState[state_name])
        print(f"LED zeigt jetzt: {state_name}")

    def stop(self) -> None:
        self._led.stop()


class ButtonProbe:
    def __init__(self, config: AppConfig):
        from .gpio_control import GpioButtonControl

        self._button = GpioButtonControl(config.gpio)

    def start(self) -> bool:
        print("Tastertest: initialisiere GPIO-Taster ...")
        try:
            self._button.start()
        except Exception as exc:
            print(f"FEHLER: GPIO-Taster konnte nicht gestartet werden: {exc}")
            print("Hinweis: Auf dem Pi muss gpiozero installiert sein und der Pin frei sein.")
            if _looks_like_missing_pin_factory(exc):
                print("Die GPIO-Pin-Factory fehlt oder ist in der .venv nicht sichtbar.")
                print("Installiere auf Raspberry Pi OS die empfohlene lgpio-Unterstützung:")
                print("  sudo apt install -y python3-lgpio")
                print("Danach im Projekt:")
                print("  ./install_service.sh")
                print("oder für den reinen Test:")
                print("  source .venv/bin/activate")
                print("  python -c \"import lgpio; print('lgpio ok')\"")
            if _looks_like_busy_pin(exc):
                print("Der Pin ist vermutlich schon durch den Messdienst oder einen zweiten Testprozess belegt.")
                print("Versuche:")
                print("  sudo systemctl stop measurement_system.service")
                print("  ps aux | grep '[p]ython.*main.py'")
                print("Falls kein Prozess sichtbar ist: Raspberry Pi einmal neu starten.")
            return False
        print("OK: Taster aktiv. Kurzer Druck, 3-s-Halten und 8-s-Halten werden angezeigt.")
        return True

    def poll(self) -> None:
        while True:
            event = self._button.get_event(timeout=0.0)
            if event is None:
                return
            print(f"TASTER: {event.event_type.value} nach {event.duration_s:.2f} s")

    def stop(self) -> None:
        self._button.stop()


class GnssProbe:
    def __init__(self, config: AppConfig):
        self.config = config
        self.errors: list[str] = []
        self.reader: ReferenceGnssReader | None = None
        self._last_printed: GnssState | None = None

    def start(self) -> bool:
        from .gnss_reference import ReferenceGnssReader

        gnss = self.config.reference_gnss
        print(f"GNSS-Test: öffne {gnss.port} mit {gnss.baudrate} Baud ...")
        self.reader = ReferenceGnssReader(
            gnss.port,
            gnss.baudrate,
            gnss.read_timeout_s,
            on_error=self._on_error,
        )
        self.reader.start()
        time.sleep(0.2)
        if self.errors:
            print(f"FEHLER: {self.errors[-1]}")
            return False
        print("OK: GNSS-Reader läuft. Warte auf RMC/GGA-NMEA-Daten ...")
        return True

    def poll(self) -> bool:
        if self.errors:
            print(f"GNSS-FEHLER: {self.errors[-1]}")
            return False
        if self.reader is None:
            return False
        snapshot = self.reader.snapshot()
        self._last_printed = snapshot
        print(_format_gnss(snapshot))
        return True

    def stop(self) -> None:
        if self.reader is not None:
            self.reader.stop()
            self.reader.join(timeout=max(self.config.reference_gnss.read_timeout_s + 1.0, 2.0))

    def _on_error(self, message: str, details: dict | None) -> None:
        if details:
            self.errors.append(f"{message} ({details})")
        else:
            self.errors.append(message)


def run_cellulink_test(config: AppConfig) -> bool:
    from .cellulink_api import (
        CellulinkApiClient,
        extract_cellular_fields,
        extract_cellulink_gnss_fields,
    )
    from .cellulink_auth import CellulinkAuthenticator

    print("Cellulink-Test: prüfe Erreichbarkeit und Login ...")
    print(f"Ziel: {config.cellulink.base_url}")
    try:
        authenticator = CellulinkAuthenticator(config.cellulink)
        authenticator.reachability_check()
        print("OK: Cellulink-Webinterface erreichbar.")
        access_token = authenticator.login()
        print("OK: Login erfolgreich, Access Token erhalten.")
        api_client = CellulinkApiClient(config.cellulink, access_token)
    except Exception as exc:
        print(f"FEHLER: Cellulink-Erreichbarkeit/Login fehlgeschlagen: {exc}")
        return False

    checks = [
        ("Profilstatus", api_client.get_profile_status),
        ("Mobilfunkstatus", api_client.get_cellular_status),
        ("Cellulink-GNSS-Status", api_client.get_gnss_status),
        ("Cellulink-GNSS-Information", api_client.get_gnss_information),
    ]

    payloads: dict[str, dict] = {}
    success = True
    for name, func in checks:
        try:
            payload = func()
            payloads[name] = payload
            print(f"OK: {name} gelesen ({len(str(payload))} Zeichen).")
        except Exception as exc:
            print(f"FEHLER: {name} konnte nicht gelesen werden: {exc}")
            success = False

    if "Mobilfunkstatus" in payloads:
        fields = extract_cellular_fields(payloads["Mobilfunkstatus"])
        print("Mobilfunk-Kurzstatus:")
        _print_selected(
            fields,
            [
                "cellular_registration_status",
                "cellular_technology",
                "cellular_frequency_band",
                "cellular_cell_id",
                "cellular_rsrp",
                "cellular_rsrq",
                "cellular_rssi",
                "cellular_sinr",
            ],
        )

    if "Cellulink-GNSS-Status" in payloads or "Cellulink-GNSS-Information" in payloads:
        fields = extract_cellulink_gnss_fields(
            payloads.get("Cellulink-GNSS-Status"),
            payloads.get("Cellulink-GNSS-Information"),
        )
        print("Cellulink-GNSS-Kurzstatus:")
        _print_selected(
            fields,
            [
                "cellulink_gnss_status",
                "cellulink_gnss_latitude",
                "cellulink_gnss_longitude",
                "cellulink_gnss_speed_kmh",
                "cellulink_gnss_used_satellites",
                "cellulink_gnss_visible_satellites",
            ],
        )
        if fields.get("cellulink_gnss_latitude") is None or fields.get("cellulink_gnss_longitude") is None:
            print("Hinweis: GNSS-Position konnte nicht aus den API-Feldern extrahiert werden.")
            print("GNSS-Rohdaten zur Feldnamenprüfung:")
            print(json.dumps(
                {
                    "status": payloads.get("Cellulink-GNSS-Status"),
                    "information": payloads.get("Cellulink-GNSS-Information"),
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ))

    return success


def _run_status_loop(
    duration_s: int,
    button_probe: ButtonProbe | None = None,
    gnss_probe: GnssProbe | None = None,
) -> bool:
    start = time.monotonic()
    next_gnss_print = time.monotonic()
    ok = True

    while True:
        now = time.monotonic()
        if duration_s > 0 and now - start >= duration_s:
            print("Testdauer erreicht.")
            return ok

        if button_probe is not None:
            button_probe.poll()

        if gnss_probe is not None and now >= next_gnss_print:
            ok = gnss_probe.poll() and ok
            next_gnss_print = now + 1.0

        command = _read_keyboard_command(timeout_s=0.2)
        if command == "q":
            print("Test per Tastatur beendet.")
            return ok
        if command == "h":
            print("q + Enter beendet den Test. Tasterereignisse werden automatisch angezeigt.")


def _read_keyboard_command(timeout_s: float) -> str | None:
    if sys.stdin.closed:
        time.sleep(timeout_s)
        return None
    try:
        readable, _, _ = select.select([sys.stdin], [], [], timeout_s)
    except (OSError, ValueError):
        time.sleep(timeout_s)
        return None
    if not readable:
        return None
    return sys.stdin.readline().strip().lower() or None


def _interactive_options(
    hardware: str | None,
    use_button: bool,
    led_state: str | None,
    duration_s: int,
) -> TestOptions:
    if hardware is not None:
        return TestOptions(
            _validate_hardware(hardware),
            use_button,
            _validate_led_state(led_state or "IDLE"),
            duration_s,
        )

    print("=== Interaktiver Hardware-Test ===")
    print("Welche Hardware soll getestet werden?")
    print("  1  keine Hardware, nur Shell/Tastatur")
    print("  2  nur Taster")
    print("  3  nur Referenz-GNSS")
    print("  4  nur Cellulink")
    print("  5  nur Status-LED")
    print("  6  Referenz-GNSS und Cellulink")
    choice = input("Auswahl [1]: ").strip() or "1"

    mapping: dict[str, tuple[HardwareMode, bool]] = {
        "1": ("none", False),
        "2": ("none", True),
        "3": ("gnss", False),
        "4": ("cellulink", False),
        "5": ("led", False),
        "6": ("both", False),
    }
    selected_hardware, selected_button = mapping.get(choice, ("none", False))
    selected_led_state = _validate_led_state(led_state or "IDLE")
    if selected_hardware == "led":
        selected_led_state = _ask_led_state(selected_led_state)
    if choice in {"3", "4", "5", "6"}:
        selected_button = _ask_yes_no("Taster zusätzlich testen? [j/N]: ", default=False)

    duration_text = input(f"Testdauer in Sekunden, 0 = bis q + Enter [{duration_s}]: ").strip()
    if duration_text:
        try:
            duration_s = max(int(duration_text), 0)
        except ValueError:
            print(f"Ungültige Dauer, verwende {duration_s} s.")

    return TestOptions(selected_hardware, selected_button, selected_led_state, duration_s)


def _validate_hardware(value: str) -> HardwareMode:
    if value not in {"none", "gnss", "cellulink", "led", "both"}:
        raise ValueError(f"Unbekannter Hardwaremodus: {value}")
    return value  # type: ignore[return-value]


def _validate_led_state(value: str) -> LedStateName:
    normalized = value.strip().upper()
    if normalized not in {"IDLE", "STARTING", "RUNNING", "STOPPING", "ERROR"}:
        raise ValueError(f"Unbekannter LED-State: {value}")
    return normalized  # type: ignore[return-value]


def _ask_led_state(default: LedStateName) -> LedStateName:
    print("Welcher LED-State soll angezeigt werden?")
    print("  1  IDLE")
    print("  2  STARTING")
    print("  3  RUNNING")
    print("  4  STOPPING")
    print("  5  ERROR")
    choice = input(f"Auswahl [{default}]: ").strip()
    mapping = {
        "1": "IDLE",
        "2": "STARTING",
        "3": "RUNNING",
        "4": "STOPPING",
        "5": "ERROR",
        "": default,
    }
    return _validate_led_state(mapping.get(choice, choice))


def _ask_yes_no(prompt: str, default: bool) -> bool:
    value = input(prompt).strip().lower()
    if not value:
        return default
    return value in {"j", "ja", "y", "yes"}


def _looks_like_busy_pin(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in ("in use", "busy", "belegt", "already", "reserv"))


def _looks_like_missing_pin_factory(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in ("invalid argument", "pin factory", "no module named"))


def _format_gnss(state: Any) -> str:
    validity = "gültig" if state.valid else "noch kein gültiger Fix"
    return (
        "GNSS: "
        f"{validity}; "
        f"time={state.gnss_time_utc or '-'}; "
        f"lat={_fmt(state.latitude)}; "
        f"lon={_fmt(state.longitude)}; "
        f"speed={_fmt(state.speed_kmh)} km/h; "
        f"sats={state.satellites_used if state.satellites_used is not None else '-'}; "
        f"hdop={_fmt(state.hdop)}; "
        f"RMC={'ja' if state.raw_rmc else 'nein'}; "
        f"GGA={'ja' if state.raw_gga else 'nein'}"
    )


def _fmt(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.6g}"


def _print_selected(data: dict, keys: list[str]) -> None:
    for key in keys:
        print(f"  {key}: {data.get(key)}")
