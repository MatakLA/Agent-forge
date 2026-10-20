"""Report helpers: totals per day/week/month/label, rankings, streaks, gaps and plain-text rendering.

Public interface: daily_totals, weekly_totals, monthly_totals, minutes_by_label, top_labels, longest_entries, average_per_day,
busiest_day, quietest_day, streaks, longest_streak, idle_gaps, percent_breakdown, histogram, weekday_profile, hour_profile,
label_trend, compare_periods, summarize, render_table, render_daily, render_weekly, render_monthly, render_summary, build_report.
Invariants: every function takes a list of Entry and never touches the store or the clock; totals are whole minutes;
empty input gives an empty or zero result, never an exception; dates are datetime.date.
"""
import datetime as dt
from collections import defaultdict

import clock


# ---------------------------------------------------------------- totals


def daily_totals(entries):
    """{date: minutes} for every day that has at least one entry."""
    out = defaultdict(int)
    for e in entries:
        out[e.day] += e.minutes
    return dict(sorted(out.items()))


def weekly_totals(entries):
    """{monday: minutes}."""
    out = defaultdict(int)
    for e in entries:
        out[clock.week_start(e.day)] += e.minutes
    return dict(sorted(out.items()))


def monthly_totals(entries):
    """{first-of-month: minutes}."""
    out = defaultdict(int)
    for e in entries:
        out[clock.month_start(e.day)] += e.minutes
    return dict(sorted(out.items()))


def minutes_by_label(entries, key=None):
    """{label: minutes}, biggest first. `key` maps an entry to the label to group by (default: its own label)."""
    out = defaultdict(int)
    for e in entries:
        out[key(e) if key else e.label] += e.minutes
    return dict(sorted(out.items(), key=lambda kv: (-kv[1], kv[0])))


def count_by_label(entries):
    out = defaultdict(int)
    for e in entries:
        out[e.label] += 1
    return dict(sorted(out.items(), key=lambda kv: (-kv[1], kv[0])))


def total_minutes(entries):
    return sum(e.minutes for e in entries)


def top_labels(entries, n=5):
    return list(minutes_by_label(entries).items())[:n]


def longest_entries(entries, n=3):
    return sorted(entries, key=lambda e: (-e.minutes, e.start, e.id))[:n]


def shortest_entries(entries, n=3):
    return sorted(entries, key=lambda e: (e.minutes, e.start, e.id))[:n]


# ---------------------------------------------------------------- averages and extremes


def average_per_day(entries, only_active_days=True):
    """Average minutes per day; over the active days only, or over the whole span first..last day."""
    totals = daily_totals(entries)
    if not totals:
        return 0.0
    if only_active_days:
        days = len(totals)
    else:
        days = (max(totals) - min(totals)).days + 1
    return sum(totals.values()) / days


def average_entry_length(entries):
    return total_minutes(entries) / len(entries) if entries else 0.0


def median_entry_length(entries):
    lengths = sorted(e.minutes for e in entries)
    if not lengths:
        return 0.0
    mid = len(lengths) // 2
    return float(lengths[mid]) if len(lengths) % 2 else (lengths[mid - 1] + lengths[mid]) / 2


def busiest_day(entries):
    totals = daily_totals(entries)
    if not totals:
        return None
    day = max(totals, key=lambda d: (totals[d], -d.toordinal()))
    return day, totals[day]


def quietest_day(entries):
    totals = daily_totals(entries)
    if not totals:
        return None
    day = min(totals, key=lambda d: (totals[d], d.toordinal()))
    return day, totals[day]


def busiest_week(entries):
    totals = weekly_totals(entries)
    if not totals:
        return None
    week = max(totals, key=lambda d: (totals[d], -d.toordinal()))
    return week, totals[week]


# ---------------------------------------------------------------- streaks and gaps


def streaks(entries):
    """Runs of consecutive days with entries, as [(first_day, last_day, length)]."""
    days = sorted(daily_totals(entries))
    out = []
    start = prev = None
    for d in days:
        if prev is not None and (d - prev).days == 1:
            prev = d
            continue
        if start is not None:
            out.append((start, prev, (prev - start).days + 1))
        start = prev = d
    if start is not None:
        out.append((start, prev, (prev - start).days + 1))
    return out


def longest_streak(entries):
    runs = streaks(entries)
    return max(runs, key=lambda r: (r[2], -r[0].toordinal())) if runs else None


def current_streak(entries, today):
    """Length of the run of days that ends today or yesterday; 0 when the last entry is older."""
    runs = streaks(entries)
    if not runs:
        return 0
    first, last, length = runs[-1]
    return length if (today - last).days <= 1 else 0


def idle_gaps(entries, min_minutes=60):
    """Gaps between the end of one entry and the start of the next on the same day, as [(end, next_start, minutes)]."""
    by_day = defaultdict(list)
    for e in entries:
        by_day[e.day].append(e)
    gaps = []
    for day in sorted(by_day):
        items = sorted(by_day[day], key=lambda e: (e.start, e.id))
        for a, b in zip(items, items[1:]):
            start_b = dt.datetime.strptime(b.start, "%Y-%m-%dT%H:%M")
            minutes = int((start_b - a.end).total_seconds() // 60)
            if minutes >= min_minutes:
                gaps.append((a.end, start_b, minutes))
    return gaps


def overlapping(entries):
    """Pairs of entries whose time ranges overlap (a data-quality check)."""
    items = sorted(entries, key=lambda e: (e.start, e.id))
    out = []
    for i, a in enumerate(items):
        for b in items[i + 1:]:
            if dt.datetime.strptime(b.start, "%Y-%m-%dT%H:%M") >= a.end:
                break
            out.append((a, b))
    return out


# ---------------------------------------------------------------- breakdowns


def percent_breakdown(entries, key=None):
    """{label: percent of all minutes}, rounded to one decimal, biggest first."""
    by = minutes_by_label(entries, key)
    total = sum(by.values())
    if not total:
        return {}
    return {k: round(100.0 * v / total, 1) for k, v in by.items()}


def weekday_profile(entries):
    """Minutes per weekday, Monday=0 .. Sunday=6; always seven values."""
    out = [0] * 7
    for e in entries:
        out[e.day.weekday()] += e.minutes
    return out


def hour_profile(entries):
    """Minutes started in each hour of the day; always 24 values."""
    out = [0] * 24
    for e in entries:
        out[int(e.start[11:13])] += e.minutes
    return out


def label_trend(entries, label):
    """Minutes per week spent on one label, as [(monday, minutes)] over every week from the first entry to the last."""
    own = [e for e in entries if e.label == label]
    if not own:
        return []
    weekly = weekly_totals(own)
    first, last = min(weekly), max(weekly)
    out, d = [], first
    while d <= last:
        out.append((d, weekly.get(d, 0)))
        d += dt.timedelta(days=7)
    return out


def compare_periods(entries, split_day):
    """Totals before and from `split_day`, with the change in percent (None when the earlier total is 0)."""
    before = total_minutes([e for e in entries if e.day < split_day])
    after = total_minutes([e for e in entries if e.day >= split_day])
    change = None if before == 0 else round(100.0 * (after - before) / before, 1)
    return {"before": before, "after": after, "change_percent": change}


def entries_between(entries, first, last):
    return [e for e in entries if first <= e.day <= last]


def entries_with_note(entries):
    return [e for e in entries if e.note.strip()]


def split_by_day(entries):
    out = defaultdict(list)
    for e in entries:
        out[e.day].append(e)
    return dict(sorted(out.items()))


def summarize(entries):
    """One dict with the headline numbers of a set of entries."""
    totals = daily_totals(entries)
    return {
        "entries": len(entries),
        "minutes": total_minutes(entries),
        "active_days": len(totals),
        "average_per_active_day": round(average_per_day(entries), 1),
        "average_entry": round(average_entry_length(entries), 1),
        "median_entry": median_entry_length(entries),
        "busiest_day": busiest_day(entries),
        "longest_streak": longest_streak(entries),
        "top_labels": top_labels(entries, 3),
    }


# ---------------------------------------------------------------- rendering


def histogram(values, width=20, fill="#"):
    """One bar per value, scaled so the biggest value is `width` characters wide."""
    top = max(values) if values else 0
    if top <= 0:
        return ["" for _ in values]
    return [fill * max(1 if v else 0, round(width * v / top)) for v in values]


def render_table(headers, rows, aligns=None):
    """Plain-text table. `aligns` is a string of 'l'/'r' per column (default all left). Cells are str()-ed."""
    cells = [[str(c) for c in r] for r in rows]
    aligns = aligns or "l" * len(headers)
    widths = [max([len(str(h))] + [len(r[i]) for r in cells]) for i, h in enumerate(headers)]

    def line(parts):
        out = []
        for i, p in enumerate(parts):
            out.append(p.rjust(widths[i]) if aligns[i] == "r" else p.ljust(widths[i]))
        return "  ".join(out).rstrip()

    return "\n".join([line([str(h) for h in headers])] + [line(r) for r in cells])


def render_daily(entries):
    totals = daily_totals(entries)
    if not totals:
        return "No entries."
    rows = [(d.isoformat(), clock.format_minutes(m)) for d, m in totals.items()]
    bars = histogram(list(totals.values()))
    rows = [(d, t, b) for (d, t), b in zip(rows, bars)]
    return render_table(["DAY", "TIME", ""], rows, "lrl")


def render_weekly(entries):
    totals = weekly_totals(entries)
    if not totals:
        return "No entries."
    rows = [(d.isoformat(), clock.format_minutes(m)) for d, m in totals.items()]
    return render_table(["WEEK OF", "TIME"], rows, "lr")


def render_monthly(entries):
    totals = monthly_totals(entries)
    if not totals:
        return "No entries."
    rows = [(d.strftime("%Y-%m"), clock.format_minutes(m)) for d, m in totals.items()]
    return render_table(["MONTH", "TIME"], rows, "lr")


def render_labels(entries, n=10):
    by = top_labels(entries, n)
    if not by:
        return "No entries."
    pct = percent_breakdown(entries)
    rows = [(label, clock.format_minutes(m), f"{pct[label]:.1f}%") for label, m in by]
    return render_table(["LABEL", "TIME", "SHARE"], rows, "lrr")


def render_summary(entries):
    s = summarize(entries)
    if not s["entries"]:
        return "No entries."
    lines = [
        f"Entries:        {s['entries']}",
        f"Total time:     {clock.format_minutes(s['minutes'])}",
        f"Active days:    {s['active_days']}",
        f"Per active day: {clock.format_minutes(round(s['average_per_active_day']))}",
        f"Average entry:  {clock.format_minutes(round(s['average_entry']))}",
    ]
    if s["busiest_day"]:
        d, m = s["busiest_day"]
        lines.append(f"Busiest day:    {d.isoformat()} ({clock.format_minutes(m)})")
    if s["longest_streak"]:
        a, b, n = s["longest_streak"]
        lines.append(f"Longest streak: {n} day{'s' if n != 1 else ''} ({a.isoformat()} to {b.isoformat()})")
    return "\n".join(lines)


def render_weekday_profile(entries):
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    prof = weekday_profile(entries)
    if not any(prof):
        return "No entries."
    rows = [(n, clock.format_minutes(m), b) for n, m, b in zip(names, prof, histogram(prof))]
    return render_table(["DAY", "TIME", ""], rows, "lrl")


def render_hour_profile(entries):
    prof = hour_profile(entries)
    if not any(prof):
        return "No entries."
    rows = [(f"{h:02d}:00", clock.format_minutes(m), b) for h, (m, b) in enumerate(zip(prof, histogram(prof))) if m]
    return render_table(["HOUR", "TIME", ""], rows, "lrl")


def render_gaps(entries, min_minutes=60):
    gaps = idle_gaps(entries, min_minutes)
    if not gaps:
        return "No gaps."
    rows = [(a.strftime("%Y-%m-%d %H:%M"), b.strftime("%H:%M"), clock.format_minutes(m)) for a, b, m in gaps]
    return render_table(["FROM", "TO", "GAP"], rows, "llr")


def render_streaks(entries):
    runs = streaks(entries)
    if not runs:
        return "No streaks."
    rows = [(a.isoformat(), b.isoformat(), n) for a, b, n in runs]
    return render_table(["FIRST", "LAST", "DAYS"], rows, "llr")


def render_longest(entries, n=3):
    rows = [(e.start, clock.format_minutes(e.minutes), e.label) for e in longest_entries(entries, n)]
    if not rows:
        return "No entries."
    return render_table(["START", "TIME", "LABEL"], rows, "lrl")


def build_report(entries, kind):
    """The text of a named report: day, week, month, labels, summary, weekday, hour, gaps, streaks, longest."""
    kinds = {
        "day": render_daily,
        "week": render_weekly,
        "month": render_monthly,
        "labels": render_labels,
        "summary": render_summary,
        "weekday": render_weekday_profile,
        "hour": render_hour_profile,
        "gaps": render_gaps,
        "streaks": render_streaks,
        "longest": render_longest,
    }
    if kind not in kinds:
        raise ValueError(f"unknown report {kind!r}, choose from {', '.join(kinds)}")
    return kinds[kind](entries)


# ---------------------------------------------------------------- goals and smoothing


def rolling_average(entries, window=7):
    """[(day, average minutes over the `window` days ending that day)] for every day from the first to the last entry."""
    totals = daily_totals(entries)
    if not totals:
        return []
    out = []
    for d in clock.date_range(min(totals), max(totals)):
        span = [totals.get(d - dt.timedelta(days=i), 0) for i in range(window)]
        out.append((d, round(sum(span) / window, 1)))
    return out


def goal_progress(entries, goal_minutes, first, last):
    """Share of a minutes goal reached between two days, capped at 100 percent; goal 0 counts as reached."""
    done = total_minutes(entries_between(entries, first, last))
    if goal_minutes <= 0:
        return 100.0
    return min(100.0, round(100.0 * done / goal_minutes, 1))


def weeks_meeting_goal(entries, goal_minutes):
    return [d for d, m in weekly_totals(entries).items() if m >= goal_minutes]


def merge_adjacent(entries):
    """Entries with the same label that touch (one ends when the next starts) as [(label, start, minutes)]."""
    items = sorted(entries, key=lambda e: (e.label, e.start, e.id))
    out = []
    for e in items:
        if out and out[-1][0] == e.label and out[-1][3] == e.start:
            label, start, minutes, _ = out[-1]
            out[-1] = (label, start, minutes + e.minutes, e.end.strftime("%Y-%m-%dT%H:%M"))
        else:
            out.append((e.label, e.start, e.minutes, e.end.strftime("%Y-%m-%dT%H:%M")))
    return [(label, start, minutes) for label, start, minutes, _ in out]


def label_first_last(entries):
    """{label: (first day, last day)}."""
    out = {}
    for e in entries:
        a, b = out.get(e.label, (e.day, e.day))
        out[e.label] = (min(a, e.day), max(b, e.day))
    return dict(sorted(out.items()))


def round_minutes(minutes, step):
    """Round to the nearest multiple of `step` (half up); step 1 changes nothing."""
    if step <= 1:
        return minutes
    return int((minutes + step // 2) // step * step)


def inactive_days(entries, first, last):
    totals = daily_totals(entries)
    return [d for d in clock.date_range(first, last) if d not in totals]
