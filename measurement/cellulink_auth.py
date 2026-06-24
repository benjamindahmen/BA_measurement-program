from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import requests
import urllib3

from .config import CellulinkConfig


class CellulinkAuthError(RuntimeError):
    pass


class CellulinkAuthenticator:
    def __init__(self, config: CellulinkConfig, timeout_s: float = 10.0):
        self.config = config
        self.timeout_s = timeout_s
        self.session = requests.Session()
        self.session.verify = config.verify_tls
        if not config.verify_tls:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    def reachability_check(self) -> None:
        url = f"{self.config.base_url}/arp_api/wbm/um/system_use_notification"
        response = self.session.get(url, timeout=self.timeout_s, allow_redirects=False)
        if response.status_code != 200:
            raise CellulinkAuthError(
                f"Cellulink reachability check failed: HTTP {response.status_code} for {url}"
            )

    def login(self) -> str:
        state = self._authorize()
        code = self._login_with_credentials(state)
        return self._exchange_code(code)

    def _authorize(self) -> str:
        url = f"{self.config.base_url}/_auth_api/authorize"
        response = self.session.get(
            url,
            params={
                "client_id": self.config.client_id,
                "response_type": "code",
                "redirect_uri": self.config.redirect_uri,
                "state": "insomnia",
            },
            timeout=self.timeout_s,
            allow_redirects=False,
        )
        location = response.headers.get("Location", "")
        state = _query_param(location, "state")
        if not state:
            raise CellulinkAuthError(
                f"Authorization response did not contain state in Location header "
                f"(HTTP {response.status_code})"
            )
        return state

    def _login_with_credentials(self, state: str) -> str:
        url = f"{self.config.base_url}/_auth_api/login"
        response = self.session.post(
            url,
            data={"username": self.config.user, "password": self.config.password, "state": state},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=self.timeout_s,
            allow_redirects=False,
        )
        if response.status_code != 302:
            raise CellulinkAuthError(f"Login failed: expected HTTP 302, got HTTP {response.status_code}")
        location = response.headers.get("Location", "")
        code = _query_param(location, "code")
        if not code:
            raise CellulinkAuthError("Login response did not contain authorization code in Location header")
        return code

    def _exchange_code(self, code: str) -> str:
        url = f"{self.config.base_url}/_auth_api/token"
        response = self.session.post(
            url,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.config.redirect_uri,
                "client_id": self.config.client_id,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=self.timeout_s,
            allow_redirects=False,
        )
        if response.status_code != 200:
            raise CellulinkAuthError(f"Token exchange failed: HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise CellulinkAuthError("Token exchange did not return JSON") from exc
        token = payload.get("access_token")
        if not token:
            raise CellulinkAuthError("Token exchange response did not contain access_token")
        return str(token)


def _query_param(location: str, name: str) -> str | None:
    parsed = urlparse(location)
    values = parse_qs(parsed.query).get(name)
    if values:
        return values[0]
    return None
