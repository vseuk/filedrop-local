"""
FileDrop — обмен файлами между устройствами по локальной сети.

Точка входа. Вся логика разнесена по пакету filedrop/:
  filedrop/
    paths.py        пути и доступ к ресурсам (в т.ч. внутри EXE)
    config.py       константы и настройки (data/config.json)
    network.py      локальные IP, формат размера
    security.py     санитизация имён, PIN, лимит попыток, токены
    state.py        разделяемое состояние сервера
    assets.py       редактируемые веб-ресурсы рядом с exe
    multipart.py    потоковый разбор загрузок
    archive.py      zip-скачивание нескольких файлов
    handler.py      HTTP-эндпоинты
    server.py       запуск HTTP-сервера
    gui.py          окно tkinter
    web/            index.html, style.css, app.js

Запуск из исходника:
  python Main.py            (или  python -m filedrop)

Сборка в EXE:
  pip install pyinstaller qrcode pillow
  pyinstaller FileDrop.spec
"""

from filedrop.gui import main

if __name__ == "__main__":
    main()
