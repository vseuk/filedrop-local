"""Веб-ресурсы: редактируемые копии рядом с exe + чтение для сервера."""

import os
import shutil

from .paths import web_folder, resource_path

ASSETS = ("index.html", "style.css", "app.js")
ASSET_VERSION = "6"             # обновляем веб-интерфейс у уже установленных копий

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
}


def ensure_web_assets():
    """Кладёт вшитые ресурсы в data/web; при смене версии перезаписывает их."""
    folder = web_folder()
    os.makedirs(folder, exist_ok=True)
    vfile = os.path.join(folder, ".version")
    try:
        with open(vfile, "r", encoding="utf-8") as f:
            current = f.read().strip()
    except OSError:
        current = ""
    refresh = current != ASSET_VERSION

    for name in ASSETS:
        target = os.path.join(folder, name)
        if refresh or not os.path.exists(target):
            try:
                shutil.copyfile(resource_path("web", name), target)
            except OSError:
                pass
    if refresh:
        try:
            with open(vfile, "w", encoding="utf-8") as f:
                f.write(ASSET_VERSION)
        except OSError:
            pass


def load_asset(name):
    """Сначала редактируемая копия рядом с exe, затем вшитый ресурс."""
    for path in (os.path.join(web_folder(), name), resource_path("web", name)):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read()
        except OSError:
            continue
    return ""
