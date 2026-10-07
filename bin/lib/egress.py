"""Egress proxy log analysis and health checks (V7 step 3). Reads the squid logs from the `egress_logs` volume."""
import ipaddress, json, os, re, subprocess, time
from collections import Counter
import sbx

IMAGE = "sandbox-worker:1.2.11"          # any image with `sh`+`cat`; used to read the log volume even when the proxy is stopped
VOLUME = "egress_logs"
ALLOWLIST = os.path.join(sbx.ROOT, "egress", "allowlist.txt")
ALLOWLIST_LOCAL = os.path.join(sbx.ROOT, "egress", "allowlist.local.txt")       # API-key runtimes' hosts (bin/worker-add)
BASELINE = os.path.join(sbx.ROOT, "etc", "egress-baseline.txt")
STATE = os.path.join(sbx.RUNS, "egress-state.json")
ROTATE_BYTES = 5 * 1024 * 1024
PRIVATE_NETS = [ipaddress.ip_network(n) for n in
                "0.0.0.0/8 10.0.0.0/8 100.64.0.0/10 127.0.0.0/8 169.254.0.0/16 172.16.0.0/12 192.168.0.0/16 ::1/128 fc00::/7 fe80::/10".split()]


def sh(*a, timeout=90):
    return subprocess.run(list(a), capture_output=True, text=True, timeout=timeout)


def _lines(path):
    out = []
    try:
        for l in open(path):
            l = l.split("#", 1)[0].strip()
            if l:
                out.append(l)
    except FileNotFoundError:
        pass
    return out


def allowlist():
    return _lines(ALLOWLIST) + _lines(ALLOWLIST_LOCAL)


def baseline():
    return set(_lines(BASELINE))


def state():
    try:
        return json.load(open(STATE))
    except Exception:
        return {}


def save_state(st):
    sbx.ensure_runs()
    json.dump(st, open(STATE, "w"))


def is_private_host(h):
    try:
        ip = ipaddress.ip_address(h.strip("[]"))
    except ValueError:
        return False
    return any(ip in n for n in PRIVATE_NETS if n.version == ip.version)


def client_map():
    """IP -> container name on workers_internal (addresses change when containers are recreated)."""
    r = sh("docker", "network", "inspect", "workers_internal")
    m = {}
    try:
        for c in json.loads(r.stdout)[0].get("Containers", {}).values():
            m[c["IPv4Address"].split("/")[0]] = c["Name"]
    except Exception:
        pass
    return m


def worker_ip(name):
    for ip, n in client_map().items():
        if n == name:
            return ip
    return None


def _parse(text, private=False):
    ev = []
    for line in text.splitlines():
        p = line.split()
        if len(p) < 5 or "/" not in p[2]:
            continue
        try:
            ts = float(p[0])
        except ValueError:
            continue
        res, _, code = p[2].partition("/")
        method, url = p[3], p[4]
        if method == "-":
            continue
        if method == "CONNECT":
            host, _, port = url.rpartition(":")
            host = host.strip("[]")
        else:
            m = re.match(r"^[a-z]+://([^/:]+)(?::(\d+))?", url)
            host, port = (m.group(1), m.group(2) or "80") if m else (url, "")
        ev.append(dict(ts=ts, client=p[1], result=res, code=code, method=method, url=url, host=host.lower(), port=port, private=private))
    return ev


def load_events():
    """All events from access.log* and private.log*; events listed in the private log are flagged private."""
    if sh("docker", "volume", "inspect", VOLUME).returncode != 0:
        return []
    r = sh("docker", "run", "--rm", "--network", "none", "--user", "13:13", "--cap-drop", "ALL", "-v", f"{VOLUME}:/logs:ro",
           "--entrypoint", "sh", IMAGE, "-c", "cat /logs/access.log* 2>/dev/null; echo '===PRIVATE==='; cat /logs/private.log* 2>/dev/null")
    a, _, b = r.stdout.partition("===PRIVATE===\n")
    main = _parse(a)
    priv = {(e["ts"], e["client"], e["url"]) for e in _parse(b, private=True)}
    for e in main:
        if (e["ts"], e["client"], e["url"]) in priv or is_private_host(e["host"]):
            e["private"] = True
    main.sort(key=lambda e: e["ts"])
    return main


def analyze(since=0.0, until=None, client=None, events=None, ack=True):
    """Summarize events in (since, until]; `client` = container name or IP to restrict to. Unacknowledged events raise alarms."""
    ev = load_events() if events is None else events
    cmap = client_map()
    ips = {v: k for k, v in cmap.items()}
    want = ips.get(client, client) if client else None
    acked = state().get("acked_until", 0.0) if ack else 0.0
    allow, base = set(allowlist()), baseline()
    res = dict(allowed=Counter(), denied=Counter(), noise=Counter(), private=[], new_domains=Counter(), plain_http=Counter(), events=0)
    seen_private = set()
    for e in ev:
        if e["client"] == "127.0.0.1" or e["ts"] <= since or (until and e["ts"] > until) or (want and e["client"] != want):
            continue
        res["events"] += 1
        who = cmap.get(e["client"], e["client"])
        if e["result"].startswith("TCP_TUNNEL"):
            res["allowed"][e["host"]] += 1
            continue
        res["denied"][e["host"]] += 1
        fresh = e["ts"] > acked
        if e["private"]:
            k = (e["ts"], e["client"], e["url"])
            if fresh and k not in seen_private:
                seen_private.add(k)
                res["private"].append(dict(ts=e["ts"], worker=who, target=e["host"] + (":" + e["port"] if e["port"] else "")))
        elif e["host"] in base:
            res["noise"][e["host"]] += 1
        elif fresh:
            if e["method"] != "CONNECT":
                res["plain_http"][(who, e["host"])] += 1
            else:
                res["new_domains"][(who, e["host"])] += 1
    return res


def alarms(res, health=None):
    """List of (level, code, message). HIGH ones are the auto-trip triggers."""
    out = []
    if health:
        for p in health["problems"]:
            out.append(("WARN" if "health: starting" in p else "HIGH", "proxy", p))
    for p in res["private"]:
        out.append(("HIGH", "private-range", f"{p['worker']} tried to reach private/loopback destination {p['target']}"))
    for (who, host), n in res["new_domains"].items():
        out.append(("HIGH", "new-domain", f"{who} tried a domain that is neither allowed nor known noise: {host} ({n}x)"))
    for (who, host), n in res["plain_http"].items():
        out.append(("WARN", "plain-http", f"{who} tried plain HTTP to {host} ({n}x)"))
    return out


def proxy_health():
    """Mode must be enforce, container running+healthy, no 'allow all' in the active config, no wildcard entries in the allowlist."""
    h = dict(mode=None, running=False, health=None, problems=[])
    r = sh("docker", "inspect", "egress-proxy")
    try:
        d = json.loads(r.stdout)[0]
    except Exception:
        h["problems"].append("egress-proxy container does not exist (workers fail closed)")
        h["ok"] = False
        return h
    h["running"] = d["State"]["Running"]
    h["health"] = (d["State"].get("Health") or {}).get("Status")
    env = dict(x.split("=", 1) for x in d["Config"].get("Env") or [] if "=" in x)
    h["mode"] = env.get("EGRESS_MODE")
    if not h["running"]:
        h["problems"].append("egress-proxy is not running (workers fail closed)")
    elif h["health"] not in (None, "healthy"):
        h["problems"].append(f"egress-proxy health: {h['health']}")
    if h["mode"] != "enforce":
        h["problems"].append(f"egress-proxy is NOT in enforce mode (mode: {h['mode']})")
    elif h["running"]:
        r = sh("docker", "exec", "egress-proxy", "grep", "-cE", r"^http_access[[:space:]]+allow[[:space:]]+all[[:space:]]*$", "/etc/squid/squid.enforce.conf")
        if r.stdout.strip() not in ("0",):
            h["problems"].append("enforce config contains 'http_access allow all'")
    for e in allowlist():
        if e.startswith(".") or "*" in e:
            h["problems"].append(f"allowlist contains a wildcard entry: {e}")
    h["ok"] = not h["problems"]
    return h


def log_sizes():
    r = sh("docker", "run", "--rm", "--network", "none", "--user", "13:13", "--cap-drop", "ALL", "-v", f"{VOLUME}:/logs:ro",
           "--entrypoint", "sh", IMAGE, "-c", "for f in /logs/*; do echo \"$(stat -c %s $f) $(basename $f)\"; done")
    return {l.split()[1]: int(l.split()[0]) for l in r.stdout.splitlines() if len(l.split()) == 2}


def rotate(force=False):
    """Rotate squid logs (squid -k rotate; keeps 14 generations) when access.log > 5 MB, or always with force. Returns a message."""
    size = log_sizes().get("access.log", 0)
    if not force and size < ROTATE_BYTES:
        return f"no rotation needed (access.log {size} bytes < {ROTATE_BYTES})"
    r = sh("docker", "exec", "egress-proxy", "sh", "-c", "squid -k rotate -f /etc/squid/squid.${EGRESS_MODE}.conf")
    if r.returncode != 0:
        return f"rotation FAILED: {r.stderr.strip() or r.stdout.strip()}"
    st = state()
    st["last_rotate"] = time.time()
    save_state(st)
    return f"rotated (access.log was {size} bytes)"


def ack(now=None):
    st = state()
    st["acked_until"] = now or time.time()
    save_state(st)
