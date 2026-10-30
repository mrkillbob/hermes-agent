"""Import-free schedule-kind predicate for mixed-version update handoffs.

An old gateway may have cron.constants cached before the checkout changes.
New siblings must resolve this helper from a fresh leaf rather than that cache.
"""


def is_recurring(job: dict) -> bool:
    """A cron/interval job (vs a one-shot); a null schedule is not recurring."""
    return (job.get("schedule") or {}).get("kind") in {"cron", "interval"}
