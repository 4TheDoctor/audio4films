"""A tiny Stremio addon that streams finished files from this computer over the home network.

One port serves both the addon (manifest + stream lists) and the video bytes, with HTTP Range
support so players can seek. Only files listed in <out_dir>/stremio.json and present on disk
are offered.
"""

from __future__ import annotations

import json
import mimetypes
import socket
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import __version__

REGISTRY_NAME = "stremio.json"
CHUNK = 1 << 20

MANIFEST = {
    "id": "community.audio4films",
    "version": __version__,
    "name": "audio4films",
    "description": "Videos muxed on your computer with an extra dubbed audio track, "
                   "streamed over your home network. The computer must be on.",
    "resources": ["stream"],
    "types": ["movie", "series"],
    "idPrefixes": ["tt"],
    "catalogs": [],
}


def load_registry(out_dir: Path) -> dict:
    try:
        return json.loads((out_dir / REGISTRY_NAME).read_text())
    except (OSError, ValueError):
        return {}


def register(out_dir: Path, stremio_id: str, file: Path, title: str = "", detail: str = "", group: str = "") -> dict:
    """Add or replace the entry for a Stremio id (tt1234567 for movies, tt1234567:S:E for episodes)."""
    out_dir = out_dir.resolve()
    rel = file.resolve().relative_to(out_dir)  # raises if the file is outside out_dir
    registry = load_registry(out_dir)
    registry[stremio_id] = {"path": rel.as_posix(), "title": title or file.stem, "detail": detail,
                            "group": group or "audio4films"}
    (out_dir / REGISTRY_NAME).write_text(json.dumps(registry, indent=2, ensure_ascii=False))
    return registry[stremio_id]


def lan_ip() -> str:
    """Best guess at this machine's LAN address (no packet is sent)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("10.255.255.255", 1))
            return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"


def make_handler(out_dir: Path):
    out_dir = out_dir.resolve()

    class Handler(BaseHTTPRequestHandler):
        server_version = f"audio4films/{__version__}"

        def _json(self, code: int, body: dict) -> None:
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "*")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_OPTIONS(self) -> None:  # noqa: N802
            self._json(200, {})

        def do_HEAD(self) -> None:  # noqa: N802
            self._route(head=True)

        def do_GET(self) -> None:  # noqa: N802
            self._route(head=False)

        def _route(self, head: bool) -> None:
            path = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path)
            if path in ("/", "/manifest.json"):
                return self._json(200, MANIFEST)
            if path.startswith("/stream/") and path.endswith(".json"):
                parts = path[: -len(".json")].split("/", 3)
                if len(parts) == 4:
                    return self._json(200, {"streams": self._streams(parts[3])})
            if path.startswith("/files/"):
                return self._file(path[len("/files/"):], head)
            self._json(404, {"error": "not found"})

        def _streams(self, stremio_id: str) -> list[dict]:
            entry = load_registry(out_dir).get(stremio_id)
            if not entry:
                return []
            file = (out_dir / entry["path"]).resolve()
            if not file.is_file() or out_dir not in file.parents:
                return []
            host = self.headers.get("Host") or f"{lan_ip()}:{self.server.server_address[1]}"
            url = f"http://{host}/files/" + urllib.parse.quote(entry["path"])
            size_gb = file.stat().st_size / 1e9
            detail = entry.get("detail") or ""
            return [{
                "name": "audio4films\nThis computer",
                "title": f"{entry.get('title', file.stem)}\n{detail + '  ·  ' if detail else ''}{size_gb:.1f} GB",
                "url": url,
                "behaviorHints": {"notWebReady": True, "bingeGroup": entry.get("group", "audio4films"),
                                  "filename": file.name},
            }]

        def _file(self, rel: str, head: bool) -> None:
            file = (out_dir / rel).resolve()
            if out_dir not in file.parents or not file.is_file():
                return self._json(404, {"error": "not found"})
            size = file.stat().st_size
            start, end = 0, size - 1
            rng = self.headers.get("Range", "")
            partial = rng.startswith("bytes=") and "," not in rng
            if partial:
                a, _, b = rng[len("bytes="):].partition("-")
                try:
                    if a:
                        start = int(a)
                        end = min(int(b), size - 1) if b else size - 1
                    else:  # suffix range: last N bytes
                        start = max(0, size - int(b))
                except ValueError:
                    partial = False
                    start, end = 0, size - 1
                if partial and start > end:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.end_headers()
                    return
            self.send_response(206 if partial else 200)
            ctype = mimetypes.guess_type(file.name)[0] or ("video/x-matroska" if file.suffix == ".mkv" else "application/octet-stream")
            self.send_header("Content-Type", ctype)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(end - start + 1))
            if partial:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            if head:
                return
            try:
                with file.open("rb") as fh:
                    fh.seek(start)
                    left = end - start + 1
                    while left > 0:
                        chunk = fh.read(min(CHUNK, left))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        left -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                pass  # the player closed the connection (normal when seeking)

        def log_message(self, fmt: str, *args) -> None:
            sys.stderr.write(f"{self.address_string()} {fmt % args}\n")

    return Handler


def make_server(out_dir: Path, host: str = "0.0.0.0", port: int = 7878) -> ThreadingHTTPServer:
    mimetypes.add_type("video/x-matroska", ".mkv")
    server = ThreadingHTTPServer((host, port), make_handler(out_dir))
    server.daemon_threads = True
    return server
