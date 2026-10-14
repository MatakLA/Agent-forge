#!/usr/bin/env python3
"""web/dashboard/cache.py: the stale-while-revalidate cache behind the dashboard's slow reads (health, egress, disk, settings)."""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web"))
from dashboard.cache import SWR  # noqa: E402

fails = 0


def check(name, ok):
    global fails
    fails += not ok
    print(("ok   " if ok else "FAIL ") + name)


now = [1000.0]
calls = []


def make(vals):
    it = iter(vals)

    def fn():
        calls.append(1)
        return next(it)
    return fn


c = SWR(clock=lambda: now[0])
fn = make([{"v": 1}, {"v": 2}, {"v": 3}])
v, age = c.get("k", 60, fn)
check("first call computes", v == {"v": 1} and len(calls) == 1)
now[0] += 30
v, age = c.get("k", 60, fn)
check("within the TTL it is served from the cache (no second call)", v == {"v": 1} and len(calls) == 1 and 29 <= age <= 31)
now[0] += 40
v, age = c.get("k", 60, fn)
check("past the TTL the old value is served at once", v == {"v": 1})
for _ in range(50):
    if len(calls) == 2:
        break
    time.sleep(0.02)
check("... and refreshed in the background", len(calls) == 2)
time.sleep(0.05)
v, age = c.get("k", 60, fn)
check("the next read sees the refreshed value", v == {"v": 2} and age < 5)
v, age = c.get("k", 60, fn, fresh=True)
check("fresh=True waits for a new answer", v == {"v": 3} and len(calls) == 3)

# a failure never replaces a good value, and nothing bad is cached when there was nothing
c2 = SWR(clock=lambda: now[0])
bad = {"ok": False, "error": "ssh: timeout"}
v, age = c2.get("x", 10, lambda: bad)
check("an error on a cold cache is returned but not stored", v == bad and age == -1)
v, age = c2.get("x", 10, lambda: {"fine": 1})
check("... the next call tries again", v == {"fine": 1})
now[0] += 20
c2.get("x", 10, lambda: bad)
time.sleep(0.1)
v, age = c2.get("x", 10, lambda: bad)
check("a failing refresh keeps the last good value", v == {"fine": 1})

# drop() after a write
c2.drop()
v, age = c2.get("x", 10, lambda: {"fine": 2})
check("drop() forgets values (the next read recomputes)", v == {"fine": 2})

# single flight: parallel cold reads cost one computation
c3 = SWR()
n = []


def slow():
    n.append(1)
    time.sleep(0.2)
    return {"v": 1}


ts = [threading.Thread(target=lambda: c3.get("s", 60, slow)) for _ in range(5)]
[t.start() for t in ts]
[t.join() for t in ts]
check("five simultaneous cold reads run the command once", len(n) == 1)

# the warmer rebuilds what was asked for recently
c4 = SWR(keep_warm=100, clock=lambda: now[0])
k = []
c4.get("w", 10, lambda: (k.append(1) or {"v": len(k)}))
now[0] += 20
c4.warm_once()
check("warm_once refreshes an entry past its TTL", len(k) == 2)
now[0] += 500
c4.warm_once()
check("... but not one nobody asked for lately", len(k) == 2)

print("ALL OK" if not fails else f"{fails} FAILED")
sys.exit(1 if fails else 0)
