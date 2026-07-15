# Raspberry Pi Measurement System

Dieses Projekt erfasst während mobiler Messfahrten zyklisch Daten eines
Cellulink-Mobilfunkrouters, Referenz-GNSS-Daten eines Waveshare MAX-M8Q
GNSS-HATs sowie Ping- und iPerf3-Messwerte. Die Ergebnisse werden in einer
SQLite-Datenbank gespeichert. Das System ist für den Headless-Betrieb im
Fahrzeug ohne Tastatur, Maus und Bildschirm ausgelegt.

## Projektstruktur

```text
measurement_system/
├── main.py
├── config.ini
├── requirements.txt
├── measurement_system.service
├── install_service.sh
├── commit_data.sh
├── exports/
│   └── run_000001_2026-07-12T12-00-00-000Z.sqlite.gz
├── measurement/
│   ├── controller.py
│   ├── gpio_control.py
│   ├── status_led.py
│   ├── hardware_test.py
│   ├── database.py
│   ├── scheduler.py
│   └── ...
└── data/
    ├── measurement.sqlite
    └── system.log
```

## Installation über GitHub auf dem Raspberry Pi

Auf dem Raspberry Pi muss keine eigene „GitHub-Anwendung“ installiert werden.
Benötigt werden Git und ein SSH-Schlüssel. Für den Headless-Betrieb wird ein
schreibberechtigter **Deploy Key** empfohlen: Er berechtigt den Pi nur für
dieses eine Repository.

Das Repository sollte **privat** sein, weil die Datenbank Positions- und
Mobilfunkmessdaten enthalten kann.

### 1. Systempakete installieren

Auf dem Raspberry Pi:

```bash
sudo apt update
sudo apt upgrade
sudo apt install -y \
    git openssh-client sqlite3 \
    python3 python3-venv python3-pip python3-lgpio \
    iperf3
```

### 2. SSH-Schlüssel auf dem Pi erzeugen

Als der Linux-Benutzer ausführen, unter dem später auch der Messdienst läuft:

```bash
ssh-keygen -t ed25519 -C "raspberry-pi-measurement"
```

Als Speicherort den vorgeschlagenen Pfad `~/.ssh/id_ed25519` übernehmen. Soll
der Pi ohne Benutzereingabe pushen können, muss die Passphrase leer bleiben.
Die private Datei `~/.ssh/id_ed25519` darf niemals kopiert oder in Git
committet werden.

Öffentlichen Schlüssel anzeigen:

```bash
cat ~/.ssh/id_ed25519.pub
```

Den kompletten Inhalt kopieren und auf GitHub beim Repository
`benjamindahmen/BA_measurement-program` eintragen:

1. `Settings`
2. `Deploy keys`
3. `Add deploy key`
4. Titel beispielsweise `Raspberry Pi Messsystem`
5. Schlüssel einfügen
6. **Allow write access** aktivieren
7. `Add key`

Ein Deploy Key ist normalerweise nur lesend. `Allow write access` ist
erforderlich, damit der Pi die Datenbank pushen kann. Details stehen in der
[GitHub-Dokumentation zu Deploy Keys](https://docs.github.com/en/authentication/connecting-to-github-with-ssh/managing-deploy-keys).

Verbindung testen:

```bash
ssh -T git@github.com
```

Beim ersten Verbindungsaufbau den angezeigten GitHub-Fingerprint prüfen und
bestätigen. Die Meldung, dass GitHub keinen Shell-Zugriff bereitstellt, ist
normal und bedeutet bei erfolgreicher Authentifizierung keinen Fehler.

### 3. Repository klonen und Git konfigurieren

```bash
cd ~
git clone git@github.com:benjamindahmen/BA_measurement-program.git measurement_system
cd measurement_system

git config user.name "Raspberry Pi Measurement System"
git config user.email "raspberry-pi@local"
```

Durch die SSH-URL verwendet auch `git push` automatisch den Deploy Key. Eine
allgemeine Anleitung zum Klonen findet sich in der
[GitHub-Dokumentation](https://docs.github.com/en/repositories/creating-and-managing-repositories/cloning-a-repository?platform=linux).

### 4. Messdienst installieren

```bash
chmod +x install_service.sh commit_data.sh
./install_service.sh
```

Das Installationsskript legt die virtuelle Umgebung an, installiert die
Python-Abhängigkeiten, passt die systemd-Unit an Projektverzeichnis und
Benutzer an und startet den Dienst. Die virtuelle Umgebung wird so angelegt,
dass auch die über apt installierte GPIO-Unterstützung verfügbar ist.

```bash
sudo systemctl status measurement_system.service
journalctl -u measurement_system.service -f
```

Der Dienst startet nach Verfügbarkeit des Netzwerks und wird nach einem Fehler
automatisch neu gestartet. Standardausgabe und Fehlerausgabe landen zusätzlich
in journald.

### 5. Spätere Softwareupdates vom Repository laden

Vor einer neuen Messfahrt und nur bei sauberem Git-Arbeitsverzeichnis:

```bash
cd ~/measurement_system
sudo systemctl stop measurement_system.service
git pull --ff-only
./install_service.sh
```

`--ff-only` verhindert, dass Git auf dem Pi unbemerkt einen Merge-Commit
erzeugt. Wird das Update abgelehnt, zuerst `git status` prüfen und keine
Datenbankdatei überschreiben.

## Serielle Schnittstelle für den MAX-M8Q

```bash
sudo raspi-config
```

Unter `Interface Options` die serielle Schnittstelle aktivieren. Die
Login-Shell über Serial muss deaktiviert, die Hardware-UART aktiviert werden.
Die Beispielkonfiguration nutzt `/dev/serial0` mit 9600 Baud. GPIO14 und GPIO15
sind dadurch belegt und dürfen nicht für den Taster verwendet werden.

## Taster und Status-LED

Der Start-/Stop-Taster wird zwischen **GPIO17 (physischer Pin 11)** und **GND**
angeschlossen. Der interne Pull-up ist aktiv; ein externer Pull-up-Widerstand
ist nicht erforderlich.

Die optionale Status-LED wird von **GPIO27 (physischer Pin 13)** über einen
geeigneten Vorwiderstand gegen **GND** angeschlossen. LED niemals ohne
Vorwiderstand betreiben.

Die Pins und Zeitgrenzen stehen in `config.ini`:

```ini
[GPIO]
BUTTON_GPIO=17
BUTTON_BOUNCE_TIME_S=0.2
STOP_HOLD_TIME_S=3.0
SHUTDOWN_HOLD_TIME_S=8.0

[StatusLED]
ENABLED=true
GPIO=27
```

Bedienung:

- kurzer Tastendruck im Zustand `IDLE`: neue Messfahrt starten
- mindestens 3 Sekunden halten: laufende Messfahrt sauber beenden
- mindestens 8 Sekunden halten: Messfahrt beenden und Raspberry Pi
  herunterfahren

Beim Halten werden die Schwellwerte unmittelbar ausgewertet. Während einer
Messung führt ein achtsekündiger Druck deshalb nach drei Sekunden zuerst den
sauberen Messstopp und anschließend das Herunterfahren aus.

Die LED zeigt den Zustand an:

| Zustand | LED-Verhalten | Bedeutung |
|---|---|---|
| `IDLE` | langsames Blinken | System ist bereit, aber es läuft keine Messung |
| `STARTING` | mittleres Blinken | Messung wird vorbereitet und Startbedingungen werden geprüft |
| `RUNNING` | dauerhaft an | Messung läuft |
| `STOPPING` | dreimaliges kurzes Blinken | Messung wird sauber beendet |
| `ERROR` | sehr schnelles Blinken | Fehlerzustand |

Alle Zustandswechsel werden unabhängig von der LED in `data/system.log`
protokolliert.

## Konfiguration

Alle Einstellungen liegen in `config.ini`. Besonders relevant sind:

- `[Cellulink]`: Routeradresse, Zugangsdaten und TLS-Prüfung
- `[ReferenceGNSS]`: UART-Port, Baudrate und Timeout
- `[Measurement]`: SQLite-Pfad und Metadaten der Messfahrt
- `[Startup]`: Mobilfunk-Neuanmeldung und Startfreigabe
- `[Ping]` und `[Iperf]`: Netzwerkziele, Intervalle und Timeouts
- `[GPIO]` und `[StatusLED]`: Pins und Bedienzeiten

Access Tokens bleiben ausschließlich im Speicher. In
`measurement_runs.config_json` wird das Routerpasswort redigiert gespeichert.

Die Startfreigabe wird über `[Startup]` gesteuert:

```ini
[Startup]
CELLULAR_RESET_ENABLED=true
CELLULAR_RECONNECT_METHOD=PUT
CELLULAR_RECONNECT_PATH=/cellular/connection-check/action
CELLULAR_RECONNECT_ACTION=Relogin
READY_TIMEOUT_S=180.0
PING_COUNT=1
```

## Headless-Betrieb

Nach dem Boot startet systemd das Programm automatisch. Es bleibt zunächst im
Zustand `IDLE`; eine Messung beginnt erst durch einen kurzen Tastendruck. Der
Start bleibt im Zustand `STARTING`, bis der Mobilfunk-Reconnect per
Cellulink-API ausgelöst wurde, Referenz-GNSS und Cellulink-GNSS jeweils einen
Fix haben und ein einzelner Ping zum konfigurierten Ping-Ziel erfolgreich war.
Erst danach wechselt die LED auf `RUNNING` und die zyklische Messwerterfassung
beginnt.

Der aktuelle Ablauf ist im systemd-Log sichtbar:

```bash
journalctl -u measurement_system.service -f
systemctl status measurement_system.service
```

Das Log enthält unter anderem erkannte Tasterereignisse, Zustandswechsel und
Statusmeldungen wie `STATUS state=STARTING action=Warte auf Fix von
Referenz-GNSS`.

Der 1-Hz-Messloop läuft getrennt vom GPIO-Eventhandling und vom 10-s-Testloop
für Ping und iPerf. Längere Netzwerkaufrufe blockieren daher weder den Taster
noch die zyklische Messwerterfassung.

Für Entwicklung und Fehlersuche kann das Programm ohne GPIO gestartet werden:

```bash
source .venv/bin/activate
python main.py --no-gpio --start-now
```

`Ctrl+C` beendet dabei die Messung sauber. Für den normalen Fahrzeugbetrieb
wird kein Tastaturereignis benötigt.

## Testmodus mit Monitor und Tastatur

Für Inbetriebnahme und Fehlersuche auf Raspberry Pi OS Lite gibt es einen
separaten Shell-Testmodus. Er ist für einen angeschlossenen Monitor und eine
Tastatur gedacht und startet keine normale Messfahrt. Dadurch können einzelne
Hardwareteile geprüft werden, ohne dass alle Komponenten angeschlossen sein
müssen.

Vor dem Test den normalen Messdienst stoppen, damit UART, GPIO und API-Zugriffe
nicht parallel benutzt werden:

```bash
cd ~/measurement_system
sudo systemctl stop measurement_system.service
source .venv/bin/activate
```

Interaktives Menü starten:

```bash
python main.py --test
```

Das Menü bietet:

- keine Hardware, nur Shell/Tastatur
- nur Taster
- nur Referenz-GNSS
- nur Cellulink
- Cellulink-Reconnect auslösen und beobachten
- nur Status-LED
- Referenz-GNSS und Cellulink

Während laufender Tests beendet `q` + `Enter` den Test. `Ctrl+C` funktioniert
ebenfalls. Wenn der Tastertest aktiv ist, werden erkannte Ereignisse direkt in
der Shell angezeigt:

- `SHORT_PRESS`
- `STOP_HOLD`
- `SHUTDOWN_HOLD`

Direkte Aufrufe ohne Menü:

```bash
# nur Shell/Tastatur, keine Hardware
python main.py --test --test-hardware none

# nur Taster, läuft bis q + Enter
python main.py --test --test-hardware none --test-button --test-seconds 0

# nur Referenz-GNSS für 60 Sekunden
python main.py --test --test-hardware gnss --test-seconds 60

# nur Cellulink-Erreichbarkeit, Login und API-Endpunkte
python main.py --test --test-hardware cellulink

# Cellulink-Reconnect auslösen und Mobilfunkstatus/Ping beobachten
python main.py --test --test-hardware reconnect --test-seconds 90

# Status-LED gezielt auf einen Zustand setzen
python main.py --test --test-hardware led --test-led-state RUNNING --test-seconds 10

# Referenz-GNSS und Cellulink, zusätzlich mit Taster
python main.py --test --test-hardware both --test-button --test-seconds 60
```

Der GNSS-Test öffnet den in `config.ini` eingestellten Port, liest NMEA-Daten
und zeigt einmal pro Sekunde an, ob RMC/GGA-Daten empfangen wurden, ob ein
gültiger Fix vorliegt und welche Position/Satellitenzahl erkannt wurde.

Der Cellulink-Test prüft Erreichbarkeit, Login und die relevanten API-Endpunkte.
Das Passwort und der Access Token werden nicht ausgegeben.

Der Reconnect-Test löst den konfigurierten Cellulink-Reconnect aus und
beobachtet danach Mobilfunkstatus und Ping. Eine echte Neuverbindung ist
typischerweise an kurzzeitigem Ping-Ausfall, Statuswechseln oder geänderten
Mobilfunkwerten erkennbar.

Der LED-Test setzt die Status-LED gezielt auf einen der Zustände `IDLE`,
`STARTING`, `RUNNING`, `STOPPING` oder `ERROR`. Mit `--test-seconds 0` bleibt
der Zustand aktiv, bis der Test mit `q` + `Enter` beendet wird.

Nach dem Test kann der Dienst wieder gestartet werden:

```bash
sudo systemctl start measurement_system.service
```

## Datenbank und Ereignisse

Standardpfad:

```text
data/measurement.sqlite
```

Enthalten sind unter anderem:

- `measurement_runs`
- `startup_snapshots`
- `samples_1hz`
- `ping_results`
- `iperf_results`
- `error_log`
- `system_events`

`system_events` enthält Programm- und Service-Starts, Tasterereignisse,
Messungsstart und -ende, Shutdown-Anforderungen sowie GPIO-, API-, GNSS-,
Ping- und iPerf-Fehler. SQLite-Zugriffe aus den Threads werden durch eine
gemeinsame Sperre serialisiert.

## Messdaten über GitHub abholen

Die laufende SQLite-Datenbank unter `data/measurement.sqlite` wird nicht direkt
versioniert. Das wäre bei mehrstündigen Fahrten über mehrere Wochen ungünstig,
weil Git jede neue Version dieser Binärdatei dauerhaft in der Historie behält.
Stattdessen bleibt die große Arbeitsdatenbank lokal auf dem Pi. Für GitHub
werden pro Messfahrt einzelne komprimierte Exportdateien unter `exports/`
erzeugt.

Nach der Fahrt zuerst die Messung mit dem dreisekündigen Tastendruck beenden.
Den Pi anschließend mit einem Netzwerk verbinden und per SSH anmelden. Im
Projektverzeichnis genügt:

```bash
cd ~/measurement_system
./commit_data.sh
```

Das Skript führt in dieser Reihenfolge aus:

1. systemd-Dienst anhalten, falls er läuft
2. SQLite-WAL vollständig in `data/measurement.sqlite` übernehmen
3. noch nicht exportierte Messfahrten anhand ihrer `run_id` erkennen
4. pro Messfahrt eine eigene Datei `exports/run_000001_....sqlite.gz` erzeugen
5. ausschließlich neue Exportdateien unter `exports/` stagen
6. Commit mit UTC-Zeitstempel erzeugen
7. Commit zum aktuellen Branch auf GitHub pushen
8. Messdienst wieder starten

`system.log`, Zugangsdaten und andere lokale Dateien werden dabei nicht
committet. Niemals `git add .` auf dem Pi verwenden, sondern für manuelle
Commits immer nur die Exportdateien auswählen:

```bash
git add exports/*.sqlite.gz
git commit -m "Messdatenexport YYYY-MM-DD"
git push origin HEAD
```

Auf dem Auswertungsrechner können die neuen Daten danach geladen werden:

```bash
git pull --ff-only
```

Wurde der Export ohne Internetverbindung bereits lokal committet, kann der
ausstehende Commit später bei vorhandener Verbindung manuell gepusht werden:

```bash
cd ~/measurement_system
git push origin HEAD
```

### Wichtige Einschränkung für SQLite-Dateien

SQLite-Dateien sind Binärdateien. Git speichert bei Änderungen neue
Dateiversionen; eine immer weiter wachsende `data/measurement.sqlite` würde das
Repository deshalb bei zwei Wochen Messbetrieb unnötig aufblasen. Darum ist
`data/measurement.sqlite` in `.gitignore` ausgeschlossen.

Die Exportdateien sind ebenfalls SQLite-Datenbanken, aber jeweils nur für eine
einzelne Messfahrt und zusätzlich mit gzip komprimiert. Das ist für GitHub
deutlich besser geeignet. GitHub warnt ab 50 MiB Dateigröße und blockiert
reguläre Git-Dateien über 100 MiB. Das Upload-Skript prüft diese Grenzen pro
Exportdatei. Für einzelne Fahrten, die trotzdem größer werden, sollte Git LFS
oder eine separate Dateiablage verwendet werden. Siehe
[GitHubs Hinweise zu großen Dateien](https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github).

Exportdateien können auf dem Auswertungsrechner entpackt werden:

```bash
gzip -dk exports/run_000001_2026-07-12T12-00-00-000Z.sqlite.gz
sqlite3 exports/run_000001_2026-07-12T12-00-00-000Z.sqlite
```

Wenn `git push` wegen neuer Remote-Commits abgelehnt wird, niemals mit
`--force` pushen. Zuerst die Codeänderungen sichern beziehungsweise auf einem
anderen Rechner zusammenführen; binäre SQLite-Konflikte kann Git nicht
automatisch auflösen.

> Die Spannungsversorgung niemals während einer laufenden Messung einfach
> trennen. Dadurch können Einträge unvollständig bleiben oder das Dateisystem
> beschädigt werden. Zum Ausschalten den Taster mindestens acht Sekunden
> gedrückt halten.
