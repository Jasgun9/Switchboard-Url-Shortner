from rest_framework.throttling import BaseThrottle

from core import ratelimit
from core.clientinfo import client_ip


class RedisThrottle(BaseThrottle):
    """Bridges DRF's throttling hook to the shared Redis limiter."""

    scope = None

    def get_scope(self, request):
        return self.scope

    def get_identifier(self, request):
        if request.user.is_authenticated:
            return f"user:{request.user.pk}"
        return f"ip:{client_ip(request)}"

    def allow_request(self, request, view):
        verdict = ratelimit.consume(self.get_scope(request), self.get_identifier(request))
        self.retry_after = verdict.retry_after
        return verdict.allowed

    def wait(self):
        return self.retry_after


class APIThrottle(RedisThrottle):
    scope = "api"


class URLCreateThrottle(RedisThrottle):
    def get_scope(self, request):
        return "user_create" if request.user.is_authenticated else "anon_create"
