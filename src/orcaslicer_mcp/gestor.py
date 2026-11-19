"""Client for the home print queue's web API (Gestor 3D), where the headless slicer keeps
its own copy of the user presets. Writes need the page's mutation token from /api/state."""
from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import quote

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
        self._base_url = cfg.base_url
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

    @staticmethod
    def _json(resp: httpx.Response) -> dict:
        path = resp.request.url.path
        try:
            body = resp.json()
        except ValueError as e:
            raise GestorError(f"the Gestor 3D answer to {path} is not JSON: {e}", resp.status_code) from e
        if not isinstance(body, dict):
            raise GestorError(f"the Gestor 3D answer to {path} is not a JSON object", resp.status_code)
        return body

    async def _state(self) -> dict:
        state = self._json(await self._send("GET", "/api/state"))
        self._token = state.get("mutationToken")
        return state

    async def presets(self) -> list[dict]:
        return (await self._state()).get("presets", [])

    async def queue(self) -> dict:
        queue = (await self._state()).get("queue") or {}
        if not isinstance(queue, dict):
            raise GestorError("the Gestor 3D queue is not a JSON object")
        return queue

    async def download(self, path: str) -> bytes:
        resp = await self._send("GET", "/api/download", params={"root": "presets", "path": path})
        return resp.content

    async def _mutation_headers(self) -> dict:
        if self._token is None:
            await self.presets()
        if not self._token:
            raise GestorError("the Gestor 3D did not hand out a mutation token")
        return {"X-Cad3d-Token": self._token}

    async def _mutate(self, path: str, extra_headers: dict | None = None, **kwargs) -> httpx.Response:
        """The token belongs to the Gestor process and changes when it restarts: on 403 fetch a
        fresh one and retry once."""
        for attempt in (1, 2):
            headers = await self._mutation_headers() | (extra_headers or {})
            try:
                return await self._send("POST", path, headers=headers, **kwargs)
            except GestorError as e:
                if e.status != 403 or attempt == 2:
                    raise
                self._token = None
        raise AssertionError("unreachable")

    async def trash(self, path: str) -> dict:
        resp = await self._mutate("/api/trash", json={"root": "presets", "path": path})
        return self._json(resp).get("entry") or {}

    async def restore(self, entry_id: str) -> dict:
        resp = await self._mutate("/api/restore", json={"id": entry_id})
        return self._json(resp).get("restored", {})

    async def upload(self, filename: str, content: bytes) -> dict:
        resp = await self._mutate("/api/preset-upload", {"Content-Type": "application/json"},
                                  params={"filename": filename}, content=content)
        return self._json(resp).get("preset", {})

    async def upload_scratch_model(self, filename: str, content: bytes) -> dict:
        """The scratch area keeps a file out of the library and deletes it by itself after a week."""
        resp = await self._mutate("/api/upload", {"Content-Type": "application/octet-stream"},
                                  params={"filename": filename, "scratch": "1"}, content=content)
        return self._json(resp).get("file") or {}

    def scratch_url(self, path: str) -> str:
        return f"{self._base_url}/api/download?root=scratch&path={quote(path)}"

    async def toggle_hold(self, job_id: str) -> None:
        await self._mutate("/api/queue/action", json={"action": "toggleHold", "id": job_id})
