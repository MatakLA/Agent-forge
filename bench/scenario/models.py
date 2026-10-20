"""Data model of tasklog: an Entry is one block of tracked time.

Public interface: Entry(start, minutes, label, tags, note, id), Entry.day, Entry.end, Entry.to_dict(), Entry.from_dict(d).
Invariants: minutes > 0; start is 'YYYY-MM-DDTHH:MM'; label is non-empty; tags are already normalized (see tags.py).
"""
import datetime as dt
from dataclasses import dataclass, field

START_FORMAT = "%Y-%m-%dT%H:%M"


@dataclass
class Entry:
    start: str
    minutes: int
    label: str
    tags: list = field(default_factory=list)
    note: str = ""
    id: int = 0

    def __post_init__(self):
        if not isinstance(self.minutes, int) or isinstance(self.minutes, bool) or self.minutes <= 0:
            raise ValueError("minutes must be a positive integer")
        if not str(self.label).strip():
            raise ValueError("label must not be empty")
        try:
            dt.datetime.strptime(self.start, START_FORMAT)
        except (TypeError, ValueError):
            raise ValueError("start must look like YYYY-MM-DDTHH:MM")
        self.label = str(self.label).strip()
        self.tags = list(self.tags)

    @property
    def day(self):
        return dt.date.fromisoformat(self.start[:10])

    @property
    def end(self):
        return dt.datetime.strptime(self.start, START_FORMAT) + dt.timedelta(minutes=self.minutes)

    def to_dict(self):
        return {"id": self.id, "start": self.start, "minutes": self.minutes, "label": self.label,
                "tags": list(self.tags), "note": self.note}

    @classmethod
    def from_dict(cls, d):
        return cls(start=d["start"], minutes=d["minutes"], label=d["label"], tags=d.get("tags", []),
                   note=d.get("note", ""), id=d.get("id", 0))
