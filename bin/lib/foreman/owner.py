"""The owner's questions and answers."""
import os

import sbx

from . import ctx
from .store import event, jload, jsave, locked
from .repo import refresh_repo
from .llm import llm
from .prompts import context_bundle


def ask(question):
    """Answer a question about the project from the repository context (one orchestrator call)."""
    refresh_repo()
    prompt = (f"{context_bundle()}\n## Question from the owner\n{question.strip()[:4000]}\n\nAnswer in {ctx.C['OWNER_LANG']}, concretely, "
              "from the repository content above. Cite file paths. If the repository does not contain the answer, say so plainly. "
              "Plain text or markdown, no JSON.")
    text, who = llm("ask", prompt)
    event(f"owner question answered by {who}: {question.strip()[:120]}")
    return text, who


def answer_question(n, answer):
    """The owner answers the planner's question n: it leaves the open list and goes into decisions.md, which every later
    plan and roadmap reads - so the team does not ask again and builds on the decision."""
    with locked():
        p = os.path.join(ctx.FD, "owner_questions.json")
        qs = jload(p, [])
        if not 1 <= n <= len(qs):
            return None
        q = qs.pop(n - 1)
        jsave(p, qs)
        if answer.strip():
            dp = os.path.join(ctx.CFG_DIR, "decisions.md")
            new = not os.path.exists(dp)
            with open(dp, "a") as f:
                if new:
                    f.write("# Owner decisions (answers to the planner's questions; read by every plan)\n\n")
                f.write(f"- Q: {q}\n  A: {' '.join(answer.split())[:2000]}  ({sbx.today()})\n")
    event(f"owner answered: {q[:120]}" if answer.strip() else f"owner dismissed the question: {q[:120]}")
    return q
