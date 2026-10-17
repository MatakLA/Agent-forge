"""Egress proxy log analysis and health checks (V7 step 3). Reads the squid logs from the `egress_logs` volume.

Public interface: allowlist(), local_allowlist(), local_endpoint(), runtime_missing_hosts(), proxy_health(), analyze().
Invariants:
- All allowlist filtering ignores blank lines and '#' comments, matching hostnames case-insensitively.
- Missing egress host resolution enforces offline coverage rules without making network calls.
"""
import ipaddress, json, os, re, subprocess, time
from collections import Counter
import runtimes
import sbx

IMAGE = "sandbox-worker:1.2.11"          # any image with `sh`+`cat`; used to read the log volume even when the proxy is stopped
VOLUME = "egress_logs"
ALLOWLIST = os.path.join(sbx.ROOT, "egress", "allowlist.txt")
ALLOWLIST_LOCAL = os.path.join(sbx.ROOT, "egress", "allowlist.local.txt")       # API-key runtimes' hosts (bin/worker-add)
LOCAL_ENDPOINT = os.path.join(sbx.ROOT, "egress", "local-endpoint.conf")     # the owner's model server (set_endpoint)
BASELINE = os.path.join(sbx.ROOT, "etc", "egress-baseline.txt")
STATE = os.path.join(sbx.RUNS, "egress-state.json")
ROTATE_BYTES = 5 * 1024 * 1024
PRIVATE_NETS = [ipaddress.ip_network(n) for n in
                "0.0.0.0/8 10.0.0.0/8 100.64.0.0/10 127.0.0.0/8 169.254.0.0/16 172.16.0.0/12 192.168.0.0/16 ::1/128 fc00::/7 fe80::/10".split()]


def sh(*a, timeout=90):
    try:
        return subprocess.run(list(a), capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:          # no docker on this machine, or it hangs: a failed call, not a crash
        return subprocess.CompletedProcess(list(a), 127, "", str(e))


def _lines(path):
    out = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for l in f:
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


def _read_logs(since=0.0):
    """One throwaway container: the log file sizes plus the access/private log lines newer than `since` (cut inside the container -
    an unrotated access.log can be 100+ MB). Returns (sizes, access_text, private_text), or None without the volume/docker."""
    if sh("docker", "volume", "inspect", VOLUME).returncode != 0:
        return None
    cut = f" | awk -v s={since:.3f} '$1>s'" if since > 0 else ""
    r = sh("docker", "run", "--rm", "--network", "none", "--user", "13:13", "--cap-drop", "ALL", "-v", f"{VOLUME}:/logs:ro",
           "--entrypoint", "sh", IMAGE, "-c",
           'for f in /logs/*; do echo "$(stat -c %s "$f") $(basename "$f")"; done; echo ===ACCESS===; '
           f"cat /logs/access.log* 2>/dev/null{cut}; echo ===PRIVATE===; cat /logs/private.log* 2>/dev/null{cut}", timeout=120)
    head, _, rest = r.stdout.partition("===ACCESS===\n")
    a, _, b = rest.partition("===PRIVATE===\n")
    sizes = {l.split()[1]: int(l.split()[0]) for l in head.splitlines() if len(l.split()) == 2 and l.split()[0].isdigit()}
    return sizes, a, b


def _events(a, b):
    main = _parse(a)
    priv = {(e["ts"], e["client"], e["url"]) for e in _parse(b, private=True)}
    for e in main:
        if (e["ts"], e["client"], e["url"]) in priv or is_private_host(e["host"]):
            e["private"] = True
    main.sort(key=lambda e: e["ts"])
    return main


def load_logs(since=0.0):
    """Events newer than `since` (events listed in the private log are flagged private) and the log file sizes, in ONE docker run."""
    got = _read_logs(since)
    return dict(events=[], sizes={}) if got is None else dict(events=_events(got[1], got[2]), sizes=got[0])


def load_events(since=0.0):
    """Events from access.log* and private.log* newer than `since`."""
    return load_logs(since)["events"]


def analyze(since=0.0, until=None, client=None, events=None, ack=True):
    """Summarize events in (since, until]; `client` = container name or IP to restrict to. Unacknowledged events raise alarms."""
    ev = load_events(since) if events is None else events
    cmap = client_map()
    ips = {v: k for k, v in cmap.items()}
    want = ips.get(client, client) if client else None
    acked = state().get("acked_until", 0.0) if ack else 0.0
    allow, base = set(allowlist()), baseline()
    ep = local_endpoint()
    local = (ep[0], str(ep[1])) if ep else None
    res = dict(allowed=Counter(), denied=Counter(), noise=Counter(), private=[], new_domains=Counter(), plain_http=Counter(), events=0)
    seen_private = set()
    for e in ev:
        if e["client"] == "127.0.0.1" or e["ts"] <= since or (until and e["ts"] > until) or (want and e["client"] != want):
            continue
        res["events"] += 1
        who = cmap.get(e["client"], e["client"])
        if e["result"].startswith("TCP_TUNNEL") or (local and (e["host"], e["port"]) == local and not e["result"].startswith("TCP_DENIED")):
            res["allowed"][e["host"]] += 1         # an allowed tunnel, or a request to the owner's own model server
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


# ---- the owner's own model server (compat / aider runtimes, COMPAT_BASE_URL)
# A public HTTPS server (OpenRouter, a cloud vLLM) is one more allow-list entry. A server on the owner's network (Ollama on another
# machine) or one without TLS needs a narrow exception instead: squid includes egress/local-endpoint.conf before it refuses private
# destinations and plain HTTP, and that file allows exactly one address and port - nothing else on that network.
def endpoint_rule(url, resolve=True):
    """How egress must allow the model server at `url`: ("allowlist", host) for a public HTTPS host, ("local", host, port) for an
    address on a private network or a plain-HTTP server, None for an unusable URL. Pure unless `resolve` (DNS lookup of a name)."""
    import runtimes, socket
    t = runtimes.endpoint_target(url)
    if not t:
        return None
    scheme, host, port = t
    private = is_private_host(host)
    if not private and resolve and not re.fullmatch(r"[\d.]+|\[?[0-9a-f:]+\]?", host):
        try:
            private = any(is_private_host(a[4][0]) for a in socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP))
        except OSError:
            private = False
    if scheme == "https" and not private and port == 443:
        return ("allowlist", host)
    return ("local", host, port)


def local_endpoint_conf(host, port):
    """The squid snippet that lets the workers reach exactly host:port (also private / plain HTTP)."""
    if not re.fullmatch(r"[A-Za-z0-9.-]{1,253}|[0-9a-fA-F:.]{2,45}", host) or not 0 < int(port) < 65536:
        raise ValueError("bad host or port")
    return (f"# written by Agent-Forge for COMPAT_BASE_URL - the workers may reach exactly this address and port\n"
            f"acl af_model_server dst {host}\nacl af_model_port port {int(port)}\n"
            f"http_access allow workers af_model_server af_model_port\n")


def local_endpoint():
    """(host, port) allowed by egress/local-endpoint.conf, or None."""
    try:
        txt = open(LOCAL_ENDPOINT).read()
    except FileNotFoundError:
        return None
    h = re.search(r"^acl af_model_server dst (\S+)$", txt, re.M)
    p = re.search(r"^acl af_model_port port (\d+)$", txt, re.M)
    return (h.group(1).lower(), int(p.group(1))) if h and p else None


def set_endpoint(url):
    """Make egress allow the model server at `url` ("" = none) and reload the proxy. Returns a list of messages."""
    msgs = []
    rule = endpoint_rule(url) if url else None
    if url and not rule:
        raise ValueError("not a usable http(s) URL")
    os.makedirs(os.path.dirname(LOCAL_ENDPOINT), exist_ok=True)
    old = open(LOCAL_ENDPOINT).read() if os.path.exists(LOCAL_ENDPOINT) else None
    new = local_endpoint_conf(rule[1], rule[2]) if rule and rule[0] == "local" else "# no local model server (COMPAT_BASE_URL)\n"
    changed = old != new
    if changed:
        open(LOCAL_ENDPOINT, "w").write(new)
        msgs.append(f"egress: model server {rule[1]}:{rule[2]} allowed (exactly that address and port)" if rule and rule[0] == "local"
                    else "egress: no local model server")
    if rule and rule[0] == "allowlist" and rule[1] not in allowlist():
        with open(ALLOWLIST_LOCAL, "a") as f:
            f.write(f"# model server (COMPAT_BASE_URL)\n{rule[1]}\n")
        msgs.append(f"egress: {rule[1]} added to the allow-list")
        changed = True
    if changed:
        msgs += reload_proxy()
    return msgs


def reload_proxy():
    """Make the running proxy read its allow-lists again (squid -k reconfigure; running tunnels are not cut). A proxy created
    before allowlist.local.txt / local-endpoint.conf existed cannot see them: it must be recreated once (bin/egress-up)."""
    if sh("docker", "inspect", "egress-proxy").returncode != 0:
        return ["egress: no proxy running - the change applies when it starts"]
    has = sh("docker", "exec", "egress-proxy", "sh", "-c", "grep -q local-endpoint.conf /etc/squid/squid.${EGRESS_MODE}.conf && "
             "test -r /etc/squid/local-endpoint.conf && test -r /etc/squid/allowlist.local.txt")
    if has.returncode != 0:
        return ["egress: this proxy is older than the local allow-list - recreate it once (bin/egress-up, or System → Health) so it applies"]
    r = sh("docker", "exec", "egress-proxy", "sh", "-c", "squid -k reconfigure -f /etc/squid/squid.${EGRESS_MODE}.conf")
    return ["egress: proxy reloaded"] if r.returncode == 0 else [f"egress: reload FAILED: {(r.stderr or r.stdout).strip()[:200]}"]


# ---- the owner's decisions about a denied host (bin/egress-report --allow / --unallow / --baseline-add, System → Egress)
HOST_RE = re.compile(r"(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,61}[a-z0-9]")


def check_host(host):
    """A host the owner may allow or ignore: an exact DNS name (no wildcard, no IP address, no private name). Returns an error or ""."""
    h = str(host or "").strip().lower()
    if not HOST_RE.fullmatch(h):
        return "an exact host name such as pypi.org (no wildcards, no IP addresses)"
    if h.endswith((".local", ".internal", ".lan", ".home", ".arpa")) or h in ("localhost",):
        return "a name on a private network cannot be allowed here"
    return ""


def local_allowlist():
    return _lines(ALLOWLIST_LOCAL)


def allow_host(host):
    """The workers may reach `host` from now on (appended to egress/allowlist.local.txt; the proxy reloads). Returns messages."""
    h = host.strip().lower()
    err = check_host(h)
    if err:
        raise ValueError(err)
    if h in allowlist():
        return [f"{h} is already allowed"]
    with open(ALLOWLIST_LOCAL, "a") as f:
        f.write(f"# allowed by the owner {time.strftime('%Y-%m-%d', time.gmtime())}\n{h}\n")
    return [f"{h} allowed for the workers"] + reload_proxy()


def unallow_host(host):
    """Take `host` off egress/allowlist.local.txt again (the shipped allowlist.txt is not touched). Returns messages."""
    h = host.strip().lower()
    if h not in local_allowlist():
        return [f"{h} is not on the local allow-list (only hosts added here can be removed here)"]
    out, skip = [], False
    lines = open(ALLOWLIST_LOCAL).read().splitlines()
    for i, l in enumerate(lines):
        if l.split("#", 1)[0].strip() == h:
            if out and out[-1].startswith("# allowed by the owner"):
                out.pop()
            continue
        out.append(l)
    open(ALLOWLIST_LOCAL, "w").write("\n".join(out) + "\n")
    return [f"{h} removed - the workers can no longer reach it"] + reload_proxy()


def ignore_host(host):
    """Accept a denied host as known noise: it stays denied, but no longer raises an alarm (etc/egress-baseline.txt)."""
    h = host.strip().lower()
    err = check_host(h)
    if err:
        raise ValueError(err)
    if h not in baseline():
        os.makedirs(os.path.dirname(BASELINE), exist_ok=True)
        with open(BASELINE, "a") as f:
            f.write(h + "\n")
    return [f"{h} stays blocked but no longer raises an alarm"]


def runtime_missing_hosts(rt, endpoint_url=''):
    """Hosts bin/lib/runtimes.py hosts(rt, endpoint_url) requires for runtime `rt` that this
    install's egress will NOT currently let a worker reach. A host counts as covered when it
    matches (verbatim, case-insensitive; blank lines and lines starting with '#' ignored) a
    line of allowlist() or local_allowlist(), or - when runtimes.uses_endpoint(rt) is True -
    equals the host local_endpoint() currently allows. Returns a sorted list of the missing
    hostnames (empty = fully covered). An rt unknown to runtimes.RUNTIMES returns whatever
    runtimes.hosts(rt, endpoint_url) returns filtered the same way (do not add a new guard;
    hosts() already defines that edge case)."""
    covered = {
        line.strip().lower()
        for line in (allowlist() + local_allowlist())
        if line.strip() and not line.strip().startswith('#')
    }
    ep = local_endpoint()
    if runtimes.uses_endpoint(rt) and ep is not None:
        covered.add(ep[0].lower())
    return sorted(h for h in runtimes.hosts(rt, endpoint_url) if h.lower() not in covered)
