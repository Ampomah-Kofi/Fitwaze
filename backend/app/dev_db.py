"""Prepare the local demo database (used by start-demo.ps1).

The demo runs on a SQLite file created straight from the models rather than
through Alembic. When a new version adds columns, `create_all` leaves the
existing tables alone, and every query touching a new column fails with a
500. This adds any missing columns in place, so pulling a new version never
means deleting the demo database and its accounts.

Only for the local SQLite demo; real databases are upgraded with
`alembic upgrade head`.
"""
from __future__ import annotations

from sqlalchemy import inspect, text

from app.db import Base, engine
import app.main  # noqa: F401  (registers every model on Base.metadata)


def prepare() -> list[str]:
    Base.metadata.create_all(engine)
    added: list[str] = []
    inspector = inspect(engine)
    with engine.begin() as connection:
        for table in Base.metadata.sorted_tables:
            existing = {column["name"] for column in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing:
                    continue
                if not column.nullable:
                    raise RuntimeError(
                        f"{table.name}.{column.name} is new and required; delete demo.db to recreate it."
                    )
                column_type = column.type.compile(dialect=engine.dialect)
                connection.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {column_type}'))
                added.append(f"{table.name}.{column.name}")
    return added


if __name__ == "__main__":
    for name in prepare():
        print(f"Added column {name}")
    print("Demo database ready.")
