#!/usr/bin/env python3
"""egress.load_logs / load_events read the proxy logs in ONE docker run and cut old lines inside the container; the dashboard's
disk view sizes Docker from fast listings (maintenance.docker_summary) instead of the minutes-long `docker system df`."""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "bin", "lib"))
sys.path.insert(0, os.path.join(ROOT, "bin"))
import egress  # noqa: E402
from foreman import maintenance  # noqa: E402

fails = 0


def check(name, ok):
    global fails
    fails += not ok
    print(("ok   " if ok else "FAIL ") + name)


calls = []
OUT = ("167 access.log\n12 access.log.0\n0 private.log\n===ACCESS===\n"
       "1760000100.100 10.0.0.5 TCP_TUNNEL/200 CONNECT api.example.com:443 - HIER_DIRECT/1.2.3.4 -\n"
       "1760000200.200 10.0.0.6 TCP_DENIED/403 CONNECT play.googleapis.com:443 - HIER_NONE/- text/html\n"
       "===PRIVATE===\n"
       "1760000200.200 10.0.0.6 TCP_DENIED/403 CONNECT play.googleapis.com:443 - HIER_NONE/- text/html\n")


def fake_sh(*a, timeout=90):
    calls.append(a)
    if a[:3] == ("docker", "volume", "inspect"):
        return subprocess.CompletedProcess(a, 0, "[]", "")
    return subprocess.CompletedProcess(a, 0, OUT, "")


egress.sh = fake_sh
logs = egress.load_logs(since=1760000000.0)
run = [c for c in calls if c[1] == "run"]
check("one docker run for events and sizes", len(run) == 1)
check("old lines are cut inside the container", "awk -v s=1760000000.000 '$1>s'" in run[0][-1])
check("sizes parsed", logs["sizes"] == {"access.log": 167, "access.log.0": 12, "private.log": 0})
check("events parsed and sorted", [e["host"] for e in logs["events"]] == ["api.example.com", "play.googleapis.com"])
check("private log flags the event", [e["private"] for e in logs["events"]] == [False, True])
calls.clear()
egress.load_events(0.0)
check("no cut when reading everything", "awk" not in [c for c in calls if c[1] == "run"][0][-1])
egress.sh = lambda *a, timeout=90: subprocess.CompletedProcess(a, 1 if a[:3] == ("docker", "volume", "inspect") else 0, "", "")
check("no volume -> no events, no sizes", egress.load_logs(1.0) == dict(events=[], sizes={}))

check("docker sizes", maintenance._bytes("3.41GB") == 3410000000 and maintenance._bytes("824kB") == 824000
      and maintenance._bytes("391B*") == 391 and maintenance._bytes("junk") == 0)
check("docker size text", maintenance._human(5) == "5B" and maintenance._human(3.7e9) == "3.7GB")

BUILDER = ("ID                          RECLAIMABLE   SIZE       LAST ACCESSED\n"
           "8d8hewwakwpn93y9tt5m1vydo   true          391B*      2 weeks ago\n"
           "qqqeunqi016lkn3osrnqkdldk   true          1.461GB    7 days ago\n"
           "Shared:\t\t3.431GB\nPrivate:\t2.844GB\nReclaimable:\t6.274GB\nTotal:\t\t6.274GB\n")


def docker_sh(cmd, timeout=60, **kw):
    out = {"image": "293MB|sandbox-egress-proxy\n3.41GB|sandbox-worker\n100MB|<none>\n", "builder": BUILDER,
           "ps": "a1\nb2\n", "volume": "egress_logs\nforgejo\nworker-1\n"}[cmd[1]]
    return 0, out, ""


maintenance.sh = docker_sh
rows = {r["Type"]: r for r in maintenance.docker_summary()}
check("images row", rows["Images"] == dict(Type="Images", TotalCount="3", Size="~3.8GB", Reclaimable="100.0MB"))
check("build cache row", rows["Build cache"] == dict(Type="Build cache", TotalCount="2", Size="6.274GB", Reclaimable="6.274GB"))
check("containers and volumes are only counted", rows["Containers"]["TotalCount"] == "2" and rows["Local volumes"]["TotalCount"] == "3"
      and rows["Containers"]["Size"] == "-")
maintenance.sh = lambda *a, **k: (1, "", "no docker")
check("no docker -> no rows", maintenance.docker_summary() == [])

print("ALL OK" if not fails else f"{fails} FAILED")
sys.exit(1 if fails else 0)
