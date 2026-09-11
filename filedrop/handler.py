"""HTTP-обработчик: маршрутизация и реализация всех эндпоинтов."""

import os
import json
import mimetypes
import urllib.parse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler

from .config import CHUNK
from .state import ServerState
from .network import human_size
from .assets import load_asset, CONTENT_TYPES
from .security import compare_pin
from .multipart import MultipartSaver, parse_boundary, MultipartError
from .archive import build_zip


def _writable_dir(folder):
    try:
        os.makedirs(folder, exist_ok=True)
        return os.access(folder, os.W_OK)
    except OSError:
        return False


def _progress(done, total):
    if ServerState.on_progress:
        ServerState.on_progress(done, total)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    # -- авторизация ------------------------------------------------------

    def client_key(self):
        return self.client_address[0]

    def authorized(self):
        if not ServerState.pin:
            return True
        cookie = self.headers.get("Cookie", "")
        for part in cookie.split(";"):
            if "=" in part:
                k, v = part.strip().split("=", 1)
                if k == "fd_auth" and ServerState.tokens.valid(v):
                    return True
        return False

    def require_auth(self):
        if self.authorized():
            return True
        self.send_json({"error": "auth"}, HTTPStatus.UNAUTHORIZED)
        return False

    # -- низкоуровневые ответы --------------------------------------------

    def send_bytes(self, body, content_type="text/html; charset=utf-8",
                   status=HTTPStatus.OK, extra=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, obj, status=HTTPStatus.OK, extra=None):
        self.send_bytes(json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                        "application/json; charset=utf-8", status, extra)

    def _query(self):
        return urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            data = json.loads(raw) if raw else {}
            return data if isinstance(data, dict) else {}
        except ValueError:
            return {}

    def _safe_path(self, raw):
        """Полный путь к существующему файлу в папке загрузок, либо None."""
        safe = os.path.basename(raw or "")
        if not safe:
            return None
        full = os.path.join(ServerState.upload_folder, safe)
        return full if os.path.isfile(full) else None

    # -- GET --------------------------------------------------------------

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        routes = {
            "/": self._serve_index,
            "/index.html": self._serve_index,
            "/style.css": lambda: self._serve_asset("style.css"),
            "/app.js": lambda: self._serve_asset("app.js"),
            "/api/auth-required": self._auth_status,
            "/api/files": self.handle_list_files,
            "/api/notes": self.handle_get_notes,
            "/dl": self.handle_download,
            "/preview": self.handle_preview,
            "/dl-zip": self.handle_zip,
        }
        handler = routes.get(path)
        if handler:
            handler()
        else:
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def _serve_index(self):
        html = load_asset("index.html")
        theme = getattr(ServerState, "theme", "light")
        if theme and theme != "light":
            html = html.replace('data-theme="light"', f'data-theme="{theme}"', 1)
        self.send_bytes(html.encode("utf-8"))

    def _serve_asset(self, name):
        ext = os.path.splitext(name)[1]
        self.send_bytes(load_asset(name).encode("utf-8"),
                        CONTENT_TYPES.get(ext, "text/plain; charset=utf-8"))

    def _auth_status(self):
        self.send_json({"required": bool(ServerState.pin),
                        "authorized": self.authorized()})

    def handle_list_files(self):
        if not self.require_auth():
            return
        folder = ServerState.upload_folder
        items = []
        try:
            for name in sorted(os.listdir(folder)):
                full = os.path.join(folder, name)
                if os.path.isfile(full):
                    size = os.path.getsize(full)
                    items.append({"name": name, "size": size,
                                  "human": human_size(size),
                                  "mtime": int(os.path.getmtime(full))})
        except OSError:
            pass
        self.send_json({"files": items})

    def handle_get_notes(self):
        if not self.require_auth():
            return
        self.send_json({"text": ServerState.notes})

    # -- скачивание (с поддержкой Range/докачки) --------------------------

    def handle_download(self, inline=False):
        if not self.authorized():
            self.send_error(HTTPStatus.UNAUTHORIZED, "Auth required")
            return
        full = self._safe_path(self._query().get("name", [""])[0])
        if not full:
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
            return
        self._send_file(full, inline)

    def handle_preview(self):
        self.handle_download(inline=True)

    def _send_file(self, full, inline):
        try:
            size = os.path.getsize(full)
        except OSError:
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
            return
        name = os.path.basename(full)
        if inline:
            ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
        else:
            ctype = "application/octet-stream"

        start, end, partial = 0, size - 1, False
        rng = self.headers.get("Range")
        if rng and rng.startswith("bytes="):
            try:
                s, _, e = rng[6:].partition("-")
                start = int(s) if s else 0
                end = int(e) if e else size - 1
                if start > end or start >= size:
                    raise ValueError
                partial = True
            except ValueError:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
        end = min(end, size - 1)
        length = end - start + 1

        self.send_response(HTTPStatus.PARTIAL_CONTENT if partial else HTTPStatus.OK)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        disp = "inline" if inline else "attachment"
        quoted = urllib.parse.quote(name)
        self.send_header("Content-Disposition", f"{disp}; filename*=UTF-8''{quoted}")
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        try:
            with open(full, "rb") as f:
                f.seek(start)
                left = length
                while left > 0:
                    chunk = f.read(min(CHUNK, left))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    left -= len(chunk)
        except (OSError, BrokenPipeError, ConnectionError):
            pass

    def handle_zip(self):
        if not self.authorized():
            self.send_error(HTTPStatus.UNAUTHORIZED, "Auth required")
            return
        names = self._query().get("name", [])
        tmp = build_zip(ServerState.upload_folder, names)
        if tmp is None:
            self.send_error(HTTPStatus.NOT_FOUND, "No files")
            return
        try:
            tmp.seek(0, os.SEEK_END)
            size = tmp.tell()
            tmp.seek(0)
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(size))
            self.send_header("Content-Disposition",
                             "attachment; filename*=UTF-8''filedrop.zip")
            self.end_headers()
            while chunk := tmp.read(CHUNK):
                self.wfile.write(chunk)
        except (OSError, BrokenPipeError, ConnectionError):
            pass
        finally:
            tmp.close()

    # -- POST -------------------------------------------------------------

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        routes = {
            "/api/login": self.handle_login,
            "/upload": self.handle_upload,
            "/api/delete": self.handle_delete,
            "/api/notes": self.handle_set_notes,
        }
        handler = routes.get(path)
        if handler:
            handler()
        else:
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def handle_login(self):
        key = self.client_key()
        if ServerState.limiter.blocked(key):
            self.send_json({"ok": False, "locked": True},
                           HTTPStatus.TOO_MANY_REQUESTS)
            return
        pin = str(self._read_json().get("pin", ""))
        if ServerState.pin and compare_pin(pin, ServerState.pin):
            ServerState.limiter.reset(key)
            token = ServerState.tokens.issue()
            self.send_json({"ok": True}, extra={
                "Set-Cookie": f"fd_auth={token}; Path=/; Max-Age=86400; SameSite=Lax"})
        else:
            ServerState.limiter.fail(key)
            self.send_json({"ok": False}, HTTPStatus.UNAUTHORIZED)

    def handle_upload(self):
        if not self.require_auth():
            return
        folder = ServerState.upload_folder
        if not _writable_dir(folder):
            self.send_json({"error": "Папка недоступна для записи"},
                           HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        ctype = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in ctype:
            self.send_error(HTTPStatus.BAD_REQUEST, "Expected multipart")
            return
        boundary = parse_boundary(ctype)
        if not boundary:
            self.send_error(HTTPStatus.BAD_REQUEST, "No boundary")
            return
        length = int(self.headers.get("Content-Length", 0) or 0)
        saver = MultipartSaver(self.rfile, length, boundary, folder, _progress)
        try:
            saved = saver.parse()
        except (MultipartError, OSError) as e:
            self.send_json({"error": str(e)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        finally:
            _progress(0, 0)            # сбрасываем индикатор приёма в GUI

        if not saved:
            self.send_json({"error": "no files"}, HTTPStatus.BAD_REQUEST)
            return
        for item in saved:
            if ServerState.on_file_received:
                ServerState.on_file_received(item["name"], item["size"])
        self.send_json({"ok": True, "files": saved})

    def handle_delete(self):
        if not self.require_auth():
            return
        full = self._safe_path(self._read_json().get("name", ""))
        if not full:
            self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return
        try:
            os.remove(full)
        except OSError as e:
            self.send_json({"error": str(e)}, HTTPStatus.INTERNAL_SERVER_ERROR)
            return
        self.send_json({"ok": True})

    def handle_set_notes(self):
        if not self.require_auth():
            return
        ServerState.notes = str(self._read_json().get("text", ""))[:100000]
        if ServerState.on_notes_changed:
            ServerState.on_notes_changed(ServerState.notes)
        self.send_json({"ok": True})
