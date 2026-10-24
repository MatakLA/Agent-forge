#!/usr/bin/env python3
"""Draws the result charts of docs/RESULTS.md as plain SVG (stdlib only, light and dark theme) from the committed data:
docs/field-report-data.json, docs/parallel-data.json (tools/ledger_stats.py) and bench/results/runs.jsonl.
Usage: python3 tools/results_charts.py [outdir]   (default docs/img)"""
import json, os, sys
from xml.sax.saxutils import escape

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
W = 760
STYLE = """<style>
:root{--bg:#ffffff;--fg:#1f2328;--mut:#59636e;--grid:#d1d9e0;--base:#9aa4af;--a:#0969da;--good:#1a7f37;--bad:#cf222e;--neu:#59636e}
@media (prefers-color-scheme: dark){:root{--bg:#0d1117;--fg:#e6edf3;--mut:#9198a1;--grid:#30363d;--base:#6e7681;--a:#4493f8;--good:#3fb950;--bad:#f85149;--neu:#9198a1}}
text{font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;fill:var(--fg);font-size:13px}
.t{font-size:17px;font-weight:600}.s{fill:var(--mut);font-size:12px}.l{fill:var(--mut)}.v{font-weight:600}.h{font-size:13px;font-weight:600}
</style>"""
COLOR = {"good": "var(--good)", "bad": "var(--bad)", "neutral": "var(--neu)", "a": "var(--a)", "base": "var(--base)"}


class Svg:
    def __init__(self, title, desc):
        self.title, self.desc, self.parts, self.y = title, desc, [], 0

    def text(self, x, y, s, cls="", anchor="start"):
        self.parts.append(f'<text x="{x:.0f}" y="{y:.0f}" class="{cls}" text-anchor="{anchor}">{escape(str(s))}</text>')

    def rect(self, x, y, w, h, color):
        self.parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{max(w, 1):.1f}" height="{h}" rx="2" fill="{color}"/>')

    def line(self, x1, y1, x2, y2, dash=False, color="var(--grid)"):
        d = ' stroke-dasharray="3 3"' if dash else ""
        self.parts.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="{color}" stroke-width="1.5"{d}/>')

    def render(self, height):
        return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {height}" width="{W}" height="{height}" role="img" aria-labelledby="t d">'
                f'<title id="t">{escape(self.title)}</title><desc id="d">{escape(self.desc)}</desc>{STYLE}'
                f'<rect width="{W}" height="{height}" fill="var(--bg)"/>' + "".join(self.parts) + "</svg>\n")


def header(svg, title, sub):
    svg.text(24, 32, title, "t")
    svg.text(24, 52, sub, "s")


def bar_rows(svg, y, rows, vmax, label_w=190, bar_w=380, marker=None):
    """rows: (label, value, color, text, marker_value|None). Returns the y below the block."""
    for label, v, color, text, mark in rows:
        svg.text(24, y + 14, label, "l")
        svg.rect(24 + label_w, y, bar_w * v / vmax, 20, COLOR.get(color, color))
        if mark:
            x = 24 + label_w + bar_w * mark / vmax
            svg.line(x, y - 3, x, y + 23, dash=True, color="var(--fg)")
        svg.text(24 + label_w + bar_w * max(v, mark or 0) / vmax + 8, y + 14, text, "v")
        y += 30
    return y


def parallel(d):
    g = d["by_workers_running"]
    one = g[0]["ok_runs_per_hour_team"]
    svg = Svg("Throughput by number of workers running at once",
              "Runs that ended ok per hour for the whole team, with 1, 2, 3-4 and 5-8 workers running at the start of a run, and how long a run takes.")
    header(svg, "More workers: more work done, but far from linear",
           f"{d['runs']} executed runs, {d['first_run']} to {d['last_run']}. Dashed line = what perfect scaling from one worker would give.")
    svg.text(24, 88, "Runs that ended ok per hour, whole team", "h")
    rows = []
    for x in g:
        ideal = one * x["workers_avg"]
        label = "1 worker" if x["group"] == "1" else f"{x['group']} workers (avg {x['workers_avg']})"
        rows.append((label, x["ok_runs_per_hour_team"], "a", f"{x['ok_runs_per_hour_team']:.1f}/h  = {x['speedup_vs_one']:.1f}x", ideal if x["group"] != "1" else None))
    y = bar_rows(svg, 98, rows, max(max(x["ok_runs_per_hour_team"], one * x["workers_avg"]) for x in g) * 1.02)
    svg.text(24, y + 26, "Mean minutes per run (the same machine and model account serve every worker)", "h")
    rows = [(("1 worker" if x["group"] == "1" else f"{x['group']} workers"), x["mean_run_min"], "base" if x["group"] == "1" else "bad",
             f"{x['mean_run_min']:.0f} min,  {x['timeout_pct']}% hit the time limit", None) for x in g]
    y = bar_rows(svg, y + 36, rows, max(x["mean_run_min"] for x in g) * 1.02)
    lo, hi = d["calendar_speedup_range"]
    svg.text(24, y + 24, f"Same {d['runs']} runs one after another: {d['one_after_another_hours_run_length_when_alone']:.0f}-{d['one_after_another_hours_same_run_length']:.0f} h"
                         f" of work, done in {d['hours_with_work_running']:.0f} h with work running = {lo}-{hi}x less calendar time, same tokens.", "s")
    svg.text(24, y + 42, "Groups are not randomised: heavy periods also had harder tasks and a busier machine. Read the trend, not the third digit.", "s")
    return svg.render(int(y + 58))


def paired(title, sub, labels, metrics, desc):
    """metrics: (name, a, b, text_a, text_b, verdict, note). Two bars per metric, scaled to the larger value of that metric."""
    svg = Svg(title, desc)
    header(svg, title, sub)
    y = 84
    for name, a, b, ta, tb, verdict, note in metrics:
        svg.text(24, y, name, "h")
        svg.text(W - 24, y, note, "s", "end")
        m = max(a, b) or 1
        for i, (lab, v, t, c) in enumerate(((labels[0], a, ta, "base"), (labels[1], b, tb, verdict))):
            yy = y + 8 + i * 24
            svg.text(24, yy + 14, lab, "l")
            svg.rect(190, yy, 340 * v / m, 18, COLOR[c])
            svg.text(190 + 340 * v / m + 8, yy + 14, t, "v")
        y += 84
    return svg, y


def upgrades(fr):
    ba = fr["before_after_private_application_project"]
    b, a, sh = ba["before"], ba["after"], ba["stop_share_of_executed_runs"]
    M = lambda n: f"{n / 1e6:.2f}M" if n >= 1e6 else f"{n / 1e3:.0f}k"
    metrics = [
        ("Tokens per merged task (median)", b["tokens_per_merged_task"]["median"], a["tokens_per_merged_task"]["median"],
         M(b["tokens_per_merged_task"]["median"]), M(a["tokens_per_merged_task"]["median"]), "good", "interval excludes zero"),
        ("Runs stopped by the token cap", sh["token_cap"]["before"], sh["token_cap"]["after"], f"{sh['token_cap']['before']}%", f"{sh['token_cap']['after']}%", "good", "22% to 0%"),
        ("Merged with no rework (first pass)", b["first_pass_pct"], a["first_pass_pct"], f"{b['first_pass_pct']}%", f"{a['first_pass_pct']}%", "neutral", "no change"),
        ("Runs stopped by the time limit", sh["timeout"]["before"], sh["timeout"]["after"], f"{sh['timeout']['before']}%", f"{sh['timeout']['after']}%", "bad", "got worse"),
        ("Median minutes per run", b["median_minutes_per_executed_run"], a["median_minutes_per_executed_run"],
         f"{b['median_minutes_per_executed_run']} min", f"{a['median_minutes_per_executed_run']} min", "bad", "got worse"),
    ]
    svg, y = paired("What the 0.9.0 efficiency features did", f"Before / after on one project, cut 2026-10-07: {b['merged']} merged tasks before, {a['merged']} after. Not randomised.",
                    ("before", "after"), metrics, "Before and after bars for tokens per merged task, token-cap stops, first-pass rate, time-limit stops and minutes per run.")
    svg.text(24, y, "Half the tokens, same quality gate result, but runs got twice as long and many more end at the time limit. Both are real.", "s")
    return svg.render(int(y + 18))


def pack(fr):
    ab = fr["pack_ab"]["agent_forge_project"]
    m, fp = ab["metrics"], ab["first_pass"]
    k = lambda n: f"{n / 1e3:.0f}k"
    metrics = [
        ("Tokens per merged task (median)", m["tokens_per_merged"]["median_no_pack"], m["tokens_per_merged"]["median_pack"],
         k(m["tokens_per_merged"]["median_no_pack"]), k(m["tokens_per_merged"]["median_pack"]), "good" if m["tokens_per_merged"]["permutation_p_median"] < 0.05 else "neutral",
         f"p = {m['tokens_per_merged']['permutation_p_median']}"),
        ("Tool calls per merged task (median)", m["tool_calls_per_merged"]["median_no_pack"], m["tool_calls_per_merged"]["median_pack"],
         f"{m['tool_calls_per_merged']['median_no_pack']:.0f}", f"{m['tool_calls_per_merged']['median_pack']:.0f}", "good" if m["tool_calls_per_merged"]["permutation_p_median"] < 0.05 else "neutral",
         f"p = {m['tool_calls_per_merged']['permutation_p_median']} (borderline)"),
        ("Merged with no rework (first pass)", fp["no_pack"]["first_pass_pct"], fp["pack"]["first_pass_pct"], f"{fp['no_pack']['first_pass_pct']}%", f"{fp['pack']['first_pass_pct']}%",
         "neutral", f"p = {fp['permutation_p']}, interval includes 0"),
        ("Reworks per merged task (mean)", m["reworks_per_merged"]["mean_no_pack"], m["reworks_per_merged"]["mean_pack"], f"{m['reworks_per_merged']['mean_no_pack']}",
         f"{m['reworks_per_merged']['mean_pack']}", "neutral", f"p = {m['reworks_per_merged']['permutation_p_mean']}"),
    ]
    svg, y = paired("Context pack: tasks assigned at random", f"Agent-Forge's own project, {fp['pack']['merged'] + fp['no_pack']['merged']} merged tasks; about half got the code-map context pack.",
                    ("no pack", "with pack"), metrics, "Randomised comparison of tasks with and without the context pack: tokens, tool calls, first pass and reworks per merged task.")
    svg.text(24, y, "Green = the difference is unlikely to be chance (p < 0.05). Grey = could be chance.", "s")
    return svg.render(int(y + 18))


def bench(runs):
    ok = {}
    for r in runs:
        if r.get("reason") == "ok" and r["version"] not in ok:
            ok[r["version"]] = r
    vs = [v for v in ("v4", "v6", "v7", "v8") if v in ok]
    names = {"v4": "v4  save-tokens rules, read hints", "v6": "v6  + spans, callers, start state", "v7": "v7  v6 + cache-friendly order", "v8": "v8  batching rule only"}
    svg = Svg("Benchmark: one task, four prompt versions", "Tokens and tool calls of one worker run per prompt version (the same small task, model and worker).")
    header(svg, "Prompt versions on the same task: one run each", "Every run passed its checks. One run per version, so differences of this size are noise.")
    svg.text(24, 88, "Tokens", "h")
    y = bar_rows(svg, 98, [(names[v], ok[v]["tokens"], "a", f"{ok[v]['tokens'] / 1e3:.1f}k", None) for v in vs], max(ok[v]["tokens"] for v in vs) * 1.02, label_w=250, bar_w=340)
    svg.text(24, y + 26, "Tool calls", "h")
    y = bar_rows(svg, y + 36, [(names[v], ok[v]["tool_calls"], "a", f"{ok[v]['tool_calls']}", None) for v in vs], max(ok[v]["tool_calls"] for v in vs) * 1.02, label_w=250, bar_w=340)
    svg.text(24, y + 24, "Not shown as a result: no version is proven better. A run costs about 7% of one account's weekly quota, so repeats are slow to buy.", "s")
    return svg.render(int(y + 40))


def main(out):
    fr = json.load(open(os.path.join(ROOT, "docs", "field-report-data.json")))
    pd = json.load(open(os.path.join(ROOT, "docs", "parallel-data.json")))
    runs = [json.loads(l) for l in open(os.path.join(ROOT, "bench", "results", "runs.jsonl")) if l.strip()]
    os.makedirs(out, exist_ok=True)
    for name, svg in (("results-parallel", parallel(pd)), ("results-upgrades", upgrades(fr)), ("results-pack", pack(fr)), ("results-bench", bench(runs))):
        with open(os.path.join(out, name + ".svg"), "w", encoding="utf-8") as f:
            f.write(svg)
        print(os.path.join(out, name + ".svg"))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "docs", "img"))
