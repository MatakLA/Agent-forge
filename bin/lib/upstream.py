"""The project's real home on GitHub or GitLab ("upstream"), when FORGE_KIND is github or gitlab.

Design: the internal Forgejo stays the only git server the workers can reach (their egress allow-list never includes
github.com / gitlab.com, and they never hold an upstream token). The orchestrator, on the Agent-Forge machine, is the only one
talking to upstream:
  - approved task branches are pushed from Forgejo to upstream and opened as a pull / merge request there, then merged
    (AUTO_MERGE=1) - upstream branch protection, required checks and reviews apply as for any contributor;
  - upstream's main is synced back into Forgejo's main (fast-forward only), so workers and the gate always build on it.
Config (project.conf or etc/foreman.conf): FORGE_KIND, UPSTREAM_REPO (owner/repo or group/project), UPSTREAM_URL (default
https://github.com / https://gitlab.com; self-hosted works too), UPSTREAM_BRANCH (main), UPSTREAM_MERGE (merge|squash|rebase).
Token: secrets file github.token (fine-grained: Contents + Pull requests read/write on the one repository) or gitlab.token
(project access token, role Developer+: api, write_repository). Never in argv - git gets it through GIT_CONFIG_* variables."""
import base64, json, os, re, subprocess, time, urllib.error, urllib.parse, urllib.request
import sbx

KINDS = ("forgejo", "github", "gitlab")
REPO_RE = re.compile(r"[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+){1,4}")
URL_RE = re.compile(r"https?://[A-Za-z0-9.-]+(:\d+)?(/[A-Za-z0-9._~/-]*)?")


class UpstreamError(Exception):
    pass


def config(project=None):
    c = sbx.project_conf(project)
    kind = (c.get("FORGE_KIND") or "forgejo").strip().lower()
    return dict(kind=kind if kind in KINDS else "forgejo", repo=c.get("UPSTREAM_REPO", "").strip(),
                url=(c.get("UPSTREAM_URL") or ("https://gitlab.com" if kind == "gitlab" else "https://github.com")).strip().rstrip("/"),
                branch=(c.get("UPSTREAM_BRANCH") or "main").strip(), merge=(c.get("UPSTREAM_MERGE") or "merge").strip())


def enabled(project=None):
    c = config(project)
    return c["kind"] in ("github", "gitlab") and bool(REPO_RE.fullmatch(c["repo"]))


class Upstream:
    def __init__(self, cfg=None, token=None):
        self.c = cfg or config()
        if self.c["kind"] not in ("github", "gitlab"):
            raise UpstreamError("FORGE_KIND is not github or gitlab")
        if not REPO_RE.fullmatch(self.c["repo"]) or not URL_RE.fullmatch(self.c["url"]):
            raise UpstreamError("UPSTREAM_REPO / UPSTREAM_URL invalid")
        self.kind = self.c["kind"]
        self.token = token if token is not None else sbx.read(os.path.join(sbx.SECRETS, f"{self.kind}.token"))
        if not self.token:
            raise UpstreamError(f"no {self.kind}.token in the secrets directory (bin/secret-set {self.kind}.token, or the dashboard's setup)")
        u = self.c["url"]
        if self.kind == "github":
            self.api_base = os.environ.get("AF_UPSTREAM_API") or ("https://api.github.com" if u == "https://github.com" else u + "/api/v3")
        else:
            self.api_base = os.environ.get("AF_UPSTREAM_API") or (u + "/api/v4")
        self.git_url = os.environ.get("AF_UPSTREAM_GIT") or f"{u}/{self.c['repo']}.git"     # AF_UPSTREAM_*: tests only
        self.pid = urllib.parse.quote(self.c["repo"], safe="")

    # ---- HTTP API
    def api(self, method, path, body=None, ok=(200, 201, 204)):
        h = {"Accept": "application/json", "Content-Type": "application/json", "User-Agent": "agent-forge"}
        if self.kind == "github":
            h.update({"Authorization": "Bearer " + self.token, "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"})
        else:
            h["PRIVATE-TOKEN"] = self.token
        req = urllib.request.Request(self.api_base + path, method=method, headers=h,
                                     data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read().decode() or "null"
                return r.status, json.loads(raw)
        except urllib.error.HTTPError as e:
            txt = e.read().decode(errors="replace")[:500]
            try:
                return e.code, json.loads(txt)
            except ValueError:
                return e.code, {"message": txt}
        except (urllib.error.URLError, OSError) as e:
            raise UpstreamError(f"{self.kind} API not reachable: {e}")

    def check(self):
        """(ok, message): the token reaches the repository and may push to it."""
        if self.kind == "github":
            c, d = self.api("GET", f"/repos/{self.c['repo']}")
            if c != 200:
                return False, f"GitHub: repository {self.c['repo']} not readable with this token (HTTP {c}: {(d or {}).get('message', '')})"
            if not (d.get("permissions") or {}).get("push"):
                return False, "GitHub: the token cannot push to this repository (needs Contents: read and write, Pull requests: read and write)"
            return True, f"GitHub: {d.get('full_name')} (default branch {d.get('default_branch')}), push allowed"
        c, d = self.api("GET", f"/projects/{self.pid}")
        if c != 200:
            return False, f"GitLab: project {self.c['repo']} not readable with this token (HTTP {c}: {(d or {}).get('message', '')})"
        perm = d.get("permissions") or {}
        level = max(((perm.get("project_access") or {}).get("access_level") or 0), ((perm.get("group_access") or {}).get("access_level") or 0))
        if level and level < 30:
            return False, "GitLab: the token's role is below Developer - it cannot push or open merge requests"
        return True, f"GitLab: {d.get('path_with_namespace')} (default branch {d.get('default_branch')})"

    # ---- git
    def git_env(self):
        user = "x-access-token" if self.kind == "github" else "oauth2"
        basic = base64.b64encode(f"{user}:{self.token}".encode()).decode()
        return {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "http.extraHeader", "GIT_CONFIG_VALUE_0": "Authorization: Basic " + basic,
                "GIT_TERMINAL_PROMPT": "0"}

    # ---- pull / merge requests
    def find_pr(self, branch, state="open"):
        """The newest PR/MR for this branch: dict(number, url, state open|merged|closed) or None."""
        if self.kind == "github":
            owner = self.c["repo"].split("/")[0]
            c, d = self.api("GET", f"/repos/{self.c['repo']}/pulls?state=all&per_page=30&head={urllib.parse.quote(owner + ':' + branch)}")
            prs = [p for p in d or [] if (p.get("head") or {}).get("ref") == branch] if c == 200 else []
            prs = [dict(number=p["number"], url=p.get("html_url"), state="merged" if p.get("merged_at") else p.get("state")) for p in prs]
        else:
            c, d = self.api("GET", f"/projects/{self.pid}/merge_requests?state=all&per_page=30&source_branch={urllib.parse.quote(branch)}")
            prs = [dict(number=p["iid"], url=p.get("web_url"), state={"opened": "open"}.get(p.get("state"), p.get("state")))
                   for p in d or [] if p.get("source_branch") == branch] if c == 200 else []
        prs.sort(key=lambda p: -p["number"])
        for p in prs:
            if state == "any" or p["state"] == state:
                return p
        return None

    def open_pr(self, branch, title, body):
        """Open (or reuse the open) PR/MR branch -> base. Returns dict(number, url)."""
        old = self.find_pr(branch, "open")
        if old:
            return old
        if self.kind == "github":
            c, d = self.api("POST", f"/repos/{self.c['repo']}/pulls", dict(title=title, head=branch, base=self.c["branch"], body=body))
            if c != 201:
                raise UpstreamError(f"GitHub: could not open a pull request (HTTP {c}: {(d or {}).get('message', '')} {(d or {}).get('errors', '')})")
            return dict(number=d["number"], url=d.get("html_url"), state="open")
        c, d = self.api("POST", f"/projects/{self.pid}/merge_requests", dict(source_branch=branch, target_branch=self.c["branch"], title=title,
                                                                           description=body, remove_source_branch=True))
        if c != 201:
            raise UpstreamError(f"GitLab: could not open a merge request (HTTP {c}: {(d or {}).get('message', '')})")
        return dict(number=d["iid"], url=d.get("web_url"), state="open")

    def pr_state(self, number):
        """dict(state open|merged|closed, mergeable True|False|None (still computing), why) of a PR/MR."""
        if self.kind == "github":
            c, d = self.api("GET", f"/repos/{self.c['repo']}/pulls/{int(number)}")
            if c != 200:
                raise UpstreamError(f"GitHub: PR #{number} not readable (HTTP {c})")
            st = "merged" if d.get("merged") else d.get("state")
            return dict(state=st, mergeable=d.get("mergeable"), why=d.get("mergeable_state") or "")
        c, d = self.api("GET", f"/projects/{self.pid}/merge_requests/{int(number)}")
        if c != 200:
            raise UpstreamError(f"GitLab: MR !{number} not readable (HTTP {c})")
        dms = d.get("detailed_merge_status") or d.get("merge_status") or ""
        mergeable = True if dms in ("mergeable", "can_be_merged") else None if dms in ("checking", "unchecked", "preparing", "") else False
        return dict(state={"opened": "open"}.get(d.get("state"), d.get("state")), mergeable=mergeable, why=dms)

    def merge(self, number):
        """Merge a PR/MR now. Returns (merged, message); not merged when upstream requires checks/reviews or sees a conflict -
        GitLab then arms 'merge when the pipeline succeeds' so it merges by itself."""
        if self.kind == "github":
            c, d = self.api("PUT", f"/repos/{self.c['repo']}/pulls/{int(number)}/merge", dict(merge_method=self.c["merge"]))
            if c == 200 and (d or {}).get("merged"):
                return True, "merged"
            return False, f"HTTP {c}: {(d or {}).get('message', '')}"
        body = dict(should_remove_source_branch=True, squash=self.c["merge"] == "squash")
        c, d = self.api("PUT", f"/projects/{self.pid}/merge_requests/{int(number)}/merge", body)
        if c == 200 and (d or {}).get("state") == "merged":
            return True, "merged"
        if c in (405, 406, 422):
            c2, d2 = self.api("PUT", f"/projects/{self.pid}/merge_requests/{int(number)}/merge", dict(body, merge_when_pipeline_succeeds=True))
            if c2 == 200:
                return False, "waiting for the pipeline (merges by itself when it succeeds)"
        return False, f"HTTP {c}: {(d or {}).get('message', '')}"


# ---- git plumbing between Forgejo (origin) and upstream, in the foreman's own clone
def _git(d, *a, env=None, timeout=180):
    r = subprocess.run(["git", *a], cwd=d, capture_output=True, encoding="utf-8", errors="replace", timeout=timeout, env=dict(os.environ, **(env or {})))
    return r.returncode, (r.stdout + r.stderr).strip()


def forgejo_env(token_file="orchestrator.token"):
    tok = sbx.read(os.path.join(sbx.SECRETS, token_file))
    return {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "http.extraHeader", "GIT_CONFIG_VALUE_0": f"Authorization: token {tok}"}


def workdir(fd):
    """A clone of the Forgejo repository used only for upstream transfers (separate from the gate's and the rebase clone)."""
    d = os.path.join(fd, "upstream")
    if not os.path.isdir(os.path.join(d, ".git")):
        os.makedirs(fd, exist_ok=True)
        rc, out = _git(fd, "clone", "-q", f"{sbx.FORGE_URL}/{sbx.FORGE_REPO}.git", d, env=forgejo_env())
        if rc:
            raise UpstreamError("cannot clone the Forgejo repository: " + out[-300:])
    return d


def push_branch(up, fd, branch):
    """Forgejo's task branch -> the same branch upstream (force: it is the task's own branch, rewritten by rebases)."""
    if not sbx.SAFE_ARG.fullmatch(branch) or branch in (up.c["branch"], "main", "master"):
        raise UpstreamError(f"refusing to push branch {branch!r}")
    d = workdir(fd)
    rc, out = _git(d, "fetch", "-q", "origin", f"+refs/heads/{branch}:refs/remotes/origin/{branch}", env=forgejo_env())
    if rc:
        raise UpstreamError(f"branch {branch} not on Forgejo: {out[-200:]}")
    rc, out = _git(d, "push", "-q", "--force", up.git_url, f"refs/remotes/origin/{branch}:refs/heads/{branch}", env=up.git_env())
    if rc:
        raise UpstreamError(f"push of {branch} to {up.kind} failed: {out[-300:]}")


def sync_main(up, fd, force=False):
    """Upstream main -> Forgejo main. Fast-forward only, unless force (the first import over Forgejo's placeholder commit).
    Returns 'uptodate' | 'synced' | 'diverged' (Forgejo main has commits upstream does not - nothing is overwritten)."""
    d = workdir(fd)
    b = up.c["branch"]
    rc, out = _git(d, "fetch", "-q", up.git_url, f"+refs/heads/{b}:refs/remotes/upstream/{b}", env=up.git_env())
    if rc:
        raise UpstreamError(f"cannot fetch {b} from {up.kind}: {out[-300:]}")
    _git(d, "fetch", "-q", "origin", env=forgejo_env())
    rc, ups = _git(d, "rev-parse", f"refs/remotes/upstream/{b}")
    rc2, ori = _git(d, "rev-parse", "refs/remotes/origin/main")
    if rc2 == 0 and ups == ori:
        return "uptodate"
    if rc2 == 0 and not force and _git(d, "merge-base", "--is-ancestor", ori, ups)[0] != 0:
        return "diverged"
    rc, out = _git(d, "push", "-q", *(["--force"] if force else []), "origin", f"refs/remotes/upstream/{b}:refs/heads/main", env=forgejo_env())
    if rc:
        raise UpstreamError("cannot update Forgejo's main (the orchestrator needs push rights on main: run scripts/forge-bootstrap "
                            "again; for an import it needs the forge-admin token): " + out[-300:])
    return "synced"
