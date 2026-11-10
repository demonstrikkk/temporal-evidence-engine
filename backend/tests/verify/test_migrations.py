"""Migrations are SQL, so a typo is not a test failure until it reaches Postgres.

Migration 021 was written to drop the `rule_version` default and named
`tev.answers` and `tev.claims`. Neither table has that column - it lives on
`tev.verification_runs` and `tev.claim_status_history` - so the migration would
have failed on the first PostgreSQL database it met. The local store has no
schema, so every hermetic test passed.

This is the cheapest possible guard: read the CREATE TABLE statements in
filename order, then assert that every `ALTER TABLE ... ALTER COLUMN ...` names a
column that was actually created. It does not execute SQL and does not replace a
live migration test; it just makes a wrong table or column name fail in CI
instead of on submission day.
"""
from __future__ import annotations

import re
from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parents[2] / "migrations"

CREATE = re.compile(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([\w.]+)\s*\(", re.I)
ALTER = re.compile(
    r"ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?([\w.]+)\s+"
    r"ALTER\s+COLUMN\s+([\w\"]+)", re.I)
CREATE_ROLE = re.compile(r"CREATE\s+ROLE\s+([\w\"\$]+)", re.I)
TO_ROLE = re.compile(r"\bTO\s+([\w\"\$]+(?:\s*,\s*[\w\"\$]+)*)", re.I)

#: Roles PostgreSQL always provides. `PUBLIC` is a pseudo-role, not a grantee
#: anyone can drop.
BUILTIN_ROLES = {"public", "current_user", "session_user", "current_role"}

#: Not column names: table constraints that can appear inside a CREATE TABLE.
CONSTRAINTS = {"PRIMARY", "UNIQUE", "CHECK", "FOREIGN", "CONSTRAINT", "EXCLUDE"}


def _body(sql: str, start: int) -> tuple[str, int]:
    """Return the parenthesised body of a CREATE TABLE and the index after it."""
    depth = 0
    quote = ""
    index = start
    while index < len(sql):
        char = sql[index]
        if quote:
            if char == quote:
                if index + 1 < len(sql) and sql[index + 1] == quote:
                    index += 1
                else:
                    quote = ""
        elif char in {"'", '"'}:
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return sql[start + 1:index], index + 1
        index += 1
    raise AssertionError("unbalanced CREATE TABLE body")


def _columns(sql: str, start: int) -> set[str]:
    body, _ = _body(sql, start)
    columns: set[str] = set()
    token: list[str] = []
    depth = 0
    quote = ""
    for index, char in enumerate(body):
        if quote:
            token.append(char)
            if char == quote:
                quote = ""
            continue
        if char in {"'", '"'}:
            quote = char
            token.append(char)
        elif char == "(":
            depth += 1
            token.append(char)
        elif char == ")":
            depth -= 1
            token.append(char)
        elif char == "," and depth == 0:
            columns.add(_first_token("".join(token)))
            token = []
        else:
            token.append(char)
    columns.add(_first_token("".join(token)))
    return {c for c in columns if c}


def _first_token(definition: str) -> str:
    for line in definition.splitlines():
        line = line.strip()
        if not line or line.startswith("--"):
            continue
        name = line.split()[0].strip('"').lower()
        if name.upper() in CONSTRAINTS:
            return ""
        return name
    return ""


def _schema() -> dict[tuple[str, str], str]:
    """(table, column) -> migration that created it, in filename order."""
    found: dict[tuple[str, str], str] = {}
    for path in sorted(MIGRATIONS.glob("*.sql")):
        sql = path.read_text(encoding="utf-8")
        for match in CREATE.finditer(sql):
            table = match.group(1).lower()
            for column in _columns(sql, match.end() - 1):
                found.setdefault((table, column), path.name)
    return found


def test_every_alter_column_names_a_column_that_was_created():
    created = _schema()
    offenders: list[str] = []
    for path in sorted(MIGRATIONS.glob("*.sql")):
        sql = path.read_text(encoding="utf-8")
        for match in ALTER.finditer(sql):
            table, column = match.group(1).lower(), match.group(2).strip('"').lower()
            if (table, column) not in created:
                offenders.append(f"{path.name}: {table}.{column}")
    assert offenders == [], (
        "ALTER COLUMN targets do not exist in any CREATE TABLE: " + ", ".join(offenders))


def test_the_rule_version_default_is_dropped_on_the_tables_that_have_it():
    """The columns that carry the stamp are runs and status history, not claims."""
    created = _schema()
    carriers = {table for (table, column) in created if column == "rule_version"}
    assert carriers == {"tev.verification_runs", "tev.claim_status_history"}, carriers

    sql = (MIGRATIONS / "021_ps05_rule_version_no_default.sql").read_text(encoding="utf-8")
    dropped = {(match.group(1).lower(), match.group(2).strip('"').lower())
               for match in ALTER.finditer(sql)}
    assert dropped == {(table, "rule_version") for table in carriers}, dropped


def _strip_comments(sql: str) -> str:
    return "\n".join(line.split("--", 1)[0] for line in sql.splitlines())


def _grantees(sql: str) -> set[str]:
    """Role names named by `GRANT ... TO` or `CREATE POLICY ... TO`."""
    names: set[str] = set()
    for statement in _strip_comments(sql).split(";"):
        if not re.search(r"\b(CREATE\s+POLICY|GRANT)\b", statement, re.I):
            continue
        match = TO_ROLE.search(statement)
        if not match:
            continue
        for name in match.group(1).split(","):
            name = name.strip().strip('"').lower()
            if name and name not in BUILTIN_ROLES and not name.startswith("%"):
                names.add(name)
    return names


def test_every_role_a_policy_grants_to_is_created_before_it_is_used():
    """Migration 001 grants to `anon`, which only Supabase provides.

    On a plain PostgreSQL the whole migration chain aborted at 001 with
    `role "anon" does not exist`, so `PERSISTENCE=postgres` could not be
    provisioned outside Supabase - something no hermetic test could see, because
    the local store has no roles. `000_roles.sql` creates them first.
    """
    created: dict[str, str] = {}
    offenders: list[str] = []
    for path in sorted(MIGRATIONS.glob("*.sql")):
        sql = path.read_text(encoding="utf-8")
        for match in CREATE_ROLE.finditer(_strip_comments(sql)):
            created.setdefault(match.group(1).strip('"').lower(), path.name)
        for name in _grantees(sql):
            if name not in created:
                offenders.append(f"{path.name} grants to {name!r}, which no earlier "
                                 f"migration creates")
    assert offenders == [], offenders
