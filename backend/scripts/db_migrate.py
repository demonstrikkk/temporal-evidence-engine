"""Apply backend/migrations/*.sql in filename order (idempotent files).

Reads DATABASE_URL from settings (never argv/env echo — the password stays in
.env). Prints per-file table counts after applying. Safe to re-run.

Two schemas are counted because two schemas exist. `public` is the inherited
DataGoblin substrate; `tev` is this product. Counting only `public` made the
PS05 migrations 014-021 look like they had done nothing, which is a poor way for
a migration log to behave when it is the thing you read after a failed deploy.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.core.config import settings  # noqa: E402

import psycopg  # noqa: E402

_COUNT = """
select coalesce(sum(c) filter (where schemaname = 'public'), 0),
       coalesce(sum(c) filter (where schemaname = 'tev'), 0)
  from (select schemaname, count(*) as c from pg_tables group by schemaname) t
"""


def _counts(conn: "psycopg.Connection") -> tuple[int, int]:
    with conn.cursor() as cur:
        cur.execute(_COUNT)
        public, tev = cur.fetchone()
    return int(public), int(tev)


def main() -> None:
    if not settings.DATABASE_URL:
        print("DATABASE_URL unset; aborting")
        raise SystemExit(1)
    mig_dir = Path(__file__).resolve().parents[1] / "migrations"
    files = sorted(mig_dir.glob("*.sql"))
    if not files:
        print("no migration files; aborting")
        raise SystemExit(1)
    with psycopg.connect(settings.DATABASE_URL, connect_timeout=30,
                         autocommit=True) as conn:
        for path in files:
            sql = path.read_text(encoding="utf-8")
            with conn.cursor() as cur:
                cur.execute(sql)
            public, tev = _counts(conn)
            print(f"applied {path.name} (public: {public} tables, tev: {tev})")
        public, tev = _counts(conn)
    print(f"migrate complete ({public} public tables, {tev} tev tables)")


if __name__ == "__main__":
    main()
