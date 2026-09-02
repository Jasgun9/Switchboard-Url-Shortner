import logging
from urllib.parse import urlsplit

from django.conf import settings
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from core import ratelimit
from core.clientinfo import client_ip
from redirector import unlock
from shortener import resolver
from shortener.models import ShortURL
from shortener.tasks import enqueue_click

log = logging.getLogger(__name__)


def index(request):
    return HttpResponseRedirect(settings.WEB_DOMAIN)


def robots(request):
    return HttpResponse("User-agent: *\nDisallow: /\n", content_type="text/plain")


@require_http_methods(["GET", "HEAD", "POST"])
def resolve(request, code):
    ip = client_ip(request)

    verdict = ratelimit.consume("redirect", ip)
    if not verdict.allowed:
        return _rate_limited(request, verdict)

    payload = resolver.resolve(code)
    if payload is None:
        return not_found(request)

    if payload["has_password"] and not unlock.is_unlocked(request, code, payload):
        return _password_gate(request, code, payload, ip)

    return _redirect_to(request, code, payload, ip)


def _redirect_to(request, code, payload, ip):
    if request.method != "HEAD":
        enqueue_click(
            payload["id"],
            ip,
            request.META.get("HTTP_USER_AGENT", ""),
            _referrer_host(request),
            timezone.now(),
        )

    response = HttpResponseRedirect(payload["destination"])
    # Redirects must never be cached by Cloudflare or the browser: a cached hop
    # would keep working after the link expires or is deleted, and would hide
    # the click from analytics.
    response["Cache-Control"] = "private, no-store, max-age=0"
    return response


def _password_gate(request, code, payload, ip):
    error = None

    if request.method == "POST":
        verdict = ratelimit.consume("link_password", f"{ip}:{code}")
        if not verdict.allowed:
            return _rate_limited(request, verdict)

        # The hash is deliberately not cached, so this is the one path that
        # reads the row from the database.
        url = ShortURL.objects.alive().filter(pk=payload["id"]).first()
        if url is None:
            return not_found(request)

        if url.check_link_password(request.POST.get("password", "")):
            ratelimit.reset("link_password", f"{ip}:{code}")
            response = _redirect_to(request, code, payload, ip)
            unlock.issue(response, code, payload)
            return response

        log.info("failed link password attempt for /%s", code)
        error = "Incorrect password."

    return render(
        request,
        "redirector/password.html",
        {"code": code, "error": error},
        status=401 if error else 200,
    )


def _rate_limited(request, verdict):
    response = render(request, "redirector/rate_limited.html", {"retry_after": verdict.retry_after}, status=429)
    response["Retry-After"] = str(verdict.retry_after)
    return response


def _referrer_host(request):
    referrer = request.META.get("HTTP_REFERER", "")
    if not referrer:
        return ""
    return (urlsplit(referrer).hostname or "")[:255]


def not_found(request, exception=None):
    return render(request, "redirector/not_found.html", status=404)


def server_error(request):
    return render(request, "redirector/error.html", status=500)
