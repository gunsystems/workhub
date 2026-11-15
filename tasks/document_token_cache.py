"""Bounded, process-local encrypted delegated tokens; never cache document access."""
from collections import OrderedDict
from threading import Lock
from time import monotonic

_entries = OrderedDict()
_lock = Lock()


def get(key):
    with _lock:
        entry = _entries.get(key)
        if entry and entry[0] > monotonic():
            _entries.move_to_end(key)
            return entry[1]
        _entries.pop(key, None)


def put(key, encrypted_token, ttl):
    if ttl <= 0:
        return
    with _lock:
        now = monotonic()
        for expired in [k for k, value in _entries.items() if value[0] <= now]:
            del _entries[expired]
        _entries[key] = (now + min(ttl, 300), encrypted_token)
        _entries.move_to_end(key)
        while len(_entries) > 128:
            _entries.popitem(last=False)


def clear():
    with _lock:
        _entries.clear()
