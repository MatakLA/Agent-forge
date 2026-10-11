"""Steps run in a thread pool (planning, gate+review+merge, maintenance...). Each returns a function that the loop applies
in the main thread, so state changes stay single-threaded."""
import concurrent.futures as cf, time


from . import ctx


POOL = cf.ThreadPoolExecutor(max_workers=6)


INFLIGHT = {}             # key -> future


SINCE = {}                # key -> epoch second the step started


PHASE = {}                # task id -> human-readable phase of its gate/review/merge step (for the dashboard)


def submit(key, fn, *a):
    if key not in INFLIGHT:
        INFLIGHT[key] = POOL.submit(fn, *a)
        SINCE[key] = time.time()
