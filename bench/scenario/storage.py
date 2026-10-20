"""JSON-file store of entries.

Public interface: Store(path) with add(entry), all(), since(day), by_id(i), update(i, **changes), delete(i); StoreError.
Invariants: ids are unique and never reused; all() is ordered by (start, id); every write replaces the file atomically;
a missing file is an empty store, a corrupt one raises StoreError instead of being overwritten.
"""
import json
import os
import tempfile

from models import Entry


class StoreError(Exception):
    pass


class Store:
    def __init__(self, path):
        self.path = path

    def _read(self):
        try:
            with open(self.path) as f:
                data = json.load(f)
        except FileNotFoundError:
            return {"next_id": 1, "entries": []}
        except json.JSONDecodeError as e:
            raise StoreError(f"{self.path} is not valid JSON: {e}")
        if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
            raise StoreError(f"{self.path} has an unexpected layout")
        data.setdefault("next_id", 1 + max([e.get("id", 0) for e in data["entries"]] or [0]))
        return data

    def _write(self, data):
        folder = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(folder, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=folder, prefix=".store-")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=1, sort_keys=True)
            os.replace(tmp, self.path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    def add(self, entry):
        data = self._read()
        entry.id = data["next_id"]
        data["next_id"] += 1
        data["entries"].append(entry.to_dict())
        self._write(data)
        return entry

    def all(self):
        entries = [Entry.from_dict(d) for d in self._read()["entries"]]
        return sorted(entries, key=lambda e: (e.start, e.id))

    def since(self, day):
        return [e for e in self.all() if e.day >= day]

    def by_id(self, entry_id):
        for e in self.all():
            if e.id == entry_id:
                return e
        return None

    def update(self, entry_id, **changes):
        data = self._read()
        for i, d in enumerate(data["entries"]):
            if d["id"] == entry_id:
                merged = dict(d, **changes)
                Entry.from_dict(merged)
                data["entries"][i] = merged
                self._write(data)
                return Entry.from_dict(merged)
        raise StoreError(f"no entry with id {entry_id}")

    def delete(self, entry_id):
        data = self._read()
        kept = [d for d in data["entries"] if d["id"] != entry_id]
        if len(kept) == len(data["entries"]):
            return False
        data["entries"] = kept
        self._write(data)
        return True
