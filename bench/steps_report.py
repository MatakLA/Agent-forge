#!/usr/bin/env python3
"""Per-run step mix from runs/*/stream.jsonl: how many tool calls write code vs read/search. Usage: steps_report.py RUNS_DIR [--json] [--html OUT] [--label-file FILE]
label-file: lines "run-dir-name<TAB>label" to group runs into variants (default: label = 'all')."""
import json, os, re, sys, glob, collections

WRITE = {"replace_file_content", "multi_replace_file_content", "write_to_file"}
def classify(name, cmd):
    if name in WRITE: return "write"
    if name == "view_file": return "read"
    if name == "run_command":
        c = (cmd or "").strip()
        if re.match(r"(sed -n|cat |head |tail )", c): return "read"
        if re.match(r"(grep|git grep|find|ls|rg)", c): return "search"
        if re.match(r"git (diff|show|log|status|fetch|checkout)", c): return "git-look"
        if re.search(r"(test|pytest|unittest|\.mjs|node )", c): return "test"
        if re.match(r"python3 -c", c): return "py-adhoc"
        if re.search(r"(git (add|commit|push)|>>?\s*\S+|tee |sed -i|patch)", c): return "write"
        return "other-cmd"
    return "other"

def scan(d):
    kinds = collections.Counter(); inp = cache = out = steps = 0
    try: f = open(os.path.join(d, "stream.jsonl"), errors="replace")
    except OSError: return None
    for line in f:
        try: e = json.loads(line)
        except ValueError: continue
        e = e.get("step_update") or {}
        if not e or e.get("state") == "ACTIVE": continue
        st = e.get("step_type")
        u = e.get("usage") or {}
        inp += u.get("input_tokens", 0) or 0; out += u.get("output_tokens", 0) or 0; cache += u.get("cache_read_tokens", 0) or 0
        if st == "tool":
            ti = e.get("tool_info") or {}
            kinds[classify(e.get("tool_name"), (ti.get("parameters") or {}).get("CommandLine"))] += 1; steps += 1
    return dict(run=os.path.basename(d), steps=steps, kinds=dict(kinds), input=inp, output=out, cache=cache)

def main():
    a = sys.argv[1:]; root = a[0]
    labels = {}
    if "--label-file" in a:
        for l in open(a[a.index("--label-file") + 1]):
            if "\t" in l: k, v = l.rstrip("\n").split("\t", 1); labels[k] = v
    rows = []
    for d in sorted(glob.glob(os.path.join(root, "*-worker-*"))):
        r = scan(d)
        if r and r["steps"]:
            if "--by-variant" in a:
                try: v = json.load(open(os.path.join(d, "meta.json"))).get("variant") or ""
                except Exception: v = ""
                r["label"] = "lean" if v.endswith("-lean") else "normal" if v else "unmarked"
            else:
                r["label"] = labels.get(r["run"], "all")
            if labels and r["run"] not in labels: continue
            rows.append(r)
    groups = collections.defaultdict(list)
    for r in rows: groups[r["label"]].append(r)
    summ = {}
    for g, rs in groups.items():
        k = collections.Counter()
        for r in rs: k.update(r["kinds"])
        n = sum(k.values()) or 1
        summ[g] = dict(runs=len(rs), steps_avg=round(sum(r["steps"] for r in rs) / len(rs), 1),
                       write_share=round(100 * k["write"] / n, 1), mix={x: round(100 * v / n, 1) for x, v in k.most_common()},
                       input_avg=int(sum(r["input"] for r in rs) / len(rs)), output_avg=int(sum(r["output"] for r in rs) / len(rs)))
    if "--json" in a: print(json.dumps(dict(summary=summ, runs=rows), indent=1)); return
    if "--html" in a:
        cols = {"write": "#2e9d57", "read": "#4c78c9", "search": "#e0a030", "git-look": "#9b59b6", "py-adhoc": "#c0504d", "test": "#17a2b8", "other-cmd": "#888", "other": "#bbb"}
        h = ["<meta charset=utf-8><title>Worker step mix</title><body style='font:14px sans-serif;max-width:900px;margin:2em auto'><h1>Worker step mix</h1>"]
        h.append("<p>" + " ".join(f"<span style='background:{c};color:#fff;padding:2px 6px'>{k}</span>" for k, c in cols.items()) + "</p>")
        h.append("<table cellpadding=6><tr><th align=left>variant<th>runs<th>avg steps<th>write %<th>avg input tok<th align=left>mix</tr>")
        for g, s in summ.items():
            bar = "".join(f"<span title='{k} {v}%' style='display:inline-block;height:18px;width:{v*3}px;background:{cols.get(k,'#999')}'></span>" for k, v in s["mix"].items())
            h.append(f"<tr><td>{g}<td>{s['runs']}<td>{s['steps_avg']}<td>{s['write_share']}<td>{s['input_avg']:,}<td>{bar}</tr>")
        h.append("</table><h2>Runs</h2><table cellpadding=4><tr><th>run<th>variant<th>steps<th>write<th>input tok</tr>")
        for r in rows: h.append(f"<tr><td>{r['run']}<td>{r['label']}<td>{r['steps']}<td>{r['kinds'].get('write',0)}<td>{r['input']:,}</tr>")
        open(a[a.index("--html") + 1], "w").write("\n".join(h)); return
    for g, s in summ.items(): print(g, json.dumps(s))
main()
