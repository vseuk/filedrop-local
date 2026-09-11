"""Потоковый разбор multipart/form-data с записью файлов сразу на диск.

Тело запроса может быть гигабайтным, поэтому оно никогда не загружается в
память целиком: читаем кусками по CHUNK и пишем файлы по мере поступления.
"""

import os

from .config import CHUNK
from .security import sanitize_filename, unique_name


class MultipartError(Exception):
    pass


def parse_boundary(content_type):
    """Достаёт boundary из заголовка Content-Type, либо None."""
    for part in content_type.split(";"):
        part = part.strip()
        if part.startswith("boundary="):
            return part.split("=", 1)[1].strip('"').encode()
    return None


class MultipartSaver:
    """Разбирает тело и сохраняет файловые части в folder. parse() -> [{name,size}]."""

    def __init__(self, rfile, length, boundary, folder, on_progress=None):
        self.rfile = rfile
        self.remaining = max(0, length)
        self.total = max(0, length)
        self.delim = b"--" + boundary
        self.folder = folder
        self.on_progress = on_progress
        self.buf = b""

    # -- буфер ------------------------------------------------------------

    def _fill(self, need):
        """Дочитывает поток, пока в буфере не наберётся need байт (или конец тела)."""
        while len(self.buf) < need and self.remaining > 0:
            chunk = self.rfile.read(min(CHUNK, self.remaining))
            if not chunk:
                self.remaining = 0
                break
            self.remaining -= len(chunk)
            self.buf += chunk
            if self.on_progress:
                self.on_progress(self.total - self.remaining, self.total)

    def _grow(self):
        """Подтягивает ещё данные. True, если буфер вырос."""
        before = len(self.buf)
        self._fill(len(self.buf) + CHUNK)
        return len(self.buf) != before

    def _read_line(self):
        """Возвращает одну строку (без CRLF) из заголовков части."""
        while b"\r\n" not in self.buf and self._grow():
            pass
        line, sep, rest = self.buf.partition(b"\r\n")
        if not sep:
            self.buf = b""
            return line
        self.buf = rest
        return line

    # -- разбор -----------------------------------------------------------

    def parse(self):
        os.makedirs(self.folder, exist_ok=True)
        saved = []

        # Пропускаем преамбулу до первого разделителя.
        self._fill(len(self.delim) + 4)
        idx = self.buf.find(self.delim)
        if idx == -1:
            raise MultipartError("boundary not found")
        self.buf = self.buf[idx:]

        while True:
            self._fill(len(self.delim) + 2)
            if not self.buf.startswith(self.delim):
                break
            tail = self.buf[len(self.delim):len(self.delim) + 2]
            if tail == b"--":
                break                       # закрывающий разделитель -> конец
            # Съедаем "delim\r\n" перед заголовками части.
            self.buf = self.buf[len(self.delim):]
            if self.buf[:2] == b"\r\n":
                self.buf = self.buf[2:]

            filename = self._read_part_headers()
            if filename is None:
                self._skip_part()           # не-файловое поле — пропускаем
                continue
            saved.append(self._stream_file(filename))

        return saved

    def _read_part_headers(self):
        """Читает заголовки части. Возвращает имя файла или None (не файл)."""
        filename = None
        while True:
            line = self._read_line()
            if line == b"":
                break
            text = line.decode("utf-8", "ignore")
            if text.lower().startswith("content-disposition"):
                for item in text.split(";"):
                    item = item.strip()
                    if item.startswith("filename="):
                        filename = item.split("=", 1)[1].strip('"')
        return filename

    def _stream_file(self, raw_name):
        """Пишет тело части в файл до следующего разделителя."""
        name = unique_name(self.folder, sanitize_filename(raw_name))
        path = os.path.join(self.folder, name)
        marker = b"\r\n" + self.delim
        keep = len(marker)
        written = 0
        with open(path, "wb") as out:
            while True:
                pos = self.buf.find(marker)
                if pos != -1:
                    out.write(self.buf[:pos])
                    written += pos
                    self.buf = self.buf[pos + 2:]   # delim остаётся в буфере
                    break
                if len(self.buf) > keep:
                    out.write(self.buf[:-keep])
                    written += len(self.buf) - keep
                    self.buf = self.buf[-keep:]
                if not self._grow() and self.remaining <= 0:
                    out.write(self.buf)             # тело кончилось без разделителя
                    written += len(self.buf)
                    self.buf = b""
                    break
        return {"name": name, "size": written}

    def _skip_part(self):
        """Проматывает тело не-файловой части до следующего разделителя."""
        marker = b"\r\n" + self.delim
        keep = len(marker)
        while True:
            pos = self.buf.find(marker)
            if pos != -1:
                self.buf = self.buf[pos + 2:]
                return
            if len(self.buf) > keep:
                self.buf = self.buf[-keep:]
            if not self._grow() and self.remaining <= 0:
                self.buf = b""
                return
