"""SQL guard for model-written queries (DECISION D-005; CLAUDE.md §5).

`validate_sql(sql, allowed, max_rows)` accepts exactly one read-only SELECT (WITH/UNION
allowed) over the caller's allow-listed views, and returns it with an enforced LIMIT.
Rejections carry a fixed reason code that never names a relation. This is one of three
layers: the role's DB user can only read its own views anyway, inside a READ ONLY
transaction with a statement timeout (app.db.readonly).
"""

import logging
import re
from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError, TokenError

Reason = str  # one of REASONS
REASONS = frozenset(
    {"parse_error", "not_select", "multiple_statements", "comment", "relation_not_allowed",
     "catalog_access", "forbidden_function", "locking_clause", "select_into"}
)  # fmt: skip

# Statement keywords sqlglot may keep as raw commands; anything else unparsed is garbage.
KNOWN_COMMANDS = frozenset(
    {"ALTER", "ANALYZE", "BEGIN", "CALL", "CHECKPOINT", "CLUSTER", "COMMENT", "COMMIT", "COPY",
     "CREATE", "DEALLOCATE", "DECLARE", "DISCARD", "DO", "DROP", "EXECUTE", "EXPLAIN", "FETCH",
     "GRANT", "IMPORT", "LISTEN", "LOAD", "LOCK", "MOVE", "NOTIFY", "PREPARE", "REASSIGN",
     "REFRESH", "REINDEX", "RELEASE", "RESET", "REVOKE", "ROLLBACK", "SAVEPOINT", "SECURITY",
     "SET", "SHOW", "START", "TRUNCATE", "UNLISTEN", "VACUUM"}
)  # fmt: skip
WRITE_NODES: tuple[type[exp.Expression], ...] = (
    exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Create, exp.Drop, exp.Alter,
    exp.TruncateTable, exp.Command, exp.Copy, exp.Set, exp.Grant,
)  # fmt: skip
QUERY_ROOTS: tuple[type[exp.Expression], ...] = (exp.Select, exp.SetOperation, exp.Subquery)
CATALOG_SCHEMAS = frozenset({"pg_catalog", "information_schema", "pg_toast"})
FORBIDDEN_PREFIXES = ("pg_", "lo_", "dblink", "xpath")
FORBIDDEN_FUNCTIONS = frozenset(
    {"set_config", "current_setting_set", "query_to_xml", "table_to_xml", "cursor_to_xml",
     "database_to_xml", "schema_to_xml", "txid_current", "nextval", "setval", "currval"}
)  # fmt: skip
# sqlglot logs the raw statement when it falls back to a Command; keep rejected SQL out of logs.
logging.getLogger("sqlglot").setLevel(logging.ERROR)

_STRINGS = re.compile(r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|\$\$.*?\$\$", re.S)


@dataclass(frozen=True)
class GuardResult:
    ok: bool
    sql: str | None = None
    reason: Reason | None = None


def _reject(reason: Reason) -> GuardResult:
    assert reason in REASONS
    return GuardResult(ok=False, reason=reason)


def _function_name(node: exp.Func) -> str:
    if isinstance(node, exp.Anonymous):
        return str(node.name).lower()
    return node.sql_name().lower()


def _apply_limit(root: exp.Expression, max_rows: int) -> None:
    limit = root.args.get("limit")
    current: int | None = None
    if isinstance(limit, exp.Limit) and isinstance(limit.expression, exp.Literal):
        text = limit.expression.this
        current = int(text) if str(text).isdigit() else None
    if current is None or current > max_rows:
        root.set("limit", exp.Limit(expression=exp.Literal.number(max_rows)))


def validate_sql(sql: str, allowed: frozenset[str], max_rows: int = 500) -> GuardResult:
    text = (sql or "").strip()
    if not text:
        return _reject("parse_error")
    try:
        statements = [s for s in sqlglot.parse(text, read="postgres") if s is not None]
    except (ParseError, TokenError):
        return _reject("parse_error")
    if not statements:
        return _reject("parse_error")
    if len(statements) > 1:
        return _reject("multiple_statements")
    if "--" in _STRINGS.sub("", text) or "/*" in _STRINGS.sub("", text):
        return _reject("comment")

    root = statements[0]
    if isinstance(root, exp.Command) and str(root.this).upper() not in KNOWN_COMMANDS:
        return _reject("parse_error")
    if not isinstance(root, QUERY_ROOTS + WRITE_NODES):  # e.g. "SELEC * FRM items" = a*b AS c
        return _reject("parse_error")
    if not isinstance(root, QUERY_ROOTS) or any(root.find_all(*WRITE_NODES)):
        return _reject("not_select")
    if root.find(exp.Into):
        return _reject("select_into")
    if root.find(exp.Lock):
        return _reject("locking_clause")

    cte_names = {cte.alias_or_name.lower() for cte in root.find_all(exp.CTE)}
    for table in root.find_all(exp.Table):
        name = table.name.lower()
        schema = table.db.lower() if table.db else ""
        if schema in CATALOG_SCHEMAS or (not schema and name.startswith("pg_")):
            return _reject("catalog_access")
        if schema:
            return _reject("relation_not_allowed")  # only the role's own unqualified views
        if name not in cte_names and name not in allowed:
            return _reject("relation_not_allowed")

    for func in root.find_all(exp.Func):
        name = _function_name(func)
        if name.startswith(FORBIDDEN_PREFIXES) or name in FORBIDDEN_FUNCTIONS:
            return _reject("forbidden_function")

    _apply_limit(root, max_rows)
    return GuardResult(ok=True, sql=root.sql(dialect="postgres"))
