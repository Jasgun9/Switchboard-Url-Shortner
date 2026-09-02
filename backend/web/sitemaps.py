from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.sitemaps import Sitemap
from django.urls import reverse

# Only the two pages that look the same to everyone. Nothing behind a login
# belongs in a sitemap.
PUBLIC_PAGES = ["home", "api-docs"]


class _ConfiguredSite:
    # Stands in for django.contrib.sites, which this project doesn't install.

    def __init__(self, domain):
        self.domain = domain


class PublicPages(Sitemap):
    changefreq = "monthly"

    def items(self):
        return PUBLIC_PAGES

    def location(self, item):
        return reverse(item)

    def priority(self, item):
        return 1.0 if item == "home" else 0.8

    def get_urls(self, page=1, site=None, protocol=None):
        # Ignore whatever host the request arrived on and use the configured
        # public origin, so a crawler reaching the server by IP still gets
        # canonical URLs back.
        parts = urlsplit(settings.WEB_DOMAIN)
        return super().get_urls(page, _ConfiguredSite(parts.netloc), parts.scheme or "https")
