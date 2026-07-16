#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_NAME="measurement_system.service"
SERVICE_USER="${SUDO_USER:-${USER}}"
UNIT_SOURCE="${PROJECT_DIR}/${SERVICE_NAME}"
UNIT_TARGET="/etc/systemd/system/${SERVICE_NAME}"

cd "${PROJECT_DIR}"
chmod +x "${PROJECT_DIR}/install_service.sh"
chmod +x "${PROJECT_DIR}/commit_data.sh"

if [[ ! -d .venv ]]; then
    python3 -m venv --system-site-packages .venv
elif [[ -f .venv/pyvenv.cfg ]]; then
    if grep -q "^include-system-site-packages = " .venv/pyvenv.cfg; then
        sed -i "s/^include-system-site-packages = .*/include-system-site-packages = true/" .venv/pyvenv.cfg
    else
        echo "include-system-site-packages = true" >> .venv/pyvenv.cfg
    fi
fi

if ! "${PROJECT_DIR}/.venv/bin/python" - <<'PY'
import sys

has_system_dist_packages = any(
    path.startswith("/usr/") and "dist-packages" in path
    for path in sys.path
)
raise SystemExit(0 if has_system_dist_packages else 1)
PY
then
    BACKUP_DIR=".venv_without_system_packages_$(date --utc +'%Y%m%dT%H%M%SZ')"
    echo "Vorhandene .venv sieht keine Systempakete; verschiebe nach ${BACKUP_DIR}."
    mv .venv "${BACKUP_DIR}"
    python3 -m venv --system-site-packages .venv
fi

"${PROJECT_DIR}/.venv/bin/python" -m pip install --upgrade pip
"${PROJECT_DIR}/.venv/bin/python" -m pip install -r requirements.txt

if "${PROJECT_DIR}/.venv/bin/python" -c "import lgpio" >/dev/null 2>&1; then
    echo "GPIO-Pin-Factory lgpio ist in .venv verfügbar."
else
    echo "Fehler: lgpio ist in .venv nicht verfügbar." >&2
    echo "Installiere auf dem Raspberry Pi python3-lgpio und starte dieses Skript erneut:" >&2
    echo "  sudo apt install -y python3-lgpio" >&2
    exit 1
fi

TEMP_UNIT="$(mktemp)"
trap 'rm -f "${TEMP_UNIT}"' EXIT
sed \
    -e "s|^WorkingDirectory=.*|WorkingDirectory=${PROJECT_DIR}|" \
    -e "s|^ExecStart=.*|ExecStart=${PROJECT_DIR}/.venv/bin/python ${PROJECT_DIR}/main.py|" \
    -e "s|^User=.*|User=${SERVICE_USER}|" \
    "${UNIT_SOURCE}" > "${TEMP_UNIT}"

sudo install -m 0644 "${TEMP_UNIT}" "${UNIT_TARGET}"
sudo systemctl daemon-reload
sudo systemctl enable "${SERVICE_NAME}"
sudo systemctl restart "${SERVICE_NAME}"

echo "Service installiert und gestartet."
echo "Status: sudo systemctl status ${SERVICE_NAME}"
echo "Log:    journalctl -u ${SERVICE_NAME} -f"
