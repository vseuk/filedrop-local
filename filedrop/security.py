"""Безопасность: санитизация имён, сравнение PIN, лимит попыток, токены."""

import os
import re
import time
import secrets
import threading

# Управляющие символы и запрещённые в путях Windows символы.
_BAD_CHARS = re.compile(r'[\x00-\x1f<>:"/\\|?*]')
_WIN_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
MAX_NAME_LEN = 200


def sanitize_filename(name, fallback="file"):
    """Безопасное имя файла: без частей пути, спецсимволов и зарезервированных имён."""
    name = os.path.basename(name or "")
    name = _BAD_CHARS.sub("_", name).strip().strip(".")
    if not name:
        return fallback
    root, ext = os.path.splitext(name)
    if root.upper() in _WIN_RESERVED:
        root = "_" + root
    name = (root + ext)[:MAX_NAME_LEN]
    return name or fallback


def unique_name(folder, filename):
    """Добавляет _1, _2… если файл с таким именем уже существует."""
    name, ext = os.path.splitext(filename)
    candidate = filename
    counter = 1
    while os.path.exists(os.path.join(folder, candidate)):
        candidate = f"{name}_{counter}{ext}"
        counter += 1
    return candidate


def compare_pin(a, b):
    """Сравнение PIN за постоянное время (защита от тайминг-атак)."""
    if not a or not b:
        return False
    return secrets.compare_digest(str(a), str(b))


class RateLimiter:
    """Блокирует ключ (IP) после нескольких неудачных попыток на время lockout."""

    def __init__(self, max_attempts=5, lockout=300):
        self.max_attempts = max_attempts
        self.lockout = lockout
        self._fails = {}            # key -> [timestamps]
        self._lock = threading.Lock()

    def blocked(self, key):
        now = time.time()
        with self._lock:
            hits = [t for t in self._fails.get(key, []) if now - t < self.lockout]
            self._fails[key] = hits
            return len(hits) >= self.max_attempts

    def fail(self, key):
        with self._lock:
            self._fails.setdefault(key, []).append(time.time())

    def reset(self, key):
        with self._lock:
            self._fails.pop(key, None)


class TokenStore:
    """Cookie-токены сессий с TTL; умеет отзывать все сессии разом."""

    def __init__(self, ttl=86400):
        self.ttl = ttl
        self._tokens = {}           # token -> expiry timestamp
        self._lock = threading.Lock()

    def issue(self):
        token = secrets.token_urlsafe(16)
        with self._lock:
            self._tokens[token] = time.time() + self.ttl
        return token

    def valid(self, token):
        if not token:
            return False
        now = time.time()
        with self._lock:
            exp = self._tokens.get(token)
            if exp is None:
                return False
            if exp < now:
                self._tokens.pop(token, None)
                return False
            return True

    def revoke_all(self):
        with self._lock:
            self._tokens.clear()

    def count(self):
        now = time.time()
        with self._lock:
            return sum(1 for exp in self._tokens.values() if exp >= now)
