"""Wall-clock budget module for the quickstart end-to-end check.

Public interface: STEP_BUDGETS, TOTAL_BUDGET, check_budget, check_total, format_report.
Invariants:
- STEP_BUDGETS defines per-step wall-clock second ceilings in execution pipeline order.
- TOTAL_BUDGET defines the 30-minute ceiling (1800s) for the complete end-to-end run.
- check_budget and check_total return ok=False with offending step/total details if elapsed exceeds budget.
- format_report outputs deterministic status lines in STEP_BUDGETS order and skips omitted steps.
"""

STEP_BUDGETS = {
    "install": 300,   # seconds
    "plan": 180,
    "dispatch": 120,
    "gate": 300,
    "merge": 60,
}
TOTAL_BUDGET = 1800  # 30 minutes - the vision's 'git clone to merged PR' promise


def check_budget(step: str, elapsed_sec: float) -> tuple[bool, str]:
    """ok=False and a message naming the step, elapsed and budget when elapsed_sec > STEP_BUDGETS[step]; raises KeyError for an unknown step."""
    if step not in STEP_BUDGETS:
        raise KeyError(f"Unknown step: {step}")
    budget = STEP_BUDGETS[step]
    if elapsed_sec > budget:
        return False, f"{step} exceeded budget: {elapsed_sec:.1f}s (budget {budget}s)"
    return True, f"{step} within budget: {elapsed_sec:.1f}s (budget {budget}s)"


def check_total(elapsed_sec: float) -> tuple[bool, str]:
    """Same shape, checked against TOTAL_BUDGET."""
    budget = TOTAL_BUDGET
    if elapsed_sec > budget:
        return False, f"total exceeded budget: {elapsed_sec:.1f}s (budget {budget}s)"
    return True, f"total within budget: {elapsed_sec:.1f}s (budget {budget}s)"


def format_report(timings: dict[str, float]) -> str:
    """One line per step in STEP_BUDGETS order present in `timings`, each
    '<step>: <elapsed:.1f>s (budget <budget>s) <OK|SLOW>', then a final
    'total: <sum:.1f>s (budget <TOTAL_BUDGET>s) <OK|SLOW>' line. Steps missing
    from `timings` are skipped, not an error."""
    lines: list[str] = []
    total_elapsed = 0.0
    for step, budget in STEP_BUDGETS.items():
        if step in timings:
            elapsed = float(timings[step])
            total_elapsed += elapsed
            status = "SLOW" if elapsed > budget else "OK"
            lines.append(f"{step}: {elapsed:.1f}s (budget {budget}s) {status}")
    total_status = "SLOW" if total_elapsed > TOTAL_BUDGET else "OK"
    lines.append(f"total: {total_elapsed:.1f}s (budget {TOTAL_BUDGET}s) {total_status}")
    return "\n".join(lines)
