import logging

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, Q, Sum
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods, require_POST

from core import ratelimit
from core.clientinfo import client_ip
from shortener.analytics import summary
from shortener.models import AliasTaken, APIKey, CodeGenerationError, ShortURL, create_api_key, create_short_url
from web.forms import APIKeyForm, LinkForm, LoginForm, RegisterForm, ShortenForm

log = logging.getLogger(__name__)

PAGE_SIZE = 20
ANALYTICS_WINDOWS = [7, 30, 90]

ORDERING_CHOICES = {
    "-created_at": "Newest first",
    "created_at": "Oldest first",
    "-click_count": "Most clicks",
    "click_count": "Fewest clicks",
}


def owned_links(user):
    return ShortURL.objects.filter(owner=user, deleted_at__isnull=True)


def home(request):
    form = ShortenForm(request.POST or None)
    created = None

    if request.method == "POST" and form.is_valid():
        scope = "user_create" if request.user.is_authenticated else "anon_create"
        identifier = f"user:{request.user.pk}" if request.user.is_authenticated else f"ip:{client_ip(request)}"

        if not ratelimit.consume(scope, identifier).allowed:
            form.add_error(None, "You have created too many links recently. Try again later.")
        else:
            try:
                created = create_short_url(
                    destination=form.cleaned_data["destination"],
                    owner=request.user if request.user.is_authenticated else None,
                    alias=form.cleaned_data["alias"],
                    expires_at=form.cleaned_data["expires_at"],
                    password=form.cleaned_data["password"],
                )
            except AliasTaken:
                form.add_error("alias", "That alias is already in use.")
            except CodeGenerationError:
                form.add_error(None, "Could not allocate a short code. Please try again.")
            else:
                form = ShortenForm()

    return render(request, "web/home.html", {"form": form, "created": created})


@require_http_methods(["GET", "POST"])
def register(request):
    if request.user.is_authenticated:
        return redirect("dashboard")

    form = RegisterForm(request.POST or None)

    if request.method == "POST":
        if not ratelimit.consume("register", f"ip:{client_ip(request)}").allowed:
            form.add_error(None, "Too many registration attempts. Try again later.")
        elif form.is_valid():
            user = form.save()
            login(request, user)
            log.info("registered user %s", user.pk)
            return redirect("dashboard")

    return render(request, "web/register.html", {"form": form})


@require_http_methods(["GET", "POST"])
def login_view(request):
    if request.user.is_authenticated:
        return redirect("dashboard")

    form = LoginForm(request.POST or None)
    identifier = f"ip:{client_ip(request)}"

    if request.method == "POST":
        if not ratelimit.consume("login", identifier).allowed:
            form.add_error(None, "Too many sign-in attempts. Try again later.")
        elif form.is_valid():
            user = authenticate(
                request,
                username=form.cleaned_data["email"].strip().lower(),
                password=form.cleaned_data["password"],
            )
            if user is None:
                log.info("failed login attempt from %s", identifier)
                form.add_error(None, "Incorrect email or password.")
            else:
                login(request, user)
                ratelimit.reset("login", identifier)
                return HttpResponseRedirect(_safe_next(request) or reverse("dashboard"))

    return render(request, "web/login.html", {"form": form, "next": request.GET.get("next", "")})


def _safe_next(request):
    destination = request.POST.get("next") or request.GET.get("next")
    if destination and url_has_allowed_host_and_scheme(destination, allowed_hosts={request.get_host()}):
        return destination
    return None


@require_POST
def logout_view(request):
    logout(request)
    return redirect("home")


@login_required
def dashboard(request):
    links = owned_links(request.user)

    search = request.GET.get("search", "").strip()
    if search:
        links = links.filter(
            Q(code__icontains=search) | Q(title__icontains=search) | Q(destination__icontains=search)
        )

    status = request.GET.get("status", "")
    now = timezone.now()
    if status == "active":
        links = links.filter(is_active=True).filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
    elif status == "expired":
        links = links.filter(expires_at__lte=now)
    elif status == "disabled":
        links = links.filter(is_active=False)

    ordering = request.GET.get("ordering", "-created_at")
    if ordering not in ORDERING_CHOICES:
        ordering = "-created_at"

    page = Paginator(links.order_by(ordering, "-id"), PAGE_SIZE).get_page(request.GET.get("page"))

    # Totals are for the whole account, not the filtered page.
    everything = owned_links(request.user)
    totals = everything.aggregate(links=Count("id"), clicks=Sum("click_count"))

    return render(
        request,
        "web/dashboard.html",
        {
            "page": page,
            "total_links": totals["links"],
            "total_clicks": totals["clicks"] or 0,
            "active_links": everything.filter(is_active=True)
            .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
            .count(),
            "search": search,
            "status": status,
            "ordering": ordering,
            "ordering_choices": ORDERING_CHOICES,
            "query": _querystring(request, exclude="page"),
            "section": "links",
        },
    )


def _querystring(request, exclude):
    params = request.GET.copy()
    params.pop(exclude, None)
    encoded = params.urlencode()
    return f"&{encoded}" if encoded else ""


@login_required
@require_http_methods(["GET", "POST"])
def link_create(request):
    form = LinkForm(request.POST or None)

    if request.method == "POST" and form.is_valid():
        if not ratelimit.consume("user_create", f"user:{request.user.pk}").allowed:
            form.add_error(None, "You have created too many links recently. Try again later.")
        else:
            try:
                link = create_short_url(
                    destination=form.cleaned_data["destination"],
                    owner=request.user,
                    title=form.cleaned_data["title"],
                    alias=form.cleaned_data["alias"],
                    expires_at=form.cleaned_data["expires_at"],
                    password=form.cleaned_data["password"],
                )
            except AliasTaken:
                form.add_error("alias", "That alias is already in use.")
            except CodeGenerationError:
                form.add_error(None, "Could not allocate a short code. Please try again.")
            else:
                messages.success(request, f"Created /{link.code}.")
                return redirect("link-detail", pk=link.pk)

    return render(request, "web/link_form.html", {"form": form, "link": None, "section": "links"})


@login_required
@require_http_methods(["GET", "POST"])
def link_edit(request, pk):
    link = get_object_or_404(owned_links(request.user), pk=pk)
    form = LinkForm(request.POST or None, instance=link, initial=LinkForm.initial_from(link))

    if request.method == "POST" and form.is_valid():
        form.apply_to(link)
        messages.success(request, "Changes saved.")
        return redirect("link-detail", pk=link.pk)

    return render(request, "web/link_form.html", {"form": form, "link": link, "section": "links"})


@login_required
def link_detail(request, pk):
    link = get_object_or_404(owned_links(request.user), pk=pk)
    return render(request, "web/link_detail.html", {"link": link, "section": "links"})


@login_required
@require_POST
def link_delete(request, pk):
    link = get_object_or_404(owned_links(request.user), pk=pk)
    link.soft_delete()
    messages.success(request, f"Deleted /{link.code}. It no longer redirects.")
    return redirect("dashboard")


@login_required
def link_analytics(request, pk):
    link = get_object_or_404(owned_links(request.user), pk=pk)

    try:
        days = int(request.GET.get("days", 30))
    except ValueError:
        days = 30

    data = summary(link, days)
    timeseries = data["timeseries"]
    peak = max((point["clicks"] for point in timeseries), default=0)

    return render(
        request,
        "web/link_analytics.html",
        {
            "link": link,
            "data": data,
            "days": data["window_days"],
            "windows": ANALYTICS_WINDOWS,
            "peak": peak,
            "series": [
                {**point, "height": round(point["clicks"] / peak * 100) if peak else 0}
                for point in timeseries
            ],
            "first_date": timeseries[0]["date"],
            "last_date": timeseries[-1]["date"],
            "section": "links",
            "breakdowns": [
                ("Countries", _with_share(data["countries"])),
                ("Regions", _with_share(data["regions"])),
                ("Cities", _with_share(data["cities"])),
                ("Devices", _with_share(data["devices"])),
                ("Browsers", _with_share(data["browsers"])),
                ("Operating systems", _with_share(data["operating_systems"])),
                ("Referrers", _with_share(data["referrers"])),
            ],
        },
    )


def _with_share(rows):
    # Bar widths as percentages of the top row; templates cannot do arithmetic.
    peak = max((row["clicks"] for row in rows), default=0)
    return [{**row, "share": round(row["clicks"] / peak * 100) if peak else 0} for row in rows]


@login_required
@require_http_methods(["GET", "POST"])
def api_keys(request):
    form = APIKeyForm(request.POST or None)
    issued = None

    if request.method == "POST" and form.is_valid():
        key, issued = create_api_key(request.user, form.cleaned_data["name"], form.cleaned_data["expires_at"])
        log.info("api key %s created for user %s", key.prefix, request.user.pk)
        form = APIKeyForm()

    return render(
        request,
        "web/api_keys.html",
        {
            "form": form,
            "issued": issued,
            "keys": APIKey.objects.filter(owner=request.user),
            "section": "keys",
        },
    )


@login_required
@require_POST
def api_key_revoke(request, pk):
    key = get_object_or_404(APIKey, pk=pk, owner=request.user)
    key.revoke()
    messages.success(request, f"Revoked “{key.name}”.")
    return redirect("api-keys")


def api_docs(request):
    return render(request, "web/api_docs.html", {"section": "docs"})


def robots(request):
    """Only the home page and the API reference are worth crawling.

    Everything else is either behind a login or per-user, and /api/ answers
    JSON that has no business in a search index.
    """
    lines = [
        "User-agent: *",
        "Disallow: /admin/",
        "Disallow: /api/",
        "Disallow: /dashboard",
        "Disallow: /links/",
        "Disallow: /keys",
        "Disallow: /login",
        "Disallow: /register",
        "",
        f"Sitemap: {settings.WEB_DOMAIN.rstrip('/')}/sitemap.xml",
        "",
    ]
    return HttpResponse("\n".join(lines), content_type="text/plain")


def not_found(request, exception=None):
    return render(request, "web/404.html", status=404)


def server_error(request):
    return render(request, "web/500.html", status=500)
