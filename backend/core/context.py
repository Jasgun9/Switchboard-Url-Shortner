from django.conf import settings


def site(request):
    """Public origins and the canonical URL for the current page.

    Canonical is built from WEB_DOMAIN rather than the Host header, so a
    poisoned Host can't rewrite the canonical tag we hand to crawlers.
    """
    web = settings.WEB_DOMAIN.rstrip("/")
    return {
        "web_domain": web,
        "short_domain": settings.SHORT_DOMAIN.rstrip("/"),
        "canonical_url": web + request.path,
    }
