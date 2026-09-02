import logging
from datetime import timedelta

from django.utils import timezone
from django.utils.crypto import constant_time_compare
from rest_framework import exceptions
from rest_framework.authentication import BaseAuthentication, get_authorization_header

from shortener.models import APIKey, hash_api_secret

log = logging.getLogger(__name__)

TOKEN_PREFIX = "usk"
# Don't touch last_used_at on every call, that's a row lock per request.
LAST_USED_RESOLUTION = timedelta(minutes=1)


def split_token(token):
    parts = token.split("_", 2)
    if len(parts) != 3 or parts[0] != TOKEN_PREFIX or not parts[1] or not parts[2]:
        return None
    return parts[1], parts[2]


class APIKeyAuthentication(BaseAuthentication):
    """`Authorization: Bearer usk_<prefix>_<secret>`.

    The prefix is the indexed lookup column; the secret is compared as a
    SHA-256 digest in constant time.
    """

    def authenticate(self, request):
        header = get_authorization_header(request).split()
        if not header or header[0].lower() != b"bearer":
            return None
        if len(header) != 2:
            raise exceptions.AuthenticationFailed("Malformed Authorization header.")

        parsed = split_token(header[1].decode("latin-1"))
        if parsed is None:
            raise exceptions.AuthenticationFailed("Malformed API key.")
        prefix, secret = parsed

        key = APIKey.objects.usable().select_related("owner").filter(prefix=prefix).first()
        if key is None or not constant_time_compare(key.secret_hash, hash_api_secret(secret)):
            log.info("API key authentication failed for prefix %s", prefix)
            raise exceptions.AuthenticationFailed("Invalid or expired API key.")
        if not key.owner.is_active:
            raise exceptions.AuthenticationFailed("Account is disabled.")

        self._touch(key)
        return key.owner, key

    def authenticate_header(self, request):
        return "Bearer"

    def _touch(self, key):
        now = timezone.now()
        if key.last_used_at is None or now - key.last_used_at > LAST_USED_RESOLUTION:
            APIKey.objects.filter(pk=key.pk).update(last_used_at=now)
