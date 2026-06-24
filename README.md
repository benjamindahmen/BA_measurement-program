# Raspberry Pi Measurement System

Dieses Projekt erfasst waehrend mobiler Messfahrten zyklisch Daten eines Cellulink-Mobilfunkrouters, Referenz-GNSS-Daten eines Waveshare MAX-M8Q GNSS-HATs sowie Ping- und iPerf3-Messwerte. Die Ergebnisse werden fuer die spaetere wissenschaftliche Auswertung in einer SQLite-Datenbank gespeichert.

## Projektstruktur

```text
measurement_system/
├── main.py
├── config.ini
├── requirements.txt
├── README.md
├── measurement/
│   ├── __init__.py
│   ├── config.py
│   ├── database.py
│   ├── cellulink_auth.py
│   ├── cellulink_api.py
│   ├── gnss_reference.py
│   ├── iperf_test.py
│   ├── ping_test.py
│   ├── scheduler.py
│   └── models.py
└── data/
    └── .gitkeep
```

## Installation auf dem Raspberry Pi

Python 3.10 oder neuer wird empfohlen.

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip iperf3
cd measurement_system
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Serielle Schnittstelle aktivieren

Den MAX-M8Q GNSS-HAT ueber die Raspberry-Pi-UART-Schnittstelle betreiben:

```bash
sudo raspi-config
```

Unter `Interface Options` die serielle Schnittstelle aktivieren. Die Login-Shell ueber Serial sollte deaktiviert werden, die Hardware-UART-Schnittstelle aktiviert. Danach neu starten:

```bash
sudo reboot
```

Die Beispielkonfiguration nutzt `/dev/serial0` mit `9600` Baud.

## Konfiguration

Alle Einstellungen liegen in `config.ini`.

Wichtige Felder:

- `[Cellulink] IP_ADDRESS`: IP-Adresse des Cellulink-Routers.
- `[Cellulink] USER` und `PASSWORD`: Login-Daten fuer den Router.
- `[Cellulink] VERIFY_TLS`: TLS-Pruefung. `false` unterdrueckt Zertifikatswarnungen und ist nur fuer Testumgebungen geeignet.
- `[Measurement] DATABASE_PATH`: Zielpfad der SQLite-Datei.
- `[Ping]`: Ziel, Intervall, Paketanzahl und Timeout fuer Ping.
- `[Iperf]`: Server, Ports, Datenmenge und Timeout fuer iPerf3.

Access Tokens werden nur im Speicher gehalten und nicht in die Datenbank oder Konsole geschrieben. In `measurement_runs.config_json` wird das Passwort redigiert gespeichert.

## Start

```bash
cd measurement_system
source .venv/bin/activate
python main.py --config config.ini
```

Das Programm fuehrt zuerst einen Reachability-Check des Cellulink durch, meldet sich per OAuth2 an, startet den GNSS-Reader und legt einen neuen Messlauf in SQLite an. Mit `Ctrl+C` wird der Messlauf sauber beendet und `end_time_system_utc` gesetzt.

## Datenbank

Die SQLite-Datei wird automatisch angelegt. Standardpfad:

```text
data/measurement.sqlite
```

Enthaltene Tabellen:

- `measurement_runs`
- `startup_snapshots`
- `samples_1hz`
- `ping_results`
- `iperf_results`
- `error_log`

Die Tabellen speichern sowohl robuste Extraktionsspalten als auch die vollstaendigen JSON-Rohantworten des Cellulink. Einzelne API-, Ping- oder iPerf-Fehler werden protokolliert und brechen die laufende Messfahrt nicht ab.

## Hinweise zu iPerf3

Der Default-Server ist `iperf3.moji.fr` auf Port `5201`. Wenn dieser Port belegt oder nicht erreichbar ist, werden die konfigurierten Fallback-Ports versucht. Oeffentliche iPerf3-Server sind nicht vollstaendig kontrollierbar; Verfuegbarkeit, Auslastung und Betreiberbedingungen sollten in der Bachelorarbeit als Randbedingung dokumentiert werden.

## Betriebshinweise

- Der 1-Hz-Messloop laeuft getrennt von Ping und iPerf3, damit lange Netzwerk-Tests die zyklische Erfassung nicht blockieren.
- Die GNSS-Referenzzeit stammt aus gueltigen RMC/GGA-Saetzen des MAX-M8Q. Zusaetzlich werden immer System-UTC und `time.monotonic_ns()` gespeichert.
- Bei `VERIFY_TLS=false` werden TLS-Warnungen unterdrueckt. Das ist praktisch fuer Labor- und Testumgebungen, sollte aber nicht als sichere Produktivkonfiguration gelten.
