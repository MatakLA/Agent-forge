"""Command line entry point of tasklog.

Public interface: main(argv=None) -> exit code; build_parser(). Subcommands: add, list, report, export.
Invariants: main() never raises for user mistakes - it prints a message to stderr and returns 2 (usage) or 1 (store problems);
output goes to stdout; the store location comes from config.store_path().
"""
import argparse
import sys

import clock
import config
import export
import reports
import tags
from models import Entry
from storage import Store, StoreError


def _date(text):
    try:
        return clock.parse_date(text)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e))


def _duration(text):
    try:
        return clock.parse_duration(text)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e))


def _tags(text):
    try:
        return tags.parse_tags(text)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e))


def build_parser():
    p = argparse.ArgumentParser(prog="tasklog", description="Track where your time goes.")
    sub = p.add_subparsers(dest="command", required=True)

    add = sub.add_parser("add", help="record a block of time")
    add.add_argument("label")
    add.add_argument("duration", type=_duration, help="90, 90m, 2h or 1h30m")
    add.add_argument("--start", help="YYYY-MM-DDTHH:MM (default: now)")
    add.add_argument("--tags", type=_tags, default=[], help="comma separated tags")
    add.add_argument("--note", default="")

    ls = sub.add_parser("list", help="list entries")
    ls.add_argument("--since", type=_date)
    ls.add_argument("--tag")

    rep = sub.add_parser("report", help="print a report")
    rep.add_argument("kind", nargs="?", default="summary")
    rep.add_argument("--since", type=_date)

    ex = sub.add_parser("export", help="export entries")
    ex.add_argument("--format", choices=["csv", "json"], default="csv")
    ex.add_argument("--since", type=_date)
    return p


def _entries(store, since):
    return store.since(since) if since else store.all()


def cmd_add(args, store):
    cfg = config.load_config()
    entry = Entry(start=args.start or clock.now_iso(), minutes=args.duration, label=args.label,
                  tags=args.tags or list(cfg["default_tags"]), note=args.note)
    store.add(entry)
    print(f"added #{entry.id}: {entry.label} {clock.format_minutes(entry.minutes)}")
    return 0


def cmd_list(args, store):
    items = _entries(store, args.since)
    if args.tag:
        wanted = tags.normalize_tag(args.tag)
        items = [e for e in items if wanted in tags.entry_tags(e)]
    if not items:
        print("No entries.")
        return 0
    rows = [(e.id, e.start, clock.format_minutes(e.minutes), e.label, ",".join(e.tags)) for e in items]
    print(reports.render_table(["ID", "START", "TIME", "LABEL", "TAGS"], rows, "rllll"))
    return 0


def cmd_report(args, store):
    print(reports.build_report(_entries(store, args.since), args.kind))
    return 0


def cmd_export(args, store):
    sys.stdout.write(export.export(_entries(store, args.since), args.format))
    return 0


COMMANDS = {"add": cmd_add, "list": cmd_list, "report": cmd_report, "export": cmd_export}


def main(argv=None):
    args = build_parser().parse_args(argv)
    store = Store(config.store_path())
    try:
        return COMMANDS[args.command](args, store)
    except StoreError as e:
        print(f"tasklog: {e}", file=sys.stderr)
        return 1
    except ValueError as e:
        print(f"tasklog: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
