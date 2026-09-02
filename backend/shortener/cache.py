"""Redis-backed cache for the redirect hot path.

The cached payload deliberately carries `expires_at` so a stale entry can never
outlive the link it describes: the redirect view re-checks expiry against the
cached value, and the entry's TTL is additionally capped at the link's lifetime.
"""

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone
from django.utils.dateparse import parse_datetime

MISSING = "__missing__"


def _key(code):
    return f"resolve:{code}"


def payload_for(url):
    return {
        "id": url.pk,
        "destination": url.destination,
        "has_password": url.has_password,
        "expires_at": url.expires_at.isoformat() if url.expires_at else None,
        # Not the hash itself: only a marker, so that changing the password
        # invalidates unlock cookies issued against the previous one.
        "password_version": url.password_updated_at.isoformat() if url.password_updated_at else "",
    }


def payload_expires_at(payload):
    raw = payload.get("expires_at")
    return parse_datetime(raw) if raw else None


def load(code):
    """Return a payload dict, the MISSING sentinel, or None for a cache miss."""
    return cache.get(_key(code))


def store(code, payload):
    ttl = settings.RESOLVE_CACHE_TTL
    expires_at = payload_expires_at(payload)
    if expires_at is not None:
        remaining = int((expires_at - timezone.now()).total_seconds())
        if remaining <= 0:
            return
        ttl = min(ttl, remaining)
    cache.set(_key(code), payload, ttl)


def store_missing(code):
    """Short negative cache so scanners hitting random codes do not reach the database."""
    cache.set(_key(code), MISSING, settings.MISSING_CACHE_TTL)


def forget(code):
    cache.delete(_key(code))
