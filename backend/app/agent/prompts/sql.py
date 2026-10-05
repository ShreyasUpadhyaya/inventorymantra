"""SQL-generation task block: verbatim from docs/PROMPTS.md §4 (changed in v0.10; current v0.10)."""

SQL_TASK = """\
TASK: Write ONE PostgreSQL query that answers the request.
- Use ONLY these relations and columns:
{schema}
- Read-only: a single SELECT (WITH … SELECT allowed). No INSERT/UPDATE/DELETE/DDL, no
  comments, no semicolons, no functions with side effects.
- Filter with the resolved IDs given in <data> rather than fuzzy name matching.
- Today is {today}; write explicit date literals and list the ranges in assumptions.
- expected_delivery_date already holds the latest recorded expected date (NULL if none was
  recorded); say "no expected date recorded" rather than guessing one.
- Return readable columns (names, codes), not raw IDs; snake_case aliases.
- Aggregate in SQL; ORDER BY for rankings and time series; at most 500 rows.
- "Last month / last quarter / last year" mean the last COMPLETED one before today; "this
  month / quarter / year" mean the current one so far.
- "What should I reorder / what needs reordering" = items whose status is reorder_now.
- "Low stock" / "kam stock" / "कम स्टॉक" = items whose status is below_min or reorder_now.
- "Coming in", "arriving", "on order", "aa rahi", "aane wala" = open purchase-order lines
  (inbound), not current stock.
- A question about one PO also returns its status, expected_delivery_date and days_overdue.
- "Items in stock" counts items with quantity > 0; it is not the total of units.
- "Who has access / who uses the system" = the users relation. A process or lifecycle
  question = purchase orders counted by status. "What will X be / when will Y happen" =
  return the recorded figures the answer rests on (answerable=true); the reply states
  facts, never a prediction.
- If the request cannot be answered from these relations, return answerable=false and
  sql=null. Never invent a table or column.
EXAMPLES:
{few_shots}\
"""
