from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, override_settings

from shortener.shortcodes import ALPHABET, generate_code
from shortener.validators import validate_alias, validate_destination


class AliasValidationTests(SimpleTestCase):
    def test_aliases_are_case_folded(self):
        self.assertEqual(validate_alias("GitHub"), "github")

    def test_rejects_reserved_names(self):
        for alias in ["admin", "api", "login", "dashboard", "robots.txt"]:
            with self.assertRaises(ValidationError):
                validate_alias(alias)

    def test_rejects_bad_characters_and_shapes(self):
        for alias in ["with space", "sla/sh", "-leading", "dot.name", "a", "x" * 40, " padded "]:
            with self.assertRaises(ValidationError):
                validate_alias(alias)

    def test_accepts_normal_aliases(self):
        for alias in ["portfolio", "my-link", "my_link", "v2-launch", "abc123"]:
            self.assertEqual(validate_alias(alias), alias)


@override_settings(BLOCK_PRIVATE_DESTINATIONS=True, SHORT_DOMAIN="https://xyz.example.net")
class DestinationValidationTests(SimpleTestCase):
    def test_rejects_dangerous_schemes(self):
        for url in [
            "javascript:alert(1)",
            "data:text/html;base64,PHNjcmlwdD4=",
            "file:///etc/passwd",
            "ftp://example.com/x",
            "//example.com/protocol-relative",
        ]:
            with self.assertRaises(ValidationError):
                validate_destination(url)

    def test_rejects_private_and_loopback_hosts(self):
        for url in [
            "http://127.0.0.1/admin",
            "http://localhost:8000/",
            "http://10.0.0.5/",
            "http://192.168.1.1/",
            "http://169.254.169.254/latest/meta-data/",
        ]:
            with self.assertRaises(ValidationError):
                validate_destination(url)

    def test_rejects_credentials_and_control_characters(self):
        with self.assertRaises(ValidationError):
            validate_destination("https://user:pass@example.com/")
        with self.assertRaises(ValidationError):
            validate_destination("https://example.com/\r\nSet-Cookie: x=1")

    def test_rejects_pointing_back_at_the_short_domain(self):
        with self.assertRaises(ValidationError):
            validate_destination("https://xyz.example.net/abc123")

    def test_accepts_ordinary_urls(self):
        url = "https://example.com/some/very/long/path?a=1&b=2#frag"
        self.assertEqual(validate_destination(url), url)


class ShortCodeTests(SimpleTestCase):
    def test_codes_use_the_unambiguous_alphabet(self):
        code = generate_code(12)
        self.assertEqual(len(code), 12)
        self.assertTrue(set(code) <= set(ALPHABET))
        self.assertTrue(set("01lIO").isdisjoint(code))

    def test_codes_are_not_sequential(self):
        codes = {generate_code(7) for _ in range(200)}
        self.assertEqual(len(codes), 200)
