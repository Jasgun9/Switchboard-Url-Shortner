"""Redis cache for the redirect hot path.

The payload carries `expires_at` so a stale entry can't outlive the link it
describes. Belt and braces: the redirect view re-checks expiry against the
cached value, and the TTL is capped at the link's remaining lifetime anyway.
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
        # A marker, not the hash. Changing the password invalidates the unlock
        # cookies handed out under the old one.
        "password_version": url.password_updated_at.isoformat() if url.password_updated_at else "",
    }


def payload_expires_at(payload):
    raw = payload.get("expires_at")
    return parse_datetime(raw) if raw else None


def load(code):
    # Return a payload dict, the MISSING sentinel, or None for a cache miss.
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
    # Short negative cache so scanners hitting random codes do not reach the database.
    cache.set(_key(code), MISSING, settings.MISSING_CACHE_TTL)


def forget(code):
    cache.delete(_key(code))
