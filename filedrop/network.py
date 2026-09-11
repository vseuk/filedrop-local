"""Сетевые помощники: локальные адреса и форматирование размера."""

import socket


def _primary_ip():
    """Определяет адрес маршрута без фактической отправки данных."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            # TEST-NET-1 используется только для выбора маршрута.
            s.connect(("192.0.2.1", 9))
            return s.getsockname()[0]
    except OSError:
        return None


def list_local_ips():
    """Все подходящие IPv4-адреса машины; основной (маршрут по умолчанию) первым."""
    ips = []
    primary = _primary_ip()
    if primary:
        ips.append(primary)
    try:
        addresses = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
        for _, _, _, _, sockaddr in addresses:
            ip = sockaddr[0]
            if ip not in ips and not ip.startswith(("127.", "0.", "169.254.")):
                ips.append(ip)
    except OSError:
        pass
    if not ips:
        ips.append("127.0.0.1")
    return ips


def get_local_ip():
    return list_local_ips()[0]


def human_size(size):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
