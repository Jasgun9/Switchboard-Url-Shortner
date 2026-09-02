import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.db.models import F
from django.utils import timezone
from kombu.exceptions import OperationalError

from core.clientinfo import hash_ip
from shortener import cache, geo
from shortener.models import ClickEvent, ShortURL
from shortener.useragents import parse_user_agent

log = logging.getLogger(__name__)

SOFT_DELETE_GRACE_DAYS = 30


@shared_task(bind=True, max_retries=3, default_retry_delay=30, ignore_result=True)
def record_click(self, short_url_id, ip, user_agent, referrer_host, occurred_at):
    """Write one click row. GeoIP and UA parsing happen here, never in the request."""
    location = geo.lookup(ip)
    agent = parse_user_agent(user_agent)

    ClickEvent.objects.create(
        short_url_id=short_url_id,
        created_at=occurred_at,
        ip_hash=hash_ip(ip),
        country=location["country"],
        region=location["region"],
        city=location["city"],
        device=agent["device"],
        browser=agent["browser"],
        os=agent["os"],
        referrer_host=referrer_host[:255],
        user_agent=(user_agent or "")[:400],
    )

    # Counters are updated with an UPDATE rather than save() so concurrent
    # clicks do not overwrite each other. click_count is not part of the
    # resolve cache payload, so nothing needs invalidating here.
    ShortURL.objects.filter(pk=short_url_id).update(
        click_count=F("click_count") + 1,
        last_clicked_at=occurred_at,
    )


def enqueue_click(short_url_id, ip, user_agent, referrer_host, occurred_at):
    """Hand a click to Celery. A broker outage costs analytics, not redirects."""
    try:
        record_click.delay(short_url_id, ip, user_agent, referrer_host, occurred_at)
    except OperationalError as exc:
        log.warning("dropped click for url=%s, broker unavailable: %s", short_url_id, exc)


@shared_task(ignore_result=True)
def purge_old_clicks():
    """Retention policy: raw click rows are deleted once they age out."""
    cutoff = timezone.now() - timedelta(days=settings.CLICK_RETENTION_DAYS)
    deleted, _ = ClickEvent.objects.filter(created_at__lt=cutoff).delete()
    if deleted:
        log.info("purged %s click events older than %s", deleted, cutoff.date())
    return deleted


@shared_task(ignore_result=True)
def purge_deleted_urls():
    """Hard-delete links that have been in the soft-deleted state long enough.

    The grace period is retention, not reservation: the code is released the
    moment the link is deleted. This only decides when the row and its click
    events are finally dropped.
    """
    cutoff = timezone.now() - timedelta(days=SOFT_DELETE_GRACE_DAYS)
    stale = ShortURL.objects.filter(deleted_at__lt=cutoff)
    codes = list(stale.values_list("code", flat=True)[:1000])
    if not codes:
        return 0
    ShortURL.objects.filter(code__in=codes).delete()
    for code in codes:
        cache.forget(code)
    log.info("hard-deleted %s soft-deleted links", len(codes))
    return len(codes)
