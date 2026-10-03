"""SQL-generation task block: verbatim from docs/PROMPTS.md §4 (unchanged since v0.4; current v0.6)."""

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
- If the request cannot be answered from these relations, return answerable=false and
  sql=null. Never invent a table or column.
EXAMPLES:
{few_shots}\
"""
