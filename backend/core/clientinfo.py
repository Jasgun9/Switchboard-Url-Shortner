import hashlib

from django.conf import settings


def client_ip(request):
    """The visitor's IP according to the one proxy header we trust.

    Anything a client can set directly is unusable for rate limiting, so the
    header name is configuration, not guesswork.
    """
    forwarded = request.META.get(settings.CLIENT_IP_HEADER)
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


def hash_ip(ip):
    """Salted, truncated digest so click rows can be counted but not reversed."""
    if not ip:
        return ""
    digest = hashlib.sha256(f"{settings.IP_HASH_SALT}:{ip}".encode()).hexdigest()
    return digest[:32]
