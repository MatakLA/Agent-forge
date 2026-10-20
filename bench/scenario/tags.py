"""Tag handling: normalising, parsing and reading the tags of entries.

Public interface: UNTAGGED, normalize_tag(raw), parse_tags(text), entry_tags(entry), all_tags(entries).
Invariants: a normalised tag is lower case, has no '#', spaces become '-', at most 30 characters, and is never empty;
an entry without tags is reported under UNTAGGED by entry_tags().
"""
import re

UNTAGGED = "(untagged)"
_VALID = re.compile(r"[a-z0-9][a-z0-9_-]{0,29}")


def normalize_tag(raw):
    tag = re.sub(r"\s+", "-", str(raw).strip().lstrip("#").strip().lower())
    if not _VALID.fullmatch(tag):
        raise ValueError(f"invalid tag {raw!r}")
    return tag


def parse_tags(text):
    """'Backend, #api  review' -> ['backend', 'api', 'review']; duplicates dropped, order kept."""
    out = []
    for part in re.split(r"[,;]", text or ""):
        part = part.strip()
        if not part:
            continue
        for word in re.split(r"\s+(?=#)", part):
            tag = normalize_tag(word)
            if tag not in out:
                out.append(tag)
    return out


def entry_tags(entry):
    return list(entry.tags) if entry.tags else [UNTAGGED]


def all_tags(entries):
    seen = set()
    for e in entries:
        seen.update(entry_tags(e))
    return sorted(seen)
