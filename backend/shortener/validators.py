import ipaddress
import re
from urllib.parse import urlsplit

from django.conf import settings
from django.core.exceptions import ValidationError

ALIAS_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

# Paths either service already serves, plus names that would be confusing or
# valuable enough that nobody should be able to claim them.
RESERVED_ALIASES = {
    "about", "account", "admin", "analytics", "api", "assets", "auth", "billing",
    "blog", "contact", "css", "dashboard", "docs", "favicon.ico", "health",
    "help", "img", "images", "js", "login", "logout", "media", "metrics", "new",
    "pricing", "privacy", "qr", "register", "reset", "robots.txt", "root",
    "security", "settings", "signin", "signup", "sitemap.xml", "static",
    "status", "support", "terms", "urls", "user", "users", "www",
}

ALLOWED_SCHEMES = {"http", "https"}

MAX_DESTINATION_LENGTH = 2048


def validate_alias(alias):
    """Normalise and check a user supplied alias.

    Returns the stored form (lower case). Aliases are case-folded so that
    /GitHub and /github cannot be handed to two different people.
    """
    if alias != alias.strip():
        raise ValidationError("Alias must not start or end with whitespace.")

    normalised = alias.lower()

    if len(normalised) < settings.ALIAS_MIN_LENGTH:
        raise ValidationError(f"Alias must be at least {settings.ALIAS_MIN_LENGTH} characters.")
    if len(normalised) > settings.ALIAS_MAX_LENGTH:
        raise ValidationError(f"Alias must be at most {settings.ALIAS_MAX_LENGTH} characters.")
    if not ALIAS_PATTERN.match(normalised):
        raise ValidationError(
            "Alias may only contain letters, digits, hyphens and underscores, and must start with a letter or digit."
        )
    if normalised in RESERVED_ALIASES:
        raise ValidationError("That alias is reserved.")

    return normalised


def _is_private_host(host):
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return host in {"localhost", "localhost.localdomain"} or host.endswith(".localhost")
    return (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
    )


def validate_destination(url):
    """Check a destination URL without ever fetching it.

    Fetching would turn every submission into an SSRF primitive, so the only
    thing that happens here is parsing.
    """
    url = url.strip()

    if not url:
        raise ValidationError("A destination URL is required.")
    if len(url) > MAX_DESTINATION_LENGTH:
        raise ValidationError(f"Destination URL must be at most {MAX_DESTINATION_LENGTH} characters.")
    if any(char in url for char in "\r\n\t"):
        raise ValidationError("Destination URL must not contain control characters.")

    parts = urlsplit(url)

    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        raise ValidationError("Only http:// and https:// URLs can be shortened.")
    if not parts.hostname:
        raise ValidationError("Destination URL must include a hostname.")
    if "@" in parts.netloc:
        raise ValidationError("Destination URL must not contain credentials.")

    host = parts.hostname.lower()

    if settings.BLOCK_PRIVATE_DESTINATIONS and _is_private_host(host):
        raise ValidationError("Destination URL must point at a public host.")

    short_host = urlsplit(settings.SHORT_DOMAIN).hostname
    if short_host and host == short_host.lower():
        raise ValidationError("Destination URL must not point back at the short link domain.")

    return url
