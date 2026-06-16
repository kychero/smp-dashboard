from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None

if load_dotenv:
    load_dotenv(Path(__file__).with_name(".env"))


def database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    return url


@contextmanager
def connect() -> Iterator:
    import psycopg

    conn = psycopg.connect(database_url())
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def rows(query: str, params: tuple | dict | None = None) -> list[dict]:
    with connect() as conn:
        with conn.cursor(row_factory=dict_row()) as cur:
            cur.execute(query, params)
            return list(cur.fetchall())


def one(query: str, params: tuple | dict | None = None) -> dict | None:
    result = rows(query, params)
    return result[0] if result else None


def dict_row():
    from psycopg.rows import dict_row as factory

    return factory

