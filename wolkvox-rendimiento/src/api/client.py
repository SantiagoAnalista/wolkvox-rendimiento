"""Cliente HTTP de la API v2 de Wolkvox. Responsabilidad única: autenticar,
reintentar y devolver el campo 'data' crudo. No transforma nada."""
from __future__ import annotations

import logging

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

log = logging.getLogger(__name__)


class WolkvoxError(Exception):
    pass


class WolkvoxClient:
    def __init__(self, servidor: str, token: str, timeout_seg: int = 90, reintentos: int = 3):
        self.base_url = f"https://wv{servidor}.wolkvox.com/api/v2"
        self._client = httpx.Client(
            headers={"wolkvox-token": token, "wolkvox_server": servidor},
            timeout=httpx.Timeout(timeout_seg, connect=15.0),
        )
        self._reintentos = reintentos

    def __enter__(self) -> "WolkvoxClient":
        return self

    def __exit__(self, *args) -> None:
        self._client.close()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=4, max=30),
        retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
        reraise=True,
    )
    def _get(self, recurso: str, params: dict) -> dict:
        url = f"{self.base_url}/{recurso}"
        log.debug("GET %s params=%s", url, params)
        resp = self._client.get(url, params=params)
        resp.raise_for_status()
        return resp.json()

    def consultar(self, recurso: str, params: dict) -> list[dict]:
        """Devuelve la lista de registros del campo 'data'."""
        payload = self._get(recurso, params)

        code = str(payload.get("code", ""))
        if code and code not in ("200", "0"):
            raise WolkvoxError(f"{recurso} {params}: code={code} msg={payload.get('msg')}")

        data = payload.get("data") or []
        if isinstance(data, dict):  # algunos endpoints devuelven un objeto único
            data = [data]

        log.info("%s -> %d registros", params.get("api", recurso), len(data))
        return data
