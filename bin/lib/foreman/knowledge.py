"""What the team knows besides the code: role playbooks (roles/, overridable per project), lessons learned, the owner's
decisions, and the orchestrator's system prompt built from them."""
import os


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
def _roles():
    d = os.path.join(ctx.ROOT, "roles")
    out = {}
    if os.path.isdir(d):
        for f in sorted(os.listdir(d)):
            if f.endswith(".md"):
                txt = open(os.path.join(d, f)).read()
                first = next((l for l in txt.splitlines() if l.startswith("# ")), "# " + f[:-3])
                out[f[:-3]] = (first[2:].strip(), txt)
    return out


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
    rs = _roles()
    slug = slugify(role)
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
