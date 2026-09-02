from datetime import timedelta
from unittest import mock

from django.test import override_settings
from django.utils import timezone

from shortener.analytics import summary
from shortener.models import ClickEvent, ShortURL
from shortener.tasks import purge_deleted_urls, purge_old_clicks, record_click
from shortener.useragents import parse_user_agent
from tests.base import AppTestCase

CHROME_WINDOWS = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)
SAFARI_IPHONE = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_1 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/17.1 Mobile/15E148 Safari/604.1"
)
GOOGLEBOT = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"


class UserAgentParsingTests(AppTestCase):
    def test_recognises_common_clients(self):
        desktop = parse_user_agent(CHROME_WINDOWS)
        self.assertEqual(desktop["device"], ClickEvent.Device.DESKTOP)
        self.assertEqual(desktop["browser"], "Chrome")
        self.assertEqual(desktop["os"], "Windows")

        mobile = parse_user_agent(SAFARI_IPHONE)
        self.assertEqual(mobile["device"], ClickEvent.Device.MOBILE)
        self.assertEqual(mobile["os"], "iOS")

        self.assertEqual(parse_user_agent(GOOGLEBOT)["device"], ClickEvent.Device.BOT)

    def test_missing_user_agent_is_not_guessed(self):
        parsed = parse_user_agent("")
        self.assertEqual(parsed["device"], ClickEvent.Device.UNKNOWN)
        self.assertEqual(parsed["browser"], "")


class RecordClickTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.url = ShortURL.objects.create(code="tracked", destination="https://example.com/")

    def test_writes_a_click_row_and_updates_the_counter(self):
        with mock.patch("shortener.tasks.geo.lookup", return_value={"country": "DE", "region": "Berlin", "city": "Berlin"}):
            record_click(self.url.pk, "203.0.113.10", CHROME_WINDOWS, "news.example.org", timezone.now())

        click = ClickEvent.objects.get()
        self.assertEqual(click.country, "DE")
        self.assertEqual(click.city, "Berlin")
        self.assertEqual(click.device, ClickEvent.Device.DESKTOP)
        self.assertEqual(click.browser, "Chrome")
        self.assertEqual(click.referrer_host, "news.example.org")

        self.url.refresh_from_db()
        self.assertEqual(self.url.click_count, 1)
        self.assertIsNotNone(self.url.last_clicked_at)

    def test_raw_ip_is_never_stored(self):
        record_click(self.url.pk, "203.0.113.10", CHROME_WINDOWS, "", timezone.now())

        click = ClickEvent.objects.get()
        self.assertNotIn("203.0.113.10", click.ip_hash)
        self.assertEqual(len(click.ip_hash), 32)

    def test_the_same_ip_hashes_consistently_for_unique_counts(self):
        record_click(self.url.pk, "203.0.113.10", CHROME_WINDOWS, "", timezone.now())
        record_click(self.url.pk, "203.0.113.10", SAFARI_IPHONE, "", timezone.now())
        record_click(self.url.pk, "198.51.100.7", CHROME_WINDOWS, "", timezone.now())

        self.assertEqual(ClickEvent.objects.values("ip_hash").distinct().count(), 2)

    def test_geoip_failure_does_not_lose_the_click(self):
        with mock.patch("shortener.tasks.geo.lookup", return_value={"country": "", "region": "", "city": ""}):
            record_click(self.url.pk, "10.0.0.1", CHROME_WINDOWS, "", timezone.now())

        self.assertEqual(ClickEvent.objects.count(), 1)


class AnalyticsSummaryTests(AppTestCase):
    def setUp(self):
        super().setUp()
        self.url = ShortURL.objects.create(code="summed1", destination="https://example.com/", click_count=4)
        now = timezone.now()
        rows = [
            ("DE", "desktop", "Chrome", "Windows", "news.example.org", 0),
            ("DE", "mobile", "Safari", "iOS", "", 1),
            ("FR", "desktop", "Firefox", "Linux", "news.example.org", 2),
            ("FR", "desktop", "Chrome", "Windows", "", 40),
        ]
        for country, device, browser, os_name, referrer, days_ago in rows:
            ClickEvent.objects.create(
                short_url=self.url,
                created_at=now - timedelta(days=days_ago),
                ip_hash=f"hash{country}{days_ago}",
                country=country,
                device=device,
                browser=browser,
                os=os_name,
                referrer_host=referrer,
            )

    def test_summary_respects_the_window(self):
        data = summary(self.url, days=30)

        self.assertEqual(data["clicks_in_window"], 3)
        self.assertEqual(data["total_clicks"], 4)
        self.assertEqual(len(data["timeseries"]), 30)

    def test_breakdowns_are_ranked(self):
        data = summary(self.url, days=30)

        self.assertEqual(data["countries"][0], {"value": "DE", "clicks": 2})
        self.assertEqual(
            {row["value"] for row in data["devices"]},
            {"desktop", "mobile"},
        )
        self.assertEqual(data["referrers"][0]["value"], "news.example.org")

    def test_unique_visitors_are_counted_by_hash(self):
        self.assertEqual(summary(self.url, days=365)["unique_visitors"], 4)


@override_settings(CLICK_RETENTION_DAYS=30)
class RetentionTaskTests(AppTestCase):
    def test_old_clicks_are_purged(self):
        url = ShortURL.objects.create(code="ageing1", destination="https://example.com/")
        ClickEvent.objects.create(short_url=url, created_at=timezone.now() - timedelta(days=45))
        ClickEvent.objects.create(short_url=url, created_at=timezone.now() - timedelta(days=5))

        self.assertEqual(purge_old_clicks(), 1)
        self.assertEqual(ClickEvent.objects.count(), 1)

    def test_long_deleted_links_are_hard_deleted_with_their_clicks(self):
        url = ShortURL.objects.create(
            code="removed", destination="https://example.com/", deleted_at=timezone.now() - timedelta(days=60)
        )
        ClickEvent.objects.create(short_url=url)
        recent = ShortURL.objects.create(
            code="pending", destination="https://example.com/", deleted_at=timezone.now()
        )

        self.assertEqual(purge_deleted_urls(), 1)
        self.assertFalse(ShortURL.objects.filter(pk=url.pk).exists())
        self.assertTrue(ShortURL.objects.filter(pk=recent.pk).exists())
        self.assertEqual(ClickEvent.objects.count(), 0)
