import secrets

from django.conf import settings
from django.core.cache import cache

from shortener.validators import RESERVED_ALIASES

# No 0/O and no 1/l/I. People read these out loud and retype them by hand.
ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"

# Once the two-character codes are mostly taken, every new link would burn a
# handful of doomed INSERTs rediscovering that. Remember where to start instead.
# The TTL is the point: it expires, we drop back to the shortest length and
# re-probe, so codes freed by deletions get picked up again within the hour.
_FLOOR_KEY = "shortcode:length-floor"
_FLOOR_TTL = 3600


def generate_code(length):
    while True:
        code = "".join(secrets.choice(ALPHABET) for _ in range(length))
        if code.lower() not in RESERVED_ALIASES:
            return code


def length_floor():
    # Shortest length still worth trying. Falls back to the minimum whenever
    # Redis is unavailable, which costs a few retries and stays correct.
    cached = cache.get(_FLOOR_KEY)
    floor = cached if isinstance(cached, int) else settings.SHORT_CODE_MIN_LENGTH
    return min(max(floor, settings.SHORT_CODE_MIN_LENGTH), settings.SHORT_CODE_MAX_LENGTH)


def remember_length_floor(length):
    if length > settings.SHORT_CODE_MIN_LENGTH:
        cache.set(_FLOOR_KEY, length, _FLOOR_TTL)


def capacity(length):
    return len(ALPHABET) ** length
