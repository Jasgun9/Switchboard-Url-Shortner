from django.conf import settings
from django.utils import timezone

from shortener import cache
from shortener.models import ShortURL


def resolve(code):
    """Look up a short code for the redirect path.

    Returns a small payload dict, or None when the code does not resolve
    (unknown, disabled, soft-deleted or expired).
    """
    # Nothing stored is longer than this, so bail before spending a cache key
    # or a query on it.
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

    # Check expiry again. An entry written just before expiry can still be
    # inside its TTL.
    expires_at = cache.payload_expires_at(payload)
    if expires_at is not None and expires_at <= timezone.now():
        cache.forget(code)
        return None

    return payload
