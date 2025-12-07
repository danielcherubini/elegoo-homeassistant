"""
Minimal local file host for Elegoo printers (used for MQTT upload).

Serves registered files over HTTP so the printer can download them
via URL provided in the SDCP Upload command.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import uuid
from dataclasses import dataclass
from typing import Dict, Optional

from aiohttp import web


@dataclass
class HostedFile:
    path: str
    size: int
    md5: str
    name: str


class ElegooFileHost:
    """Lightweight aiohttp file host."""

    def __init__(self, host: str = "0.0.0.0", port: int = 0) -> None:  # noqa: S104
        self.host = host
        self.port = port
        self._runner: Optional[web.AppRunner] = None
        self._site: Optional[web.TCPSite] = None
        self._app: Optional[web.Application] = None
        self._routes: Dict[str, HostedFile] = {}
        self._lock = asyncio.Lock()

    @property
    def is_running(self) -> bool:
        return self._runner is not None and self._site is not None

    async def start(self) -> None:
        if self.is_running:
            return

        self._app = web.Application()
        self._app.add_routes([web.get("/{name}", self._serve)])
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, self.host, self.port)
        await self._site.start()
        # Discover bound port (if ephemeral)
        if self._runner.addresses:
            # addresses is list[(host, port)]
            self.port = self._runner.addresses[0][1]

    async def stop(self) -> None:
        if self._site:
            await self._site.stop()
        if self._runner:
            await self._runner.cleanup()
        self._app = None
        self._runner = None
        self._site = None
        self._routes.clear()

    @staticmethod
    def _compute_md5(path: str) -> str:
        md5 = hashlib.md5()
        with open(path, "rb") as f:  # noqa: PTH123
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                md5.update(chunk)
        return md5.hexdigest()

    async def register_file(self, path: str, *, name: str | None = None) -> HostedFile:
        """Register a file to be served.

        Returns HostedFile with generated name used in URL.
        """
        async with self._lock:
            if not os.path.exists(path):  # noqa: PTH110
                msg = f"File does not exist: {path}"
                raise FileNotFoundError(msg)

            base = name or os.path.basename(path)  # noqa: PTH119
            ext = os.path.splitext(base)[1]
            route_name = f"{uuid.uuid4().hex}{ext}"
            size = os.path.getsize(path)  # noqa: PTH202
            md5 = await asyncio.get_running_loop().run_in_executor(None, self._compute_md5, path)
            hosted = HostedFile(path=path, size=size, md5=md5, name=route_name)
            self._routes[route_name] = hosted
            return hosted

    async def unregister_file(self, name: str) -> None:
        async with self._lock:
            self._routes.pop(name, None)

    async def _serve(self, request: web.Request) -> web.StreamResponse:
        name = request.match_info.get("name", "")
        hosted = self._routes.get(name)
        if not hosted:
            return web.Response(status=404, text="Not Found")

        headers = {
            "Content-Type": "application/octet-stream",
            "Etag": hosted.md5,
            "Content-Length": str(hosted.size),
        }

        resp = web.StreamResponse(status=200, headers=headers)
        await resp.prepare(request)

        with open(hosted.path, "rb") as f:  # noqa: PTH123
            while True:
                data = f.read(1024 * 1024)
                if not data:
                    break
                await resp.write(data)

        await resp.write_eof()
        return resp

