from __future__ import annotations

from contextlib import closing

from .app import load_env
from .data_store import DB_PATH, SCHEMA_VERSION, connect


def run(path: str = DB_PATH) -> int:
    """Apply idempotent SQLite schema migrations through the canonical connector."""
    load_env()
    with closing(connect(path)) as connection:
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        connection.commit()
    print(f"database migrated: path={path} schema_version={SCHEMA_VERSION}")
    return SCHEMA_VERSION


if __name__ == "__main__":
    run()
