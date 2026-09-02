from datetime import timedelta
from unittest import mock

from django.core.cache import cache as django_cache
from django.test import override_settings
from django.utils import timezone

from shortener import cache
from shortener.models import ShortURL, create_short_url
from tests.base import AppTestCase

REDIRECT_URLS = "config.urls_redirect"


@override_settings(ROOT_URLCONF=REDIRECT_URLS)
class RedirectTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.url = ShortURL.objects.create(code="abc1234", destination="https://example.com/target")

    def test_known_code_redirects(self):
        response = self.client.get("/abc1234")
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "https://example.com/target")

    def test_redirect_is_not_cacheable(self):
        response = self.client.get("/abc1234")
        self.assertIn("no-store", response["Cache-Control"])

    def test_unknown_code_returns_404(self):
        self.assertEqual(self.client.get("/nosuch1").status_code, 404)

    def test_expired_link_stops_redirecting(self):
        self.url.expires_at = timezone.now() - timedelta(minutes=1)
        self.url.save()
        self.assertEqual(self.client.get("/abc1234").status_code, 404)

    def test_soft_deleted_link_stops_redirecting(self):
        self.url.soft_delete()
        self.assertEqual(self.client.get("/abc1234").status_code, 404)

    def test_disabled_link_stops_redirecting(self):
        self.url.is_active = False
        self.url.save()
        self.assertEqual(self.client.get("/abc1234").status_code, 404)

    def test_click_is_handed_to_celery_not_written_inline(self):
        with mock.patch("redirector.views.enqueue_click") as enqueue:
            self.client.get("/abc1234", HTTP_USER_AGENT="Mozilla/5.0", HTTP_REFERER="https://news.example.org/a")

        enqueue.assert_called_once()
        args = enqueue.call_args.args
        self.assertEqual(args[0], self.url.pk)
        self.assertEqual(args[3], "news.example.org")

    def test_broker_outage_does_not_break_the_redirect(self):
        from kombu.exceptions import OperationalError

        with mock.patch("shortener.tasks.record_click.delay", side_effect=OperationalError("down")):
            response = self.client.get("/abc1234")

        self.assertEqual(response.status_code, 302)


@override_settings(ROOT_URLCONF=REDIRECT_URLS)
class ResolveCacheTests(AppTestCase):
    def test_second_request_is_served_without_touching_the_database(self):
        ShortURL.objects.create(code="cached1", destination="https://example.com/x")

        self.client.get("/cached1")
        with self.assertNumQueries(0):
            response = self.client.get("/cached1")

        self.assertEqual(response["Location"], "https://example.com/x")

    def test_editing_a_link_invalidates_the_cache(self):
        url = ShortURL.objects.create(code="edited1", destination="https://example.com/old")
        self.client.get("/edited1")

        url.destination = "https://example.com/new"
        url.save()

        response = self.client.get("/edited1")
        self.assertEqual(response["Location"], "https://example.com/new")

    def test_deleting_a_link_invalidates_the_cache(self):
        url = ShortURL.objects.create(code="dropped", destination="https://example.com/x")
        self.client.get("/dropped")

        url.soft_delete()

        self.assertEqual(self.client.get("/dropped").status_code, 404)

    def test_stale_cache_entry_cannot_outlive_the_expiry(self):
        url = ShortURL.objects.create(
            code="ticking", destination="https://example.com/x", expires_at=timezone.now() + timedelta(seconds=30)
        )
        self.client.get("/ticking")

        # Simulate a cached payload that survived past the link's expiry.
        payload = cache.payload_for(url)
        payload["expires_at"] = (timezone.now() - timedelta(seconds=1)).isoformat()
        django_cache.set("resolve:ticking", payload, 300)

        self.assertEqual(self.client.get("/ticking").status_code, 404)

    def test_a_reclaimed_alias_redirects_to_the_new_destination(self):
        """The old link's cached entry must not survive the handover."""
        first = ShortURL.objects.create(code="promo", destination="https://example.com/old")
        self.assertEqual(self.client.get("/promo")["Location"], "https://example.com/old")

        first.soft_delete()
        create_short_url(destination="https://example.com/new", alias="promo")

        self.assertEqual(self.client.get("/promo")["Location"], "https://example.com/new")

    def test_a_deleted_alias_stops_redirecting_before_it_is_reclaimed(self):
        first = ShortURL.objects.create(code="promo", destination="https://example.com/old")
        self.client.get("/promo")

        first.soft_delete()

        self.assertEqual(self.client.get("/promo").status_code, 404)

    def test_unknown_codes_are_negatively_cached(self):
        self.client.get("/scanned")
        with self.assertNumQueries(0):
            self.assertEqual(self.client.get("/scanned").status_code, 404)


@override_settings(ROOT_URLCONF=REDIRECT_URLS)
class PasswordProtectionTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.url = ShortURL(code="locked1", destination="https://example.com/private")
        self.url.set_link_password("open-sesame")
        self.url.save()

    def test_get_shows_the_password_form_instead_of_redirecting(self):
        response = self.client.get("/locked1")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "example.com/private")

    def test_wrong_password_is_rejected(self):
        response = self.client.post("/locked1", {"password": "nope"})
        self.assertEqual(response.status_code, 401)

    def test_correct_password_redirects_and_unlocks_subsequent_requests(self):
        response = self.client.post("/locked1", {"password": "open-sesame"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "https://example.com/private")

        follow_up = self.client.get("/locked1")
        self.assertEqual(follow_up.status_code, 302)

    def test_changing_the_password_invalidates_existing_unlock_cookies(self):
        self.client.post("/locked1", {"password": "open-sesame"})

        self.url.set_link_password("a-new-password")
        self.url.save()

        self.assertEqual(self.client.get("/locked1").status_code, 200)

    @override_settings(RATE_LIMITS={"redirect": (100, 60), "link_password": (3, 900)})
    def test_password_attempts_are_rate_limited(self):
        for _ in range(3):
            self.client.post("/locked1", {"password": "wrong"})

        response = self.client.post("/locked1", {"password": "wrong"})
        self.assertEqual(response.status_code, 429)
        self.assertIn("Retry-After", response)


@override_settings(ROOT_URLCONF=REDIRECT_URLS, RATE_LIMITS={"redirect": (3, 60)})
class RedirectRateLimitTests(AppTestCase):
    def test_abusive_traffic_is_throttled(self):
        ShortURL.objects.create(code="hotlink", destination="https://example.com/x")

        for _ in range(3):
            self.assertEqual(self.client.get("/hotlink").status_code, 302)

        self.assertEqual(self.client.get("/hotlink").status_code, 429)

    def test_redis_outage_fails_open(self):
        import redis

        ShortURL.objects.create(code="openfai", destination="https://example.com/x")
        self.redis.pipeline = mock.Mock(side_effect=redis.ConnectionError("down"))

        self.assertEqual(self.client.get("/openfai").status_code, 302)
