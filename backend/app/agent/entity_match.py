"""Deterministic entity matcher (DECISION D-026).

The model only *points* at the words naming a record (guard `entity_mentions`, copied as
typed); this code decides which record they mean, or asks, or says nothing matched.
Lookups take the mention as a query parameter only, and respect the caller's role
(an Inventory Manager asking about a vendor gets `denied`, never "not found").

Runs on the app's own connection: pg_trgm functions live in `public`, which the role
users can't reach. Only names/codes the caller's role may see are returned.
"""

import re
from dataclasses import dataclass, field
from typing import Literal

import psycopg

from app.policy import permissions

Kind = Literal["item", "vendor", "location", "po"]
Outcome = Literal["match", "clarify", "not_found", "denied"]

MIN_CANDIDATE = 0.30  # trigram score to be considered at all
MIN_MATCH = 0.50  # score needed to answer or to be offered as an option
CLEAR_GAP = 0.15  # lead over the runner-up needed to answer without asking
MIN_SUGGESTION = 0.35  # score needed to be suggested after "not found"
MAX_OPTIONS = 4
MAX_SUGGESTIONS = 3

# From the catalogue's own naming standards (inv_sub_categories_2.convention_notes).
SYNONYMS = {
    r"\btorx\b": "hexalobular",
    r"\bhexalobul(?:a|er|ar)\b": "hexalobular",
    r"\bpen head\b": "pan head",
    r"\bismb\b": "ms beam",
}
_TRANSLATE = str.maketrans("×*–—-", "xx   ")
GENERIC_LOCATION_WORDS = re.compile(r"\b(godown|gowdown|warehouse|store|location|wh)\b", re.I)
ITEM_CODE = re.compile(r"^\s*([A-Za-z]{2}(?:-[A-Za-z0-9]+)+)\s*$")
# SQL-side normalisation mirroring `normalise` (× * -> x; dashes -> space).
SQL_NORMALISED = "regexp_replace(translate(lower({col}), '×–—*-', 'x   x '), '\\s+', ' ', 'g')"


@dataclass(frozen=True)
class Candidate:
    code: str
    name: str
    score: float = 0.0


@dataclass(frozen=True)
class Resolution:
    outcome: Outcome
    candidates: list[Candidate] = field(default_factory=list)


def normalise(text: str) -> str:
    text = text.lower().translate(_TRANSLATE)
    text = re.sub(r"\s+", " ", text).strip()
    for pattern, replacement in SYNONYMS.items():
        text = re.sub(pattern, replacement, text)
    return text


def atoms(text: str) -> list[tuple[str, str]]:
    """Spec atoms (letter prefix, number) from tokens with digits; units dropped.
    'od40xid26x70' -> [('od','40'), ('id','26'), ('','70')]."""
    found: list[tuple[str, str]] = []
    for token in text.split():
        if not re.search(r"\d", token):
            continue
        for part in token.split("x"):
            m = re.fullmatch(r"([a-z]*)(\d+(?:\.\d+)?)(?:mm|m|kg|a|v|w|va|hp|p)?", part)
            if m:
                found.append((m.group(1), m.group(2)))
    return found


def _compatible(query: tuple[str, str], cand: tuple[str, str]) -> bool:
    if query[0] and cand[0] and query[0] != cand[0]:
        return False
    return cand[1] == query[1] or cand[1].startswith(query[1] + ".")  # id26 ~ id26.5


def spec_ok(query_atoms: list[tuple[str, str]], candidate_name: str) -> bool:
    remaining = atoms(normalise(candidate_name))
    for q in query_atoms:
        hit = next((c for c in remaining if _compatible(q, c)), None)
        if hit is None:
            return False
        remaining.remove(hit)
    return True


def _trigrams(word: str) -> set[str]:
    padded = f"  {word} "
    return {padded[i : i + 3] for i in range(len(padded) - 2)}


def _word_like(a: str, b: str) -> bool:
    """Same word, or a close spelling of a real word (>= 4 letters): contacter ~ contactor."""
    if a == b:
        return True
    if len(a) < 4 or len(b) < 4:
        return False
    ta, tb = _trigrams(a), _trigrams(b)
    return len(ta & tb) / len(ta | tb) >= 0.5


def shares_word(query: str, name: str) -> bool:
    """Trigram scores alone let fragments match ('zzzz' ~ bearing '6902 ZZ'); a candidate
    must share at least one real word with the mention."""
    name_words = re.findall(r"[a-z][a-z0-9.]*", normalise(name))
    query_words = re.findall(r"[a-z][a-z0-9.]*", query)
    return any(_word_like(q, n) for q in query_words for n in name_words)


def _decide(query: str, scored: list[Candidate], use_specs: bool) -> Resolution:
    q_atoms = atoms(query) if use_specs else []
    scored = [c for c in scored if shares_word(query, c.name)]
    pool = [c for c in scored if c.score >= MIN_CANDIDATE and spec_ok(q_atoms, c.name)]
    words = [w for w in query.split() if not re.search(r"\d", w) and len(w) >= 2]
    with_all_words = [c for c in pool if all(w in normalise(c.name).split() for w in words)]
    if with_all_words:
        pool = with_all_words
    if (
        pool
        and pool[0].score >= MIN_MATCH
        and (len(pool) == 1 or pool[0].score - pool[1].score >= CLEAR_GAP)
    ):
        return Resolution("match", [pool[0]])
    close = [c for c in pool if c.score >= MIN_MATCH]
    if len(close) >= 2:
        return Resolution("clarify", close[:MAX_OPTIONS])
    suggestions = [c for c in scored if c.score >= MIN_SUGGESTION][:MAX_SUGGESTIONS]
    return Resolution("not_found", suggestions)


def _trigram(conn: psycopg.Connection, sql: str, query: str) -> list[Candidate]:
    rows = conn.execute(sql, {"q": query}).fetchall()
    return [
        Candidate(code=str(code), name=str(name), score=round(float(s), 3))
        for code, name, s in rows
    ]


def _resolve_item(conn: psycopg.Connection, text: str) -> Resolution:
    code = ITEM_CODE.match(text)
    if code:
        row = conn.execute(
            "SELECT item_code, name FROM inv_items WHERE upper(item_code) = upper(%s)",
            (code.group(1),),
        ).fetchone()
        if row:
            return Resolution("match", [Candidate(row[0], row[1], 1.0)])
    query = normalise(text)
    n = SQL_NORMALISED.format(col="name")
    scored = _trigram(
        conn,
        f"""SELECT item_code, name,
                   greatest(similarity({n}, %(q)s), word_similarity(%(q)s, {n})) AS s
            FROM inv_items WHERE status = 'active'
            ORDER BY s DESC, item_code LIMIT 15""",  # noqa: S608 (constant SQL; query is a parameter)
        query,
    )
    return _decide(query, scored, use_specs=True)


def _resolve_vendor(conn: psycopg.Connection, text: str) -> Resolution:
    query = normalise(text)
    n = SQL_NORMALISED.format(col="name")
    scored = _trigram(
        conn,
        f"""SELECT name, name, greatest(similarity({n}, %(q)s), word_similarity(%(q)s, {n})) AS s
            FROM proc_vendors WHERE is_active
            ORDER BY s DESC, name LIMIT 15""",  # noqa: S608
        query,
    )
    return _decide(query, scored, use_specs=False)


def _resolve_location(conn: psycopg.Connection, text: str) -> Resolution:
    query = re.sub(r"\s+", " ", GENERIC_LOCATION_WORDS.sub(" ", text)).strip()
    compact = re.sub(r"[\s\-]", "", query).lower()
    rows = conn.execute("SELECT name FROM inv_locations WHERE is_active ORDER BY name").fetchall()
    exact = [name for (name,) in rows if re.sub(r"[\s\-]", "", name).lower() == compact]
    if len(exact) == 1:
        return Resolution("match", [Candidate(exact[0], exact[0], 1.0)])
    scored = _trigram(
        conn,
        """SELECT name, name, greatest(similarity(lower(name), lower(%(q)s)),
                                      word_similarity(lower(%(q)s), lower(name))) AS s
           FROM inv_locations WHERE is_active ORDER BY s DESC, name LIMIT 10""",
        query,
    )
    return _decide(normalise(query), scored, use_specs=False)


def _resolve_po(conn: psycopg.Connection, text: str) -> Resolution:
    numbers = [int(n) for n in re.findall(r"\d+", text)]
    if not numbers or len(numbers) > 2:
        return Resolution("not_found")
    year = numbers[0] if len(numbers) == 2 and numbers[0] >= 2000 else None
    seq = numbers[-1]
    pattern = f"PO-{year}-{seq:04d}" if year else f"PO-%-{seq:04d}"
    rows = conn.execute(
        "SELECT po_number FROM proc_purchase_orders WHERE po_number LIKE %s ORDER BY po_number",
        (pattern,),
    ).fetchall()
    found = [Candidate(r[0], r[0], 1.0) for r in rows]
    if len(found) == 1:
        return Resolution("match", found)
    if len(found) > 1:
        return Resolution("clarify", found[:MAX_OPTIONS])
    return Resolution("not_found")


AREA_FOR_KIND: dict[Kind, str] = {
    "item": "stock", "vendor": "vendors", "location": "stock", "po": "inbound",
}  # fmt: skip
RESOLVERS = {
    "item": _resolve_item,
    "vendor": _resolve_vendor,
    "location": _resolve_location,
    "po": _resolve_po,
}


def resolve(conn: psycopg.Connection, role: str, kind: Kind, text: str) -> Resolution:
    if not permissions.can_read(role, AREA_FOR_KIND[kind]):
        return Resolution("denied")
    return RESOLVERS[kind](conn, text[:120])
