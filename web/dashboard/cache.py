"""Stale-while-revalidate cache for the slow read-only calls: each one is an ssh round trip into the VM that runs a
CLI (doctor, egress-report, foreman disk/settings), so the page asks the cache and a background thread keeps it warm."""
import threading
import time

_MISSING = object()


class _Entry:
    def __init__(self, fn, ttl):
        self.fn, self.ttl = fn, ttl
        self.val, self.at = _MISSING, 0.0
        self.asked = time.time()
        self.retry_at = 0.0
        self.lock = threading.Lock()          # single flight: one ssh per key at a time


def _failed(val):
    return isinstance(val, dict) and (val.get("ok") is False or ("error" in val and len(val) <= 2))


class SWR:
    def __init__(self, keep_warm=900, clock=time.time):
        self._d = {}
        self._mu = threading.Lock()
        self._clock = clock
        self.keep_warm = keep_warm

    def _entry(self, key, ttl, fn):
        with self._mu:
            e = self._d.get(key)
            if e is None:
                e = self._d[key] = _Entry(fn, ttl)
            e.fn, e.ttl, e.asked = fn, ttl, self._clock()
            return e

    def _refresh(self, e):
        """Compute and store. A failed answer never replaces a good one (it is retried soon). Returns the value."""
        with e.lock:
            if e.val is not _MISSING and self._clock() - e.at < e.ttl:
                return e.val                  # somebody else just refreshed it
            val = e.fn()
            if _failed(val) and e.val is not _MISSING:
                e.retry_at = self._clock() + 15
                return e.val
            if not _failed(val):
                e.val, e.at = val, self._clock()
            return val

    def get(self, key, ttl, fn, fresh=False):
        """(value, age in seconds). Younger than ttl: returned as is. Older: returned at once and refreshed in the
        background. Nothing cached yet (or fresh=True): the caller waits for fn()."""
        e = self._entry(key, ttl, fn)
        now = self._clock()
        if e.val is _MISSING or fresh:
            val = self._refresh_forced(e) if fresh else self._refresh(e)
            return val, (0 if not _failed(val) else -1)
        val, age = e.val, now - e.at
        if age >= ttl and now >= e.retry_at and not e.lock.locked():
            threading.Thread(target=self._refresh, args=(e,), daemon=True).start()
        return val, age

    def _refresh_forced(self, e):
        with e.lock:
            val = e.fn()
            if _failed(val) and e.val is not _MISSING:
                return e.val
            if not _failed(val):
                e.val, e.at = val, self._clock()
            return val

    def drop(self):
        """After a write: forget every value (the keys stay known, so the warmer rebuilds the ones in use)."""
        with self._mu:
            for e in self._d.values():
                e.val, e.at = _MISSING, 0.0

    def warm_once(self):
        now = self._clock()
        with self._mu:
            todo = [e for e in self._d.values() if now - e.asked < self.keep_warm and now >= e.retry_at
                    and (e.val is _MISSING or now - e.at >= e.ttl)]
        for e in todo:
            try:
                self._refresh(e)
            except Exception:
                e.retry_at = self._clock() + 30

    def warm_loop(self, every=20):
        while True:
            time.sleep(every)
            self.warm_once()
