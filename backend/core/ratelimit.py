import logging
import time
from dataclasses import dataclass

import redis
from django.conf import settings

from core import redis_client

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    limit: int
    remaining: int
    retry_after: int


def _window_key(scope, identifier, window):
    now = int(time.time())
    start = now - (now % window)
    return f"rl:{scope}:{identifier}:{start}", start + window - now


def consume(scope, identifier):
    """Count one request against a fixed window and say whether it is allowed.

    Fixed windows allow a burst around the boundary, which is fine for the
    limits here and keeps the counter to a single round trip.
    """
    limit, window = settings.RATE_LIMITS[scope]
    key, seconds_left = _window_key(scope, identifier, window)
    try:
        pipe = redis_client.get_client().pipeline()
        pipe.incr(key)
        pipe.expire(key, seconds_left + 1)
        count = pipe.execute()[0]
    except redis.RedisError:
        # Fail open. Locking everyone out of login for the length of a Redis outage
        # is worse than whatever slips through.
        log.warning("rate limit skipped, redis unavailable (scope=%s)", scope)
        return Verdict(True, limit, limit, 0)

    if count > limit:
        return Verdict(False, limit, 0, seconds_left)
    return Verdict(True, limit, limit - count, 0)


def reset(scope, identifier):
    # Clear a counter, e.g. after a successful login.
    _, window = settings.RATE_LIMITS[scope]
    key, _ = _window_key(scope, identifier, window)
    try:
        redis_client.get_client().delete(key)
    except redis.RedisError:
        log.warning("rate limit reset skipped, redis unavailable (scope=%s)", scope)
