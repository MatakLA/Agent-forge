"""From a finished run to main: auto-rebase, the gate (tests in a clean container), the review, mutation tests, and the
merge (internal Forgejo PR, or a GitHub/GitLab pull/merge request); rework / block / finish."""
import json, os, re, threading, time

import sbx

from . import ctx
from .util import approvals, extract_json, git_auth_env, parse_numstat, sh
from .store import event, jload, jsave, notify, save_task
from .jobs import INFLIGHT, PHASE, submit
from .repo import refresh_repo
from .knowledge import add_lesson
from .llm import llm
from .prompts import review_prompt


UNION_EXT = (".jsonl", ".md", ".txt", ".csv")


REBASE_LOCK = threading.Lock()


def auto_rebase(branch):
    """Rebase a task branch onto main without bothering the worker. Conflicts in SHARED_FILES that are line-based (.jsonl, .md,
    .txt, .csv - registries every task appends to) are resolved by keeping both sides (git merge-file --union); any other
    conflict aborts. Returns uptodate | rebased | conflict | missing | push-failed.
    Serialized: all gate steps share one rebase clone, and two interleaved rebases there could push one branch's result to
    another branch."""
    with REBASE_LOCK:
        return _auto_rebase(branch)


def _auto_rebase(branch):
    if not sbx.SAFE_ARG.fullmatch(branch or "") or branch in ("main", "master"):
        return "missing"
    auth = git_auth_env("orchestrator.token")
    who = ["-c", "user.name=orchestrator", "-c", "user.email=orchestrator@agent-forge.local"]
    d = os.path.join(ctx.FD, "rebase")
    if not os.path.isdir(os.path.join(d, ".git")):
        sh(["git", "clone", "-q", f"{sbx.FORGE_URL}/{sbx.FORGE_REPO}.git", d], timeout=180, env=auth)
    g = lambda *a, **kw: sh(["git", *a], cwd=d, timeout=kw.get("timeout", 120), env={"GIT_EDITOR": "true"})
    sh(["git", "fetch", "-q", "--prune", "origin"], cwd=d, timeout=180, env=auth)
    g("rebase", "--abort")
    if g("checkout", "-q", "-B", "_af_rebase", f"origin/{branch}")[0] != 0:
        return "missing"
    if g("merge-base", "--is-ancestor", "origin/main", "HEAD")[0] == 0:
        return "uptodate"
    shared = {f.strip() for f in str(ctx.C["SHARED_FILES"]).split(",") if f.strip()}
    rc = g(*who, "rebase", "origin/main", timeout=300)[0]
    for _ in range(60):
        if rc == 0:
            break
        conflicted = [f for f in g("diff", "--name-only", "--diff-filter=U")[1].split() if f]
        if not conflicted or any(f not in shared or not f.endswith(UNION_EXT) for f in conflicted):
            g("rebase", "--abort")
            return "conflict"
        for f in conflicted:
            parts = []
            for st in (2, 1, 3):           # ours, base, theirs
                r = g("show", f":{st}:{f}")
                tmp = os.path.join(ctx.FD, f".union-{st}")
                open(tmp, "w").write(r[1] if r[0] == 0 else "")
                parts.append(tmp)
            out = sh(["git", "merge-file", "--union", "-p", *parts], cwd=d)[1]
            open(os.path.join(d, f), "w").write(out)
            g("add", f)
        rc = g(*who, "-c", "core.editor=true", "rebase", "--continue", timeout=300)[0]
    if rc != 0:
        g("rebase", "--abort")
        return "conflict"
    r = sh(["git", "push", "-q", f"--force-with-lease={branch}:origin/{branch}", "origin", f"HEAD:{branch}"], cwd=d, timeout=180, env=auth)
    return "rebased" if r[0] == 0 else "push-failed"


def requeue_gate(t, why):
    """Test + review again (main moved, merge check pending). Each round costs a review call, so after 4 in a row the task is
    blocked for the owner instead of looping."""
    t = live_task(t)
    if not t:
        return
    t["regates"] = t.get("regates", 0) + 1
    t["history"].append(dict(at=sbx.now(), outcome="regate", why=why))
    # Invariant: the 4-round cap blocks after exactly 4 rounds (not 3 or 5), per task budget.
    if t["regates"] >= 4:
        save_task(t)
        return block(t, f"approved but could not be merged after {t['regates']} tries (last: {why})")
    t["status"] = "gate"
    t["not_before"] = time.time() + 30 * t["regates"]
    save_task(t)
    event(f"task {t['id']}: {why} - testing + review again", task=t["id"])


# ---------------------------------------------------------------- upstream (GitHub / GitLab): see bin/lib/upstream.py
def upstream_obj():
    """The project's GitHub/GitLab connection when FORGE_KIND says so, else None (merges stay on the internal Forgejo)."""
    import upstream
    if not upstream.enabled():
        return None
    try:
        return upstream.Upstream()
    except upstream.UpstreamError as e:
        event(f"upstream not usable: {e}")
        return None


def merge_upstream(t, up, who, fb, gate):
    """Approved branch -> pushed to upstream -> PR/MR -> merged (AUTO_MERGE) -> upstream main synced into Forgejo. When upstream
    needs more (required checks or reviews, a human merge with AUTO_MERGE=0) the task waits in status "merging"."""
    import upstream
    PHASE[t["id"]] = f"pull request on {up.kind}"
    body = (f"Task {t['id']} ({t['role']}), goal {t['goal']}.\n\nReview by {who}: {fb}\n\nGate (full test suite in a clean "
            f"container):\n```\n{gate[-2500:]}\n```\n\nOpened by Agent-Forge.\n")
    try:
        upstream.push_branch(up, ctx.FD, t["branch"])
        pr = up.open_pr(t["branch"], f"{t['id']}: {t['title']}"[:120], body)
    except upstream.UpstreamError as e:
        return lambda: requeue_gate(t, f"{up.kind} not reachable / refused: {str(e)[:200]}")
    if not ctx.C["AUTO_MERGE"]:
        return lambda: await_merge(t, pr, f"PR open on {up.kind} - waiting for you to merge it", who, fb)
    return lambda: await_merge(t, pr, f"PR opened on {up.kind}", who, fb, now=True)


def await_merge(t, pr, why, who="", fb="", now=False):
    t = live_task(t)
    if not t:
        return
    t.update(status="merging", upstream_pr=pr, not_before=0 if now else time.time() + 60, merge_reviewer=who, merge_feedback=fb[:800])
    save_task(t)
    event(f"task {t['id']}: {why}: {pr.get('url') or '#' + str(pr.get('number'))}", task=t["id"])


def step_upstream_merge(t):
    """A task in "merging": merge its upstream PR/MR when possible, or notice that it was merged / closed upstream."""
    import upstream
    up = upstream_obj()
    if not up:
        return lambda: None
    n = (t.get("upstream_pr") or {}).get("number")
    if not n:
        p = up.find_pr(t["branch"], "any")
        n = p.get("number") if p else None
    if not n:
        return lambda: block(t, f"could not locate {up.kind} PR for {t['branch']}")
    try:
        st = {}
        for _ in range(10):                     # GitHub computes mergeability asynchronously
            st = up.pr_state(n)
            if st["state"] != "open" or st["mergeable"] is not None:
                break
            time.sleep(3)
        if st["state"] == "open" and ctx.C["AUTO_MERGE"] and st["mergeable"]:
            ok, msg = up.merge(n)
            # Invariant: external merge between polls - re-read pr_state on failure so manual/external merges are recognized immediately.
            st = up.pr_state(n)
            if not ok and st.get("state") != "merged":
                st = dict(st, why=msg)
        if st["state"] == "merged":
            upstream.sync_main(up, ctx.FD)
            return lambda: finish(t, f"merged {up.kind} PR #{n}", t.get("merge_reviewer") or "foreman", t.get("merge_feedback") or "", n)
        if st["state"] == "closed":
            return lambda: block(t, f"its {up.kind} PR #{n} was closed without merging")
        if st["mergeable"] is False and re.search(r"dirty|conflict|need_rebase|behind", str(st.get("why")), re.I):
            upstream.sync_main(up, ctx.FD)
            rb = auto_rebase(t["branch"])
            if rb in ("rebased", "uptodate"):
                return lambda: requeue_gate(t, f"main moved on {up.kind} - auto-rebased, testing again before the merge")
            return lambda: rework(t, "Approved, but the branch conflicts with main. `git fetch origin && git rebase origin/main`, resolve, "
                                     "rerun the full test suite, `git push --force-with-lease`. Change nothing else.", "", count=False)
    except upstream.UpstreamError as e:
        st = dict(why=str(e))
    why = str(st.get("why") or "")

    def wait():
        t2 = jload(os.path.join(ctx.FD, "tasks", t["id"] + ".json"))
        if t2.get("merge_wait_why") != why:
            event(f"task {t['id']}: {up.kind} PR #{n} not merged yet ({why or 'waiting'}) - checking every 2 min", task=t["id"])
        t2.update(not_before=time.time() + 120, merge_wait_why=why)
        save_task(t2)
    return wait


def maybe_upstream_sync():
    """Every minute while connected: upstream main -> Forgejo main (fast-forward), so new tasks and the gate build on it."""
    import upstream
    if "upstream:sync" in INFLIGHT or not upstream.enabled() or time.time() - SYNC.get("at", 0) < 60:
        return
    SYNC["at"] = time.time()

    def step():
        up = upstream_obj()
        r = upstream.sync_main(up, ctx.FD) if up else "off"

        def apply():
            if r == "synced":
                event(f"main synced from {up.kind}")
            if r == "diverged" and SYNC.get("last") != "diverged":
                msg = f"Forgejo's main has commits that {up.kind}'s main does not have - not overwriting; run bin/upstream status"
                event(msg); sbx.alert(msg)
            SYNC["last"] = r
        return apply
    submit("upstream:sync", step)


SYNC = {}


def already_merged_pr(branch):
    """The number of an already-merged PR for `branch`, or None - used for crash recovery: a merge can succeed and
    then the process dies before the task file is marked done (next tick would otherwise re-run the whole
    gate+review on a now-empty diff and get confused by it). With an upstream: its PR/MR."""
    up = upstream_obj()
    if up:
        import upstream
        try:
            p = up.find_pr(branch, "merged")
            return p["number"] if p else None
        except upstream.UpstreamError:
            return None
    rc, out, err = sh([os.path.join(ctx.BIN, "forgejo-api"), "GET", f"/repos/{sbx.FORGE_REPO}/pulls?state=closed&limit=50"], timeout=30)
    if rc != 0:
        return None
    try:
        prs = json.loads(out)
    except Exception:
        return None
    if not isinstance(prs, list):
        return None
    # Invariant: PRs with head=None (e.g. deleted ref) must not raise AttributeError and hide merged PRs.
    return next((p["number"] for p in prs if isinstance(p, dict) and (p.get("head") or {}).get("ref") == branch and p.get("merged")), None)


def step_gate_review_merge(t):
    pr = already_merged_pr(t["branch"])
    if pr:
        up = upstream_obj()
        if up:
            try:
                import upstream
                upstream.sync_main(up, ctx.FD)
            except Exception:
                pass
        return lambda: finish(t, f"already merged as PR #{pr} (recovered after an interruption)", "foreman", "", pr)
    PHASE[t["id"]] = "rebase onto main"
    rb = auto_rebase(t["branch"])
    if rb == "conflict":
        return lambda: rework(t, "Your branch conflicts with main in a way that cannot be merged automatically. `git fetch origin && git rebase "
                                 "origin/main`, resolve the conflicts keeping both sides' intent, rerun the full test suite, `git push "
                                 "--force-with-lease`. Change nothing else.", "", count=False)
    if rb == "rebased":
        event(f"task {t['id']}: auto-rebased onto main", task=t["id"])
    PHASE[t["id"]] = "tests in a clean container"
    refresh_repo()
    rc, out, err = sh([os.path.join(ctx.BIN, "review-branch"), t["branch"]], timeout=900)
    gate = (out + err)[-8000:]
    if rc == 2:
        # infrastructure trouble is not the worker's fault and must not burn one of its attempts
        if "could not fetch the branch" in gate and sh(["git", "ls-remote", "--heads", "origin"], cwd=ctx.REPO, timeout=60,
                                                       env=git_auth_env("monitor.token"))[0] != 0:
            return lambda: requeue_gate(t, "Forgejo did not answer while the gate fetched the branch")
        if re.search(r"Cannot connect to the Docker daemon|Unable to find image|docker: Error response from daemon|No space left on device", gate):
            return lambda: requeue_gate(t, "the gate could not start its test container (Docker problem)")
        if "== SECRET:" in gate:
            return lambda: rework(t, "Gate failed: your branch adds a secret (" + gate.split("== SECRET: the branch adds ", 1)[1].split(" - refused")[0] +
                                     "). Never put keys or tokens into the repository. Remove it from every commit of the branch (rewrite the "
                                     "branch, e.g. `git rebase -i` and drop or edit the commit), then `git push --force-with-lease`.", gate)
        why = "branch was not pushed" if "could not fetch the branch" in gate else "the test suite fails in a clean container"
        return lambda: rework(t, f"Gate failed: {why}. Fix it (see the gate output) and push again.", gate)
    _, diff, _ = sh(["git", "diff", f"origin/main...origin/{t['branch']}"], cwd=ctx.REPO)
    _, numstat, _ = sh(["git", "diff", "--numstat", f"origin/main...origin/{t['branch']}"], cwd=ctx.REPO)
    added, removed = parse_numstat(numstat)
    t = jload(os.path.join(ctx.FD, "tasks", t["id"] + ".json")) or t
    t["diff"] = dict(added=added, removed=removed)
    save_task(t)
    os.makedirs(os.path.join(ctx.FD, "diffs"), exist_ok=True)
    open(os.path.join(ctx.FD, "diffs", t["id"] + ".diff"), "w").write(diff[:400000])
    if len(diff) > ctx.C["DIFF_MAX"]:
        diff = diff[:ctx.C["DIFF_MAX"]] + f"\n... (diff truncated, {len(diff)} bytes total)"
    PHASE[t["id"]] = "review by the orchestrator"
    text, who = llm("review", review_prompt(t, gate, diff), t["id"])
    v = extract_json(text) or {}
    verdict, fb = v.get("verdict"), str(v.get("feedback", ""))[:4000]
    if verdict not in ("approve", "reject", "rework"):
        # the review itself produced nothing usable (tool-call denial, rambling with no final JSON, ...) - this is a
        # reviewer/infra failure, not the worker's fault, so it must not burn one of the worker's rework attempts
        return lambda: retry_review(t, who, text)
    lesson = str(v.get("lesson") or "")
    if verdict == "reject":
        return lambda: (add_lesson(lesson, t["id"]), block(t, f"rejected by {who}: {fb}"))
    if verdict != "approve":
        return lambda: (add_lesson(lesson, t["id"]),
                        rework(t, (f"Review ({who}): " + fb) if fb else f"Review ({who}) asked for rework but gave no specifics - re-check the spec.", gate))
    muts = [m for m in v.get("mutations", []) if isinstance(m, dict) and {"name", "file", "old", "new"} <= set(m)][:4]
    if muts:
        spec = os.path.join(ctx.FD, "mutations", t["id"] + ".json")
        jsave(spec, muts)
        PHASE[t["id"]] = f"mutation tests ({len(muts)})"
        rc2, out2, _ = sh([os.path.join(ctx.BIN, "review-branch"), t["branch"], "--mutations", spec], timeout=1500)
        surv = [l.strip() for l in out2.splitlines() if l.strip().startswith(("SURVIVED", "BAD-SPEC"))]
        t2 = jload(os.path.join(ctx.FD, "tasks", t["id"] + ".json")) or t
        t2["mutations"] = [l.strip() for l in out2.splitlines() if l.strip().startswith(("KILLED", "SURVIVED", "BAD-SPEC"))][:8]
        save_task(t2)
        if any(l.startswith("SURVIVED") for l in surv):
            return lambda: rework(t, "Your tests do not catch these deliberate bugs in your code (mutation check); add tests that fail for "
                                     "each of them:\n" + "\n".join(f"- {m['name']}: in {m['file']} replace `{m['old']}` with `{m['new']}`"
                                                                   for m in muts if any(m['name'] in l for l in surv if l.startswith('SURVIVED'))), out2[-3000:])
    if "merge" in approvals() and not t.get("merge_approved"):
        return lambda: await_owner(t, "merge", who, fb, gate)
    return do_merge(t, who, fb, gate)


def do_merge(t, who, fb, gate):
    """Merge an approved branch: a pull / merge request on GitHub or GitLab, or a PR on the internal Forgejo. Returns the
    function the loop applies (finish, or rework / requeue / block when the merge itself fails)."""
    # Invariant: crash recovery / idempotency - retrying do_merge must detect prior merge and finish cleanly without duplicate PR.
    pr = already_merged_pr(t["branch"])
    if pr:
        return lambda: finish(t, f"already merged as PR #{pr} (recovered after an interruption)", who or "foreman", fb, pr)
    up = upstream_obj()
    if up:
        return merge_upstream(t, up, who, fb, gate)
    if not ctx.C["AUTO_MERGE"]:
        return lambda: finish(t, "approved (auto-merge off: merge by hand)", who, fb, None)
    PHASE[t["id"]] = "merge"
    body = os.path.join(ctx.FD, "tasks", t["id"] + ".pr.md")
    how = "after your approval in the dashboard" if t.get("merge_approved") else "(AUTO_MERGE=1) after the gate and the review passed"
    open(body, "w").write(f"Task {t['id']} ({t['role']}), goal {t['goal']}.\n\nReview by {who}: {fb}\n\nGate:\n```\n{gate[-2500:]}\n```\n"
                          f"\nMerged by Agent-Forge {how}.\n")
    rc, out, err = sh([os.path.join(ctx.BIN, "open-pr"), t["branch"], f"{t['id']}: {t['title']}"[:120], body], timeout=60)
    m = re.search(r"opened PR #(\d+)", out)
    if not m:
        prs = sh([os.path.join(ctx.BIN, "forgejo-api"), "GET", f"/repos/{sbx.FORGE_REPO}/pulls?state=open&limit=50"], timeout=60)[1]
        try:
            m2 = [p["number"] for p in json.loads(prs) if isinstance(p, dict) and (p.get("head") or {}).get("ref") == t["branch"]]
        except Exception:
            m2 = []
        if not m2:
            return lambda: block(t, f"could not open a PR: {(out + err)[-300:]}")
        num = m2[0]
    else:
        num = int(m.group(1))
    rc, out, err = sh([os.path.join(ctx.BIN, "merge-pr"), str(num)], timeout=150)
    if rc != 0:
        rb2 = auto_rebase(t["branch"]) if "not mergeable" in out + err else None
        if rb2 == "rebased":
            return lambda: requeue_gate(t, "main moved while it was reviewed - auto-rebased")
        if rb2 == "uptodate":
            # the branch already contains main, so this is no conflict - Forgejo's check was not done yet; try again later
            return lambda: requeue_gate(t, "merge check of the PR was still running - trying again")
        if rb2 in ("push-failed", "missing"):
            return lambda: requeue_gate(t, f"could not update the branch before merging ({rb2}) - trying again")
        if "not mergeable" in out + err:
            return lambda: rework(t, "Approved, but the branch conflicts with main. `git fetch origin && git rebase origin/main`, resolve, rerun "
                                     "the full test suite, `git push --force-with-lease`. Change nothing else.", "", count=False)
        return lambda: block(t, f"merge failed: {(out + err)[-300:]}")
    return lambda: finish(t, f"merged PR #{num}", who, fb, num)


def await_owner(t, kind, who="", fb="", gate=""):
    """APPROVE contains "merge": the reviewed, tested branch waits for the owner's OK (dashboard: Merge / Send back)."""
    t = live_task(t)
    if not t:
        return
    t.update(status="approval", awaiting=kind, merge_reviewer=who, merge_feedback=fb[:800], gate_tail=gate[-8000:], review_fails=0)
    save_task(t)
    event(f"task {t['id']}: approved by {who} - waiting for your OK to merge", task=t["id"])
    notify("approval", text=f"Merge {t['id']}: {t['title']}\nReview ({who}): {fb[:400]}")


def step_owner_merge(t):
    """The owner approved the merge. Main may have moved since the review: an auto-rebase that changed the branch sends it
    through the gate and the review again (it merges right after, the approval stays); a conflict goes back to the worker."""
    # Invariant: crash recovery - detect prior merge before re-rebasing or duplicate merge.
    pr = already_merged_pr(t["branch"])
    if pr:
        return lambda: finish(t, f"already merged as PR #{pr} (recovered after an interruption)", t.get("merge_reviewer") or "owner", t.get("merge_feedback") or "", pr)
    PHASE[t["id"]] = "merge (approved by you)"
    rb = auto_rebase(t["branch"])
    if rb == "conflict":
        return lambda: rework(t, "Approved, but the branch conflicts with main. `git fetch origin && git rebase origin/main`, resolve, rerun "
                                 "the full test suite, `git push --force-with-lease`. Change nothing else.", "", count=False)
    if rb == "rebased":
        return lambda: requeue_gate(t, "main moved since your approval - auto-rebased")
    if rb in ("missing", "push-failed"):
        return lambda: requeue_gate(t, f"could not update the branch before merging ({rb})")
    return do_merge(t, t.get("merge_reviewer") or "owner", t.get("merge_feedback") or "", t.get("gate_tail") or "")


def live_task(t):
    """The task as it is on disk now - None when the owner cancelled it meanwhile (a step that was already running must not
    bring it back)."""
    t = jload(os.path.join(ctx.FD, "tasks", t["id"] + ".json"))
    if not t or t.get("status") == "cancelled":
        if t:
            event(f"task {t['id']}: cancelled meanwhile - result of the running step dropped", task=t["id"])
        return None
    return t


def retry_review(t, who, raw_text):
    """The review call produced no parseable verdict (denied tool call, rambling with no JSON, ...). Not the worker's
    fault: retry the review itself a few times (same branch, no new worker run) before escalating."""
    t = live_task(t)
    if not t:
        return
    t["review_fails"] = t.get("review_fails", 0) + 1
    note = (raw_text or "(empty reply)")[:500]
    t["history"].append(dict(at=sbx.now(), outcome="review-retry", reviewer=who, note=note))
    if t["review_fails"] > 3:
        block(t, f"the reviewer ({who}) could not produce a usable verdict after {t['review_fails']} tries - last reply: {note[:300]}")
        return
    t["status"] = "gate"
    save_task(t)
    event(f"task {t['id']}: {who} gave no usable review verdict (retry {t['review_fails']}/3, worker not penalised): {note[:200]}", task=t["id"])


def rework(t, feedback, gate_tail, count=True):
    t = live_task(t)
    if not t:
        return
    if count:
        t["attempts"] += 1
    t["history"].append(dict(at=sbx.now(), outcome="rework", feedback=feedback[:1500]))
    if t["attempts"] > ctx.C["MAX_REWORK"]:
        return block(t, f"still failing after {t['attempts']} attempts: {feedback[:300]}")
    t.update(status="pending", feedback=feedback, gate_tail=gate_tail, review_fails=0, regates=0, not_before=0)
    t.pop("merge_approved", None)              # new work needs a new approval
    t.pop("awaiting", None)
    save_task(t)
    event(f"task {t['id']} back to the worker (attempt {t['attempts'] + 1}): {feedback[:200]}", task=t["id"])


def block(t, why):
    t = live_task(t)
    if not t:
        return
    t["history"].append(dict(at=sbx.now(), outcome="blocked", why=why[:1500]))
    t.update(status="blocked", blocked_why=why)
    save_task(t)
    sbx.alert(f"foreman: task {t['id']} blocked: {why[:200]}")
    event(f"task {t['id']} BLOCKED - needs the owner: {why[:300]}", task=t["id"])
    notify("task_blocked", t)


def finish(t, what, who, fb, pr):
    t = jload(os.path.join(ctx.FD, "tasks", t["id"] + ".json"))
    t["history"].append(dict(at=sbx.now(), outcome="done", what=what, reviewer=who, feedback=fb[:800]))
    t.update(status="done", pr=pr, pr_url=(t.get("upstream_pr") or {}).get("url"), finished=sbx.now(), feedback="", gate_tail="",
             review_fails=0, regates=0, not_before=0,
             total_lines=total_lines_after_merge(t.get("files", [])))
    save_task(t)
    event(f"task {t['id']} done: {what} (review: {who})", task=t["id"])


def total_lines_after_merge(files):
    """Sum of line counts of the task's files as they stand on main right after its merge (0 for a file the merge deleted)."""
    if not files:
        return None
    sh(["git", "fetch", "-q", "origin", "main"], cwd=ctx.REPO, timeout=60)
    total = 0
    for f in files:
        _, out, _ = sh(["git", "show", f"origin/main:{f}"], cwd=ctx.REPO)
        total += out.count("\n") + (1 if out and not out.endswith("\n") else 0)
    return total
