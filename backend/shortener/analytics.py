from datetime import timedelta

from django.db.models import Count
from django.db.models.functions import TruncDate
from django.utils import timezone

from shortener.models import ClickEvent

MAX_WINDOW_DAYS = 365
TOP_N = 10


def _breakdown(queryset, field, limit=TOP_N):
    rows = (
        queryset.values(field)
        .annotate(clicks=Count("id"))
        .order_by("-clicks", field)[:limit]
    )
    return [{"value": row[field] or "unknown", "clicks": row["clicks"]} for row in rows]


def summary(short_url, days=30):
    """Everything the analytics page needs, in a handful of grouped queries."""
    days = max(1, min(days, MAX_WINDOW_DAYS))
    since = timezone.now() - timedelta(days=days)
    clicks = ClickEvent.objects.filter(short_url=short_url, created_at__gte=since)

    by_day = (
        clicks.annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(clicks=Count("id"))
        .order_by("day")
    )
    timeseries = {row["day"].isoformat(): row["clicks"] for row in by_day}

    # Fill the gaps so the chart does not silently skip quiet days.
    today = timezone.localdate()
    series = []
    for offset in range(days - 1, -1, -1):
        day = (today - timedelta(days=offset)).isoformat()
        series.append({"date": day, "clicks": timeseries.get(day, 0)})

    return {
        "window_days": days,
        "total_clicks": short_url.click_count,
        "clicks_in_window": clicks.count(),
        "unique_visitors": clicks.exclude(ip_hash="").values("ip_hash").distinct().count(),
        "timeseries": series,
        "countries": _breakdown(clicks, "country"),
        "regions": _breakdown(clicks.exclude(region=""), "region"),
        "cities": _breakdown(clicks.exclude(city=""), "city"),
        "devices": _breakdown(clicks, "device", limit=5),
        "browsers": _breakdown(clicks, "browser"),
        "operating_systems": _breakdown(clicks, "os"),
        "referrers": _breakdown(clicks, "referrer_host"),
    }
