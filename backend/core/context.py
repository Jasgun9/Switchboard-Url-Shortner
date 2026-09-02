from django.conf import settings


def site(request):
    """Public origins of the two services, for templates in both of them."""
    return {
        "web_domain": settings.WEB_DOMAIN,
        "short_domain": settings.SHORT_DOMAIN.rstrip("/"),
    }
