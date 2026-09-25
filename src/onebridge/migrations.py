from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import MetaData, text
from sqlalchemy.engine import Engine


SCHEMA_VERSION = 2


@dataclass(frozen=True, slots=True)
class MigrationStatus:
    current_version: int
    target_version: int
    applied_versions: tuple[int, ...]

    @property
    def up_to_date(self) -> bool:
        return self.current_version == self.target_version

    def to_dict(self) -> dict:
        return {
            "current_version": self.current_version,
            "target_version": self.target_version,
            "applied_versions": list(self.applied_versions),
            "up_to_date": self.up_to_date,
        }


def _ensure_history_table(connection) -> None:
    connection.execute(text(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name VARCHAR(200) NOT NULL,
            applied_at VARCHAR(64) NOT NULL
        )
        """
    ))


def _applied(connection) -> tuple[int, ...]:
    rows = connection.execute(
        text(
            "SELECT version FROM schema_migrations "
            "ORDER BY version"
        )
    ).fetchall()
    return tuple(int(row[0]) for row in rows)


def _record(connection, version: int, name: str) -> None:
    connection.execute(
        text(
            "INSERT INTO schema_migrations"
            "(version,name,applied_at) "
            "VALUES(:version,:name,:applied_at)"
        ),
        {
            "version": int(version),
            "name": str(name),
            "applied_at": datetime.now(
                timezone.utc
            ).isoformat(),
        },
    )


def migrate_database(
    engine: Engine,
    metadata: MetaData,
) -> MigrationStatus:
    """Apply deterministic forward-only OneBridge schema migrations.

    Migration 1 establishes the pre-release-request baseline. Migration 2
    adds persistent release requests for two-person production approvals.
    Existing databases are adopted with checkfirst DDL and then versioned,
    so the first migration run is safe for the current pre-migration schema.
    """

    with engine.begin() as connection:
        if engine.dialect.name == "postgresql":
            connection.execute(
                text(
                    "SELECT pg_advisory_xact_lock(:lock_key)"
                ),
                {"lock_key": 1263421769},
            )

        _ensure_history_table(connection)
        applied = set(_applied(connection))
        unexpected = [
            version
            for version in applied
            if version > SCHEMA_VERSION
        ]
        if unexpected:
            raise RuntimeError(
                "database schema is newer than this OneBridge build"
            )

        if 1 not in applied:
            baseline_tables = [
                table
                for table in metadata.sorted_tables
                if table.name != "release_requests"
            ]
            metadata.create_all(
                bind=connection,
                tables=baseline_tables,
                checkfirst=True,
            )
            _record(connection, 1, "baseline_control_plane")
            applied.add(1)

        if 2 not in applied:
            release_requests = metadata.tables.get(
                "release_requests"
            )
            if release_requests is None:
                raise RuntimeError(
                    "release_requests metadata is unavailable"
                )
            release_requests.create(
                bind=connection,
                checkfirst=True,
            )
            _record(
                connection,
                2,
                "two_person_release_requests",
            )
            applied.add(2)

        ordered = tuple(sorted(applied))
        current = ordered[-1] if ordered else 0
        return MigrationStatus(
            current_version=current,
            target_version=SCHEMA_VERSION,
            applied_versions=ordered,
        )


def schema_status(engine: Engine) -> MigrationStatus:
    with engine.begin() as connection:
        _ensure_history_table(connection)
        applied = _applied(connection)
    current = applied[-1] if applied else 0
    return MigrationStatus(
        current_version=current,
        target_version=SCHEMA_VERSION,
        applied_versions=applied,
    )
