from unittest import mock

import redis
from django.test import override_settings

from tests.base import AppTestCase


class HealthTests(AppTestCase):
    def test_liveness_does_not_touch_dependencies(self):
        with self.assertNumQueries(0):
            response = self.client.get("/health/live")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")

    def test_readiness_reports_each_dependency(self):
        response = self.client.get("/health/ready")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["checks"], {"database": "ok", "redis": "ok"})

    def test_redis_outage_is_reported_but_still_ready(self):
        self.redis.ping = mock.Mock(side_effect=redis.ConnectionError("down"))

        response = self.client.get("/health/ready")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["checks"]["redis"], "error")

    @override_settings(ROOT_URLCONF="config.urls_redirect")
    def test_the_redirect_service_exposes_health_too(self):
        self.assertEqual(self.client.get("/health/live").status_code, 200)

    @override_settings(ROOT_URLCONF="config.urls_redirect")
    def test_the_redirect_service_does_not_expose_the_api_or_admin(self):
        self.assertEqual(self.client.get("/api/v1/urls/").status_code, 404)
        self.assertEqual(self.client.get("/admin/").status_code, 404)
