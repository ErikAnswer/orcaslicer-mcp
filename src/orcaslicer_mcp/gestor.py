"""Client for the home print queue's web API (Gestor 3D), where the headless slicer keeps
its own copy of the user presets. Writes need the page's mutation token from /api/state."""
from __future__ import annotations

import os
from dataclasses import dataclass

import httpx


class GestorError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class GestorConfig:
    base_url: str
    timeout: float


def load_gestor_config() -> GestorConfig:
    raw_timeout = os.environ.get("ORCA_GESTOR_TIMEOUT", "20")
    try:
        timeout = float(raw_timeout)
    except ValueError as e:
        raise GestorError(f"ORCA_GESTOR_TIMEOUT must be a number of seconds, got {raw_timeout!r}") from e
    if not timeout > 0:
        raise GestorError(f"ORCA_GESTOR_TIMEOUT must be positive, got {raw_timeout!r}")
    return GestorConfig(
        base_url=os.environ.get("ORCA_GESTOR_URL", "http://192.168.1.17").rstrip("/"),
        timeout=timeout)


class GestorClient:
    def __init__(self, cfg: GestorConfig):
        self._http = httpx.AsyncClient(base_url=cfg.base_url, timeout=cfg.timeout)
        self._token: str | None = None

    async def __aenter__(self) -> "GestorClient":
        return self

    async def __aexit__(self, *exc) -> None:
        await self._http.aclose()

    async def _send(self, method: str, path: str, **kwargs) -> httpx.Response:
        try:
            resp = await self._http.request(method, path, **kwargs)
        except httpx.HTTPError as e:
            raise GestorError(f"Gestor 3D not reachable at {self._http.base_url}: {e}") from e
        if resp.status_code >= 400:
            try:
                message = resp.json().get("error") or resp.text
            except ValueError:
                message = resp.text
            raise GestorError(message, resp.status_code)
        return resp

    async def presets(self) -> list[dict]:
        state = (await self._send("GET", "/api/state")).json()
        self._token = state.get("mutationToken")
        return state.get("presets", [])

    async def download(self, path: str) -> bytes:
        resp = await self._send("GET", "/api/download", params={"root": "presets", "path": path})
        return resp.content

    async def _mutation_headers(self) -> dict:
        if self._token is None:
            await self.presets()
        if not self._token:
            raise GestorError("the Gestor 3D did not hand out a mutation token")
        return {"X-Cad3d-Token": self._token}

    async def trash(self, path: str) -> dict:
        resp = await self._send("POST", "/api/trash", json={"root": "presets", "path": path},
                                headers=await self._mutation_headers())
        return resp.json().get("entry", {})

    async def restore(self, entry_id: str) -> dict:
        resp = await self._send("POST", "/api/restore", json={"id": entry_id},
                                headers=await self._mutation_headers())
        return resp.json().get("restored", {})

    async def upload(self, filename: str, content: bytes) -> dict:
        headers = await self._mutation_headers() | {"Content-Type": "application/json"}
        resp = await self._send("POST", "/api/preset-upload", params={"filename": filename},
                                content=content, headers=headers)
        return resp.json().get("preset", {})
