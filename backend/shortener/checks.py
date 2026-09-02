"""Startup checks for the database this project is actually pointed at.

Both of these guard mistakes that otherwise stay quiet until something has
already gone wrong in production.
"""

from django.conf import settings
from django.core.checks import Error, register
from django.db import DEFAULT_DB_ALIAS, connections

SQLITE = "sqlite"


@register("database", deploy=True)
def sqlite_in_production(app_configs, **kwargs):
    # SQLite has one writer and lives on one box, so it can't back two Gunicorn
    # services and a Celery worker. Easy to end up here by forgetting DATABASE_URL.
    engine = settings.DATABASES[DEFAULT_DB_ALIAS]["ENGINE"]
    if SQLITE not in engine:
        return []
    return [
        Error(
            "SQLite is configured but DEBUG is off.",
            hint="Set DATABASE_URL to a PostgreSQL URL, e.g. postgres://user:pass@host:5432/dbname.",
            id="shortener.E001",
        )
    ]


@register("database")
def backend_supports_partial_unique(app_configs, **kwargs):
    # ShortURL leans on UNIQUE(code) WHERE code_released_at IS NULL. Django only
    # warns when a backend can't do that and then quietly skips the constraint,
    # which would leave short codes with no uniqueness at all. Fail loudly instead.
    connection = connections[DEFAULT_DB_ALIAS]
    if connection.features.supports_partial_indexes:
        return []
    return [
        Error(
            f"{connection.display_name} does not support partial unique constraints.",
            hint=(
                "Short codes are kept unique by UNIQUE(code) WHERE code_released_at IS NULL. "
                "Without it two live links can share a code and alias claims stop being safe. "
                "Use PostgreSQL, or rework the constraint around a nullable column."
            ),
            id="shortener.E002",
        )
    ]
