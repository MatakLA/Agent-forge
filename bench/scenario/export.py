"""Exporting entries as CSV or JSON text.

Public interface: to_csv(entries), to_json(entries), export(entries, fmt).
Invariants: CSV has a header row and one row per entry with tags joined by ';'; JSON is a list of entry dicts sorted as given;
an unknown format raises ValueError.
"""
import csv
import io
import json

COLUMNS = ["id", "start", "minutes", "label", "tags", "note"]


def to_csv(entries):
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(COLUMNS)
    for e in entries:
        w.writerow([e.id, e.start, e.minutes, e.label, ";".join(e.tags), e.note])
    return buf.getvalue()


def to_json(entries):
    return json.dumps([e.to_dict() for e in entries], indent=1)


def export(entries, fmt):
    if fmt == "csv":
        return to_csv(entries)
    if fmt == "json":
        return to_json(entries)
    raise ValueError(f"unknown export format {fmt!r}")
