"""Упаковка нескольких файлов в zip для скачивания одним архивом."""

import os
import zipfile
import tempfile

from .security import sanitize_filename


def build_zip(folder, names):
    """Складывает выбранные файлы в временный zip.

    Возвращает открытый временный файл (перемотанный в начало) или None,
    если ни одного файла найти не удалось. Вызывающий обязан его закрыть.
    """
    tmp = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024)
    added = 0
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        for raw in names:
            safe = os.path.basename(raw or "")
            if not safe:
                continue
            full = os.path.join(folder, safe)
            if os.path.isfile(full):
                zf.write(full, arcname=sanitize_filename(safe))
                added += 1
    if added == 0:
        tmp.close()
        return None
    tmp.seek(0)
    return tmp
