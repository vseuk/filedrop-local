"""Константы и пользовательские настройки (data/config.json)."""

import os
import json

from .paths import config_path, downloads_folder

DEFAULT_PORT = 8080
MAX_PORT_TRIES = 20
CHUNK = 1024 * 256

DEFAULTS = {
    "port": DEFAULT_PORT,
    "folder": "",            # используется только для постоянного режима
    "temporary_folder": True, # новая временная папка на каждый запуск
    "pin_on_start": False,
    "theme": "light",        # тема веб-интерфейса по умолчанию
}


def load_config():
    data = dict(DEFAULTS)
    try:
        with open(config_path(), "r", encoding="utf-8") as f:
            loaded = json.load(f)
        if isinstance(loaded, dict):
            data.update(loaded)
    except (OSError, ValueError):
        pass
    if not data.get("folder") and not data.get("temporary_folder"):
        data["folder"] = downloads_folder()
    return data


def save_config(data):
    try:
        os.makedirs(os.path.dirname(config_path()), exist_ok=True)
        with open(config_path(), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except OSError:
        pass
