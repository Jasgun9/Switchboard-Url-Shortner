from datetime import timedelta
from unittest import mock

from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from shortener.models import ShortURL
from tests.base import AppTestCase


class URLCreationTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.make_user()
        self.client.force_login(self.user)

    def test_creates_a_link_with_a_random_code(self):
        response = self.client.post(
            "/api/v1/urls/", {"destination": "https://example.com/a/long/path"}, content_type="application/json"
        )

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(len(body["code"]), 7)
        self.assertTrue(body["short_url"].endswith(body["code"]))
        self.assertEqual(ShortURL.objects.get(code=body["code"]).owner, self.user)

    def test_creates_a_link_with_a_custom_alias(self):
        response = self.client.post(
            "/api/v1/urls/",
            {"destination": "https://example.com/", "alias": "Portfolio"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["code"], "portfolio")
        self.assertTrue(ShortURL.objects.get(code="portfolio").is_custom_alias)

    def test_duplicate_alias_returns_a_conflict(self):
        ShortURL.objects.create(code="portfolio", destination="https://example.com/")

        response = self.client.post(
            "/api/v1/urls/",
            {"destination": "https://example.com/other", "alias": "portfolio"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "ALIAS_ALREADY_EXISTS")

    def test_alias_race_is_decided_by_the_database(self):
        """Both requests see the alias as free; the loser must get a clean 409."""
        ShortURL.objects.create(code="contested", destination="https://example.com/winner")

        response = self.client.post(
            "/api/v1/urls/",
            {"destination": "https://example.com/loser", "alias": "contested"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(ShortURL.objects.filter(code="contested").count(), 1)

    def test_alias_of_a_deleted_link_can_be_reused_through_the_api(self):
        first = ShortURL.objects.create(code="promo", destination="https://example.com/old", owner=self.user)
        first.soft_delete()

        response = self.client.post(
            "/api/v1/urls/",
            {"destination": "https://example.com/new", "alias": "promo"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["code"], "promo")
        self.assertEqual(ShortURL.objects.alive().get(code="promo").destination, "https://example.com/new")

    def test_alias_of_an_expired_link_can_be_reused_through_the_api(self):
        ShortURL.objects.create(
            code="promo",
            destination="https://example.com/old",
            owner=self.user,
            expires_at=timezone.now() - timedelta(days=1),
        )

        response = self.client.post(
            "/api/v1/urls/",
            {"destination": "https://example.com/new", "alias": "promo"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(ShortURL.objects.alive().get(code="promo").destination, "https://example.com/new")

    def test_reserved_alias_is_rejected(self):
        response = self.client.post(
            "/api/v1/urls/", {"destination": "https://example.com/", "alias": "admin"}, content_type="application/json"
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "VALIDATION_ERROR")
        self.assertIn("alias", response.json()["error"]["details"])

    def test_dangerous_destinations_are_rejected(self):
        for destination in ["javascript:alert(1)", "data:text/html,x", "file:///etc/passwd", "http://127.0.0.1/"]:
            response = self.client.post(
                "/api/v1/urls/", {"destination": destination}, content_type="application/json"
            )
            self.assertEqual(response.status_code, 400, destination)

    def test_expiry_must_be_in_the_future(self):
        response = self.client.post(
            "/api/v1/urls/",
            {"destination": "https://example.com/", "expires_at": (timezone.now() - timedelta(days=1)).isoformat()},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)

    def test_link_password_is_never_returned(self):
        response = self.client.post(
            "/api/v1/urls/",
            {"destination": "https://example.com/", "password": "letmein-please"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.json()["has_password"])
        self.assertNotIn("password", response.json())
        self.assertNotIn("password_hash", response.json())

    def test_code_generation_failure_is_reported_cleanly(self):
        with mock.patch("shortener.models.generate_code", return_value="abcdefg"):
            ShortURL.objects.create(code="abcdefg", destination="https://example.com/")
            response = self.client.post(
                "/api/v1/urls/", {"destination": "https://example.com/x"}, content_type="application/json"
            )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "CODE_GENERATION_FAILED")


class AnonymousCreationTests(AppTestCase):
    def test_anonymous_visitors_can_shorten_without_an_owner(self):
        response = self.client.post(
            "/api/v1/urls/", {"destination": "https://example.com/"}, content_type="application/json"
        )

        self.assertEqual(response.status_code, 201)
        self.assertIsNone(ShortURL.objects.get(code=response.json()["code"]).owner)

    def test_anonymous_visitors_cannot_list_links(self):
        response = self.client.get("/api/v1/urls/")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "AUTHENTICATION_REQUIRED")

    @override_settings(RATE_LIMITS={"anon_create": (2, 3600), "user_create": (100, 3600), "api": (100, 60)})
    def test_anonymous_creation_is_rate_limited(self):
        for _ in range(2):
            self.client.post("/api/v1/urls/", {"destination": "https://example.com/"}, content_type="application/json")

        response = self.client.post(
            "/api/v1/urls/", {"destination": "https://example.com/"}, content_type="application/json"
        )
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()["error"]["code"], "RATE_LIMITED")


class URLAuthorizationTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.owner = self.make_user("owner@example.com")
        self.other = self.make_user("other@example.com")
        self.url = ShortURL.objects.create(code="private", destination="https://example.com/", owner=self.owner)

    def test_another_user_cannot_read_update_or_delete_a_link(self):
        self.client.force_login(self.other)
        detail = f"/api/v1/urls/{self.url.pk}/"

        self.assertEqual(self.client.get(detail).status_code, 404)
        self.assertEqual(
            self.client.patch(detail, {"title": "stolen"}, content_type="application/json").status_code, 404
        )
        self.assertEqual(self.client.delete(detail).status_code, 404)
        self.assertEqual(self.client.get(f"{detail}analytics/").status_code, 404)

        self.url.refresh_from_db()
        self.assertEqual(self.url.title, "")
        self.assertIsNone(self.url.deleted_at)

    def test_list_only_returns_the_callers_links(self):
        ShortURL.objects.create(code="theirs1", destination="https://example.com/", owner=self.other)
        self.client.force_login(self.owner)

        results = self.client.get("/api/v1/urls/").json()["results"]

        self.assertEqual([row["code"] for row in results], ["private"])


class URLManagementTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.make_user()
        self.client.force_login(self.user)
        self.url = ShortURL.objects.create(code="managed", destination="https://example.com/old", owner=self.user)

    def test_patch_updates_destination_and_expiry(self):
        expires = (timezone.now() + timedelta(days=2)).isoformat()
        response = self.client.patch(
            f"/api/v1/urls/{self.url.pk}/",
            {"destination": "https://example.com/new", "expires_at": expires},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.url.refresh_from_db()
        self.assertEqual(self.url.destination, "https://example.com/new")
        self.assertIsNotNone(self.url.expires_at)

    def test_patch_cannot_change_the_short_code(self):
        response = self.client.patch(
            f"/api/v1/urls/{self.url.pk}/", {"alias": "renamed"}, content_type="application/json"
        )

        self.assertEqual(response.status_code, 400)
        self.url.refresh_from_db()
        self.assertEqual(self.url.code, "managed")

    def test_patch_can_remove_a_link_password(self):
        self.url.set_link_password("something-secret")
        self.url.save()

        response = self.client.patch(
            f"/api/v1/urls/{self.url.pk}/", {"password": ""}, content_type="application/json"
        )

        self.assertEqual(response.status_code, 200)
        self.url.refresh_from_db()
        self.assertFalse(self.url.has_password)

    def test_delete_is_a_soft_delete_and_hides_the_link(self):
        response = self.client.delete(f"/api/v1/urls/{self.url.pk}/")

        self.assertEqual(response.status_code, 204)
        self.url.refresh_from_db()
        self.assertIsNotNone(self.url.deleted_at)
        self.assertEqual(self.client.get("/api/v1/urls/").json()["count"], 0)

    def test_search_and_status_filters(self):
        ShortURL.objects.create(
            code="expired1",
            destination="https://other.example.org/",
            owner=self.user,
            expires_at=timezone.now() - timedelta(days=1),
        )

        matched = self.client.get("/api/v1/urls/?search=other.example").json()
        self.assertEqual([row["code"] for row in matched["results"]], ["expired1"])

        active = self.client.get("/api/v1/urls/?status=active").json()
        self.assertEqual([row["code"] for row in active["results"]], ["managed"])

        expired = self.client.get("/api/v1/urls/?status=expired").json()
        self.assertEqual([row["code"] for row in expired["results"]], ["expired1"])

    def test_pagination_metadata(self):
        for index in range(25):
            ShortURL.objects.create(code=f"page{index:03d}", destination="https://example.com/", owner=self.user)

        page = self.client.get("/api/v1/urls/?page_size=10&page=2").json()

        self.assertEqual(page["count"], 26)
        self.assertEqual(page["page"], 2)
        self.assertEqual(page["total_pages"], 3)
        self.assertEqual(len(page["results"]), 10)

    def test_listing_does_not_scale_queries_with_the_number_of_rows(self):
        for index in range(5):
            ShortURL.objects.create(code=f"nplus{index}", destination="https://example.com/", owner=self.user)
        with CaptureQueriesContext(connection) as few:
            self.client.get("/api/v1/urls/")

        for index in range(5, 20):
            ShortURL.objects.create(code=f"nplus{index}", destination="https://example.com/", owner=self.user)
        with CaptureQueriesContext(connection) as many:
            self.client.get("/api/v1/urls/")

        self.assertEqual(len(few.captured_queries), len(many.captured_queries))


class QRCodeTests(AppTestCase):
    def test_qr_endpoint_returns_a_png_and_is_cached(self):
        ShortURL.objects.create(code="qrlink1", destination="https://example.com/")

        response = self.client.get("/api/v1/qr/qrlink1.png")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/png")
        self.assertTrue(response.content.startswith(b"\x89PNG"))

        with mock.patch("shortener.qr.qrcode.make") as maker:
            self.client.get("/api/v1/qr/qrlink1.png")
        maker.assert_not_called()

    def test_qr_for_a_deleted_link_is_not_served(self):
        url = ShortURL.objects.create(code="qrgone1", destination="https://example.com/")
        url.soft_delete()

        self.assertEqual(self.client.get("/api/v1/qr/qrgone1.png").status_code, 404)
