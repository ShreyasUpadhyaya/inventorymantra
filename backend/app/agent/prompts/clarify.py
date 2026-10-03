"""Clarify task block: verbatim from docs/PROMPTS.md §3 (unchanged since v0.5; current v0.6)."""

CLARIFY_TASK = """\
TASK: Decide if the request can be answered precisely. Do not answer it.
Ask only when one of these is unclear AND no conventional default fits:
- time window ("a few days back", "recently", "earlier")
- entity (no match, or several plausible matches, in <data>)
- measure ("how are we doing": spend? stock value? overdue orders?)
Conventional defaults (do not ask): "this month" = current calendar month; "last month" =
previous calendar month; "last week" = previous Monday–Sunday; "last N POs" = most recent by
date; stock questions = current stock, all warehouses.
Questions about the current state (current stock, overdue, open or pending items, status
splits, totals with no period mentioned) need no period: use all current records. Explicit
periods ("last 3 quarters", "August", a date such as "tomorrow") are already clear.
If unsure, do not ask.
Write the question and 2–4 options in the user's language, using real values from <data>
(actual item names, actual ranges such as "23–30 Sep 2026").
Return {needs_clarification, dimension, question, options, default_applied}.\
"""
