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
    python3 -m venv .venv
fi

"${PROJECT_DIR}/.venv/bin/python" -m pip install --upgrade pip
"${PROJECT_DIR}/.venv/bin/python" -m pip install -r requirements.txt

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
