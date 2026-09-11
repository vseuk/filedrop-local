"""Файловые пути и доступ к встроенным ресурсам (в т.ч. внутри собранного EXE)."""

import os
import sys
import shutil
import tempfile


def _package_dir():
    return os.path.dirname(os.path.abspath(__file__))


def base_dir():
    """Папка рядом с exe (или корень проекта при запуске из исходника)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(_package_dir())


def resource_path(*parts):
    """Путь к ресурсу, вшитому в сборку PyInstaller (read-only по умолчанию)."""
    if getattr(sys, "frozen", False):
        base = sys._MEIPASS  # noqa: SLF001 — официальный путь распаковки PyInstaller
    else:
        base = _package_dir()
    return os.path.join(base, *parts)


def data_folder():
    return os.path.join(base_dir(), "data")


def web_folder():
    """Редактируемые копии веб-ресурсов рядом с exe."""
    return os.path.join(data_folder(), "web")


def downloads_folder():
    return os.path.join(data_folder(), "downloads")


def config_path():
    return os.path.join(data_folder(), "config.json")


def create_temporary_folder():
    """Создаёт отдельное хранилище только на время работы приложения."""
    return tempfile.mkdtemp(prefix="filedrop-")


def cleanup_temporary_folder(folder):
    """Удаляет только переданную временную папку после завершения работы."""
    if folder:
        shutil.rmtree(folder, ignore_errors=True)
