import redis
from django.conf import settings

_client = None


def get_client():
    # Shared redis-py client. It pools connections internally and is thread safe.
    global _client
    if _client is None:
        _client = redis.Redis.from_url(
            settings.REDIS_URL,
            # Same as the cache backend: fail fast instead of making every rate-limit
            # check sit through a connect timeout.
            socket_connect_timeout=0.3,
            socket_timeout=1,
            retry_on_timeout=False,
            decode_responses=True,
            health_check_interval=30,
        )
    return _client


def reset_client():
    # Used by tests that swap REDIS_URL or a fake client.
    global _client
    _client = None
