from datetime import timedelta

from django.utils import timezone

from shortener.models import APIKey, ShortURL, create_api_key
from tests.base import AppTestCase


class APIKeyManagementTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.make_user()
        self.client.force_login(self.user)

    def test_secret_is_returned_once_and_never_stored(self):
        response = self.client.post("/api/v1/api-keys/", {"name": "ci"}, content_type="application/json")

        self.assertEqual(response.status_code, 201)
        token = response.json()["token"]
        self.assertTrue(token.startswith("usk_"))

        key = APIKey.objects.get(prefix=response.json()["prefix"])
        self.assertNotIn(token, key.secret_hash)
        self.assertNotIn(token.split("_")[2], key.secret_hash)

        listing = self.client.get("/api/v1/api-keys/").json()["results"]
        self.assertNotIn("token", listing[0])

    def test_delete_revokes_the_key_without_dropping_the_audit_row(self):
        key, _ = create_api_key(self.user, "temporary")

        self.assertEqual(self.client.delete(f"/api/v1/api-keys/{key.pk}/").status_code, 204)

        key.refresh_from_db()
        self.assertIsNotNone(key.revoked_at)
        self.assertFalse(key.is_active)

    def test_users_cannot_see_or_revoke_other_users_keys(self):
        other = self.make_user("other@example.com")
        key, _ = create_api_key(other, "theirs")

        self.assertEqual(self.client.get(f"/api/v1/api-keys/{key.pk}/").status_code, 404)
        self.assertEqual(self.client.delete(f"/api/v1/api-keys/{key.pk}/").status_code, 404)

        key.refresh_from_db()
        self.assertIsNone(key.revoked_at)


class APIKeyAuthenticationTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.make_user()
        self.key, self.token = create_api_key(self.user, "cli")
        ShortURL.objects.create(code="mylink1", destination="https://example.com/", owner=self.user)

    def get(self, path, token):
        return self.client.get(path, HTTP_AUTHORIZATION=f"Bearer {token}")

    def test_valid_key_authenticates(self):
        response = self.get("/api/v1/urls/", self.token)

        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["code"] for row in response.json()["results"]], ["mylink1"])

    def test_last_used_at_is_recorded(self):
        self.get("/api/v1/urls/", self.token)

        self.key.refresh_from_db()
        self.assertIsNotNone(self.key.last_used_at)

    def test_tampered_secret_is_rejected(self):
        prefix, secret = self.token.split("_")[1], self.token.split("_")[2]
        forged = f"usk_{prefix}_{secret[:-1]}x"

        response = self.get("/api/v1/urls/", forged)

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "AUTHENTICATION_FAILED")

    def test_malformed_token_is_rejected(self):
        for token in ["garbage", "usk_only", "usk__nosecret"]:
            self.assertEqual(self.get("/api/v1/urls/", token).status_code, 401, token)

    def test_revoked_key_stops_working(self):
        self.key.revoke()
        self.assertEqual(self.get("/api/v1/urls/", self.token).status_code, 401)

    def test_expired_key_stops_working(self):
        self.key.expires_at = timezone.now() - timedelta(seconds=1)
        self.key.save(update_fields=["expires_at"])

        self.assertEqual(self.get("/api/v1/urls/", self.token).status_code, 401)

    def test_key_cannot_reach_another_users_link(self):
        other = self.make_user("other@example.com")
        theirs = ShortURL.objects.create(code="notmine", destination="https://example.com/", owner=other)

        self.assertEqual(self.get(f"/api/v1/urls/{theirs.pk}/", self.token).status_code, 404)

    def test_key_cannot_mint_more_keys(self):
        response = self.client.post(
            "/api/v1/api-keys/",
            {"name": "escalation"},
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {self.token}",
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(APIKey.objects.filter(name="escalation").count(), 0)
