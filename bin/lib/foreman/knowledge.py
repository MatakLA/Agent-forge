"""What the team knows besides the code: role playbooks (roles/, overridable per project), lessons learned, the owner's
decisions, and the orchestrator's system prompt built from them."""
import os, re

from . import ctx
from .util import slugify
from .store import event


def load_system_prompt():
    """Stable orchestrator system prompt: generic team rules (prompts/orchestrator.md), the role catalogue, and the
    project's own rules (etc/team-rules.md, optional). Passed as a system prompt so Claude Code can cache it."""
    parts = []
    for p in (os.path.join(ctx.ROOT, "prompts", "orchestrator.md"), os.path.join(ctx.CFG_DIR, "team-rules.md")):
        try:
            parts.append(open(p).read().strip())
        except FileNotFoundError:
            pass
    parts.append("## Role catalogue (assign exactly one per task; each worker gets the full playbook)\n" + role_catalogue())
    ls = lessons()
    if ls:
        parts.append("## Lessons this team learned from earlier reworks (apply them when planning and reviewing)\n" + "\n".join(f"- {l}" for l in ls))
    return "\n\n".join(parts)


def lessons():
    try:
        return [l[2:].strip() for l in open(os.path.join(ctx.CFG_DIR, "lessons.md")) if l.startswith("- ")][-int(ctx.C["LESSONS_MAX"]):]
    except FileNotFoundError:
        return []


def add_lesson(text, source):
    """One general, reusable sentence from a review that sent work back. Deduplicated; only the newest LESSONS_MAX are used."""
    text = " ".join(str(text or "").split())[:300]
    if len(text) < 15 or text.lower() in {l.lower() for l in lessons()}:
        return
    p = os.path.join(ctx.CFG_DIR, "lessons.md")
    new = not os.path.exists(p)
    with open(p, "a") as f:
        if new:
            f.write("# Lessons learned (written by the orchestrator after reworks; edit or delete freely)\n\n")
        f.write(f"- {text}\n")
    event(f"lesson learned ({source}): {text[:160]}")


# ---------------------------------------------------------------- roles (roles/*.md playbooks)
ROLE_SLUG = re.compile(r"[a-z][a-z0-9-]{1,40}")
def _roles():
    """slug -> (title, playbook): roles/ of the install, with this project's own versions (its roles/ directory, written from
    the dashboard) taking precedence."""
    out = {}
    for d in (os.path.join(ctx.ROOT, "roles"), os.path.join(ctx.CFG_DIR, "roles")):
        if os.path.isdir(d):
            for f in sorted(os.listdir(d)):
                if f.endswith(".md") and ROLE_SLUG.fullmatch(f[:-3]):
                    txt = open(os.path.join(d, f)).read()
                    first = next((l for l in txt.splitlines() if l.startswith("# ")), "# " + f[:-3])
                    out[f[:-3]] = (first[2:].strip(), txt)
    return dict(sorted(out.items()))


def role_catalogue():
    rs = _roles()
    if not rs:
        return "(no roles/ directory - use plain role names such as backend developer, tester, security tester)"
    lines = []
    for slug, (title, txt) in rs.items():
        mission = next((l.strip() for l in txt.splitlines() if l.lower().startswith("**mission")), "")
        lines.append(f"- `{slug}` - {title}" + (f": {mission.split(':', 1)[-1].strip(' *')}" if mission else ""))
    return "\n".join(lines)


def role_playbook(role):
    """The playbook for a role name as the planner wrote it ('backend developer', 'security-tester', ...)."""
    if not isinstance(role, str) or not role:
        return ""
    if ".." in role or "/" in role or "\\" in role or os.path.isabs(role):
        return ""
    if role.startswith("role:"):
        role = role[5:]
    slug = slugify(role)
    if not ROLE_SLUG.fullmatch(slug):
        return ""
    rs = _roles()
    if slug in rs:
        return rs[slug][1]
    words = set(slug.split("-"))
    best = max(rs.items(), key=lambda kv: (len(words & set(kv[0].split("-"))), -len(set(kv[0].split("-")) - words)), default=None)
    if best and words & set(best[0].split("-")):
        return best[1][1]
    return ""


def decisions():
    try:
        return open(os.path.join(ctx.CFG_DIR, "decisions.md")).read()[-12000:]
    except FileNotFoundError:
        return ""


# ---------------------------------------------------------------- the team's documents, edited in the dashboard
DOC_MAX = 200_000
DOCS = {"vision": "The product vision - the autopilot plans goals from it",
        "team-rules": "Rules every plan, review and worker follows in this project",
        "decisions": "Your answers to the planner's questions - binding for every plan",
        "lessons": "One sentence per lesson the reviews taught the team (the newest LESSONS_MAX are used)"}


def doc_path(name, write=False):
    """Where a document lives (None for an unknown name). role:<slug> reads this project's own version or the install's; it
    is always written to this project's roles/ directory, so the install's playbook stays untouched."""
    if not isinstance(name, str) or not name:
        return None
    if ".." in name or "/" in name or "\\" in name or os.path.isabs(name):
        return None
    if name in DOCS:
        if name == "vision":
            intended_dir = ctx.PDIR or ctx.ROOT
            target = os.path.join(intended_dir, ctx.C["VISION_FILE"])
        else:
            intended_dir = ctx.CFG_DIR
            target = os.path.join(intended_dir, name + ".md")
    elif name.startswith("role:") and ROLE_SLUG.fullmatch(name[5:]):
        slug = name[5:]
        own_dir = os.path.join(ctx.CFG_DIR, "roles")
        own = os.path.join(own_dir, slug + ".md")
        if write or os.path.exists(own):
            intended_dir = own_dir
            target = own
        else:
            install_dir = os.path.join(ctx.ROOT, "roles")
            intended_dir = install_dir
            target = os.path.join(install_dir, slug + ".md")
    else:
        return None

    base = os.path.realpath(intended_dir)
    resolved = os.path.realpath(target)
    try:
        if os.path.commonpath([base, resolved]) != base or resolved == base:
            return None
    except ValueError:
        return None
    return target


def doc_list():
    out = [dict(name=k, title=k.replace("-", " ").capitalize(), about=v, exists=os.path.exists(doc_path(k) or "")) for k, v in DOCS.items()]
    own = os.path.join(ctx.CFG_DIR, "roles")
    for slug, (title, _) in _roles().items():
        out.append(dict(name="role:" + slug, title=title, about="role playbook", exists=True,
                        customized=os.path.exists(os.path.join(own, slug + ".md"))))
    return out


def doc_get(name):
    p = doc_path(name)
    if not p:
        return None
    try:
        text = open(p).read()
    except FileNotFoundError:
        text = ""
    return dict(name=name, text=text, customized=isinstance(name, str) and name.startswith("role:") and p == doc_path(name, write=True))


def doc_set(name, text):
    """Save a document (from the dashboard). Returns an error message or ""."""
    p = doc_path(name, write=True)
    if not p:
        return "unknown document"
    if not isinstance(text, str):
        return "not a text"
    if len(text) > DOC_MAX:
        return f"too long (max {DOC_MAX // 1000} kB)"
    if "\x00" in text:
        return "not a text"
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    open(tmp, "w").write(text if text.endswith("\n") or not text else text + "\n")
    os.replace(tmp, p)
    event(f"{name} edited by the owner ({len(text):,} characters)")
    return ""


def doc_reset(name):
    """A role back to the install's playbook (this project's own version is deleted)."""
    if not isinstance(name, str) or not name.startswith("role:") or not ROLE_SLUG.fullmatch(name[5:]):
        return "only a role can be reset"
    p = doc_path(name, write=True)
    if not p:
        return "only a role can be reset"
    if os.path.exists(p):
        os.remove(p)
        event(f"{name} reset to the install's playbook")
    return ""
