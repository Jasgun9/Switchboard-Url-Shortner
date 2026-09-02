from unittest import mock

import fakeredis
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, override_settings

User = get_user_model()

LOCAL_CACHE = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "tests",
    }
}

# The manifest storage would need a collectstatic run before every test session.
PLAIN_STATIC = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


@override_settings(
    CACHES=LOCAL_CACHE,
    STORAGES=PLAIN_STATIC,
    CELERY_TASK_ALWAYS_EAGER=True,
    BLOCK_PRIVATE_DESTINATIONS=True,
)
class AppTestCase(TestCase):
    """Isolates each test from real Redis while still exercising the cache and
    rate-limit code paths."""

    def setUp(self):
        super().setUp()
        cache.clear()
        self.redis = fakeredis.FakeRedis(decode_responses=True)
        redis_patcher = mock.patch("core.redis_client.get_client", return_value=self.redis)
        redis_patcher.start()
        self.addCleanup(redis_patcher.stop)

        # Clicks are dispatched to Celery, never executed inline. Tests that
        # care about the task call it directly.
        click_patcher = mock.patch("shortener.tasks.record_click.delay")
        self.dispatched_clicks = click_patcher.start()
        self.addCleanup(click_patcher.stop)

    def make_user(self, email="user@example.com", password="correct-horse-battery"):
        return User.objects.create_user(email=email, password=password)
