"""Thin Postgres access + migration runner. Reads creds from secrets.json
(copy secrets.example.json and fill in your own project).

Usage:
    python -m core.db db/migrations/0030_state_machine.sql
    python -m core.db --query "select count(*) from contacts;"
"""
from __future__ import annotations
import json
import pathlib
import sys
import time
from urllib.parse import urlparse

import psycopg2

_ROOT = pathlib.Path(__file__).resolve().parent.parent


def connect():
    """Connect to the Supabase Postgres DB.

    Retries up to 4 times with exponential back-off (1 s, 2 s, 4 s) to
    tolerate transient DNS / network flakiness on the Supabase pooler host.
    """
    cfg = json.loads((_ROOT / "secrets.json").read_text())
    ref = urlparse(cfg["supabase_url"]).hostname.split(".")[0]
    # Use the IPv4 Session pooler — the direct host db.<ref>.supabase.co is IPv6-only
    # and unreachable on IPv4-only networks. Pooler region host + tenant-qualified user.
    pooler_host = cfg.get("pooler_host", "aws-1-eu-central-1.pooler.supabase.com")
    # macOS DNS blips killed whole runs (fathom-sync died twice on 2026-07-14/15
    # with 'could not translate host name' - all 4 fast retries inside the same
    # blip). Longer tail + a public-DNS resolve fallback ride it out.
    delays = [1, 3, 8, 20, 45]
    last_exc: Exception | None = None
    host = pooler_host
    for attempt, delay in enumerate([0] + delays, start=1):
        if delay:
            time.sleep(delay)
        if attempt == 3 and host == pooler_host:
            # system DNS still failing -> try resolving via public DNS once
            try:
                import subprocess
                out = subprocess.run(["dig", "+short", "@1.1.1.1", pooler_host],
                                     capture_output=True, text=True, timeout=10).stdout.strip().splitlines()
                ips = [l for l in out if l and l[0].isdigit()]
                if ips:
                    host = ips[0]
                    print(f"[connect] system DNS down - pinned pooler IP {host} via 1.1.1.1", file=sys.stderr)
            except Exception:
                pass
        try:
            return psycopg2.connect(
                host=host,
                port=5432,
                user=f"postgres.{ref}",
                password=cfg["db_password"],
                dbname="postgres",
                sslmode="require",
                connect_timeout=15,
            )
        except psycopg2.OperationalError as exc:
            last_exc = exc
            print(
                f"[connect] attempt {attempt}/6 failed: {exc}",
                file=sys.stderr,
            )
    raise last_exc  # type: ignore[misc]


def run_sql(sql: str) -> list | None:
    conn = connect()
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            if cur.description:  # a SELECT-like statement
                return cur.fetchall()
            return None
    finally:
        conn.close()


def run_tx(statements: list) -> None:
    """Execute a list of SQL statements inside a single transaction.

    All statements succeed or all roll back.  No result is returned (use
    run_sql for SELECT queries).

    Each item may be either:
      - a literal SQL string, e.g. "update contacts set x=1 where id='...'", or
      - a (sql, params) tuple for a PARAMETERIZED statement, e.g.
        ("update contacts set emails=%s where id=%s", (["a@b"], cid)).
    Parameterized form is required whenever a value is not a trusted literal
    (arrays, jsonb, free text) — psycopg2 adapts Python list->array, dict via
    Json()->jsonb, and escapes safely. `params` may be None.

    Example:
        run_tx([
            "update interactions set contact_id='new' where contact_id='old'",
            ("update contacts set raw=%s where id=%s", (Json(d), cid)),
            "delete from contacts where id='old'",
        ])
    """
    conn = connect()
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            for stmt in statements:
                if isinstance(stmt, str):
                    cur.execute(stmt)
                else:
                    sql, params = stmt
                    cur.execute(sql, params)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print(__doc__)
        return
    if argv[0] == "--query":
        rows = run_sql(argv[1])
        for r in rows or []:
            print(r)
        return
    path = pathlib.Path(argv[0])
    run_sql(path.read_text())
    print(f"applied {path}")


if __name__ == "__main__":
    main()
