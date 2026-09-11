"""Создание и запуск HTTP-сервера."""

import threading
from http.server import ThreadingHTTPServer

from .config import MAX_PORT_TRIES
from .handler import Handler


class FileDropHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


def start_server(port, tries=MAX_PORT_TRIES):
    """Поднимает сервер на первом свободном порту начиная с port.

    Возвращает (httpd, port) или (None, None), если свободного порта нет.
    """
    for offset in range(tries):
        try:
            httpd = FileDropHTTPServer(("0.0.0.0", port + offset), Handler)
        except OSError:
            continue
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd, port + offset
    return None, None
