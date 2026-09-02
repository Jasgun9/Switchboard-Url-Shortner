from django.conf import settings
from django.utils import timezone

from shortener import cache
from shortener.models import ShortURL


def resolve(code):
    """Look up a short code for the redirect path.

    Returns a small payload dict, or None when the code does not resolve
    (unknown, disabled, soft-deleted or expired).
    """
    # No stored code can be longer than this, so an oversized path is answered
    # without spending a cache key or a query on it.
    if not code or len(code) > settings.ALIAS_MAX_LENGTH:
        return None

    payload = cache.load(code)

    if payload == cache.MISSING:
        return None

    if payload is None:
        url = ShortURL.objects.alive().filter(code=code).first()
        if url is None or url.is_expired:
            cache.store_missing(code)
            return None
        payload = cache.payload_for(url)
        cache.store(code, payload)

    # Second expiry check: a cached entry written just before expiry could still
    # be within its TTL.
    expires_at = cache.payload_expires_at(payload)
    if expires_at is not None and expires_at <= timezone.now():
        cache.forget(code)
        return None

    return payload
