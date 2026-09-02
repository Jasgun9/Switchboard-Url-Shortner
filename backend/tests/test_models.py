from datetime import timedelta
from unittest import mock

from django.db import IntegrityError
from django.utils import timezone

from shortener import resolver
from shortener.models import (
    AliasTaken,
    ClickEvent,
    CodeGenerationError,
    ShortURL,
    create_short_url,
    save_with_random_code,
)
from tests.base import AppTestCase


class SaveWithRandomCodeTests(AppTestCase):
    def test_retries_when_a_generated_code_already_exists(self):
        ShortURL.objects.create(code="taken12", destination="https://example.com/first")

        with mock.patch(
            "shortener.models.generate_code", side_effect=["taken12", "taken12", "free123"]
        ) as generator:
            url = save_with_random_code(ShortURL(destination="https://example.com/second"))

        self.assertEqual(url.code, "free123")
        self.assertEqual(generator.call_count, 3)
        self.assertEqual(ShortURL.objects.count(), 2)

    def test_gives_up_after_the_configured_attempts(self):
        ShortURL.objects.create(code="taken12", destination="https://example.com/first")

        with mock.patch("shortener.models.generate_code", return_value="taken12"):
            with self.assertRaises(CodeGenerationError):
                save_with_random_code(ShortURL(destination="https://example.com/second"))

        self.assertEqual(ShortURL.objects.count(), 1)

    def test_duplicate_code_is_rejected_by_the_database(self):
        ShortURL.objects.create(code="portfolio", destination="https://example.com/a")
        with self.assertRaises(IntegrityError):
            ShortURL.objects.create(code="portfolio", destination="https://example.com/b")


class ShortURLStateTests(AppTestCase):
    def test_expiry_is_evaluated_against_current_time(self):
        past = ShortURL.objects.create(
            code="expired", destination="https://example.com/", expires_at=timezone.now() - timedelta(seconds=1)
        )
        future = ShortURL.objects.create(
            code="livelnk", destination="https://example.com/", expires_at=timezone.now() + timedelta(days=1)
        )
        self.assertTrue(past.is_expired)
        self.assertFalse(past.is_resolvable())
        self.assertFalse(future.is_expired)
        self.assertTrue(future.is_resolvable())

    def test_link_password_is_hashed_and_verifiable(self):
        url = ShortURL(code="secret1", destination="https://example.com/")
        url.set_link_password("hunter2000")
        url.save()

        self.assertNotIn("hunter2000", url.password_hash)
        self.assertTrue(url.check_link_password("hunter2000"))
        self.assertFalse(url.check_link_password("wrong"))
        self.assertIsNotNone(url.password_updated_at)

    def test_soft_delete_stops_resolution_but_keeps_the_row(self):
        url = ShortURL.objects.create(code="gonesoon", destination="https://example.com/")
        url.soft_delete()

        url.refresh_from_db()
        self.assertFalse(url.is_resolvable())
        self.assertFalse(ShortURL.objects.alive().filter(code="gonesoon").exists())
        self.assertTrue(ShortURL.objects.filter(code="gonesoon").exists())
        self.assertTrue(url.is_code_released)


class CodeReuseTests(AppTestCase):
    """A deleted or expired link gives its alias back."""

    def test_deleted_links_alias_can_be_claimed_again(self):
        first = create_short_url(destination="https://example.com/old", alias="promo")
        first.soft_delete()

        second = create_short_url(destination="https://example.com/new", alias="promo")

        self.assertNotEqual(first.pk, second.pk)
        self.assertEqual(second.code, "promo")
        self.assertEqual(ShortURL.objects.alive().get(code="promo").pk, second.pk)

    def test_expired_links_alias_can_be_claimed_again(self):
        first = create_short_url(
            destination="https://example.com/old",
            alias="promo",
            expires_at=timezone.now() + timedelta(seconds=1),
        )
        ShortURL.objects.filter(pk=first.pk).update(expires_at=timezone.now() - timedelta(seconds=1))

        second = create_short_url(destination="https://example.com/new", alias="promo")

        self.assertEqual(ShortURL.objects.alive().get(code="promo").pk, second.pk)
        first.refresh_from_db()
        self.assertTrue(first.is_code_released)

    def test_reclaiming_keeps_the_old_links_history(self):
        first = create_short_url(destination="https://example.com/old", alias="promo")
        ClickEvent.objects.create(short_url=first)
        first.soft_delete()

        create_short_url(destination="https://example.com/new", alias="promo")

        first.refresh_from_db()
        self.assertEqual(first.clicks.count(), 1)
        self.assertEqual(first.destination, "https://example.com/old")

    def test_a_live_alias_is_still_protected(self):
        create_short_url(destination="https://example.com/old", alias="promo")

        with self.assertRaises(AliasTaken):
            create_short_url(destination="https://example.com/new", alias="promo")

    def test_a_disabled_link_keeps_its_alias(self):
        """Disabling is meant to be reversible, so it must not free the code."""
        first = create_short_url(destination="https://example.com/old", alias="promo")
        first.is_active = False
        first.save()

        with self.assertRaises(AliasTaken):
            create_short_url(destination="https://example.com/new", alias="promo")

    def test_only_one_claimant_wins_a_released_alias(self):
        """Both requests see the alias as free; the constraint picks the winner."""
        first = create_short_url(destination="https://example.com/old", alias="promo")
        first.soft_delete()

        winner = create_short_url(destination="https://example.com/a", alias="promo")
        with self.assertRaises(AliasTaken):
            create_short_url(destination="https://example.com/b", alias="promo")

        self.assertEqual(ShortURL.objects.filter(code="promo", code_released_at__isnull=True).count(), 1)
        self.assertEqual(ShortURL.objects.alive().get(code="promo").pk, winner.pk)

    def test_released_rows_may_share_a_code(self):
        for _ in range(3):
            create_short_url(destination="https://example.com/", alias="promo").soft_delete()

        self.assertEqual(ShortURL.objects.filter(code="promo").count(), 3)

    def test_a_released_link_never_resolves_even_before_it_is_reclaimed(self):
        url = create_short_url(destination="https://example.com/", alias="promo")
        url.soft_delete()

        self.assertIsNone(resolver.resolve("promo"))
