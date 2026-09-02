import logging

import redis
from django.db import DatabaseError, connection
from django.http import JsonResponse
from django.views.decorators.cache import never_cache

from core import redis_client

log = logging.getLogger(__name__)


@never_cache
def live(request):
    """Process is up. Deliberately touches nothing else."""
    return JsonResponse({"status": "ok"})


@never_cache
def ready(request):
    checks = {}

    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        checks["database"] = "ok"
    except DatabaseError as exc:
        log.error("readiness: database check failed: %s", exc)
        checks["database"] = "error"

    try:
        redis_client.get_client().ping()
        checks["redis"] = "ok"
    except redis.RedisError as exc:
        log.warning("readiness: redis check failed: %s", exc)
        checks["redis"] = "error"

    # Redis being down degrades performance but the service still resolves URLs
    # from the database, so only the database gates readiness.
    healthy = checks["database"] == "ok"
    return JsonResponse(
        {"status": "ok" if healthy else "unavailable", "checks": checks},
        status=200 if healthy else 503,
    )
