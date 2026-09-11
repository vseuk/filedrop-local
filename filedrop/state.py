"""Разделяемое состояние сервера. Заполняется и читается из GUI и обработчика."""

from .security import RateLimiter, TokenStore
from .paths import downloads_folder


class ServerState:
    upload_folder = downloads_folder()
    pin = None                     # None -> доступ без авторизации
    tokens = TokenStore()
    limiter = RateLimiter()
    notes = ""                     # общий текстовый буфер между устройствами
    theme = "light"                # тема веб-интерфейса по умолчанию (light/dark)

    # callbacks в GUI (вызываются из потоков сервера)
    on_file_received = None        # (filename, size)
    on_progress = None             # (received_bytes, total_bytes); (0, 0) -> сброс
    on_notes_changed = None        # (text)
