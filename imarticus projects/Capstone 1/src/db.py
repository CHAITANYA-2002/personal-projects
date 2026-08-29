"""Warehouse access.

The only module that talks to MySQL directly. Everything else asks for an
engine here, so switching to a managed instance later is a change to .env and
nothing more.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Iterable

import pandas as pd
from sqlalchemy import Engine, create_engine, text

from config import settings

log = logging.getLogger(__name__)


@lru_cache(maxsize=2)
def get_engine(include_database: bool = True) -> Engine:
    """Return a pooled SQLAlchemy engine for the warehouse."""
    return create_engine(
        settings.database_url(include_database=include_database),
        pool_pre_ping=True,
        pool_recycle=3600,
        future=True,
    )


def ensure_database() -> None:
    """Confirm the target schema exists, creating it only if we are allowed to.

    The project user is granted rights on its own schema and nothing else, so
    CREATE DATABASE will be refused once the schema already exists. That is the
    expected steady state, not an error — reachability is what actually matters.
    """
    name = settings.MYSQL_DATABASE

    if _database_reachable():
        log.info("database %s is present", name)
        return

    try:
        with get_engine(include_database=False).connect() as conn:
            conn.execute(
                text(
                    f"CREATE DATABASE IF NOT EXISTS `{name}` "
                    "CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci"
                )
            )
            conn.commit()
    except Exception as exc:
        raise RuntimeError(
            f"Cannot reach or create database {name!r} as user "
            f"{settings.MYSQL_USER!r} on {settings.MYSQL_HOST}:{settings.MYSQL_PORT}. "
            "Run data/interim/create_user.sql in MySQL Workbench as root first, "
            "and check that the MySQL80 service is running."
        ) from exc

    log.info("created database %s", name)


def _database_reachable() -> bool:
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        log.debug("database not reachable yet: %s", exc)
        return False


def run_sql_file(path: Path) -> int:
    """Execute every statement in a .sql file. Returns the statement count."""
    if not path.exists():
        raise FileNotFoundError(f"SQL file not found: {path}")

    statements = split_statements(path.read_text(encoding="utf-8"))

    engine = get_engine()
    with engine.connect() as conn:
        for stmt in statements:
            try:
                conn.execute(text(stmt))
            except Exception as exc:
                head = " ".join(stmt.split())[:120]
                raise RuntimeError(f"Failed on statement: {head}...") from exc
        conn.commit()

    log.info("ran %d statements from %s", len(statements), path.name)
    return len(statements)


def split_statements(script: str) -> list[str]:
    """Split a SQL script on statement boundaries.

    A plain split on ';' is wrong: semicolons appear inside comments and string
    literals, and one in a prose comment will tear a statement in half. This
    walks the text instead, tracking quoting and comment state, and only treats
    a semicolon as a terminator when it sits in open code.
    """
    statements: list[str] = []
    buffer: list[str] = []
    index = 0
    length = len(script)
    quote: str | None = None

    while index < length:
        char = script[index]
        pair = script[index:index + 2]

        if quote is not None:
            buffer.append(char)
            if char == "\\" and quote != "`" and index + 1 < length:
                buffer.append(script[index + 1])   # escaped char, not a closer
                index += 2
                continue
            if char == quote:
                quote = None
            index += 1
            continue

        if pair == "--" or char == "#":
            end = script.find("\n", index)
            index = length if end == -1 else end + 1
            continue

        if pair == "/*":
            end = script.find("*/", index + 2)
            index = length if end == -1 else end + 2
            continue

        if char in ("'", '"', "`"):
            quote = char
            buffer.append(char)
            index += 1
            continue

        if char == ";":
            _flush(buffer, statements)
            index += 1
            continue

        buffer.append(char)
        index += 1

    _flush(buffer, statements)
    return statements


def _flush(buffer: list[str], statements: list[str]) -> None:
    statement = "".join(buffer).strip()
    buffer.clear()
    if statement:
        statements.append(statement)


def write_frame(
    frame: pd.DataFrame,
    table: str,
    *,
    if_exists: str = "append",
    chunksize: int = 20_000,
) -> int:
    """Bulk insert a dataframe. Returns rows written."""
    if frame.empty:
        log.warning("nothing to write to %s — frame is empty", table)
        return 0

    frame.to_sql(
        table,
        get_engine(),
        if_exists=if_exists,
        index=False,
        chunksize=chunksize,
        method="multi",
    )
    log.info("wrote %d rows to %s", len(frame), table)
    return len(frame)


def truncate(tables: Iterable[str]) -> None:
    """Empty tables in the given order. Caller is responsible for FK ordering."""
    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(text("SET FOREIGN_KEY_CHECKS = 0"))
        for table in tables:
            conn.execute(text(f"TRUNCATE TABLE `{table}`"))
            log.info("truncated %s", table)
        conn.execute(text("SET FOREIGN_KEY_CHECKS = 1"))
        conn.commit()


def read_sql(query: str, params: dict | None = None) -> pd.DataFrame:
    """Run a SELECT and return a dataframe."""
    return pd.read_sql(text(query), get_engine(), params=params or {})


def table_counts(tables: Iterable[str]) -> dict[str, int]:
    """Row count per table, for verification after a load."""
    engine = get_engine()
    counts: dict[str, int] = {}
    with engine.connect() as conn:
        for table in tables:
            result = conn.execute(text(f"SELECT COUNT(*) FROM `{table}`"))
            counts[table] = int(result.scalar_one())
    return counts
