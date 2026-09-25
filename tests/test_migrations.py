from pathlib import Path

from sqlalchemy import create_engine, inspect, text

from onebridge.db import Database
from onebridge.migrations import SCHEMA_VERSION


def test_database_initialization_applies_schema_versions(tmp_path: Path):
    db = Database(f"sqlite:///{tmp_path / 'schema.db'}")
    status = db.migrate()

    assert status.current_version == SCHEMA_VERSION
    assert status.up_to_date
    assert status.applied_versions == (1, 2)

    tables = set(inspect(db.engine).get_table_names())
    assert "schema_migrations" in tables
    assert "release_requests" in tables


def test_migration_adopts_existing_unversioned_database(tmp_path: Path):
    path = tmp_path / "legacy.db"
    url = f"sqlite:///{path}"
    engine = create_engine(url, future=True)
    with engine.begin() as connection:
        connection.execute(text(
            "CREATE TABLE projects ("
            "id VARCHAR(96) PRIMARY KEY, "
            "tenant_id VARCHAR(200), "
            "status VARCHAR(40), "
            "goal TEXT, "
            "created_at DATETIME, "
            "updated_at DATETIME"
            ")"
        ))

    db = Database(url)
    first = db.migrate()
    second = db.migrate()

    assert first.current_version == SCHEMA_VERSION
    assert second.applied_versions == (1, 2)
    assert set(inspect(db.engine).get_table_names()) >= {
        "projects",
        "release_requests",
        "schema_migrations",
    }
