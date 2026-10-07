#!/usr/bin/env python3
"""GitHub / GitLab integration (bin/lib/upstream.py) without network or tokens: fake GitHub and GitLab REST APIs (a few
endpoints, in-memory pull/merge requests whose merge really merges in a local bare repository) and local git repositories
standing in for Forgejo and for upstream. Checks: import over Forgejo's placeholder, pushing a task branch, opening / finding /
merging a PR (MR), syncing main back (fast-forward), refusing to overwrite a diverged Forgejo main, and the access check.
Run: python3 tests/test_upstream.py   (needs git)"""
import json, os, re, shutil, subprocess, sys, tempfile, threading, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
T = tempfile.mkdtemp(prefix="af-upstream-")
bad = 0


def check(name, ok):
    global bad
    bad += not ok
    print(("ok   " if ok else "FAIL ") + name)


def git(d, *a):
    r = subprocess.run(["git", *a], cwd=d, capture_output=True, text=True,
                       env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"))
    if r.returncode:
        raise RuntimeError(f"git {a}: {r.stderr}")
    return r.stdout.strip()


def bare_with(path, files):
    """A bare repository whose main has one commit per (name, content)."""
    git(T, "init", "-q", "--bare", "-b", "main", path)
    w = path + ".w"
    git(T, "clone", "-q", path, w)
    for name, content in files:
        open(os.path.join(w, name), "w").write(content)
        git(w, "add", "-A"); git(w, "commit", "-qm", f"add {name}")
    git(w, "push", "-q", "origin", "HEAD:main")
    shutil.rmtree(w)


class Fake(BaseHTTPRequestHandler):
    """GitHub (/repos/...) and GitLab (/api/v4/projects/...) - just what upstream.py uses."""
    kind, prs, upstream_git, push = "github", {}, "", True

    def log_message(self, *a):
        pass

    def _send(self, code, obj):
        raw = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(raw)))
        self.end_headers(); self.wfile.write(raw)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def _merge(self, branch):
        w = tempfile.mkdtemp(dir=T)
        git(w, "clone", "-q", Fake.upstream_git, "r")
        r = os.path.join(w, "r")
        git(r, "merge", "--no-ff", "-m", f"Merge {branch}", f"origin/{branch}")
        git(r, "push", "-q", "origin", "HEAD:main")

    def do_GET(self):
        auth = self.headers.get("Authorization", "") + self.headers.get("PRIVATE-TOKEN", "")
        if "good" not in auth:
            return self._send(401, {"message": "Bad credentials"})
        u = urllib.parse.urlsplit(self.path)
        q = urllib.parse.parse_qs(u.query)
        if Fake.kind == "github":
            if u.path == "/repos/acme/shop":
                return self._send(200, {"full_name": "acme/shop", "default_branch": "main", "permissions": {"push": Fake.push}})
            if u.path == "/repos/acme/shop/pulls":
                head = q.get("head", [""])[0].split(":", 1)[-1]
                return self._send(200, [dict(number=n, html_url=f"https://gh/pr/{n}", state=p["state"], merged_at="x" if p["merged"] else None,
                                             head={"ref": p["branch"]}) for n, p in Fake.prs.items() if p["branch"] == head])
            m = re.fullmatch(r"/repos/acme/shop/pulls/(\d+)", u.path)
            if m:
                p = Fake.prs[int(m.group(1))]
                return self._send(200, dict(number=int(m.group(1)), state=p["state"], merged=p["merged"], mergeable=True, mergeable_state="clean"))
        else:
            if u.path == "/api/v4/projects/grp%2Fshop" or u.path == "/api/v4/projects/grp/shop":
                return self._send(200, {"path_with_namespace": "grp/shop", "default_branch": "main",
                                        "permissions": {"project_access": {"access_level": 30 if Fake.push else 20}}})
            if u.path.endswith("/merge_requests"):
                sb = q.get("source_branch", [""])[0]
                return self._send(200, [dict(iid=n, web_url=f"https://gl/mr/{n}", state="merged" if p["merged"] else "opened",
                                             source_branch=p["branch"]) for n, p in Fake.prs.items() if p["branch"] == sb])
            m = re.search(r"/merge_requests/(\d+)$", u.path)
            if m:
                p = Fake.prs[int(m.group(1))]
                return self._send(200, dict(iid=int(m.group(1)), state="merged" if p["merged"] else "opened", detailed_merge_status="mergeable"))
        self._send(404, {"message": "not faked: " + u.path})

    def do_POST(self):
        b = self._body()
        n = len(Fake.prs) + 1
        if Fake.kind == "github" and self.path == "/repos/acme/shop/pulls":
            Fake.prs[n] = dict(branch=b["head"], state="open", merged=False)
            return self._send(201, dict(number=n, html_url=f"https://gh/pr/{n}"))
        if Fake.kind == "gitlab" and self.path.endswith("/merge_requests"):
            Fake.prs[n] = dict(branch=b["source_branch"], state="open", merged=False)
            return self._send(201, dict(iid=n, web_url=f"https://gl/mr/{n}"))
        self._send(404, {})

    def do_PUT(self):
        self._body()
        m = re.search(r"/(?:pulls|merge_requests)/(\d+)/merge$", self.path)
        if not m:
            return self._send(404, {})
        p = Fake.prs[int(m.group(1))]
        self._merge(p["branch"])
        p.update(merged=True, state="closed")
        return self._send(200, {"merged": True} if Fake.kind == "github" else {"state": "merged"})


srv = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
threading.Thread(target=srv.serve_forever, daemon=True).start()
api = f"http://127.0.0.1:{srv.server_address[1]}"

# a throwaway Agent-Forge root (bin/ only) whose "Forgejo" is a local bare repository
root = os.path.join(T, "af")
shutil.copytree(os.path.join(ROOT, "bin"), os.path.join(root, "bin"))
os.makedirs(os.path.join(root, "etc")); os.makedirs(os.path.join(T, "secrets"))
os.environ.update(SANDBOX_SECRETS=os.path.join(T, "secrets"), FORGE_URL="file://" + os.path.join(T, "forgejo"), FORGE_REPO="org/shop")
open(os.path.join(T, "secrets", "orchestrator.token"), "w").write("x")
sys.path.insert(0, os.path.join(root, "bin", "lib"))
import upstream  # noqa: E402

try:
    for kind, repo in (("github", "acme/shop"), ("gitlab", "grp/shop")):
        Fake.kind, Fake.prs, Fake.push = kind, {}, True
        for d in ("forgejo", "up.git", "fd"):
            shutil.rmtree(os.path.join(T, d), ignore_errors=True)
        os.makedirs(os.path.join(T, "forgejo", "org"))
        bare_with(os.path.join(T, "forgejo", "org", "shop.git"), [("README.md", "placeholder\n")])
        Fake.upstream_git = os.path.join(T, "up.git")
        bare_with(Fake.upstream_git, [("app.py", "print('v1')\n"), ("lib.py", "X = 1\n")])
        os.environ.update(AF_UPSTREAM_API=api + ("/api/v4" if kind == "gitlab" else ""), AF_UPSTREAM_GIT="file://" + Fake.upstream_git)
        cfg = dict(kind=kind, repo=repo, url=f"https://{kind}.com", branch="main", merge="merge")
        fd = os.path.join(T, "fd")
        up = upstream.Upstream(cfg, token="good-token")
        ok, msg = up.check()
        check(f"{kind}: access check ok ({msg})", ok)
        check(f"{kind}: a token without push rights is reported", (setattr(Fake, "push", False), not up.check()[0])[1])
        Fake.push = True
        check(f"{kind}: a wrong token is reported", not upstream.Upstream(cfg, token="bad").check()[0])
        check(f"{kind}: import replaces Forgejo's placeholder with upstream main", upstream.sync_main(up, fd, force=True) == "synced"
              and git(T, "--git-dir", os.path.join(T, "forgejo", "org", "shop.git"), "rev-parse", "main")
              == git(T, "--git-dir", Fake.upstream_git, "rev-parse", "main"))
        # a worker's task branch on Forgejo
        w = os.path.join(T, "w")
        shutil.rmtree(w, ignore_errors=True)
        git(T, "clone", "-q", os.path.join(T, "forgejo", "org", "shop.git"), w)
        git(w, "checkout", "-q", "-b", "feat/0001-x"); open(os.path.join(w, "feature.py"), "w").write("Y = 2\n")
        git(w, "add", "-A"); git(w, "commit", "-qm", "feature"); git(w, "push", "-q", "origin", "feat/0001-x")
        upstream.push_branch(up, fd, "feat/0001-x")
        check(f"{kind}: the task branch is pushed upstream", "feature" in git(T, "--git-dir", Fake.upstream_git, "log", "--oneline", "feat/0001-x"))
        pr = up.open_pr("feat/0001-x", "0001-x: feature", "body")
        check(f"{kind}: PR/MR opened and found again (no duplicate)", pr["number"] == 1 and up.open_pr("feat/0001-x", "t", "b")["number"] == 1
              and len(Fake.prs) == 1)
        st = up.pr_state(1)
        check(f"{kind}: PR state open and mergeable", st["state"] == "open" and st["mergeable"] is True)
        merged, why = up.merge(1)
        check(f"{kind}: merged ({why}); found as merged for crash recovery", merged and up.pr_state(1)["state"] == "merged"
              and (up.find_pr("feat/0001-x", "merged") or {}).get("number") == 1)
        check(f"{kind}: upstream main synced back into Forgejo (fast-forward)", upstream.sync_main(up, fd) == "synced"
              and "feature" in git(T, "--git-dir", os.path.join(T, "forgejo", "org", "shop.git"), "log", "--oneline", "main"))
        check(f"{kind}: nothing to do when equal", upstream.sync_main(up, fd) == "uptodate")
        git(w, "checkout", "-q", "main"); git(w, "pull", "-q", "origin", "main")
        open(os.path.join(w, "local.py"), "w").write("Z = 3\n"); git(w, "add", "-A"); git(w, "commit", "-qm", "local only")
        git(w, "push", "-q", "origin", "main")
        check(f"{kind}: a diverged Forgejo main is never overwritten", upstream.sync_main(up, fd) == "diverged")
        try:
            upstream.push_branch(up, fd, "main")
            check(f"{kind}: refuses to push main", False)
        except upstream.UpstreamError:
            check(f"{kind}: refuses to push main", True)
finally:
    srv.shutdown()
    shutil.rmtree(T, ignore_errors=True)
print("upstream tests: " + ("ALL OK" if not bad else f"{bad} FAILED"))
sys.exit(1 if bad else 0)
