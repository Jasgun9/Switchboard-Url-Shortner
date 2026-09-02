from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.utils import timezone

from shortener.models import APIKey, ShortURL, create_api_key
from tests.base import AppTestCase

User = get_user_model()


class HomePageTests(AppTestCase):
    def test_anonymous_visitor_can_shorten_a_link(self):
        response = self.client.post("/", {"destination": "https://example.com/long/path", "alias": ""})

        self.assertEqual(response.status_code, 200)
        link = ShortURL.objects.get()
        self.assertIsNone(link.owner)
        self.assertContains(response, link.code)

    def test_signed_in_visitor_owns_what_they_shorten(self):
        user = self.make_user()
        self.client.force_login(user)

        self.client.post("/", {"destination": "https://example.com/", "alias": "portfolio"})

        self.assertEqual(ShortURL.objects.get(code="portfolio").owner, user)

    def test_dangerous_destination_is_rejected_with_a_field_error(self):
        response = self.client.post("/", {"destination": "javascript:alert(1)", "alias": ""})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Only http:// and https:// URLs can be shortened.")
        self.assertEqual(ShortURL.objects.count(), 0)

    def test_taken_alias_is_reported_not_crashed(self):
        ShortURL.objects.create(code="portfolio", destination="https://example.com/")

        response = self.client.post("/", {"destination": "https://example.com/other", "alias": "portfolio"})

        self.assertContains(response, "That alias is already in use.")
        self.assertEqual(ShortURL.objects.filter(code="portfolio").count(), 1)

    @override_settings(RATE_LIMITS={"anon_create": (2, 3600)})
    def test_anonymous_shortening_is_rate_limited(self):
        for _ in range(2):
            self.client.post("/", {"destination": "https://example.com/", "alias": ""})

        response = self.client.post("/", {"destination": "https://example.com/", "alias": ""})

        self.assertContains(response, "too many links")
        self.assertEqual(ShortURL.objects.count(), 2)


class AuthPageTests(AppTestCase):
    def test_register_creates_an_account_and_signs_in(self):
        response = self.client.post(
            "/register",
            {"email": "New@Example.com", "display_name": "", "password": "correct-horse-battery"},
        )

        self.assertRedirects(response, "/dashboard")
        user = User.objects.get()
        self.assertEqual(user.email, "new@example.com")
        self.assertTrue(user.check_password("correct-horse-battery"))

    def test_weak_password_is_rejected(self):
        response = self.client.post(
            "/register", {"email": "weak@example.com", "display_name": "", "password": "password12"}
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(User.objects.count(), 0)

    def test_login_and_logout(self):
        self.make_user("me@example.com", "correct-horse-battery")

        response = self.client.post("/login", {"email": "me@example.com", "password": "correct-horse-battery"})
        self.assertRedirects(response, "/dashboard")

        self.assertRedirects(self.client.post("/logout"), "/")
        self.assertEqual(self.client.get("/dashboard").status_code, 302)

    def test_bad_credentials_do_not_say_which_half_was_wrong(self):
        self.make_user("me@example.com", "correct-horse-battery")

        response = self.client.post("/login", {"email": "me@example.com", "password": "nope"})

        self.assertContains(response, "Incorrect email or password.")

    def test_login_redirects_back_to_the_requested_page(self):
        user = self.make_user("me@example.com", "correct-horse-battery")
        link = ShortURL.objects.create(code="mine123", destination="https://example.com/", owner=user)

        response = self.client.post(
            "/login",
            {"email": "me@example.com", "password": "correct-horse-battery", "next": f"/links/{link.pk}"},
        )

        self.assertRedirects(response, f"/links/{link.pk}")

    def test_login_ignores_an_offsite_next(self):
        self.make_user("me@example.com", "correct-horse-battery")

        response = self.client.post(
            "/login",
            {"email": "me@example.com", "password": "correct-horse-battery", "next": "https://evil.example/"},
        )

        self.assertRedirects(response, "/dashboard")

    def test_logout_requires_post(self):
        self.client.force_login(self.make_user())
        self.assertEqual(self.client.get("/logout").status_code, 405)

    @override_settings(RATE_LIMITS={"login": (3, 900)})
    def test_login_attempts_are_rate_limited(self):
        self.make_user("me@example.com", "correct-horse-battery")

        for _ in range(3):
            self.client.post("/login", {"email": "me@example.com", "password": "wrong"})

        response = self.client.post("/login", {"email": "me@example.com", "password": "correct-horse-battery"})

        self.assertContains(response, "Too many sign-in attempts")


class DashboardTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.make_user()
        self.client.force_login(self.user)
        self.link = ShortURL.objects.create(
            code="managed", destination="https://example.com/old", owner=self.user, title="Docs"
        )

    def test_lists_only_the_signed_in_users_links(self):
        other = self.make_user("other@example.com")
        ShortURL.objects.create(code="theirs1", destination="https://example.com/", owner=other)

        response = self.client.get("/dashboard")

        self.assertContains(response, "/managed")
        self.assertNotContains(response, "/theirs1")

    def test_search_and_status_filters(self):
        ShortURL.objects.create(
            code="expired1",
            destination="https://other.example.org/",
            owner=self.user,
            expires_at=timezone.now() - timedelta(days=1),
        )

        self.assertContains(self.client.get("/dashboard?search=other.example"), "/expired1")
        self.assertNotContains(self.client.get("/dashboard?search=other.example"), "/managed")
        self.assertNotContains(self.client.get("/dashboard?status=active"), "/expired1")
        self.assertNotContains(self.client.get("/dashboard?status=expired"), "/managed")

    def test_pagination_keeps_the_active_filters(self):
        for index in range(25):
            ShortURL.objects.create(code=f"page{index:03d}", destination="https://example.com/", owner=self.user)

        response = self.client.get("/dashboard?status=active&page=2")

        self.assertEqual(response.context["page"].number, 2)
        self.assertContains(response, "status=active")

    def test_dashboard_requires_signing_in(self):
        self.client.logout()
        response = self.client.get("/dashboard")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login", response["Location"])


class LinkManagementPageTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.make_user()
        self.client.force_login(self.user)
        self.link = ShortURL.objects.create(code="managed", destination="https://example.com/old", owner=self.user)

    def test_create_form_makes_a_link_with_a_password_and_expiry(self):
        expires = (timezone.now() + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M")

        response = self.client.post(
            "/links/new",
            {
                "destination": "https://example.com/new",
                "title": "Launch",
                "alias": "launch",
                "expires_at": expires,
                "password": "letmein-please",
            },
        )

        link = ShortURL.objects.get(code="launch")
        self.assertRedirects(response, f"/links/{link.pk}")
        self.assertTrue(link.has_password)
        self.assertTrue(link.check_link_password("letmein-please"))
        self.assertIsNotNone(link.expires_at)

    def test_edit_updates_the_destination_and_leaves_the_code_alone(self):
        response = self.client.post(
            f"/links/{self.link.pk}/edit",
            {
                "destination": "https://example.com/new",
                "title": "",
                "expires_at": "",
                "password": "",
                "is_active": "on",
            },
        )

        self.assertRedirects(response, f"/links/{self.link.pk}")
        self.link.refresh_from_db()
        self.assertEqual(self.link.destination, "https://example.com/new")
        self.assertEqual(self.link.code, "managed")

    def test_edit_can_remove_a_link_password(self):
        self.link.set_link_password("something-secret")
        self.link.save()

        self.client.post(
            f"/links/{self.link.pk}/edit",
            {
                "destination": self.link.destination,
                "title": "",
                "expires_at": "",
                "password": "",
                "remove_password": "on",
                "is_active": "on",
            },
        )

        self.link.refresh_from_db()
        self.assertFalse(self.link.has_password)

    def test_delete_is_a_soft_delete_and_needs_post(self):
        self.assertEqual(self.client.get(f"/links/{self.link.pk}/delete").status_code, 405)

        response = self.client.post(f"/links/{self.link.pk}/delete")

        self.assertRedirects(response, "/dashboard")
        self.link.refresh_from_db()
        self.assertIsNotNone(self.link.deleted_at)
        self.assertTrue(ShortURL.objects.filter(pk=self.link.pk).exists())

    def test_another_user_cannot_reach_a_link_by_changing_the_id(self):
        self.client.force_login(self.make_user("other@example.com"))

        for path in ["", "/edit", "/analytics"]:
            self.assertEqual(self.client.get(f"/links/{self.link.pk}{path}").status_code, 404, path)

        self.assertEqual(self.client.post(f"/links/{self.link.pk}/delete").status_code, 404)
        self.assertEqual(
            self.client.post(
                f"/links/{self.link.pk}/edit",
                {"destination": "https://evil.example/", "title": "", "expires_at": "", "password": ""},
            ).status_code,
            404,
        )

        self.link.refresh_from_db()
        self.assertEqual(self.link.destination, "https://example.com/old")
        self.assertIsNone(self.link.deleted_at)

    def test_analytics_page_renders_for_an_unclicked_link(self):
        response = self.client.get(f"/links/{self.link.pk}/analytics?days=7")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["series"]), 7)
        self.assertEqual(response.context["peak"], 0)


class APIKeyPageTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.user = self.make_user()
        self.client.force_login(self.user)

    def test_creating_a_key_shows_the_secret_once(self):
        response = self.client.post("/keys", {"name": "deploy script", "expires_at": ""})

        self.assertEqual(response.status_code, 200)
        token = response.context["issued"]
        self.assertTrue(token.startswith("usk_"))
        self.assertContains(response, token)

        self.assertNotContains(self.client.get("/keys"), token)

    def test_revoking_a_key_needs_post_and_only_works_on_your_own(self):
        mine, _ = create_api_key(self.user, "mine")
        theirs, _ = create_api_key(self.make_user("other@example.com"), "theirs")

        self.assertEqual(self.client.get(f"/keys/{mine.pk}/revoke").status_code, 405)
        self.assertEqual(self.client.post(f"/keys/{theirs.pk}/revoke").status_code, 404)
        self.assertRedirects(self.client.post(f"/keys/{mine.pk}/revoke"), "/keys")

        mine.refresh_from_db()
        theirs.refresh_from_db()
        self.assertFalse(mine.is_active)
        self.assertTrue(theirs.is_active)

    def test_keys_page_requires_signing_in(self):
        self.client.logout()
        self.assertEqual(self.client.get("/keys").status_code, 302)
        self.assertEqual(APIKey.objects.count(), 0)


class PublicPageTests(AppTestCase):
    def test_api_docs_render_without_an_account(self):
        response = self.client.get("/docs")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "/api/v1/urls/")

    def test_unknown_page_returns_404(self):
        self.assertEqual(self.client.get("/no-such-page").status_code, 404)
